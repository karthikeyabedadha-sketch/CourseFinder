"""Supabase store implementation with full v2 parity to LocalStore.

Enforces the exact same editorial workflow, untrusted origin guards, and
filter sanitization as LocalStore.
"""

import re
from datetime import datetime, timedelta, timezone

from supabase import create_client

from app import workflow
from app.store.constants import (
    COURSE_REPORT_REASONS,
    DEFAULT_CATEGORIES,
    REVIEW_REPORT_REASONS,
    REVIEW_STATUSES,
    SUBMISSION_STATUSES,
    UNTRUSTED_ORIGINS,
)
from app.store.errors import DuplicateError, NotFoundError, StoreError
from app.store.local import TOGGLEABLE_FLAGS
from app.utils import new_id, normalise_url, search_terms, slugify

SAFE_FILTER_RE = re.compile(r"[^a-zA-Z0-9\s\-_]")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _safe_postgrest_term(term):
    """Strip characters that have syntactic meaning in PostgREST .or_() strings."""
    return SAFE_FILTER_RE.sub("", term or "").strip()[:50]


class SupabaseStore:
    backend = "supabase"

    def __init__(self, config, min_rating_count=3):
        self.url = config["SUPABASE_URL"]
        self.anon_key = config["SUPABASE_ANON_KEY"]
        self.service_key = config["SUPABASE_SERVICE_ROLE_KEY"]
        self.seed_sample = config.get("SEED_SAMPLE_COURSES", False)
        self.min_rating_count = int(min_rating_count)
        self.admin = create_client(self.url, self.service_key)
        self.auth_client = create_client(self.url, self.anon_key or self.service_key)

    def ping(self):
        self._table("categories").select("id").limit(1).execute()
        return True

    def create_owner_session(self, token_hash, expires_at):
        self._table("owner_sessions").delete().lte("expires_at", _now()).execute()
        self._table("owner_sessions").insert(
            {"token_hash": token_hash, "expires_at": expires_at}
        ).execute()

    def validate_owner_session(self, token_hash):
        result = (
            self._table("owner_sessions")
            .select("token_hash")
            .eq("token_hash", token_hash)
            .gt("expires_at", _now())
            .limit(1)
            .execute()
        )
        return bool(result.data)

    def revoke_owner_session(self, token_hash):
        self._table("owner_sessions").delete().eq("token_hash", token_hash).execute()

    def initialize(self):
        self.ensure_categories()

    def _table(self, name):
        return self.admin.table(name)

    def ensure_categories(self):
        existing = self._table("categories").select("id").limit(1).execute()
        if existing.data:
            return
        rows = []
        for index, (slug, name, description) in enumerate(DEFAULT_CATEGORIES):
            rows.append(
                {
                    "id": new_id(),
                    "slug": slug,
                    "name": name,
                    "description": description,
                    "sort_order": index,
                    "is_active": True,
                }
            )
        self._table("categories").insert(rows).execute()

    def seed_sample_courses(self):
        return None

    # --------------------------------------------------------------------- users
    def create_user(self, email, password, display_name=None):
        email = email.strip().lower()
        if self.get_user_by_email(email):
            raise DuplicateError("An account with this email already exists.")
        result = self.auth_client.auth.sign_up({"email": email, "password": password})
        user = result.user
        if not user:
            raise ValueError("Registration failed. If email confirmation is enabled, check your inbox.")
        profile = {
            "id": str(user.id),
            "email": email,
            "display_name": (display_name or email.split("@")[0]).strip(),
            "role": "user",
            "is_active": True,
        }
        self._table("profiles").upsert(profile).execute()
        return self.get_user(str(user.id))

    def authenticate(self, email, password):
        try:
            result = self.auth_client.auth.sign_in_with_password(
                {"email": email.strip().lower(), "password": password}
            )
        except Exception:
            return None
        if not result.user:
            return None
        user_id = str(result.user.id)
        profile = self.get_user(user_id)
        if not profile:
            self._table("profiles").insert(
                {
                    "id": user_id,
                    "email": email.strip().lower(),
                    "display_name": email.split("@")[0],
                    "role": "user",
                    "is_active": True,
                }
            ).execute()
            profile = self.get_user(user_id)
        return profile if profile and profile.get("is_active", True) else None

    def get_user(self, user_id):
        result = self._table("profiles").select(
            "id, email, display_name, role, is_active, created_at"
        ).eq("id", user_id).limit(1).execute()
        return result.data[0] if result.data else None

    def get_user_by_email(self, email):
        result = (
            self._table("profiles")
            .select("id, email, display_name, role, is_active, created_at")
            .eq("email", email.strip().lower())
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    def update_display_name(self, user_id, display_name):
        self._table("profiles").update({"display_name": display_name.strip()}).eq("id", user_id).execute()
        return self.get_user(user_id)

    def change_password(self, user_id, current_password, new_password):
        user = self.get_user(user_id)
        if not user or not self.authenticate(user["email"], current_password):
            return False
        self.admin.auth.admin.update_user_by_id(user_id, {"password": new_password})
        self.record_action(user_id, "user", user_id, "password_changed")
        return True

    def set_user_role(self, email, role, actor_label="setup-script"):
        if role not in ("user", "admin"):
            raise ValueError("Invalid role")
        user = self.get_user_by_email(email)
        if not user:
            raise ValueError("No user with that email")
        self._table("profiles").update({"role": role}).eq("id", user["id"]).execute()
        self.record_action(None, "user", user["id"], "role_changed", to_status=role, label=actor_label)
        return self.get_user(user["id"])

    def count_users(self):
        result = self._table("profiles").select("id", count="exact").execute()
        return result.count or 0

    def list_users(self, q=None, page=1, per_page=50):
        query = self._table("profiles").select(
            "id, email, display_name, role, is_active, created_at", count="exact"
        )
        if q:
            term = re.sub(r"[^a-zA-Z0-9@._+\-\s]", "", q).strip()[:80]
            if term:
                query = query.or_(
                    f"display_name.ilike.%{term}%,email.ilike.%{term}%"
                )
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 100))
        start = (page - 1) * per_page
        result = query.order("created_at", desc=True).range(start, start + per_page - 1).execute()
        total = result.count or 0
        return {
            "items": result.data or [],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max((total + per_page - 1) // per_page, 1),
        }

    def count_admins(self):
        result = (
            self._table("profiles")
            .select("id", count="exact")
            .eq("role", "admin")
            .eq("is_active", True)
            .execute()
        )
        return result.count or 0

    def update_user_role(self, user_id, role, actor_id=None):
        if role not in ("user", "admin"):
            raise ValueError("Invalid role")
        user = self.get_user(user_id)
        if not user:
            raise NotFoundError("User not found.")
        if user["role"] == role:
            return user
        self._table("profiles").update({"role": role}).eq("id", user_id).execute()
        self.record_action(
            actor_id,
            "user",
            user_id,
            "role_changed",
            from_status=user["role"],
            to_status=role,
            label=user["email"],
        )
        return self.get_user(user_id)

    def update_user_active(self, user_id, is_active, actor_id=None):
        user = self.get_user(user_id)
        if not user:
            raise NotFoundError("User not found.")
        is_active = bool(is_active)
        if bool(user.get("is_active", True)) == is_active:
            return user
        self._table("profiles").update({"is_active": is_active}).eq("id", user_id).execute()
        self.record_action(
            actor_id,
            "user",
            user_id,
            "account_status_changed",
            from_status="active" if user.get("is_active", True) else "suspended",
            to_status="active" if is_active else "suspended",
            label=user["email"],
        )
        return self.get_user(user_id)

    # ---------------------------------------------------------------- categories
    def list_categories(self, active_only=False):
        query = self._table("categories").select("*").order("sort_order")
        if active_only:
            query = query.eq("is_active", True)
        cats = query.execute().data or []
        courses = self._table("courses").select("category_id, status").execute().data or []
        pub_counts, tot_counts = {}, {}
        for c in courses:
            cid = c.get("category_id")
            if cid:
                tot_counts[cid] = tot_counts.get(cid, 0) + 1
                if c.get("status") == "published":
                    pub_counts[cid] = pub_counts.get(cid, 0) + 1
        for cat in cats:
            cat["published_count"] = pub_counts.get(cat["id"], 0)
            cat["total_count"] = tot_counts.get(cat["id"], 0)
        return cats

    def get_category(self, category_id):
        result = self._table("categories").select("*").eq("id", category_id).limit(1).execute()
        return result.data[0] if result.data else None

    def get_category_by_slug(self, slug):
        result = self._table("categories").select("*").eq("slug", slug).limit(1).execute()
        return result.data[0] if result.data else None

    def save_category(self, data, category_id=None, actor_id=None):
        payload = {
            "slug": slugify(data.get("slug") or data["name"]),
            "name": data["name"].strip(),
            "description": data.get("description") or "",
            "sort_order": int(data.get("sort_order") or 0),
            "is_active": bool(data.get("is_active", True)),
        }
        creating = category_id is None
        try:
            if category_id:
                self._table("categories").update(payload).eq("id", category_id).execute()
            else:
                category_id = new_id()
                payload["id"] = category_id
                self._table("categories").insert(payload).execute()
        except Exception as exc:
            raise DuplicateError("A category with that name already exists.") from exc
        if actor_id:
            self.record_action(actor_id, "category", category_id, "created" if creating else "edited", label=payload["name"])
        return self.get_category(category_id)

    def delete_category(self, category_id, actor_id=None):
        cat = self.get_category(category_id)
        if not cat:
            raise NotFoundError("Category not found.")
        used = self._table("courses").select("id", count="exact").eq("category_id", category_id).execute().count or 0
        if used:
            raise StoreError(f"Cannot delete “{cat['name']}” because {used} course(s) are assigned to it.")
        self._table("categories").delete().eq("id", category_id).execute()
        if actor_id:
            self.record_action(actor_id, "category", category_id, "deleted", label=cat["name"])

    # ----------------------------------------------------------------- providers
    def list_providers(self, with_published_courses=False):
        providers = self._table("providers").select("*").order("name").execute().data or []
        courses = self._table("courses").select("provider_id, status").execute().data or []
        pub_counts, tot_counts = {}, {}
        for c in courses:
            pid = c.get("provider_id")
            if pid:
                tot_counts[pid] = tot_counts.get(pid, 0) + 1
                if c.get("status") == "published":
                    pub_counts[pid] = pub_counts.get(pid, 0) + 1
        out = []
        for p in providers:
            p["published_count"] = pub_counts.get(p["id"], 0)
            p["total_count"] = tot_counts.get(p["id"], 0)
            if not with_published_courses or p["published_count"] > 0:
                out.append(p)
        return out

    def get_provider(self, provider_id):
        result = self._table("providers").select("*").eq("id", provider_id).limit(1).execute()
        return result.data[0] if result.data else None

    def get_or_create_provider(self, name, website_url=None):
        slug = slugify(name)
        result = self._table("providers").select("*").eq("slug", slug).limit(1).execute()
        if result.data:
            return result.data[0]
        payload = {"id": new_id(), "slug": slug, "name": name.strip(), "website_url": website_url or ""}
        self._table("providers").insert(payload).execute()
        return payload

    def save_provider(self, data, provider_id=None, actor_id=None):
        payload = {
            "slug": slugify(data.get("slug") or data["name"]),
            "name": data["name"].strip(),
            "website_url": data.get("website_url") or "",
        }
        creating = provider_id is None
        try:
            if provider_id:
                self._table("providers").update(payload).eq("id", provider_id).execute()
            else:
                provider_id = new_id()
                payload["id"] = provider_id
                self._table("providers").insert(payload).execute()
        except Exception as exc:
            raise DuplicateError("A provider with that name already exists.") from exc
        if actor_id:
            self.record_action(actor_id, "provider", provider_id, "created" if creating else "edited", label=payload["name"])
        return self.get_provider(provider_id)

    def delete_provider(self, provider_id, actor_id=None):
        prov = self.get_provider(provider_id)
        if not prov:
            raise NotFoundError("Provider not found.")
        used = self._table("courses").select("id", count="exact").eq("provider_id", provider_id).execute().count or 0
        if used:
            raise StoreError(f"Cannot delete “{prov['name']}” because {used} course(s) reference it.")
        self._table("providers").delete().eq("id", provider_id).execute()
        if actor_id:
            self.record_action(actor_id, "provider", provider_id, "deleted", label=prov["name"])

    # ------------------------------------------------------------------- courses
    def _attach_ratings(self, courses):
        if not courses:
            return courses
        ids = [c["id"] for c in courses]
        reviews = (
            self._table("reviews")
            .select("course_id, rating")
            .eq("status", "approved")
            .in_("course_id", ids)
            .execute()
            .data
            or []
        )
        buckets = {}
        for review in reviews:
            buckets.setdefault(review["course_id"], []).append(review["rating"])
        for course in courses:
            ratings = buckets.get(course["id"], [])
            count = len(ratings)
            course["review_count"] = count
            if count >= self.min_rating_count:
                course["user_rating"] = round(sum(ratings) / count, 1)
            else:
                course["user_rating"] = None
            course["user_rating_hidden_reason"] = (
                f"User ratings are shown after {self.min_rating_count} approved reviews."
                if 0 < count < self.min_rating_count
                else None
            )
            provider = course.get("providers") or {}
            category = course.get("categories") or {}
            if isinstance(provider, dict):
                course["provider_name"] = provider.get("name")
                course["provider_url"] = provider.get("website_url")
            if isinstance(category, dict):
                course["category_name"] = category.get("name")
                course["category_slug"] = category.get("slug")
        return courses

    def get_course(self, course_id=None, slug=None, include_unpublished=False):
        query = self._table("courses").select("*, providers(*), categories(*)")
        if course_id:
            query = query.eq("id", course_id)
        else:
            query = query.eq("slug", slug)
        result = query.limit(1).execute()
        course = result.data[0] if result.data else None
        if not course:
            return None
        if not include_unpublished and course.get("status") != "published":
            return None
        return self._attach_ratings([course])[0]

    def find_duplicates(self, title, official_url, exclude_id=None):
        url_key = normalise_url(official_url)
        by_title = self._table("courses").select("*, providers(*), categories(*)").ilike("title", title.strip()).execute().data or []
        by_url = self._table("courses").select("*, providers(*), categories(*)").eq("url_key", url_key).execute().data or []
        seen = {}
        for row in by_title + by_url:
            if exclude_id and row["id"] == exclude_id:
                continue
            seen[row["id"]] = row
        return self._attach_ratings(list(seen.values()))

    def find_url_duplicate(self, official_url, exclude_id=None):
        url_key = normalise_url(official_url)
        rows = self._table("courses").select("id, title, status").eq("url_key", url_key).execute().data or []
        for row in rows:
            if not exclude_id or row["id"] != exclude_id:
                return row
        return None

    def list_courses(
        self,
        q=None,
        category_slug=None,
        provider_id=None,
        pricing=None,
        difficulty=None,
        language=None,
        min_rating=None,
        sort="newest",
        page=1,
        per_page=12,
        include_unpublished=False,
        public_only=None,
        status=None,
        featured_only=False,
        underrated_only=False,
        needs_verification_only=False,
        exclude_id=None,
    ):
        if public_only is not None:
            include_unpublished = not bool(public_only)
        query = self._table("courses").select("*, providers(*), categories(*)", count="exact")
        if not include_unpublished:
            query = query.eq("status", "published")
        if status:
            query = query.eq("status", status)
        if featured_only:
            query = query.eq("is_featured", True)
        if underrated_only:
            query = query.eq("is_underrated", True)
        if needs_verification_only:
            query = query.eq("needs_verification", True)
        if category_slug:
            category = self.get_category_by_slug(category_slug)
            if category:
                query = query.eq("category_id", category["id"])
            else:
                return {"items": [], "total": 0, "page": page, "per_page": per_page, "pages": 1}
        if provider_id:
            query = query.eq("provider_id", provider_id)
        if pricing == "free":
            query = query.eq("is_free", True)
        elif pricing == "paid":
            query = query.eq("is_free", False)
        elif pricing == "unknown":
            query = query.is_("is_free", "null")
        if difficulty:
            query = query.eq("difficulty", difficulty)
        if language:
            query = query.ilike("language", language)
        if exclude_id:
            query = query.neq("id", exclude_id)

        terms = [_safe_postgrest_term(t) for t in search_terms(q)]
        terms = [t for t in terms if t]
        for term in terms:
            query = query.or_(
                f"title.ilike.%{term}%,keywords.ilike.%{term}%,short_description.ilike.%{term}%,skills_taught.ilike.%{term}%,subcategory.ilike.%{term}%"
            )

        if sort == "title":
            query = query.order("title")
        elif sort == "updated" and include_unpublished:
            query = query.order("updated_at", desc=True)
        elif sort == "editorial":
            query = query.order("editorial_score", desc=True, nullsfirst=False)
        elif sort == "price_low":
            query = query.order("price", desc=False, nullsfirst=False)
        elif sort == "price_high":
            query = query.order("price", desc=True, nullsfirst=False)
        else:
            query = query.order("updated_at", desc=True)

        per_page = max(1, min(int(per_page), 100))
        page = max(1, int(page))
        start = (page - 1) * per_page
        end = start + per_page - 1
        result = query.range(start, end).execute()
        items = self._attach_ratings(result.data or [])
        if min_rating:
            threshold = float(min_rating)
            items = [c for c in items if c.get("user_rating") is not None and c["user_rating"] >= threshold]
        if sort == "rating":
            items.sort(key=lambda c: (c["user_rating"] is None, -(c["user_rating"] or 0), c.get("title") or ""))
        total = result.count if result.count is not None else len(items)
        return {
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max((total + per_page - 1) // per_page, 1),
        }

    def has_public_ratings(self):
        reviews = self._table("reviews").select("course_id").eq("status", "approved").execute().data or []
        counts = {}
        for r in reviews:
            counts[r["course_id"]] = counts.get(r["course_id"], 0) + 1
        return any(n >= self.min_rating_count for n in counts.values())

    def public_languages(self):
        rows = self._table("courses").select("language").eq("status", "published").execute().data or []
        return sorted({r["language"] for r in rows if r.get("language")})

    def similar_courses(self, course, limit=4):
        if not course.get("category_id"):
            return []
        rows = (
            self._table("courses")
            .select("*, providers(*), categories(*)")
            .eq("status", "published")
            .eq("category_id", course["category_id"])
            .neq("id", course["id"])
            .limit(int(limit))
            .execute()
            .data
            or []
        )
        return self._attach_ratings(rows)

    def _optional_bool(self, value):
        if value is None or value == "":
            return None
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "on", "yes")
        return bool(value)

    def create_course(self, data, actor_id=None, origin=None):
        return self.save_course(data, course_id=None, actor_id=actor_id, origin=origin)

    def save_course(self, data, course_id=None, actor_id=None, origin=None):
        """Create or update a course's CONTENT. Never changes its status."""
        now = _now()
        url = data["official_url"].strip()
        payload = {
            "title": data["title"].strip(),
            "provider_id": data.get("provider_id") or None,
            "category_id": data.get("category_id") or None,
            "subcategory": data.get("subcategory") or "",
            "official_url": url,
            "url_key": normalise_url(url),
            "short_description": data.get("short_description") or "",
            "description": data.get("description") or "",
            "language": (data.get("language") or "").strip(),
            "is_free": self._optional_bool(data.get("is_free")),
            "price": float(data["price"]) if data.get("price") not in (None, "") else None,
            "currency": (data.get("currency") or "USD").strip().upper(),
            "pricing_notes": data.get("pricing_notes") or "",
            "difficulty": data.get("difficulty") or "",
            "prerequisites": data.get("prerequisites") or "",
            "duration": data.get("duration") or "",
            "duration_verified": bool(data.get("duration_verified")),
            "certificate_available": self._optional_bool(data.get("certificate_available")),
            "certificate_verified": bool(data.get("certificate_verified")),
            "teaching_format": data.get("teaching_format") or "",
            "skills_taught": data.get("skills_taught") or "",
            "learning_outcomes": data.get("learning_outcomes") or "",
            "intended_audience": data.get("intended_audience") or "",
            "strengths": data.get("strengths") or "",
            "limitations": data.get("limitations") or "",
            "editorial_assessment": data.get("editorial_assessment") or "",
            "editorial_score": float(data["editorial_score"]) if data.get("editorial_score") not in (None, "") else None,
            "editorial_score_public": bool(data.get("editorial_score_public")),
            "editorial_why_recommend": data.get("editorial_why_recommend") or "",
            "editorial_who_should_take": data.get("editorial_who_should_take") or "",
            "editorial_who_should_avoid": data.get("editorial_who_should_avoid") or "",
            "editorial_value_for_money": data.get("editorial_value_for_money") or "",
            "editorial_approved": bool(data.get("editorial_approved")),
            "recommendation_status": data.get("recommendation_status") or "none",
            "last_verified_at": data.get("last_verified_at") or None,
            "is_featured": bool(data.get("is_featured")),
            "is_underrated": bool(data.get("is_underrated")),
            "needs_verification": bool(data.get("needs_verification")),
            "keywords": data.get("keywords") or "",
            "source_url": data.get("source_url") or None,
            "source_checked_at": data.get("source_checked_at") or None,
            "updated_at": now,
        }
        if "thumbnail_path" in data and data["thumbnail_path"] is not None:
            payload["thumbnail_path"] = data["thumbnail_path"] or None
        creating = course_id is None
        record_origin = (origin or data.get("origin") or "admin") if creating else None
        if creating and record_origin in UNTRUSTED_ORIGINS:
            payload.update(
                is_featured=False,
                is_underrated=False,
                editorial_approved=False,
                editorial_score_public=False,
                recommendation_status="none",
                needs_verification=True,
            )
        if creating:
            course_id = new_id()
            payload.update(
                id=course_id,
                slug=slugify(data.get("slug") or data["title"]),
                origin=record_origin,
                status="draft",
                created_at=now,
            )
            self._table("courses").insert(payload).execute()
        else:
            if data.get("slug"):
                payload["slug"] = slugify(data["slug"])
            self._table("courses").update(payload).eq("id", course_id).execute()
        self.record_action(actor_id, "course", course_id, "created" if creating else "edited", label=payload["title"])
        return self.get_course(course_id=course_id, include_unpublished=True)

    def toggle_course_flag(self, course_id, flag, value=None, actor_id=None):
        if flag not in TOGGLEABLE_FLAGS:
            raise ValueError(f"Unsupported flag: {flag}")
        course = self.get_course(course_id=course_id, include_unpublished=True)
        if not course:
            raise NotFoundError("Course not found.")
        old_val = bool(course.get(flag))
        new_val = (not old_val) if value is None else bool(value)
        self._table("courses").update({flag: new_val, "updated_at": _now()}).eq("id", course_id).execute()
        self.record_action(
            actor_id,
            "course",
            course_id,
            f"flag_{flag}",
            from_status="on" if old_val else "off",
            to_status="on" if new_val else "off",
            label=course["title"],
        )
        return self.get_course(course_id=course_id, include_unpublished=True)

    def set_course_status(self, course_id, target, actor_id=None, note=None):
        course = self.get_course(course_id=course_id, include_unpublished=True)
        if not course:
            raise NotFoundError("Course not found.")
        workflow.check_transition(course["status"], target)
        now = _now()
        updates = {"status": target, "updated_at": now}
        if target == workflow.PUBLISHED:
            updates["published_at"] = now
        self._table("courses").update(updates).eq("id", course_id).execute()
        self.record_action(
            actor_id,
            "course",
            course_id,
            "status_changed",
            from_status=course["status"],
            to_status=target,
            note=note,
            label=course["title"],
        )
        return self.get_course(course_id=course_id, include_unpublished=True)

    def delete_course(self, course_id, actor_id=None):
        course = self.get_course(course_id=course_id, include_unpublished=True)
        if not course:
            raise NotFoundError("Course not found.")
        if course["status"] == workflow.PUBLISHED:
            raise workflow.WorkflowError("Unpublish or archive a live course before deleting it.")
        self._table("reports").delete().eq("target_type", "course").eq("target_id", course_id).execute()
        self._table("course_submissions").update({"course_id": None}).eq("course_id", course_id).execute()
        self._table("reviews").delete().eq("course_id", course_id).execute()
        self._table("saved_courses").delete().eq("course_id", course_id).execute()
        self._table("course_admin_notes").delete().eq("course_id", course_id).execute()
        self._table("courses").delete().eq("id", course_id).execute()
        self.record_action(actor_id, "course", course_id, "deleted", from_status=course["status"], label=course["title"])

    def get_admin_notes(self, course_id):
        res = self._table("course_admin_notes").select("notes").eq("course_id", course_id).limit(1).execute()
        return res.data[0]["notes"] if res.data else ""

    def set_admin_notes(self, course_id, notes):
        self._table("course_admin_notes").upsert(
            {"course_id": course_id, "notes": (notes or "").strip(), "updated_at": _now()}
        ).execute()

    def export_courses(self):
        rows = self._table("courses").select("*, providers(*), categories(*)").order("title").execute().data or []
        notes_rows = self._table("course_admin_notes").select("course_id, notes").execute().data or []
        notes = {r["course_id"]: r["notes"] for r in notes_rows}
        courses = self._attach_ratings(rows)
        for c in courses:
            c["admin_notes"] = notes.get(c["id"], "")
        return courses

    def dashboard_counts(self):
        def count(table, **eq):
            query = self._table(table).select("id", count="exact")
            for key, value in eq.items():
                query = query.eq(key, value)
            return query.execute().count or 0

        stale_cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).date().isoformat()
        stale_verification = (
            self._table("courses")
            .select("id", count="exact")
            .eq("status", "published")
            .or_(f"last_verified_at.is.null,last_verified_at.lt.{stale_cutoff}")
            .execute()
            .count
            or 0
        )
        by_status = {s: count("courses", status=s) for s in workflow.STATUSES}
        total = sum(by_status.values())
        free_all = count("courses", is_free=True)
        paid_all = count("courses", is_free=False)
        return {
            "total_courses": total,
            "published_courses": by_status["published"],
            "draft_courses": by_status["draft"],
            "pending_courses": by_status["pending_review"],
            "approved_courses": by_status["approved"],
            "archived_courses": by_status["archived"],
            "unpublished_courses": max(total - by_status["published"], 0),
            "free_courses": free_all,
            "paid_courses": paid_all,
            "unknown_pricing_courses": max(total - free_all - paid_all, 0),
            "free_published_courses": count("courses", is_free=True, status="published"),
            "paid_published_courses": count("courses", is_free=False, status="published"),
            "featured_courses": count("courses", is_featured=True, status="published"),
            "underrated_courses": count("courses", is_underrated=True, status="published"),
            "total_users": self.count_users(),
            "total_reviews": count("reviews"),
            "reviews_pending": count("reviews", status="pending"),
            "reports_pending": count("reports", status="pending"),
            "submissions_pending": count("course_submissions", status="pending"),
            "messages_unhandled": count("contact_messages", is_handled=False),
            "needs_verification": count("courses", needs_verification=True),
            "stale_verification": stale_verification,
        }

    # ------------------------------------------------------------------- reviews
    def add_review(self, course_id, user_id, rating, body):
        course = self.get_course(course_id=course_id)
        if not course:
            raise NotFoundError("That course is not available.")
        existing = (
            self._table("reviews")
            .select("id")
            .eq("course_id", course_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
            .data
        )
        now = _now()
        payload = {
            "course_id": course_id,
            "user_id": user_id,
            "rating": int(rating),
            "body": body.strip(),
            "status": "pending",
            "updated_at": now,
        }
        if existing:
            self._table("reviews").update(payload).eq("id", existing[0]["id"]).execute()
            return existing[0]["id"], False
        payload["id"] = new_id()
        payload["created_at"] = now
        self._table("reviews").insert(payload).execute()
        return payload["id"], True

    def get_review(self, review_id):
        res = self._table("reviews").select("*").eq("id", review_id).limit(1).execute()
        return res.data[0] if res.data else None

    def get_user_review(self, course_id, user_id):
        res = (
            self._table("reviews")
            .select("*")
            .eq("course_id", course_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def list_reviews(self, course_id=None, status=None, user_id=None, for_admin=False):
        query = self._table("reviews").select("*, profiles(display_name, email), courses(title, slug, status)")
        if course_id:
            query = query.eq("course_id", course_id)
        if status:
            query = query.eq("status", status)
        if user_id:
            query = query.eq("user_id", user_id)
        rows = query.order("created_at", desc=True).execute().data or []
        out = []
        for row in rows:
            profile = row.get("profiles") or {}
            course = row.get("courses") or {}
            if not for_admin and not user_id and course.get("status") != "published":
                continue
            row["display_name"] = profile.get("display_name")
            if for_admin:
                row["email"] = profile.get("email")
            row["course_title"] = course.get("title")
            row["course_slug"] = course.get("slug")
            row["course_status"] = course.get("status")
            out.append(row)
        return out

    def set_review_status(self, review_id, status, actor_id=None):
        if status not in REVIEW_STATUSES:
            raise ValueError("Invalid review status")
        review = self.get_review(review_id)
        if not review:
            raise NotFoundError("Review not found.")
        self._table("reviews").update({"status": status}).eq("id", review_id).execute()
        self.record_action(actor_id, "review", review_id, "status_changed", from_status=review["status"], to_status=status)

    def delete_review(self, review_id, user_id=None, actor_id=None):
        review = self.get_review(review_id)
        if not review or (user_id and review["user_id"] != user_id):
            return False
        self._table("reports").delete().eq("target_type", "review").eq("target_id", review_id).execute()
        self._table("reviews").delete().eq("id", review_id).execute()
        if actor_id:
            self.record_action(actor_id, "review", review_id, "deleted", from_status=review["status"])
        return True

    # --------------------------------------------------------------------- saved
    def save_course_for_user(self, user_id, course_id):
        if not self.get_course(course_id=course_id):
            raise NotFoundError("That course is not available.")
        self._table("saved_courses").upsert(
            {"user_id": user_id, "course_id": course_id, "created_at": _now()}
        ).execute()

    def unsave_course_for_user(self, user_id, course_id):
        self._table("saved_courses").delete().eq("user_id", user_id).eq("course_id", course_id).execute()

    def is_saved(self, user_id, course_id):
        result = (
            self._table("saved_courses")
            .select("user_id")
            .eq("user_id", user_id)
            .eq("course_id", course_id)
            .limit(1)
            .execute()
        )
        return bool(result.data)

    def list_saved(self, user_id):
        saved = self._table("saved_courses").select("course_id").eq("user_id", user_id).execute().data or []
        ids = [row["course_id"] for row in saved]
        if not ids:
            return []
        courses = (
            self._table("courses")
            .select("*, providers(*), categories(*)")
            .in_("id", ids)
            .eq("status", "published")
            .execute()
            .data
            or []
        )
        return self._attach_ratings(courses)

    # ------------------------------------------------------------------- reports
    def add_report(self, target_type, target_id, reporter_id, reason, details=""):
        valid = COURSE_REPORT_REASONS if target_type == "course" else REVIEW_REPORT_REASONS
        if target_type not in ("course", "review") or reason not in {r for r, _ in valid}:
            raise ValueError("Invalid report")
        if target_type == "course" and not self.get_course(course_id=target_id):
            raise NotFoundError("That course is not available.")
        if target_type == "review":
            review = self.get_review(target_id)
            if not review or review["status"] != "approved":
                raise NotFoundError("That review is not available.")
        existing = (
            self._table("reports")
            .select("id")
            .eq("reporter_id", reporter_id)
            .eq("target_type", target_type)
            .eq("target_id", target_id)
            .eq("status", "pending")
            .limit(1)
            .execute()
            .data
        )
        if existing:
            return None
        report_id = new_id()
        self._table("reports").insert(
            {
                "id": report_id,
                "target_type": target_type,
                "target_id": target_id,
                "reporter_id": reporter_id,
                "reason": reason,
                "details": (details or "").strip(),
                "status": "pending",
                "created_at": _now(),
            }
        ).execute()
        return report_id

    def list_reports(self, status="pending"):
        query = self._table("reports").select("*")
        if status:
            query = query.eq("status", status)
        return query.order("created_at", desc=True).execute().data or []

    def resolve_report(self, report_id, status, actor_id=None):
        if status not in ("resolved", "dismissed"):
            raise ValueError("Invalid report status")
        self._table("reports").update({"status": status, "resolved_at": _now(), "resolved_by": actor_id}).eq("id", report_id).execute()
        self.record_action(actor_id, "report", report_id, status)

    # --------------------------------------------------------------- submissions
    def add_submission(self, submitter_id, title, official_url, provider_name="", note=""):
        sub_id = new_id()
        self._table("course_submissions").insert(
            {
                "id": sub_id,
                "submitter_id": submitter_id,
                "title": title.strip(),
                "official_url": official_url.strip(),
                "provider_name": (provider_name or "").strip(),
                "note": (note or "").strip(),
                "status": "pending",
                "created_at": _now(),
            }
        ).execute()
        return sub_id

    def count_recent_submissions(self, submitter_id, hours=24):
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        res = (
            self._table("course_submissions")
            .select("id", count="exact")
            .eq("submitter_id", submitter_id)
            .gte("created_at", since)
            .execute()
        )
        return res.count or 0

    def get_submission(self, submission_id):
        res = self._table("course_submissions").select("*").eq("id", submission_id).limit(1).execute()
        return res.data[0] if res.data else None

    def list_submissions(self, status="pending"):
        query = self._table("course_submissions").select("*, profiles(display_name)")
        if status:
            query = query.eq("status", status)
        rows = query.order("created_at", desc=True).execute().data or []
        for r in rows:
            prof = r.get("profiles") or {}
            r["submitter_name"] = prof.get("display_name")
        return rows

    def set_submission_status(self, submission_id, status, actor_id=None, course_id=None):
        if status not in SUBMISSION_STATUSES or status == "pending":
            raise ValueError("Invalid submission status")
        self._table("course_submissions").update(
            {"status": status, "course_id": course_id, "reviewed_at": _now(), "reviewed_by": actor_id}
        ).eq("id", submission_id).execute()
        self.record_action(actor_id, "submission", submission_id, status)

    # ------------------------------------------------------------------- imports
    def create_import_batch(self, admin_id, filename, payload):
        batch_id = new_id()
        self._table("import_batches").insert(
            {"id": batch_id, "admin_id": admin_id, "filename": filename, "payload": payload, "created_at": _now()}
        ).execute()
        return batch_id

    def get_import_batch(self, batch_id, admin_id):
        query = self._table("import_batches").select("*").eq("id", batch_id).limit(1)
        if admin_id is None:
            query = query.is_("admin_id", "null")
        else:
            query = query.eq("admin_id", admin_id)
        res = query.execute()
        return res.data[0] if res.data else None

    def delete_import_batch(self, batch_id):
        self._table("import_batches").delete().eq("id", batch_id).execute()

    # --------------------------------------------------------------- audit trail
    def record_action(
        self, actor_id, entity_type, entity_id, action, from_status=None, to_status=None, note=None, label=None
    ):
        self._table("moderation_actions").insert(
            {
                "id": new_id(),
                "actor_id": actor_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "label": label,
                "action": action,
                "from_status": from_status,
                "to_status": to_status,
                "note": note,
                "created_at": _now(),
            }
        ).execute()

    def list_actions(self, limit=100, entity_id=None):
        query = self._table("moderation_actions").select("*, profiles(display_name)")
        if entity_id:
            query = query.eq("entity_id", entity_id)
        rows = query.order("created_at", desc=True).limit(int(limit)).execute().data or []
        for r in rows:
            prof = r.get("profiles") or {}
            r["actor_name"] = prof.get("display_name")
        return rows

    # ------------------------------------------------------------------- contact
    def add_contact_message(self, name, email, message):
        self._table("contact_messages").insert(
            {
                "id": new_id(),
                "name": name.strip(),
                "email": email.strip(),
                "message": message.strip(),
                "is_handled": False,
                "created_at": _now(),
            }
        ).execute()

    def list_contact_messages(self, handled=None):
        query = self._table("contact_messages").select("*")
        if handled is not None:
            query = query.eq("is_handled", bool(handled))
        return query.order("created_at", desc=True).execute().data or []

    def set_contact_handled(self, message_id, handled=True):
        self._table("contact_messages").update(
            {"is_handled": bool(handled), "handled_at": _now() if handled else None}
        ).eq("id", message_id).execute()
