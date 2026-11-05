import hashlib
import logging
import secrets
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import MIN_PRODUCTION_SECRET_LENGTH, WEAK_SECRET_KEYS, Config, resolve_owner_access_secret
from app.store.factory import create_store

csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=["200 per hour"])

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data:",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "object-src 'none'",
    ]
)
PRIVATE_PREFIXES = ("/admin", "/account", "/auth")


def resolve_secret_key(app):
    """Never run with a guessable signing key.

    Production refuses to start without a strong key. Development falls back to a
    random per-process key (sessions reset on restart) instead of a public default.
    """
    key = app.config.get("SECRET_KEY") or ""
    weak = key in WEAK_SECRET_KEYS
    if app.config.get("APP_ENV") == "production":
        if weak or len(key) < MIN_PRODUCTION_SECRET_LENGTH:
            raise RuntimeError(
                "SECRET_KEY is missing, too short, or a placeholder. Set a random value of at "
                f"least {MIN_PRODUCTION_SECRET_LENGTH} characters in the environment."
            )
        return key
    if weak and not app.config.get("TESTING"):
        app.logger.warning("SECRET_KEY not set; using a temporary random key. Sessions end on restart.")
        return secrets.token_hex(32)
    return key or secrets.token_hex(32)


def create_app(test_config=None):
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(Config)
    # Refresh OWNER_ACCESS_SECRET from current environment if not explicitly overridden
    app.config["OWNER_ACCESS_SECRET"] = resolve_owner_access_secret()
    if test_config:
        app.config.update(test_config)
        if "OWNER_ACCESS_SECRET" in test_config:
            app.config["OWNER_ACCESS_SECRET"] = str(test_config.get("OWNER_ACCESS_SECRET") or "").strip()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app.config["SECRET_KEY"] = resolve_secret_key(app)

    if app.config.get("TRUSTED_PROXY_COUNT", 0) > 0:
        n = app.config["TRUSTED_PROXY_COUNT"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    upload_dir = Path(app.root_path) / "static" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    app.config.setdefault("UPLOAD_FOLDER", str(upload_dir))

    csrf.init_app(app)
    limiter.init_app(app)

    store = create_store(app)
    store.initialize()
    app.extensions["store"] = store

    from app.blueprints.account import account_bp
    from app.blueprints.admin import admin_bp
    from app.blueprints.api import api_bp
    from app.blueprints.auth import auth_bp
    from app.blueprints.public import public_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)

    csrf.exempt(api_bp)  # the API is read-only (GET)

    @app.before_request
    def load_current_user():
        """Look the user up on every request. The role always comes from the
        database, never from the cookie, so demoting an admin takes effect at once."""
        g.user = None
        g.owner_authenticated = False
        if request.endpoint == "static":
            return

        owner_token = session.get("owner_session_token")
        if owner_token and isinstance(owner_token, str):
            try:
                token_hash = hashlib.sha256(owner_token.encode("utf-8")).hexdigest()
                g.owner_authenticated = store.validate_owner_session(token_hash)
                if not g.owner_authenticated:
                    session.pop("owner_session_token", None)
            except Exception:
                app.logger.warning("Could not validate Owner Studio session", exc_info=True)
                session.pop("owner_session_token", None)

        user_id = session.get("user_id")
        if not user_id:
            return
        try:
            user = store.get_user(user_id)
        except Exception:
            app.logger.warning("Could not load user from the data store", exc_info=True)
            return  # fail closed: treat as signed out
        if user and user.get("is_active", True):
            g.user = user
        else:
            # Remove invalid/suspended user_id without destroying an active Owner Studio or CSRF token
            session.pop("user_id", None)

    @app.context_processor
    def inject_globals():
        try:
            categories = store.list_categories(active_only=True)
        except Exception:
            app.logger.warning("Could not load categories", exc_info=True)
            categories = []
        return {
            "current_user": getattr(g, "user", None),
            "owner_authenticated": getattr(g, "owner_authenticated", False),
            "nav_categories": categories,
            "support_email": app.config.get("SUPPORT_EMAIL"),
        }

    def error_page(code, title, message):
        if request.path.startswith("/api/") or request.path.startswith("/admin/api/"):
            return jsonify({"error": title.lower().replace(" ", "_"), "message": message}), code
        if request.endpoint == "admin.studio":
            return render_template("admin/owner_access.html", error=message), code
        return render_template("errors/error.html", code=code, title=title, message=message), code

    @app.errorhandler(400)
    def bad_request(_e):
        return error_page(400, "Bad request", "That request could not be understood.")

    @app.errorhandler(CSRFError)
    def csrf_failed(_e):
        return error_page(400, "Session expired", "Your form expired. Go back, reload the page and try again.")

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_e):
        if request.path.startswith("/api/") or request.path.startswith("/admin/api/"):
            return jsonify({"error": "not_found"}), 404
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def too_large(_e):
        return error_page(413, "File too large", "That upload is larger than the 2 MB limit.")

    @app.errorhandler(429)
    def rate_limited(_e):
        return error_page(429, "Too many requests", "Please wait a little while and try again.")

    @app.errorhandler(500)
    def server_error(_e):
        return error_page(500, "Something went wrong", "An error occurred on our side. It has been logged.")

    @app.after_request
    def security_headers(response):
        h = response.headers
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "strict-origin-when-cross-origin"
        h["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        h["Content-Security-Policy"] = CSP
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        if app.config.get("APP_ENV") == "production":
            h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.path.startswith(PRIVATE_PREFIXES):
            h["Cache-Control"] = "no-store"
        return response

    return app
