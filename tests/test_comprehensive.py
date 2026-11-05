"""Comprehensive test suite covering all 12 required areas for CourseFinder:

1. User registration and login (including password change and session handling).
2. Unauthorized access to admin routes (anonymous & regular user).
3. Course creation and editing (admin CRUD, duplicate checks, private admin notes).
4. Draft visibility restrictions (public catalogue, search, detail, and JSON API).
5. Authorized publication workflow (draft -> pending_review -> approved -> published -> archived).
6. Prevention of self-promotion to administrator (registration, profile, promote script token check).
7. Course search, advanced filtering (category, provider, pricing, difficulty, language, rating, featured, underrated) and sorting.
8. Saved/bookmarked courses (save, list in library, unsave, draft courses cannot be saved).
9. Review submission, editing, deletion, duplicate-submission handling, and admin moderation.
10. CSV import validation, preview, commit as draft, and formula-injection-safe CSV export.
11. Database permissions and triggers (direct SQLite trigger enforcement for draft insert, approval before publish, untrusted origins, and pending reviews).
12. Main frontend user journeys, CSRF enforcement, and security headers.
"""

import io
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app import create_app, workflow
from app.store.errors import DuplicateError, NotFoundError, StoreError


def _register(client, email="learner@example.com", password="correcthorsebatterystaple", name="Learner"):
    return client.post(
        "/auth/register",
        data={"display_name": name, "email": email, "password": password},
        follow_redirects=True,
    )


def _login(client, email, password="correcthorsebatterystaple"):
    return client.post(
        "/auth/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


def _make_admin(client, store, email="admin@example.com", password="correcthorsebatterystaple"):
    store.create_user(email, password, "Editor Admin")
    store.set_user_role(email, "admin")
    client.post(
        "/admin/studio",
        data={"owner_secret": "test-owner-access-secret-at-least-32"},
    )
    return store.get_user_by_email(email)


# ---------------------------------------------------------------------------
# 1. User registration, login, logout, and profile / password management
# ---------------------------------------------------------------------------
def test_user_registration_login_logout_and_profile(client, store):
    res = _register(client, "alice@example.com", "strongpassword123", "Alice")
    assert res.status_code == 200
    assert "My library" in res.get_data(as_text=True)

    # Duplicate email registration is rejected
    client.post("/auth/logout", follow_redirects=True)
    dup = _register(client, "alice@example.com", "strongpassword123", "Alice2")
    assert "An account with this email already exists." in dup.get_data(as_text=True)

    # Wrong password fails cleanly
    bad_login = _login(client, "alice@example.com", "wrongpassword")
    assert "Email or password is incorrect." in bad_login.get_data(as_text=True)

    # Valid login succeeds and blocks open redirects
    redir = client.post(
        "/auth/login?next=https://evil.example.com/phish",
        data={"email": "alice@example.com", "password": "strongpassword123"},
        follow_redirects=False,
    )
    assert redir.status_code == 302
    assert "evil.example.com" not in redir.headers["Location"]

    # Profile page renders and updates display name
    prof_get = client.get("/account/profile")
    assert prof_get.status_code == 200
    prof_post = client.post(
        "/account/profile",
        data={"action": "profile", "display_name": "Alice Updated"},
        follow_redirects=True,
    )
    assert prof_post.status_code == 200
    assert store.get_user_by_email("alice@example.com")["display_name"] == "Alice Updated"

    # Password change works and verifies current password
    bad_pw = client.post(
        "/account/profile",
        data={
            "action": "password",
            "current_password": "wrongcurrent",
            "new_password": "newsecurepassword123",
            "confirm_password": "newsecurepassword123",
        },
        follow_redirects=True,
    )
    assert "Current password is incorrect." in bad_pw.get_data(as_text=True)

    good_pw = client.post(
        "/account/profile",
        data={
            "action": "password",
            "current_password": "strongpassword123",
            "new_password": "newsecurepassword123",
            "confirm_password": "newsecurepassword123",
        },
        follow_redirects=True,
    )
    assert "Password updated." in good_pw.get_data(as_text=True)
    assert store.authenticate("alice@example.com", "newsecurepassword123") is not None


# ---------------------------------------------------------------------------
# 2. Unauthorized access to admin routes
# ---------------------------------------------------------------------------
def test_unauthorized_access_to_all_admin_routes_blocked(client):
    admin_paths = [
        "/admin/",
        "/admin/courses",
        "/admin/courses/new",
        "/admin/categories",
        "/admin/providers",
        "/admin/reviews",
        "/admin/reports",
        "/admin/submissions",
        "/admin/messages",
        "/admin/audit-log",
        "/admin/import",
        "/admin/export.csv",
    ]
    # Anonymous requests are sent to the password-only Owner Studio screen.
    for path in admin_paths:
        res = client.get(path, follow_redirects=False)
        assert res.status_code == 302
        assert res.headers["Location"].endswith("/admin/studio")

    # Regular login does not authenticate Owner Studio.
    _register(client, "regular@example.com")
    for path in admin_paths:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/admin/studio")
    assert client.post("/admin/courses/new", data={"title": "Hack"}).status_code == 302
    assert client.post("/admin/categories", data={"name": "Hack"}).status_code == 302


# ---------------------------------------------------------------------------
# 3. Course creation and editing & 4. Draft visibility restrictions
# ---------------------------------------------------------------------------
def test_course_creation_defaults_to_draft_and_is_hidden_everywhere_public(client, store):
    _make_admin(client, store)
    cat = store.list_categories()[0]

    # Admin creates a new course via the form — even ticking featured/underrated/editorial_approved
    # keeps the course in 'draft' status so it never appears publicly before approval & publication.
    res = client.post(
        "/admin/courses/new",
        data={
            "title": "Distributed Systems Engineering",
            "official_url": "https://pdos.csail.mit.edu/6.824/",
            "new_provider_name": "MIT PDOS",
            "new_provider_url": "https://pdos.csail.mit.edu",
            "category_id": cat["id"],
            "subcategory": "Systems",
            "short_description": "Graduate-level distributed systems engineering with Go labs.",
            "description": "Covers fault tolerance, replication, Raft consensus, and distributed transactions.",
            "language": "English",
            "is_free": "true",
            "currency": "USD",
            "difficulty": "advanced",
            "prerequisites": "Operating systems and programming in Go or C.",
            "duration": "15 weeks",
            "duration_verified": "y",
            "skills_taught": "Distributed Systems\nRaft Consensus\nFault Tolerance",
            "recommendation_status": "recommended",
            "is_featured": "y",
            "is_underrated": "y",
            "admin_notes": "Checked syllabus on official PDOS page.",
        },
        follow_redirects=True,
    )
    assert res.status_code == 200
    assert "Saved as a draft" in res.get_data(as_text=True)

    # Verify in store: status MUST be 'draft'
    all_courses = store.list_courses(q="Distributed Systems Engineering", include_unpublished=True)["items"]
    assert len(all_courses) == 1
    draft = all_courses[0]
    assert draft["status"] == "draft"
    assert store.get_admin_notes(draft["id"]) == "Checked syllabus on official PDOS page."

    # Admin can preview the draft
    preview = client.get(f"/admin/courses/{draft['id']}/preview")
    assert preview.status_code == 200
    assert "Administrator preview" in preview.get_data(as_text=True)

    # Edit the course content via admin form — status must remain 'draft'
    edit_res = client.post(
        f"/admin/courses/{draft['id']}/edit",
        data={
            "title": "MIT 6.5840: Distributed Systems Engineering",
            "official_url": "https://pdos.csail.mit.edu/6.824/",
            "provider_id": draft["provider_id"],
            "category_id": cat["id"],
            "short_description": "Updated short description for MIT Distributed Systems.",
            "description": "Covers fault tolerance, replication, Raft, and sharded key-value stores.",
            "is_free": "true",
            "currency": "USD",
            "difficulty": "advanced",
            "recommendation_status": "recommended",
            "admin_notes": "Updated course number to 6.5840.",
        },
        follow_redirects=True,
    )
    assert edit_res.status_code == 200
    updated = store.get_course(course_id=draft["id"], include_unpublished=True)
    assert updated["title"] == "MIT 6.5840: Distributed Systems Engineering"
    assert updated["status"] == "draft"

    # Sign out and verify the draft is completely invisible on homepage, catalogue, category page, detail page, and API
    client.post("/auth/logout")
    assert "6.5840" not in client.get("/").get_data(as_text=True)
    assert "6.5840" not in client.get("/courses?q=6.5840").get_data(as_text=True)
    assert "6.5840" not in client.get(f"/categories/{cat['slug']}").get_data(as_text=True)
    assert client.get(f"/courses/{updated['slug']}").status_code == 404
    assert client.get(f"/api/v1/courses/{updated['slug']}").status_code == 404
    api_list = client.get("/api/v1/courses?q=6.5840").get_json()
    assert api_list["total"] == 0


# ---------------------------------------------------------------------------
# 5. Authorized publication workflow & editorial controls
# ---------------------------------------------------------------------------
def test_authorized_publication_workflow_and_editorial_flags(client, store):
    _make_admin(client, store)
    cat = store.list_categories()[0]
    provider = store.get_or_create_provider("Stanford Online", "https://online.stanford.edu")
    course = store.save_course(
        {
            "title": "Compilers",
            "official_url": "https://online.stanford.edu/courses/soe-ycscs1-compilers",
            "provider_id": provider["id"],
            "category_id": cat["id"],
            "short_description": "Lexical analysis, parsing, semantic analysis, and code generation.",
            "description": "Full compiler implementation course.",
            "is_free": True,
            "difficulty": "intermediate",
        }
    )
    cid = course["id"]
    assert course["status"] == "draft"

    # Cannot jump directly from draft to published
    jump = client.post(f"/admin/courses/{cid}/transition", data={"target": "published"}, follow_redirects=True)
    assert "A course must be approved before it can be published." in jump.get_data(as_text=True)
    assert store.get_course(course_id=cid, include_unpublished=True)["status"] == "draft"

    # Step 1: draft -> pending_review
    client.post(f"/admin/courses/{cid}/transition", data={"target": "pending_review"}, follow_redirects=True)
    assert store.get_course(course_id=cid, include_unpublished=True)["status"] == "pending_review"
    assert client.get(f"/courses/{course['slug']}").status_code == 404

    # Step 2: pending_review -> approved (still not live!)
    client.post(f"/admin/courses/{cid}/transition", data={"target": "approved"}, follow_redirects=True)
    assert store.get_course(course_id=cid, include_unpublished=True)["status"] == "approved"
    assert client.get(f"/courses/{course['slug']}").status_code == 404

    # Step 3: approved -> published (now live!)
    client.post(f"/admin/courses/{cid}/transition", data={"target": "published"}, follow_redirects=True)
    live = store.get_course(course_id=cid, include_unpublished=True)
    assert live["status"] == "published"
    assert live["published_at"] is not None
    assert client.get(f"/courses/{course['slug']}").status_code == 200

    # Quick-toggle Featured and Underrated flags via admin route
    client.post(f"/admin/courses/{cid}/flag", data={"flag": "is_featured"}, follow_redirects=True)
    client.post(f"/admin/courses/{cid}/flag", data={"flag": "is_underrated"}, follow_redirects=True)
    flagged = store.get_course(course_id=cid)
    assert flagged["is_featured"] is True
    assert flagged["is_underrated"] is True

    # Cannot delete a published course while live
    del_live = client.post(
        f"/admin/courses/{cid}/delete",
        data={"confirm": "Compilers"},
        follow_redirects=True,
    )
    assert "Unpublish or archive a live course before deleting it." in del_live.get_data(as_text=True)

    # Step 4: Unpublish (published -> draft) removes it from public view immediately
    client.post(f"/admin/courses/{cid}/transition", data={"target": "draft"}, follow_redirects=True)
    assert client.get(f"/courses/{course['slug']}").status_code == 404

    # Now deletion with exact title confirmation works
    del_ok = client.post(
        f"/admin/courses/{cid}/delete",
        data={"confirm": "Compilers"},
        follow_redirects=True,
    )
    assert "Course deleted." in del_ok.get_data(as_text=True)
    assert store.get_course(course_id=cid, include_unpublished=True) is None


# ---------------------------------------------------------------------------
# 6. Prevention of self-promotion to administrator
# ---------------------------------------------------------------------------
def test_prevention_of_self_promotion_to_admin(client, store):
    # Attempt to pass role=admin during registration
    client.post(
        "/auth/register",
        data={
            "display_name": "Attacker",
            "email": "attacker@example.com",
            "password": "attackerpassword123",
            "role": "admin",
        },
        follow_redirects=True,
    )
    user = store.get_user_by_email("attacker@example.com")
    assert user["role"] == "user"

    # Attempt to pass role=admin during profile update
    client.post(
        "/account/profile",
        data={"action": "profile", "display_name": "Attacker2", "role": "admin"},
        follow_redirects=True,
    )
    assert store.get_user_by_email("attacker@example.com")["role"] == "user"

    # Verify promote_admin.py rejects wrong or short setup tokens
    script = Path(__file__).resolve().parents[1] / "scripts" / "promote_admin.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--email", "attacker@example.com", "--token", "wrong-token"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert store.get_user_by_email("attacker@example.com")["role"] == "user"


# ---------------------------------------------------------------------------
# 7. Course search, advanced filtering, and sorting
# ---------------------------------------------------------------------------
def test_course_search_filtering_and_sorting(client, store):
    cats = {c["slug"]: c["id"] for c in store.list_categories()}
    prov1 = store.get_or_create_provider("Provider Alpha", "https://alpha.example.com")
    prov2 = store.get_or_create_provider("Provider Beta", "https://beta.example.com")

    def make_live(title, url, cat_slug, prov_id, is_free, price, diff, lang, underrated=False, featured=False, score=None):
        c = store.save_course(
            {
                "title": title,
                "official_url": url,
                "provider_id": prov_id,
                "category_id": cats[cat_slug],
                "short_description": f"Summary for {title}",
                "description": f"Detailed syllabus for {title}",
                "is_free": is_free,
                "price": price,
                "currency": "USD",
                "difficulty": diff,
                "language": lang,
                "is_underrated": underrated,
                "is_featured": featured,
                "editorial_score": score,
                "editorial_score_public": bool(score),
                "editorial_approved": bool(score),
            }
        )
        store.set_course_status(c["id"], workflow.PENDING_REVIEW)
        store.set_course_status(c["id"], workflow.APPROVED)
        store.set_course_status(c["id"], workflow.PUBLISHED)
        return c

    make_live(
        "Applied Bayesian Data Science",
        "https://alpha.example.com/bayesian",
        "data-science",
        prov1["id"],
        False,
        49.0,
        "intermediate",
        "English",
        underrated=True,
        score=9.5,
    )
    make_live(
        "Foundations of UI Design Systems",
        "https://beta.example.com/ui-design",
        "design",
        prov2["id"],
        True,
        None,
        "beginner",
        "Spanish",
        featured=True,
        score=8.0,
    )

    # Filter by underrated
    underrated_html = client.get("/courses?underrated=1").get_data(as_text=True)
    assert "Applied Bayesian Data Science" in underrated_html
    assert "Foundations of UI Design Systems" not in underrated_html

    # Filter by pricing=paid
    paid_html = client.get("/courses?pricing=paid").get_data(as_text=True)
    assert "Applied Bayesian Data Science" in paid_html
    assert "Foundations of UI Design Systems" not in paid_html

    # Filter by provider
    prov_html = client.get(f"/courses?provider={prov2['id']}").get_data(as_text=True)
    assert "Foundations of UI Design Systems" in prov_html
    assert "Applied Bayesian Data Science" not in prov_html

    # Filter by language
    lang_html = client.get("/courses?language=Spanish").get_data(as_text=True)
    assert "Foundations of UI Design Systems" in lang_html
    assert "Applied Bayesian Data Science" not in lang_html

    # Sort by editorial score
    sorted_items = store.list_courses(sort="editorial")["items"]
    assert sorted_items[0]["title"] == "Applied Bayesian Data Science"


# ---------------------------------------------------------------------------
# 8. Saved / bookmarked courses
# ---------------------------------------------------------------------------
def test_saved_courses_workflow_and_draft_protection(client, store):
    _register(client, "bookmarker@example.com")
    live = store.list_courses()["items"][0]
    draft = store.list_courses(include_unpublished=True, status="draft")["items"][0]

    # Save a published course
    res = client.post(f"/account/save/{live['id']}", follow_redirects=True)
    assert res.status_code == 200
    lib = client.get("/account/").get_data(as_text=True)
    assert live["title"] in lib

    # Cannot save a draft course
    bad_save = client.post(f"/account/save/{draft['id']}", follow_redirects=True)
    assert "That course is not available." in bad_save.get_data(as_text=True)

    # Unsave published course
    client.post(f"/account/unsave/{live['id']}", follow_redirects=True)
    assert len(store.list_saved(store.get_user_by_email("bookmarker@example.com")["id"])) == 0


# ---------------------------------------------------------------------------
# 9. Review submission, editing, deletion, duplicate prevention & moderation
# ---------------------------------------------------------------------------
def test_review_lifecycle_and_moderation(client, store):
    _register(client, "reviewer1@example.com", name="Reviewer One")
    live = store.list_courses()["items"][0]

    # Too-short review is rejected by server-side validation
    short_res = client.post(
        f"/courses/{live['slug']}/review",
        data={"rating": "5", "body": "Too short"},
        follow_redirects=True,
    )
    assert "Field must be between 20 and 4000 characters long." in short_res.get_data(as_text=True)

    # Valid review submission creates 1 pending review
    ok_res = client.post(
        f"/courses/{live['slug']}/review",
        data={"rating": "5", "body": "Rigorous problem sets that genuinely improved my systems thinking."},
        follow_redirects=True,
    )
    assert "waiting for moderation" in ok_res.get_data(as_text=True)

    # Submitting a second time from the same user updates the existing review (no duplicate row)
    upd_res = client.post(
        f"/courses/{live['slug']}/review",
        data={"rating": "4", "body": "Updated review after finishing the final project: demanding and worthwhile."},
        follow_redirects=True,
    )
    assert "updated and is waiting for moderation" in upd_res.get_data(as_text=True)
    pending = store.list_reviews(course_id=live["id"], status="pending")
    assert len(pending) == 1
    assert pending[0]["rating"] == 4

    # Admin moderates (approves) the review
    client.post("/auth/logout")
    _make_admin(client, store, "moderator@example.com")
    client.post(f"/admin/reviews/{pending[0]['id']}/status", data={"status": "approved"}, follow_redirects=True)
    detail_html = client.get(f"/courses/{live['slug']}").get_data(as_text=True)
    assert "Updated review after finishing the final project" in detail_html

    # Author can delete their own review
    client.post("/auth/logout")
    _login(client, "reviewer1@example.com")
    client.post(f"/courses/{live['slug']}/review/delete", follow_redirects=True)
    assert len(store.list_reviews(course_id=live["id"], status="approved")) == 0


# ---------------------------------------------------------------------------
# 10. CSV import validation & formula-injection-safe export
# ---------------------------------------------------------------------------
def test_csv_import_validation_draft_enforcement_and_export(client, store):
    _make_admin(client, store)

    csv_content = (
        "title,official_url,provider,category,short_description,description,difficulty,is_free,price\n"
        "Statistical Rethinking,https://xcelab.net/rm/,Richard McElreath,data-science,Bayesian data analysis course.,Full lectures and homework.,intermediate,true,\n"
        "Bad Javascript URL,javascript:alert(1),Evil,data-science,Bad,Bad,beginner,true,\n"
        "Bad Category Row,https://example.com/bad-cat,Prov,non-existent-category,Short,Desc,beginner,true,\n"
    )
    upload = client.post(
        "/admin/import",
        data={"file": (io.BytesIO(csv_content.encode("utf-8")), "courses.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    html = upload.get_data(as_text=True)
    assert upload.status_code == 200
    assert "<strong>1</strong> row(s) ready, <strong>2</strong> with errors" in html
    assert "official_url must be a valid http/https URL" in html
    assert "unknown category" in html

    # Extract batch_id from form action and commit
    import re

    match = re.search(r"/admin/import/([a-f0-9\-]+)/commit", html)
    assert match is not None
    batch_id = match.group(1)
    commit_res = client.post(f"/admin/import/{batch_id}/commit", follow_redirects=True)
    assert "Imported 1 course(s) as drafts." in commit_res.get_data(as_text=True)

    imported = store.list_courses(q="Statistical Rethinking", include_unpublished=True)["items"]
    assert len(imported) == 1
    assert imported[0]["status"] == "draft"
    assert imported[0]["origin"] == "csv_import"
    assert imported[0]["is_featured"] is False
    assert imported[0]["needs_verification"] is True

    # Verify CSV export escapes spreadsheet formula payloads
    store.set_admin_notes(imported[0]["id"], "=cmd|' /C calc'!A0")
    export_res = client.get("/admin/export.csv")
    assert export_res.status_code == 200
    assert "'=cmd|' /C calc'!A0" in export_res.get_data(as_text=True)


# ---------------------------------------------------------------------------
# 11. Database permissions & SQLite triggers
# ---------------------------------------------------------------------------
def test_database_triggers_block_direct_publish_and_untrusted_auto_feature(store):
    with store._conn() as conn:
        # 1. Direct INSERT with status='published' is aborted by SQLite trigger
        with pytest.raises(sqlite3.IntegrityError, match="a new course must start as a draft"):
            conn.execute(
                "INSERT INTO courses (id, slug, title, official_url, status, created_at, updated_at) "
                "VALUES ('id-1', 'slug-1', 'Bypass Draft', 'https://example.com/1', 'published', '2026-01-01', '2026-01-01')"
            )

        # 2. Direct INSERT from untrusted origin with is_featured=1 is aborted by SQLite trigger
        with pytest.raises(sqlite3.IntegrityError, match="untrusted course origins cannot auto-feature"):
            conn.execute(
                "INSERT INTO courses (id, slug, title, official_url, status, origin, is_featured, created_at, updated_at) "
                "VALUES ('id-2', 'slug-2', 'Bypass Feature', 'https://example.com/2', 'draft', 'ai_suggestion', 1, '2026-01-01', '2026-01-01')"
            )

        # 3. Direct UPDATE from draft -> published (skipping approved) is aborted by SQLite trigger
        draft = store.list_courses(include_unpublished=True, status="draft")["items"][0]
        with pytest.raises(sqlite3.IntegrityError, match="a course must be approved before it can be published"):
            conn.execute("UPDATE courses SET status = 'published' WHERE id = ?", (draft["id"],))


# ---------------------------------------------------------------------------
# 12. Main frontend journeys, CSRF protection, and security headers
# ---------------------------------------------------------------------------
def test_csrf_protection_and_security_headers(tmp_path):
    secure_app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": True,
            "DATA_BACKEND": "local",
            "DATABASE_PATH": str(tmp_path / "csrf.db"),
            "SEED_SAMPLE_COURSES": False,
            "SECRET_KEY": "a-very-strong-random-secret-key-for-csrf-testing",
            "RATELIMIT_ENABLED": False,
        }
    )
    c = secure_app.test_client()

    # Check security headers on public response
    home = c.get("/")
    assert home.status_code == 200
    assert home.headers.get("X-Frame-Options") == "DENY"
    assert home.headers.get("X-Content-Type-Options") == "nosniff"
    assert "default-src 'self'" in home.headers.get("Content-Security-Policy", "")

    # POST without CSRF token is rejected with 400
    post_no_csrf = c.post(
        "/auth/register",
        data={"display_name": "NoCSRF", "email": "nocsrf@example.com", "password": "password1234"},
    )
    assert post_no_csrf.status_code == 400


def test_user_course_suggestion_creates_draft_only_when_admin_accepts(client, store):
    _register(client, "suggester@example.com")
    sub_page = client.get("/account/submit")
    assert sub_page.status_code == 200

    res = client.post(
        "/account/submit",
        data={
            "title": "Nand2Tetris: Build a Modern Computer from First Principles",
            "official_url": "https://www.nand2tetris.org/",
            "provider_name": "Hebrew University of Jerusalem",
            "note": "Hands-on hardware to software construction.",
        },
        follow_redirects=True,
    )
    assert "not published automatically" in res.get_data(as_text=True)

    # Not in courses table yet
    assert len(store.list_courses(q="Nand2Tetris", include_unpublished=True)["items"]) == 0

    # Admin accepts suggestion -> creates an unverified draft
    client.post("/auth/logout")
    _make_admin(client, store, "chief@example.com")
    subs = store.list_submissions(status="pending")
    assert len(subs) == 1
    client.post(f"/admin/submissions/{subs[0]['id']}", data={"decision": "accept"}, follow_redirects=True)

    drafts = store.list_courses(q="Nand2Tetris", include_unpublished=True)["items"]
    assert len(drafts) == 1
    assert drafts[0]["status"] == "draft"
    assert drafts[0]["origin"] == "user_submission"
    assert drafts[0]["needs_verification"] is True
