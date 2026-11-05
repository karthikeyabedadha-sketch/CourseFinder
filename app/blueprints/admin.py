import csv
import hashlib
import hmac
import io
import json
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

from app import limiter, workflow
from app.ai_assistant import generate_course_assistance
from app.config import MIN_OWNER_SECRET_LENGTH, WEAK_SECRET_KEYS, resolve_owner_access_secret
from app.forms import CategoryForm, CourseForm, ImportForm, ProviderForm
from app.security import owner_session_required, same_site_referrer
from app.store.constants import DIFFICULTIES
from app.store.errors import DuplicateError, NotFoundError, StoreError
from app.utils import (
    allowed_image,
    is_safe_http_url,
    looks_like_image,
    normalise_url,
    parse_page,
    safe_upload_name,
    split_lines,
    spreadsheet_safe,
    to_bool_or_none,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

IMPORT_MAX_ROWS = 500
IMPORT_COLUMNS = [
    "title",
    "official_url",
    "provider",
    "category",
    "short_description",
    "description",
    "difficulty",
    "is_free",
    "price",
    "currency",
    "language",
    "duration",
    "prerequisites",
    "learning_outcomes",
    "source_url",
    "source_checked_at",
]
EXPORT_COLUMNS = [
    "id",
    "slug",
    "title",
    "status",
    "origin",
    "provider_name",
    "category_name",
    "official_url",
    "difficulty",
    "is_free",
    "price",
    "currency",
    "language",
    "duration",
    "prerequisites",
    "learning_outcomes",
    "last_verified_at",
    "source_url",
    "source_checked_at",
    "is_featured",
    "is_underrated",
    "needs_verification",
    "admin_notes",
]

FLAG_LABELS = {
    "is_featured": ("Featured", "Removed from featured"),
    "is_underrated": ("Marked as underrated gem", "Removed underrated badge"),
    "needs_verification": ("Marked as needing verification", "Verification flag cleared"),
}

ADMIN_SORTS = ("updated", "newest", "title", "editorial", "price_low", "price_high")


def store():
    return current_app.extensions["store"]


@admin_bp.before_request
def protect_admin():
    """Require a server-validated Owner Studio session on every admin route."""
    if request.endpoint == "admin.studio":
        return
    if getattr(g, "owner_authenticated", False):
        return
    return redirect(url_for("admin.studio"))


@admin_bp.app_context_processor
def workflow_context():
    return {
        "status_help": workflow.STATUS_HELP,
        "status_labels": workflow.STATUS_LABELS,
        "actions_for": workflow.actions_for,
    }


def _actor():
    user = getattr(g, "user", None)
    if getattr(g, "owner_authenticated", False):
        return user["id"] if user else None
    return None


@admin_bp.route("/")
@owner_session_required
def dashboard():
    s = store()
    return render_template(
        "admin/dashboard.html",
        counts=s.dashboard_counts(),
        pending_reviews=s.list_reviews(status="pending", for_admin=True)[:8],
        recent_actions=s.list_actions(limit=10),
    )


def verify_owner_secret(submitted_secret, configured_secret):
    """Verify the submitted Owner Studio password against the configured server secret.

    Returns ``(is_valid, error_message)``. Uses constant-time comparison and never logs
    or exposes the secret value.
    """
    configured = (configured_secret or "").strip()
    submitted = (submitted_secret or "").strip()
    if not configured or configured in WEAK_SECRET_KEYS:
        return False, "Owner access is not configured. Set OWNER_ACCESS_SECRET before starting CourseFinder."
    if len(configured) < MIN_OWNER_SECRET_LENGTH:
        return False, "Owner access is misconfigured. Use a secret with at least 16 characters."
    if not submitted:
        # Constant-time dummy comparison so timing does not leak secret length
        hmac.compare_digest(configured.encode("utf-8"), b"")
        return False, "Enter your owner password to unlock Owner Studio."
    if not hmac.compare_digest(submitted.encode("utf-8"), configured.encode("utf-8")):
        return False, "Incorrect owner password. Please try again."
    return True, None


@admin_bp.route("/studio", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def studio():
    if getattr(g, "owner_authenticated", False):
        return redirect(url_for("admin.dashboard"))

    error = None
    configured_secret = resolve_owner_access_secret(current_app.config)
    if request.method == "POST":
        submitted_secret = request.form.get("owner_secret", "")
        is_valid, error = verify_owner_secret(submitted_secret, configured_secret)
        if is_valid:
            old_token = session.get("owner_session_token")
            if old_token and isinstance(old_token, str):
                old_hash = hashlib.sha256(old_token.encode("utf-8")).hexdigest()
                store().revoke_owner_session(old_hash)
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            expires_at = (
                datetime.now(timezone.utc)
                + timedelta(seconds=current_app.config["OWNER_SESSION_LIFETIME_SECONDS"])
            ).isoformat()
            store().create_owner_session(token_hash, expires_at)
            session.clear()
            session.permanent = True
            session["owner_session_token"] = token
            return redirect(url_for("admin.dashboard"))

    return render_template("admin/owner_access.html", error=error)


@admin_bp.route("/logout", methods=["POST"])
@owner_session_required
def owner_logout():
    token = session.get("owner_session_token")
    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        store().revoke_owner_session(token_hash)
    session.clear()
    flash("Owner Studio locked.", "success")
    return redirect(url_for("admin.studio"))


@admin_bp.route("/users")
@owner_session_required
def users():
    q = request.args.get("q", "").strip()[:100]
    s = store()
    result = s.list_users(q=q or None, page=parse_page(request.args.get("page")))
    return render_template("admin/users.html", result=result, q=q, admin_count=s.count_admins())


@admin_bp.route("/users/<user_id>")
@owner_session_required
def user_detail(user_id):
    s = store()
    user = s.get_user(user_id)
    if not user:
        abort(404)
    return render_template(
        "admin/user_detail.html",
        user=user,
        reviews=s.list_reviews(user_id=user_id, for_admin=True),
        activity=s.list_actions(entity_id=user_id, limit=20),
    )


@admin_bp.route("/users/<user_id>/role", methods=["POST"])
@owner_session_required
def user_role_update(user_id):
    s = store()
    user = s.get_user(user_id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("admin.users"))
    role = request.form.get("role", "")
    if role not in ("user", "admin"):
        flash("Invalid user role.", "error")
        return redirect(url_for("admin.users"))
    if role == user["role"]:
        flash("That user already has this role.", "error")
        return redirect(url_for("admin.users"))
    if user_id == _actor():
        flash("You cannot change your own administrator role.", "error")
        return redirect(url_for("admin.users"))
    if user["role"] == "admin" and role == "user" and s.count_admins() <= 1:
        flash("The last administrator cannot be demoted.", "error")
        return redirect(url_for("admin.users"))
    try:
        s.update_user_role(user_id, role, actor_id=_actor())
    except NotFoundError:
        flash("User not found.", "error")
        return redirect(url_for("admin.users"))
    flash(f"Updated {user['email']} to {role}.", "success")
    return redirect(url_for("admin.users", q=request.form.get("q", "").strip() or None))


@admin_bp.route("/users/<user_id>/status", methods=["POST"])
@owner_session_required
def user_status_update(user_id):
    s = store()
    user = s.get_user(user_id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("admin.users"))
    active = request.form.get("status") == "active"
    if request.form.get("status") not in ("active", "suspended"):
        flash("Invalid account status.", "error")
        return redirect(url_for("admin.users"))
    if bool(user.get("is_active", True)) == active:
        flash("That account already has this status.", "error")
        return redirect(url_for("admin.users"))
    if user_id == _actor():
        flash("You cannot suspend your own account.", "error")
        return redirect(url_for("admin.users"))
    if user["role"] == "admin" and user.get("is_active", True) and not active and s.count_admins() <= 1:
        flash("The last active administrator cannot be suspended.", "error")
        return redirect(url_for("admin.users"))
    try:
        s.update_user_active(user_id, active, actor_id=_actor())
    except NotFoundError:
        flash("User not found.", "error")
        return redirect(url_for("admin.users"))
    flash(f"{user['email']} is now {'active' if active else 'suspended'}.", "success")
    return redirect(url_for("admin.users", q=request.form.get("q", "").strip() or None))


# ------------------------------------------------------------------ courses
def _fill_course_choices(form):
    form.category_id.choices = [(c["id"], c["name"]) for c in store().list_categories()]
    form.provider_id.choices = [("", "Select a provider")] + [(p["id"], p["name"]) for p in store().list_providers()]


def _course_payload(form):
    fields = (
        "title official_url provider_id category_id subcategory short_description description language is_free price "
        "currency pricing_notes difficulty prerequisites duration duration_verified certificate_available "
        "certificate_verified teaching_format skills_taught learning_outcomes intended_audience strengths limitations "
        "editorial_assessment editorial_score editorial_score_public editorial_why_recommend editorial_who_should_take "
        "editorial_who_should_avoid editorial_value_for_money editorial_approved recommendation_status "
        "last_verified_at keywords is_featured is_underrated needs_verification source_url source_checked_at slug"
    ).split()
    data = {name: getattr(form, name).data for name in fields}
    if form.new_provider_name.data:
        provider = store().get_or_create_provider(form.new_provider_name.data, form.new_provider_url.data)
        data["provider_id"] = provider["id"]
    return data


def _save_thumbnail(form):
    file = form.thumbnail.data
    if not file or not getattr(file, "filename", None):
        return None
    if not allowed_image(file.filename):
        flash("Thumbnail ignored: only png, jpg or gif images are accepted.", "error")
        return None
    if not looks_like_image(file.stream):
        flash("Thumbnail ignored: the file is not a real image.", "error")
        return None
    filename = safe_upload_name(secure_filename(file.filename))
    file.save(Path(current_app.config["UPLOAD_FOLDER"]) / filename)
    return f"uploads/{filename}"


def _form_data_from_course(course):
    def tri(value):
        return "" if value is None else ("true" if value else "false")

    keys = (
        "title official_url provider_id category_id subcategory short_description description language price pricing_notes "
        "prerequisites duration teaching_format skills_taught learning_outcomes intended_audience strengths limitations editorial_assessment "
        "editorial_score editorial_why_recommend editorial_who_should_take editorial_who_should_avoid "
        "editorial_value_for_money keywords source_url source_checked_at slug"
    ).split()
    data = {k: course.get(k) for k in keys}
    data.update(
        provider_id=course.get("provider_id") or "",
        category_id=course.get("category_id") or "",
        is_free=tri(course.get("is_free")),
        currency=course.get("currency") or "USD",
        difficulty=course.get("difficulty") or "",
        certificate_available=tri(course.get("certificate_available")),
        recommendation_status=course.get("recommendation_status") or "none",
        last_verified_at=course.get("last_verified_at") or "",
        source_url=course.get("source_url") or "",
        source_checked_at=course.get("source_checked_at") or "",
        admin_notes=store().get_admin_notes(course["id"]),
    )
    for flag in (
        "duration_verified",
        "certificate_verified",
        "editorial_score_public",
        "editorial_approved",
        "is_featured",
        "is_underrated",
        "needs_verification",
    ):
        data[flag] = bool(course.get(flag))
    return data


@admin_bp.route("/courses")
@owner_session_required
def courses():
    status = request.args.get("status", "").strip()
    if status not in workflow.STATUSES:
        status = ""
    pricing = request.args.get("pricing", "").strip()
    if pricing not in ("free", "paid", "unknown"):
        pricing = ""
    difficulty = request.args.get("difficulty", "").strip()
    if difficulty not in DIFFICULTIES:
        difficulty = ""
    flag = request.args.get("flag", "").strip()
    if flag not in ("featured", "underrated", "needs_verification"):
        flag = ""
    sort = request.args.get("sort", "updated").strip()
    if sort not in ADMIN_SORTS:
        sort = "updated"
    q = request.args.get("q", "").strip()[:100]
    category = request.args.get("category", "").strip()[:80]
    result = store().list_courses(
        q=q or None,
        status=status or None,
        category_slug=category or None,
        pricing=pricing or None,
        difficulty=difficulty or None,
        featured_only=(flag == "featured"),
        underrated_only=(flag == "underrated"),
        needs_verification_only=(flag == "needs_verification"),
        page=parse_page(request.args.get("page")),
        per_page=20,
        include_unpublished=True,
        sort=sort,
    )
    return render_template(
        "admin/courses.html",
        result=result,
        q=q,
        status=status,
        pricing=pricing,
        difficulty=difficulty,
        flag=flag,
        sort=sort,
        category=category,
        categories=store().list_categories(),
        statuses=workflow.STATUSES,
        labels=workflow.STATUS_LABELS,
    )


def _handle_course_form(form, course=None):
    """Validate, check duplicates, save content. Returns (course, duplicates) – course is set on success."""
    s = store()
    course_id = course["id"] if course else None
    duplicates = s.find_duplicates(form.title.data, form.official_url.data, exclude_id=course_id)
    url_dup = s.find_url_duplicate(form.official_url.data, exclude_id=course_id)
    if url_dup:
        form.official_url.errors.append(f"This official URL is already used by “{url_dup['title']}”.")
        return None, duplicates
    if duplicates and request.form.get("confirm_duplicate") != "1":
        flash(
            "A course with the same title already exists. Check the match below, then tick the confirmation box to save anyway.",
            "error",
        )
        return None, duplicates
    payload = _course_payload(form)
    thumb = _save_thumbnail(form)
    if thumb:
        payload["thumbnail_path"] = thumb
    elif course and request.form.get("remove_thumbnail") == "1":
        payload["thumbnail_path"] = ""
    try:
        saved = s.save_course(payload, course_id=course_id, actor_id=_actor())
    except DuplicateError as exc:
        form.official_url.errors.append(str(exc))
        return None, duplicates
    s.set_admin_notes(saved["id"], form.admin_notes.data)
    return saved, duplicates


@admin_bp.route("/courses/new", methods=["GET", "POST"])
@owner_session_required
def course_new():
    form = CourseForm()
    _fill_course_choices(form)
    duplicates = []
    if form.validate_on_submit():
        course, duplicates = _handle_course_form(form)
        if course:
            flash("Saved as a draft. It is not public until you approve and publish it.", "success")
            return redirect(url_for("admin.course_edit", course_id=course["id"]))
    return render_template("admin/course_form.html", form=form, course=None, duplicates=duplicates, transitions=[])


@admin_bp.route("/courses/<course_id>/edit", methods=["GET", "POST"])
@owner_session_required
def course_edit(course_id):
    s = store()
    course = s.get_course(course_id=course_id, include_unpublished=True)
    if not course:
        flash("Course not found.", "error")
        return redirect(url_for("admin.courses"))
    duplicates = []
    if request.method == "POST":
        form = CourseForm()
        _fill_course_choices(form)
        if form.validate_on_submit():
            saved, duplicates = _handle_course_form(form, course)
            if saved:
                flash("Changes saved. The publication status was not changed.", "success")
                return redirect(url_for("admin.course_edit", course_id=course_id))
    else:
        form = CourseForm(data=_form_data_from_course(course))
        _fill_course_choices(form)
    return render_template(
        "admin/course_form.html",
        form=form,
        course=course,
        duplicates=duplicates,
        transitions=workflow.actions_for(course["status"]),
        history=s.list_actions(limit=15, entity_id=course_id),
    )


@admin_bp.route("/courses/<course_id>/preview")
@owner_session_required
def course_preview(course_id):
    s = store()
    course = s.get_course(course_id=course_id, include_unpublished=True)
    if not course:
        flash("Course not found.", "error")
        return redirect(url_for("admin.courses"))
    return render_template(
        "courses/detail.html",
        course=course,
        skills_list=split_lines(course.get("skills_taught")),
        form=None,
        saved=False,
        my_review=None,
        reviews=s.list_reviews(course_id=course["id"], status="approved", for_admin=True),
        similar=[],
        report_form=None,
        review_report_form=None,
        preview=True,
    )


@admin_bp.route("/courses/<course_id>/transition", methods=["POST"])
@owner_session_required
def course_transition(course_id):
    """The only way a course changes status. Every move is checked against the workflow table."""
    target = request.form.get("target", "")
    fallback = url_for("admin.course_edit", course_id=course_id)
    if not workflow.is_valid_status(target):
        flash("Unknown status.", "error")
        return redirect(fallback)
    try:
        course = store().set_course_status(
            course_id,
            target,
            actor_id=_actor(),
            note=(request.form.get("note") or "")[:500] or None,
        )
    except NotFoundError:
        flash("Course not found.", "error")
        return redirect(url_for("admin.courses"))
    except workflow.WorkflowError as exc:
        flash(str(exc), "error")
        return redirect(same_site_referrer(fallback))
    flash(f"“{course['title']}” is now {workflow.STATUS_LABELS[target].lower()}.", "success")
    return redirect(same_site_referrer(fallback))


@admin_bp.route("/courses/<course_id>/flag", methods=["POST"])
@owner_session_required
def course_flag(course_id):
    """Quick toggle for editorial badges (is_featured, is_underrated, needs_verification)."""
    flag = request.form.get("flag", "").strip()
    fallback = url_for("admin.course_edit", course_id=course_id)
    if flag not in FLAG_LABELS:
        flash("Invalid course flag.", "error")
        return redirect(same_site_referrer(fallback))
    try:
        course = store().toggle_course_flag(course_id, flag, actor_id=_actor())
    except NotFoundError:
        flash("Course not found.", "error")
        return redirect(url_for("admin.courses"))
    on_msg, off_msg = FLAG_LABELS[flag]
    flash(f"“{course['title']}”: {on_msg if course.get(flag) else off_msg}.", "success")
    return redirect(same_site_referrer(fallback))


@admin_bp.route("/courses/<course_id>/delete", methods=["GET", "POST"])
@owner_session_required
def course_delete(course_id):
    course = store().get_course(course_id=course_id, include_unpublished=True)
    if not course:
        flash("Course not found.", "error")
        return redirect(url_for("admin.courses"))
    if request.method == "POST":
        if request.form.get("confirm") != course["title"]:
            flash("Type the course title exactly to confirm deletion.", "error")
            return render_template("admin/course_delete.html", course=course)
        try:
            store().delete_course(course_id, actor_id=_actor())
        except workflow.WorkflowError as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin.course_edit", course_id=course_id))
        flash("Course deleted.", "success")
        return redirect(url_for("admin.courses"))
    return render_template("admin/course_delete.html", course=course)


# --------------------------------------------------- AI Content Assistant
def _resolve_ai_inputs(data):
    s = store()
    provider_name = (data.get("new_provider_name") or data.get("provider_name") or "").strip()
    if not provider_name and data.get("provider_id"):
        prov = s.get_provider(data.get("provider_id"))
        if prov:
            provider_name = prov["name"]
    category_slug = (data.get("category_slug") or "").strip()
    category_name = (data.get("category_name") or "").strip()
    if data.get("category_id") and not category_slug:
        cat = s.get_category(data.get("category_id"))
        if cat:
            category_slug = cat["slug"]
            category_name = cat["name"]
    return {
        "title": data.get("title") or "",
        "provider_name": provider_name,
        "category_slug": category_slug,
        "category_name": category_name,
        "difficulty": data.get("difficulty") or "",
        "short_description": data.get("short_description") or "",
        "description": data.get("description") or "",
        "skills_taught": data.get("skills_taught") or "",
        "learning_outcomes": data.get("learning_outcomes") or "",
        "prerequisites": data.get("prerequisites") or "",
        "intended_audience": data.get("intended_audience") or "",
        "raw_notes": data.get("raw_notes") or "",
        "mode": data.get("mode") or "all",
    }


@admin_bp.route("/api/ai-assist", methods=["POST"])
@limiter.limit("30 per hour")
@owner_session_required
def api_ai_assist():
    """Owner Studio JSON endpoint to generate or improve course descriptions, skills, tags, and outlines.

    Never saves or publishes anything automatically; returns draft suggestions for the editor.
    """
    data = request.get_json(silent=True) or request.form.to_dict()
    inputs = _resolve_ai_inputs(data)
    if not inputs["title"].strip() and not inputs["raw_notes"].strip() and not inputs["description"].strip():
        return jsonify({"error": "Enter at least a course title, syllabus notes, or draft description first."}), 400
    suggestions = generate_course_assistance(**inputs)
    store().record_action(_actor(), "ai_assistant", None, "generated_suggestions", label=inputs["title"][:120] or "Draft")
    return jsonify(suggestions)


@admin_bp.route("/ai-assistant", methods=["GET", "POST"])
@limiter.limit("30 per hour", methods=["POST"])
@owner_session_required
def ai_assistant():
    """Dedicated Owner Studio AI Content Assistant workspace."""
    s = store()
    categories = s.list_categories()
    providers = s.list_providers()
    suggestions = None
    form_values = {
        "title": "",
        "official_url": "",
        "provider_id": "",
        "new_provider_name": "",
        "category_id": categories[0]["id"] if categories else "",
        "difficulty": "",
        "raw_notes": "",
        "mode": "all",
        "short_description": "",
        "description": "",
        "skills_taught": "",
        "learning_outcomes": "",
        "keywords": "",
        "category_suggestion": "",
        "category_suggestion_slug": "",
        "prerequisites": "",
        "intended_audience": "",
        "editorial_assessment": "",
        "editorial_who_should_take": "",
        "editorial_who_should_avoid": "",
    }

    if request.method == "POST":
        for k in form_values:
            if k in request.form:
                form_values[k] = request.form.get(k, "").strip()
        action = request.form.get("action", "generate")

        if action == "save_draft":
            title = form_values["title"]
            official_url = form_values["official_url"]
            if not title or len(title) > 200:
                flash("Course title is required (max 200 characters) to save a draft.", "error")
            elif not is_safe_http_url(official_url):
                flash("Enter a valid official course URL (http or https) to save a draft.", "error")
            elif s.find_url_duplicate(official_url):
                dup = s.find_url_duplicate(official_url)
                flash(f"A course with this official URL already exists (“{dup['title']}”).", "error")
            else:
                provider_id = form_values["provider_id"] or None
                if form_values["new_provider_name"]:
                    provider_id = s.get_or_create_provider(form_values["new_provider_name"])["id"]
                draft = s.save_course(
                    {
                        "title": title,
                        "official_url": official_url,
                        "provider_id": provider_id,
                        "category_id": form_values["category_id"] or None,
                        "difficulty": form_values["difficulty"],
                        "short_description": form_values["short_description"][:280],
                        "description": form_values["description"][:8000],
                        "skills_taught": form_values["skills_taught"][:2000],
                        "learning_outcomes": form_values["learning_outcomes"][:3000],
                        "keywords": form_values["keywords"][:300],
                        "prerequisites": form_values["prerequisites"][:2000],
                        "intended_audience": form_values["intended_audience"][:2000],
                        "editorial_assessment": form_values["editorial_assessment"][:4000],
                        "editorial_who_should_take": form_values["editorial_who_should_take"][:4000],
                        "editorial_who_should_avoid": form_values["editorial_who_should_avoid"][:4000],
                    },
                    actor_id=_actor(),
                    origin="ai_suggestion",
                )
                s.set_admin_notes(
                    draft["id"],
                    "Draft created with Owner Studio AI Content Assistant. Verify all details against the official course page before approving or publishing.",
                )
                flash(
                    "Saved as an unverified draft. Review and verify all details before approving or publishing.",
                    "success",
                )
                return redirect(url_for("admin.course_edit", course_id=draft["id"]))
            suggestions = {k: form_values[k] for k in (
                "short_description", "description", "skills_taught", "learning_outcomes", "keywords",
                "prerequisites", "intended_audience", "editorial_assessment",
                "editorial_who_should_take", "editorial_who_should_avoid",
                "category_suggestion", "category_suggestion_slug",
            )}
        else:
            inputs = _resolve_ai_inputs(form_values)
            if not inputs["title"].strip() and not inputs["raw_notes"].strip() and not inputs["description"].strip():
                flash("Enter a course title or paste raw syllabus/course notes first.", "error")
            else:
                suggestions = generate_course_assistance(**inputs)
                for k, v in suggestions.items():
                    if k in form_values:
                        form_values[k] = v
                s.record_action(
                    _actor(),
                    "ai_assistant",
                    None,
                    "generated_suggestions",
                    label=inputs["title"][:120] or "Draft",
                )
                flash(suggestions["notice"], "success")

    return render_template(
        "admin/ai_assistant.html",
        categories=categories,
        providers=providers,
        difficulties=DIFFICULTIES,
        values=form_values,
        suggestions=suggestions,
    )


# --------------------------------------------------- categories & providers
def _category_data(form):
    return {
        "name": form.name.data,
        "description": form.description.data,
        "sort_order": form.sort_order.data or 0,
        "is_active": form.is_active.data,
    }


@admin_bp.route("/categories", methods=["GET", "POST"])
@owner_session_required
def categories():
    form = CategoryForm()
    if form.validate_on_submit():
        try:
            store().save_category(_category_data(form), actor_id=_actor())
            flash("Category saved.", "success")
            return redirect(url_for("admin.categories"))
        except DuplicateError as exc:
            form.name.errors.append(str(exc))
    return render_template("admin/categories.html", form=form, categories=store().list_categories())


@admin_bp.route("/categories/<category_id>", methods=["GET", "POST"])
@owner_session_required
def category_update(category_id):
    s = store()
    category = s.get_category(category_id)
    if not category:
        abort(404)
    form = (
        CategoryForm(data={**category, "is_active": bool(category["is_active"])})
        if request.method == "GET"
        else CategoryForm()
    )
    if request.method == "POST" and form.validate_on_submit():
        try:
            s.save_category(_category_data(form), category_id=category_id, actor_id=_actor())
            flash("Category updated.", "success")
            return redirect(url_for("admin.categories"))
        except DuplicateError as exc:
            form.name.errors.append(str(exc))
    return render_template("admin/category_edit.html", form=form, category=category)


@admin_bp.route("/categories/<category_id>/delete", methods=["POST"])
@owner_session_required
def category_delete(category_id):
    try:
        store().delete_category(category_id, actor_id=_actor())
        flash("Category deleted.", "success")
    except (NotFoundError, StoreError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.categories"))


@admin_bp.route("/providers", methods=["GET", "POST"])
@owner_session_required
def providers():
    form = ProviderForm()
    if form.validate_on_submit():
        try:
            store().save_provider({"name": form.name.data, "website_url": form.website_url.data}, actor_id=_actor())
            flash("Provider saved.", "success")
            return redirect(url_for("admin.providers"))
        except DuplicateError as exc:
            form.name.errors.append(str(exc))
    return render_template("admin/providers.html", form=form, providers=store().list_providers())


@admin_bp.route("/providers/<provider_id>", methods=["GET", "POST"])
@owner_session_required
def provider_update(provider_id):
    s = store()
    provider = s.get_provider(provider_id)
    if not provider:
        abort(404)
    form = ProviderForm(data=provider) if request.method == "GET" else ProviderForm()
    if request.method == "POST" and form.validate_on_submit():
        try:
            s.save_provider(
                {"name": form.name.data, "website_url": form.website_url.data},
                provider_id=provider_id,
                actor_id=_actor(),
            )
            flash("Provider updated.", "success")
            return redirect(url_for("admin.providers"))
        except DuplicateError as exc:
            form.name.errors.append(str(exc))
    return render_template("admin/provider_edit.html", form=form, provider=provider)


@admin_bp.route("/providers/<provider_id>/delete", methods=["POST"])
@owner_session_required
def provider_delete(provider_id):
    try:
        store().delete_provider(provider_id, actor_id=_actor())
        flash("Provider deleted.", "success")
    except (NotFoundError, StoreError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.providers"))


# ------------------------------------------------------------------ reviews
@admin_bp.route("/reviews")
@owner_session_required
def reviews():
    status = request.args.get("status", "pending")
    if status not in ("pending", "approved", "rejected", "all"):
        status = "pending"
    items = store().list_reviews(status=None if status == "all" else status, for_admin=True)
    return render_template("admin/reviews.html", reviews=items, status=status)


@admin_bp.route("/reviews/<review_id>/status", methods=["POST"])
@owner_session_required
def review_status(review_id):
    status = request.form.get("status")
    if status not in ("approved", "rejected", "pending"):
        flash("Invalid review status.", "error")
    else:
        try:
            store().set_review_status(review_id, status, actor_id=_actor())
            flash("Review updated.", "success")
        except NotFoundError:
            flash("Review not found.", "error")
    return redirect(same_site_referrer(url_for("admin.reviews")))


@admin_bp.route("/reviews/<review_id>/delete", methods=["POST"])
@owner_session_required
def review_delete(review_id):
    if store().delete_review(review_id, actor_id=_actor()):
        flash("Review deleted.", "success")
    return redirect(same_site_referrer(url_for("admin.reviews")))


# ------------------------------------------------- reports / submissions / inbox
@admin_bp.route("/reports")
@owner_session_required
def reports():
    status = request.args.get("status", "pending")
    if status not in ("pending", "resolved", "dismissed", "all"):
        status = "pending"
    return render_template(
        "admin/reports.html",
        reports=store().list_reports(None if status == "all" else status),
        status=status,
    )


@admin_bp.route("/reports/<report_id>", methods=["POST"])
@owner_session_required
def report_resolve(report_id):
    status = request.form.get("status")
    try:
        store().resolve_report(report_id, status, actor_id=_actor())
        flash("Report updated.", "success")
    except (ValueError, NotFoundError):
        flash("Could not update that report.", "error")
    return redirect(url_for("admin.reports"))


@admin_bp.route("/submissions")
@owner_session_required
def submissions():
    status = request.args.get("status", "pending")
    if status not in ("pending", "accepted", "rejected", "all"):
        status = "pending"
    return render_template(
        "admin/submissions.html",
        submissions=store().list_submissions(None if status == "all" else status),
        status=status,
    )


@admin_bp.route("/submissions/<submission_id>", methods=["POST"])
@owner_session_required
def submission_decide(submission_id):
    s = store()
    sub = s.get_submission(submission_id)
    if not sub or sub["status"] != "pending":
        flash("That suggestion was already handled or does not exist.", "error")
        return redirect(url_for("admin.submissions"))
    if request.form.get("decision") == "reject":
        s.set_submission_status(submission_id, "rejected", actor_id=_actor())
        flash("Suggestion rejected.", "success")
        return redirect(url_for("admin.submissions"))
    if s.find_url_duplicate(sub["official_url"]):
        flash(
            "A course with that official URL already exists. Reject the suggestion or edit the existing course.",
            "error",
        )
        return redirect(url_for("admin.submissions"))
    provider = s.get_or_create_provider(sub["provider_name"]) if sub.get("provider_name") else None
    course = s.save_course(
        {
            "title": sub["title"],
            "official_url": sub["official_url"],
            "provider_id": provider["id"] if provider else None,
            "short_description": "",
            "description": "",
        },
        actor_id=_actor(),
        origin="user_submission",
    )
    s.set_admin_notes(course["id"], f"User suggestion. Their note (unverified): {sub.get('note') or '-'}")
    s.set_submission_status(submission_id, "accepted", actor_id=_actor(), course_id=course["id"])
    flash("Created a draft from the suggestion. Research and verify it before publishing.", "success")
    return redirect(url_for("admin.course_edit", course_id=course["id"]))


@admin_bp.route("/messages")
@owner_session_required
def messages():
    handled = request.args.get("handled")
    return render_template(
        "admin/messages.html",
        messages=store().list_contact_messages(handled=None if handled is None else handled == "1"),
    )


@admin_bp.route("/messages/<message_id>", methods=["POST"])
@owner_session_required
def message_handled(message_id):
    store().set_contact_handled(message_id, request.form.get("handled") != "0")
    return redirect(url_for("admin.messages"))


@admin_bp.route("/audit-log")
@owner_session_required
def audit_log():
    return render_template("admin/audit_log.html", actions=store().list_actions(limit=200))


# --------------------------------------------------------------- CSV export
@admin_bp.route("/export.csv")
@limiter.limit("20 per hour")
@owner_session_required
def export_csv():
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(EXPORT_COLUMNS)
    for course in store().export_courses():
        writer.writerow([spreadsheet_safe(course.get(col)) for col in EXPORT_COLUMNS])
    store().record_action(_actor(), "export", None, "csv_export")
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=coursefinder-{date.today().isoformat()}.csv"},
    )


# --------------------------------------------------------------- CSV import
def _validate_import_rows(rows):
    s = store()
    categories = {}
    for c in s.list_categories():
        categories[c["slug"]] = c
        categories[c["name"].lower()] = c
    seen_urls, results = set(), []
    for number, raw in enumerate(rows, start=2):  # row 1 is the header
        row = {k: (v or "").strip() for k, v in raw.items() if k}
        errors = []
        title, url = row.get("title", ""), row.get("official_url", "")
        if not title or len(title) > 200:
            errors.append("title is required (max 200 characters)")
        if not is_safe_http_url(url):
            errors.append("official_url must be a valid http/https URL")
        else:
            key = normalise_url(url)
            if key in seen_urls:
                errors.append("duplicate official_url inside this file")
            seen_urls.add(key)
            dup = s.find_url_duplicate(url)
            if dup:
                errors.append(f"official_url already exists (“{dup['title']}”)")
        category = categories.get(row.get("category", "").lower())
        if row.get("category") and not category:
            errors.append(f"unknown category “{row['category']}”")
        if row.get("difficulty") and row["difficulty"] not in DIFFICULTIES:
            errors.append("difficulty must be one of: " + ", ".join(DIFFICULTIES))
        is_free = to_bool_or_none(row.get("is_free"))
        if row.get("is_free") and row["is_free"].lower() not in ("true", "false", "yes", "no", "1", "0"):
            errors.append("is_free must be true or false")
        price = None
        if row.get("price"):
            try:
                price = float(row["price"])
                if price < 0:
                    raise ValueError
            except ValueError:
                errors.append("price must be a number ≥ 0")
        for field in ("source_url",):
            if row.get(field) and not is_safe_http_url(row[field]):
                errors.append(f"{field} must be a valid http/https URL")
        if row.get("source_checked_at"):
            try:
                date.fromisoformat(row["source_checked_at"])
            except ValueError:
                errors.append("source_checked_at must be YYYY-MM-DD")
        record = {
            "title": title,
            "official_url": url,
            "provider_name": row.get("provider", ""),
            "category_id": category["id"] if category else None,
            "short_description": row.get("short_description", "")[:280],
            "description": row.get("description", "")[:8000],
            "difficulty": row.get("difficulty", ""),
            "is_free": is_free,
            "price": price,
            "currency": row.get("currency") or "USD",
            "language": row.get("language", ""),
            "duration": row.get("duration", ""),
            "prerequisites": row.get("prerequisites", ""),
            "learning_outcomes": row.get("learning_outcomes", "")[:3000],
            "source_url": row.get("source_url") or None,
            "source_checked_at": row.get("source_checked_at") or None,
        }
        results.append({"row": number, "title": title, "errors": errors, "record": None if errors else record})
    return results


@admin_bp.route("/import", methods=["GET", "POST"])
@limiter.limit("20 per hour", methods=["POST"])
@owner_session_required
def import_csv():
    form = ImportForm()
    if form.validate_on_submit() and form.file.data:
        raw = form.file.data.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            flash("That file is larger than 2 MB.", "error")
        else:
            try:
                text = raw.decode("utf-8-sig")
                reader = csv.DictReader(io.StringIO(text))
                missing = {"title", "official_url"} - set(reader.fieldnames or [])
                if missing:
                    flash("Missing required column(s): " + ", ".join(sorted(missing)), "error")
                else:
                    rows = list(reader)
                    if not rows:
                        flash("The file has no data rows.", "error")
                    elif len(rows) > IMPORT_MAX_ROWS:
                        flash(f"Too many rows ({len(rows)}). The limit is {IMPORT_MAX_ROWS} per import.", "error")
                    else:
                        results = _validate_import_rows(rows)
                        batch = store().create_import_batch(
                            _actor(),
                            secure_filename(form.file.data.filename),
                            json.dumps(results),
                        )
                        return redirect(url_for("admin.import_preview", batch_id=batch))
            except (UnicodeDecodeError, csv.Error):
                flash("Could not read that file. Save it as a UTF-8 CSV.", "error")
    return render_template("admin/import.html", form=form, columns=IMPORT_COLUMNS)


@admin_bp.route("/import/<batch_id>")
@owner_session_required
def import_preview(batch_id):
    batch = store().get_import_batch(batch_id, _actor())
    if not batch:
        abort(404)
    results = json.loads(batch["payload"])
    return render_template(
        "admin/import_preview.html",
        batch=batch,
        results=results,
        ok=sum(1 for r in results if not r["errors"]),
        bad=sum(1 for r in results if r["errors"]),
    )


@admin_bp.route("/import/<batch_id>/commit", methods=["POST"])
@owner_session_required
def import_commit(batch_id):
    s = store()
    batch = s.get_import_batch(batch_id, _actor())
    if not batch:
        abort(404)
    created = skipped = 0
    for item in json.loads(batch["payload"]):
        if item["errors"]:
            continue
        record = dict(item["record"])
        provider_name = record.pop("provider_name", "")
        if provider_name:
            record["provider_id"] = s.get_or_create_provider(provider_name)["id"]
        try:
            s.save_course(record, actor_id=_actor(), origin="csv_import")  # always a draft
            created += 1
        except DuplicateError:
            skipped += 1
    s.delete_import_batch(batch_id)
    s.record_action(_actor(), "import", batch_id, "csv_import", note=f"{created} drafts created, {skipped} skipped")
    flash(
        f"Imported {created} course(s) as drafts. {skipped} skipped. Nothing is public until you publish it.",
        "success",
    )
    return redirect(url_for("admin.courses", status="draft"))
