import hashlib
import re
from datetime import datetime, timedelta, timezone


OWNER_SECRET = "test-owner-access-secret-at-least-32"


def _unlock(client, secret=OWNER_SECRET):
    return client.post("/admin/studio", data={"owner_secret": secret})


def test_owner_access_screen_is_password_only_and_does_not_expose_secret(client):
    response = client.get("/admin/studio")

    assert response.status_code == 200
    assert b'name="owner_secret"' in response.data
    assert b'type="password"' in response.data
    assert b"Unlock Owner Studio" in response.data
    assert b'type="email"' not in response.data
    assert b'name="email"' not in response.data
    assert OWNER_SECRET.encode() not in response.data
    assert b"Sign Up" not in response.data


def test_missing_owner_secret_fails_closed(app, client):
    app.config["OWNER_ACCESS_SECRET"] = ""

    response = client.post("/admin/studio", data={"owner_secret": OWNER_SECRET})

    assert response.status_code == 200
    assert b"Owner access is not configured" in response.data
    assert client.get("/admin/").status_code == 302

    # Short (< 16 chars) secret also fails closed
    app.config["OWNER_ACCESS_SECRET"] = "too-short-12"
    short_res = client.post("/admin/studio", data={"owner_secret": "too-short-12"})
    assert short_res.status_code == 200
    assert b"Owner access is misconfigured" in short_res.data
    assert client.get("/admin/").status_code == 302


def test_empty_password_rejection(client):
    empty_res = client.post("/admin/studio", data={"owner_secret": ""})
    assert empty_res.status_code == 200
    assert b"Enter your owner password to unlock Owner Studio." in empty_res.data
    assert client.get("/admin/").status_code == 302

    whitespace_res = client.post("/admin/studio", data={"owner_secret": "   \t\r\n  "})
    assert whitespace_res.status_code == 200
    assert b"Enter your owner password to unlock Owner Studio." in whitespace_res.data
    assert client.get("/admin/").status_code == 302


def test_invalid_and_valid_owner_secret_attempts(client, store):
    invalid = _unlock(client, "incorrect owner secret")
    assert invalid.status_code == 200
    assert b"Incorrect owner password" in invalid.data
    assert client.get("/admin/").status_code == 302
    assert client.get("/admin/").headers["Location"].endswith("/admin/studio")

    unlocked = _unlock(client)
    assert unlocked.status_code == 302
    assert unlocked.headers["Location"].endswith("/admin/")
    assert client.get("/admin/").status_code == 200
    with client.session_transaction() as browser_session:
        token = browser_session["owner_session_token"]
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    assert store.validate_owner_session(token_hash)


def test_whitespace_and_crlf_normalization_on_owner_secret(app, client, store):
    app.config["OWNER_ACCESS_SECRET"] = f"  {OWNER_SECRET}\r\n "
    unlocked = _unlock(client, f" {OWNER_SECRET} \n")
    assert unlocked.status_code == 302
    assert unlocked.headers["Location"].endswith("/admin/")
    assert client.get("/admin/").status_code == 200


def test_session_persistence_across_pages_and_refreshes_and_stale_user_id(client, store):
    _unlock(client)

    # Session persists across multiple Owner Studio pages and repeated refreshes
    for path in (
        "/admin/",
        "/admin/",
        "/admin/courses",
        "/admin/courses/new",
        "/admin/categories",
        "/admin/providers",
        "/admin/users",
        "/admin/reviews",
        "/admin/reports",
        "/admin/submissions",
        "/admin/messages",
        "/admin/import",
        "/admin/audit-log",
        "/admin/ai-assistant",
    ):
        res = client.get(path)
        assert res.status_code == 200, f"Failed on {path}"

    # Even if a stale or suspended user_id is present in the session cookie,
    # refreshing Owner Studio pages removes only the stale user_id and keeps Owner Studio unlocked.
    with client.session_transaction() as browser_session:
        browser_session["user_id"] = "stale-or-deleted-user-id"

    first_refresh = client.get("/admin/")
    second_refresh = client.get("/admin/users")
    assert first_refresh.status_code == 200
    assert second_refresh.status_code == 200
    with client.session_transaction() as browser_session:
        assert "user_id" not in browser_session
        assert "owner_session_token" in browser_session


def test_owner_session_logout_revokes_server_side_session(client, store):
    _unlock(client)
    with client.session_transaction() as browser_session:
        token = browser_session["owner_session_token"]
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    assert store.validate_owner_session(token_hash)

    logout = client.post("/admin/logout")

    assert logout.status_code == 302
    assert logout.headers["Location"].endswith("/admin/studio")
    assert not store.validate_owner_session(token_hash)
    assert client.get("/admin/").headers["Location"].endswith("/admin/studio")

    # Following the redirect to /admin/studio renders the "Owner Studio locked." flash message
    studio_page = client.get("/admin/studio")
    assert studio_page.status_code == 200
    assert b"Owner Studio locked." in studio_page.data


def test_owner_without_user_login_can_write_audit_and_import_records(client, store):
    _unlock(client)

    response = client.get("/admin/export.csv")

    assert response.status_code == 200
    assert store.list_actions()[0]["actor_id"] is None
    batch_id = store.create_import_batch(None, "courses.csv", "[]")
    assert store.get_import_batch(batch_id, None)["filename"] == "courses.csv"


def test_expired_and_revoked_server_sessions_are_rejected(client, store):
    _unlock(client)
    with client.session_transaction() as browser_session:
        token = browser_session["owner_session_token"]
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with store._conn() as connection:
        connection.execute(
            "UPDATE owner_sessions SET expires_at = ? WHERE token_hash = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), token_hash),
        )

    assert client.get("/admin/").headers["Location"].endswith("/admin/studio")


def test_every_admin_get_route_requires_owner_session(app, client):
    with app.test_request_context():
        rules = [
            rule.rule
            for rule in app.url_map.iter_rules()
            if rule.endpoint.startswith("admin.")
            and rule.endpoint != "admin.studio"
            and "GET" in rule.methods
        ]

    assert rules
    for path in rules:
        path = re.sub(r"<(?:[^:<>]+:)?[^<>]+>", "test-id", path)
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 302, f"{path} was not gated"
        assert response.headers["Location"].endswith("/admin/studio"), path


def test_every_admin_post_route_requires_owner_session(app, client):
    with app.test_request_context():
        rules = [
            rule.rule
            for rule in app.url_map.iter_rules()
            if rule.endpoint.startswith("admin.")
            and rule.endpoint != "admin.studio"
            and "POST" in rule.methods
        ]

    assert rules
    for path in rules:
        path = re.sub(r"<(?:[^:<>]+:)?[^<>]+>", "test-id", path)
        response = client.post(path, follow_redirects=False)
        assert response.status_code == 302, f"{path} was not gated"
        assert response.headers["Location"].endswith("/admin/studio"), path


def test_database_admin_login_does_not_bypass_owner_secret(client, store):
    member = store.create_user("member@example.com", "regular-password", "Member")
    store.update_user_role(member["id"], "admin")
    client.post(
        "/auth/login",
        data={"email": "member@example.com", "password": "regular-password"},
    )

    response = client.get("/admin/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/studio")
    assert client.get("/admin/studio").status_code == 200


def test_owner_unlock_post_requires_csrf_and_accepts_valid_csrf(app, client):
    app.config["WTF_CSRF_ENABLED"] = True

    # Missing CSRF token is rejected with 400 on the Owner Studio page
    response = _unlock(client)
    assert response.status_code == 400
    assert b"Your form expired" in response.data
    assert client.get("/admin/").status_code == 302

    # Valid CSRF token succeeds and redirects to /admin/
    studio_get = client.get("/admin/studio")
    match = re.search(rb'name="csrf_token" value="([^"]+)"', studio_get.data)
    assert match is not None
    csrf_token = match.group(1).decode("utf-8")

    valid_unlock = client.post(
        "/admin/studio",
        data={"csrf_token": csrf_token, "owner_secret": OWNER_SECRET},
    )
    assert valid_unlock.status_code == 302
    assert valid_unlock.headers["Location"].endswith("/admin/")
    assert client.get("/admin/").status_code == 200


def test_owner_unlock_is_rate_limited(tmp_path):
    from app import create_app

    app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "DATA_BACKEND": "local",
            "DATABASE_PATH": str(tmp_path / "rate-limit.db"),
            "SEED_SAMPLE_COURSES": False,
            "SECRET_KEY": "test-secret-key-for-rate-limit",
            "OWNER_ACCESS_SECRET": OWNER_SECRET,
            "RATELIMIT_ENABLED": True,
            "RATELIMIT_STORAGE_URI": "memory://",
        }
    )
    client = app.test_client()
    responses = [_unlock(client, "wrong secret") for _ in range(6)]

    assert all(response.status_code == 200 for response in responses[:5])
    assert responses[5].status_code == 429
    assert b"Please wait a little while and try again." in responses[5].data
