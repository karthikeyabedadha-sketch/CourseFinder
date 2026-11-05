import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

# Values that must never be used as a real signing key.
WEAK_SECRET_KEYS = {
    "",
    "dev-only-change-me",
    "replace-with-a-long-random-string",
    "replace-with-a-long-random-string-at-least-32-chars",
    "replace-with-a-different-long-random-string-at-least-32-chars",
    "changeme",
    "secret",
    "test-secret",
}
MIN_PRODUCTION_SECRET_LENGTH = 32
MIN_OWNER_SECRET_LENGTH = 16


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name, default=0):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def resolve_owner_access_secret(config=None):
    """Read and normalize OWNER_ACCESS_SECRET from config or environment."""
    raw = None
    if config is not None and "OWNER_ACCESS_SECRET" in config:
        raw = config.get("OWNER_ACCESS_SECRET")
    if raw is None:
        raw = os.getenv("OWNER_ACCESS_SECRET", "")
    return str(raw or "").strip()


APP_ENV = (os.getenv("FLASK_ENV") or "development").strip().lower()


class Config:
    APP_ENV = APP_ENV
    SECRET_KEY = (os.getenv("SECRET_KEY") or "").strip()
    ADMIN_SETUP_TOKEN = (os.getenv("ADMIN_SETUP_TOKEN") or "").strip()
    OWNER_ACCESS_SECRET = (os.getenv("OWNER_ACCESS_SECRET") or "").strip()
    OWNER_SESSION_LIFETIME_SECONDS = 8 * 60 * 60

    DATA_BACKEND = os.getenv("DATA_BACKEND", "local").strip().lower()
    DATABASE_PATH = os.getenv("DATABASE_PATH", "instance/coursefinder.db")
    SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
    SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    # Sample courses are OFF by default. When enabled they are loaded as unverified
    # DRAFTS for the administrator to review. They are never published automatically.
    SEED_SAMPLE_COURSES = _env_bool("SEED_SAMPLE_COURSES", False)

    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:5000")
    SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "")

    # Number of reverse proxies in front of the app (Render, Nginx, ...). Needed so
    # rate limiting sees the visitor's real IP address. Leave 0 when not behind a proxy.
    TRUSTED_PROXY_COUNT = _env_int("TRUSTED_PROXY_COUNT", 0)

    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 60 * 60 * 8
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = APP_ENV == "production"
    PERMANENT_SESSION_LIFETIME = timedelta(days=14)
    PREFERRED_URL_SCHEME = "https" if APP_ENV == "production" else "http"

    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    MIN_PUBLIC_RATING_COUNT = 3
    PER_PAGE = 12

    # Use a shared store such as redis://... when running more than one worker.
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True
