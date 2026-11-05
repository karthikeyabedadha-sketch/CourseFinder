import pytest

from app import create_app, workflow


@pytest.fixture()
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "DATA_BACKEND": "local",
            "DATABASE_PATH": str(tmp_path / "test.db"),
            "SEED_SAMPLE_COURSES": True,
            "SECRET_KEY": "test-secret-key-for-automated-tests-only",
            "OWNER_ACCESS_SECRET": "test-owner-access-secret-at-least-32",
            "OWNER_SESSION_LIFETIME_SECONDS": 3600,
            "ADMIN_SETUP_TOKEN": "test-setup-token-at-least-16-chars",
            "RATELIMIT_ENABLED": False,
        }
    )
    # Sample courses always load as drafts. Publish CS50 via the multi-step editorial
    # workflow so public catalogue tests have a live CS50 record while the remaining
    # sample records stay as drafts.
    s = application.extensions["store"]
    drafts = s.list_courses(q="CS50", include_unpublished=True, status="draft")["items"]
    if drafts:
        first_id = drafts[0]["id"]
        s.set_course_status(first_id, workflow.PENDING_REVIEW)
        s.set_course_status(first_id, workflow.APPROVED)
        s.set_course_status(first_id, workflow.PUBLISHED)
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def store(app):
    return app.extensions["store"]
