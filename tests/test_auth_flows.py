from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import workflow
from app.store.errors import EmailNotConfirmedError
from app.store import supabase_store
from app.store.supabase_store import SupabaseStore


def test_unconfirmed_registration_does_not_create_flask_login_session(client, app, store, monkeypatch):
    def unconfirmed(_email, _password):
        raise EmailNotConfirmedError()

    monkeypatch.setattr(store, "authenticate", unconfirmed)
    response = client.post(
        "/auth/register",
        data={"display_name": "New Member", "email": "pending@example.com", "password": "correcthorse"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"confirm your address" in response.data
    with client.session_transaction() as browser_session:
        assert "user_id" not in browser_session


def test_login_shows_confirmation_guidance(client, store, monkeypatch):
    store.create_user("pending@example.com", "correcthorse", "Pending")

    def unconfirmed(_email, _password):
        raise EmailNotConfirmedError()

    monkeypatch.setattr(store, "authenticate", unconfirmed)
    response = client.post(
        "/auth/login",
        data={"email": "pending@example.com", "password": "correcthorse"},
    )

    assert response.status_code == 200
    assert b"Confirm your email address before signing in" in response.data


def test_confirmation_and_password_reset_pages_are_available(client):
    assert client.get("/auth/resend-confirmation").status_code == 200
    assert client.get("/auth/forgot-password").status_code == 200
    reset = client.get("/auth/reset-password")
    assert reset.status_code == 200
    assert b"password-reset-form" in reset.data


def test_password_reset_uses_recovery_token_and_clears_session(client, store, monkeypatch):
    store.backend = "supabase"
    calls = []
    monkeypatch.setattr(
        store,
        "update_password_from_recovery",
        lambda token, password: calls.append((token, password)) or True,
        raising=False,
    )
    response = client.post(
        "/auth/reset-password",
        data={
            "access_token": "short-lived-recovery-token",
            "password": "new-secret-password",
            "confirm_password": "new-secret-password",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Password updated" in response.data
    assert calls == [("short-lived-recovery-token", "new-secret-password")]


def test_supabase_email_not_confirmed_is_a_distinct_safe_auth_error():
    class AuthFailure(Exception):
        code = "email_not_confirmed"
        status = 400

    store = object.__new__(SupabaseStore)
    store.auth_client = SimpleNamespace(
        auth=SimpleNamespace(sign_in_with_password=lambda _credentials: (_ for _ in ()).throw(AuthFailure()))
    )

    with pytest.raises(EmailNotConfirmedError):
        store.authenticate("member@example.com", "password-not-logged")


def test_supabase_confirmation_and_recovery_requests_use_the_configured_redirect():
    auth = SimpleNamespace(resend=Mock(), reset_password_for_email=Mock())
    store = object.__new__(SupabaseStore)
    store.auth_client = SimpleNamespace(auth=auth)
    store.public_base_url = "https://coursefinder.example"

    store.resend_confirmation(" MEMBER@example.com ", "https://coursefinder.example/auth/login")
    store.request_password_reset(" MEMBER@example.com ", "https://coursefinder.example/auth/reset-password")

    auth.resend.assert_called_once_with(
        {
            "type": "signup",
            "email": "member@example.com",
            "options": {"email_redirect_to": "https://coursefinder.example/auth/login"},
        }
    )
    auth.reset_password_for_email.assert_called_once_with(
        "member@example.com", options={"redirect_to": "https://coursefinder.example/auth/reset-password"}
    )


def test_recovery_password_update_uses_short_lived_token_as_bearer(monkeypatch):
    store = object.__new__(SupabaseStore)
    store.url = "https://project.supabase.co"
    store.anon_key = "public-anon-test-key"
    response = SimpleNamespace(is_success=True, status_code=200)
    request = Mock(return_value=response)
    monkeypatch.setattr(supabase_store.httpx, "put", request)

    assert store.update_password_from_recovery("short-lived-token", "replacement-password")
    args, kwargs = request.call_args
    assert args == ("https://project.supabase.co/auth/v1/user",)
    assert kwargs["headers"]["Authorization"] == "Bearer short-lived-token"
    assert kwargs["headers"]["apikey"] == "public-anon-test-key"
    assert kwargs["json"] == {"password": "replacement-password"}


def test_migration_transition_path_keeps_original_publication_state():
    from scripts.migrate_sqlite_to_supabase import transition_path

    assert transition_path(workflow.DRAFT, workflow.PUBLISHED) == [
        workflow.PENDING_REVIEW,
        workflow.APPROVED,
        workflow.PUBLISHED,
    ]
    assert transition_path(workflow.DRAFT, workflow.APPROVED) == [
        workflow.PENDING_REVIEW,
        workflow.APPROVED,
    ]
    assert transition_path(workflow.DRAFT, workflow.DRAFT) == []
