from functools import wraps
from urllib.parse import urlparse

from flask import abort, g, jsonify, redirect, request, session, url_for

from app.utils import CONTROL_RE


def safe_next_url(target, fallback):
    """Return ``target`` only if it is a plain path on this site.

    Blocks open redirects such as ``//evil.com``, ``/\\evil.com``,
    ``https://evil.com`` and ``javascript:`` values.
    """
    if not target or not isinstance(target, str):
        return fallback
    if CONTROL_RE.search(target) or "\\" in target:
        return fallback
    if not target.startswith("/") or target.startswith("//"):
        return fallback
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return fallback
    return target


def same_site_referrer(fallback):
    """Redirect target taken from the Referer header, but only if it is this site."""
    ref = request.referrer
    if not ref:
        return fallback
    parsed = urlparse(ref)
    if parsed.netloc != request.host:
        return fallback
    return safe_next_url(parsed.path + (f"?{parsed.query}" if parsed.query else ""), fallback)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, "user", None):
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped


def owner_session_required(view):
    """Require a valid server-side Owner Studio session on administrative routes."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if getattr(g, "owner_authenticated", False):
            return view(*args, **kwargs)
        return redirect(url_for("admin.studio"))

    return wrapped


def admin_required(view):
    """Use on any privileged view. Accepts a validated Owner Studio session or an
    authenticated database administrator (see ``load_current_user``)."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if getattr(g, "owner_authenticated", False):
            return view(*args, **kwargs)
        user = getattr(g, "user", None)
        if not user:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        if user.get("role") != "admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def is_admin_user(user):
    return bool(user) and user.get("role") == "admin"


def clear_session():
    session.clear()
