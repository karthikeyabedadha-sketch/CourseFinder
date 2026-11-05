import sqlite3
from io import BytesIO

import pytest

from scripts import provision_owner
from app.store.local import LocalStore
from app.utils import allowed_image, looks_like_image


def _sign_in_admin(client, store):
    store.create_user("owner@example.com", "correcthorse", "Owner")
    store.set_user_role("owner@example.com", "admin")
    client.post(
        "/admin/studio",
        data={"owner_secret": "test-owner-access-secret-at-least-32"},
    )
    return store.get_user_by_email("owner@example.com")


def test_admin_user_directory_searches_and_never_exposes_password_hash(client, store):
    _sign_in_admin(client, store)
    store.create_user("learner@example.com", "correcthorse", "Taylor Learner")

    response = client.get("/admin/users?q=Taylor")

    assert response.status_code == 200
    assert b"Taylor Learner" in response.data
    assert b"learner@example.com" in response.data
    assert b"password_hash" not in response.data
    assert b"Promote to admin" in response.data


def test_admin_can_view_account_details_reviews_and_activity_but_regular_users_cannot(
    client, store
):
    _sign_in_admin(client, store)
    member = store.create_user("member@example.com", "correcthorse", "Member")
    course = store.list_courses(public_only=True)["items"][0]
    store.add_review(
        course["id"],
        member["id"],
        4,
        "A detailed review about the course content and learning experience.",
    )
    store.record_action(None, "user", member["id"], "account_status_changed", to_status="active")

    details = client.get(f"/admin/users/{member['id']}")

    assert details.status_code == 200
    assert b"member@example.com" in details.data
    assert b"Recent reviews (1)" in details.data
    assert b"detailed review" in details.data
    assert b"Account Status Changed" in details.data

    client.post("/auth/logout")
    client.post("/auth/login", data={"email": "member@example.com", "password": "correcthorse"})
    assert client.get(f"/admin/users/{member['id']}").status_code == 302


def test_admin_can_promote_and_demote_accounts_without_demoting_self(client, store):
    owner = _sign_in_admin(client, store)
    learner = store.create_user("learner@example.com", "correcthorse", "Taylor Learner")

    promoted = client.post(f"/admin/users/{learner['id']}/role", data={"role": "admin"})
    assert promoted.status_code == 302
    assert store.get_user(learner["id"])["role"] == "admin"

    demoted = client.post(f"/admin/users/{learner['id']}/role", data={"role": "user"})
    assert demoted.status_code == 302
    assert store.get_user(learner["id"])["role"] == "user"
    assert store.get_user(owner["id"])["role"] == "admin"

    changes = [
        action
        for action in store.list_actions(limit=20)
        if action["entity_id"] == learner["id"] and action["action"] == "role_changed"
    ]
    assert [action["to_status"] for action in changes] == ["user", "admin"]


def test_first_owner_setup_creates_hashed_admin_and_disables_repeat_setup(
    app, client, store
):
    owner = store.create_first_owner("owner@example.com", "new-owner-password")

    assert owner["role"] == "admin"
    assert owner["is_active"] == 1
    assert store.count_users() == 1
    assert store.authenticate("owner@example.com", "new-owner-password")["id"] == owner["id"]
    with sqlite3.connect(store.path) as connection:
        password_hash = connection.execute(
            "SELECT password_hash FROM users WHERE id = ?", (owner["id"],)
        ).fetchone()[0]
    assert password_hash != "new-owner-password"
    client.post(
        "/auth/login",
        data={"email": "owner@example.com", "password": "new-owner-password"},
    )
    assert client.get("/admin/").status_code == 302
    client.post(
        "/admin/studio",
        data={"owner_secret": "test-owner-access-secret-at-least-32"},
    )
    assert client.get("/admin/").status_code == 200
    with pytest.raises(ValueError, match="owner account already exists"):
        store.create_first_owner("second@example.com", "another-secure-password")
    assert store.count_users() == 1
    assert any(
        action["action"] == "first_owner_created" and action["entity_id"] == owner["id"]
        for action in store.list_actions(limit=20)
    )


def test_first_owner_command_uses_hidden_prompts_and_active_local_database(
    app, store, monkeypatch, capsys
):
    from app.config import Config

    prompts = iter(
        [
            "owner@example.com",
            "a-new-owner-password",
            "a-new-owner-password",
        ]
    )
    monkeypatch.setattr(Config, "DATA_BACKEND", "local")
    monkeypatch.setattr(Config, "DATABASE_PATH", str(store.path))
    monkeypatch.setattr(provision_owner, "input", lambda _prompt: next(prompts), raising=False)
    monkeypatch.setattr(provision_owner.getpass, "getpass", lambda _prompt: next(prompts))

    provision_owner.main()

    owner = store.get_user_by_email("owner@example.com")
    assert owner["role"] == "admin"
    assert store.authenticate("owner@example.com", "a-new-owner-password")["id"] == owner["id"]
    assert "a-new-owner-password" not in capsys.readouterr().out


def test_first_owner_command_refuses_existing_owner_remote_backend_and_missing_database(
    monkeypatch, tmp_path
):
    from app.config import Config

    missing_database = tmp_path / "must-not-be-created.db"
    monkeypatch.setattr(Config, "DATA_BACKEND", "supabase")
    monkeypatch.setattr(Config, "DATABASE_PATH", str(missing_database))

    with pytest.raises(SystemExit, match="only supports the configured local SQLite database"):
        provision_owner.main()
    assert not missing_database.exists()

    monkeypatch.setattr(Config, "DATA_BACKEND", "local")
    with pytest.raises(SystemExit, match="database does not exist"):
        provision_owner.main()
    assert not missing_database.exists()

    store = LocalStore(tmp_path / "has-owner.db")
    store.initialize()
    store.create_first_owner("owner@example.com", "secure-owner-password")
    monkeypatch.setattr(Config, "DATABASE_PATH", str(store.path))
    with pytest.raises(SystemExit, match="owner account already exists"):
        provision_owner.main()
    assert store.count_users() == 1


def test_last_admin_cannot_be_demoted_and_regular_users_cannot_manage_accounts(client, store):
    owner = _sign_in_admin(client, store)
    member = store.create_user("member@example.com", "correcthorse", "Member")
    client.post("/auth/logout")
    client.post("/auth/login", data={"email": "member@example.com", "password": "correcthorse"})

    assert client.get("/admin/users").status_code == 302
    denied = client.post(f"/admin/users/{owner['id']}/role", data={"role": "user"})
    assert denied.status_code == 302
    denied_status = client.post(
        f"/admin/users/{owner['id']}/status", data={"status": "suspended"}
    )
    assert denied_status.status_code == 302
    assert store.get_user(owner["id"])["role"] == "admin"

    client.post("/auth/logout")
    client.post(
        "/admin/studio",
        data={"owner_secret": "test-owner-access-secret-at-least-32"},
    )
    last_admin = client.post(f"/admin/users/{owner['id']}/role", data={"role": "user"})
    assert last_admin.status_code == 302
    assert store.get_user(owner["id"])["role"] == "admin"
    assert store.get_user(member["id"])["role"] == "user"


def test_dashboard_reports_total_reviews(client, store):
    _sign_in_admin(client, store)
    reviewer = store.create_user("reviewer@example.com", "correcthorse", "Reviewer")
    course = store.list_courses(public_only=True)["items"][0]
    store.add_review(
        course["id"],
        reviewer["id"],
        5,
        "A useful course with clear explanations and challenging exercises.",
    )

    response = client.get("/admin/")

    assert response.status_code == 200
    assert b"Total reviews" in response.data
    assert str(store.dashboard_counts()["total_reviews"]).encode() in response.data


def test_admin_role_changes_require_csrf(app, client, store):
    _sign_in_admin(client, store)
    member = store.create_user("member@example.com", "correcthorse", "Member")
    app.config["WTF_CSRF_ENABLED"] = True

    response = client.post(f"/admin/users/{member['id']}/role", data={"role": "admin"})

    assert response.status_code == 400
    assert store.get_user(member["id"])["role"] == "user"
    status_response = client.post(
        f"/admin/users/{member['id']}/status", data={"status": "suspended"}
    )
    assert status_response.status_code == 400
    assert store.get_user(member["id"])["is_active"] == 1


def test_admin_can_suspend_and_reactivate_accounts_and_suspended_users_cannot_use_sessions(
    app, client, store
):
    _sign_in_admin(client, store)
    member = store.create_user("member@example.com", "correcthorse", "Member")
    with app.test_client() as member_client:
        member_client.post(
            "/auth/login",
            data={"email": "member@example.com", "password": "correcthorse"},
        )
        assert member_client.get("/account/").status_code == 200

        suspended = client.post(
            f"/admin/users/{member['id']}/status", data={"status": "suspended"}
        )
        assert suspended.status_code == 302
        assert store.get_user(member["id"])["is_active"] == 0
        assert any(
            action["action"] == "account_status_changed"
            and action["entity_id"] == member["id"]
            and action["to_status"] == "suspended"
            for action in store.list_actions(limit=20)
        )
        assert member_client.get("/account/").status_code == 302
        failed_login = member_client.post(
            "/auth/login",
            data={"email": "member@example.com", "password": "correcthorse"},
        )
        assert failed_login.status_code == 200

        reactivated = client.post(
            f"/admin/users/{member['id']}/status", data={"status": "active"}
        )
        assert reactivated.status_code == 302
        assert member_client.post(
            "/auth/login",
            data={"email": "member@example.com", "password": "correcthorse"},
        ).status_code == 302
        assert member_client.get("/account/").status_code == 200


def test_webp_thumbnails_are_accepted_by_the_upload_allowlist():
    assert allowed_image("course.webp")
    assert looks_like_image(BytesIO(b"RIFF\x04\x00\x00\x00WEBP"))


def test_learning_outcomes_column_is_added_without_losing_existing_courses(tmp_path):
    database_path = tmp_path / "existing-coursefinder.db"
    store = LocalStore(database_path)
    store.initialize()
    existing_user = store.create_user("existing@example.com", "correcthorse", "Existing User")
    existing = store.save_course(
        {
            "title": "Existing Course",
            "official_url": "https://courses.example.com/existing",
            "short_description": "Existing record.",
            "description": "Preserve this existing course.",
        }
    )

    with sqlite3.connect(database_path) as connection:
        connection.execute("ALTER TABLE courses DROP COLUMN learning_outcomes")
        connection.execute("ALTER TABLE users DROP COLUMN is_active")
        connection.execute("PRAGMA user_version = 5")

    store.initialize()

    preserved = store.get_course(course_id=existing["id"], include_unpublished=True)
    with sqlite3.connect(database_path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert preserved["title"] == "Existing Course"
    assert preserved["learning_outcomes"] == ""
    assert store.get_user(existing_user["id"])["is_active"] == 1
    assert version == 6
