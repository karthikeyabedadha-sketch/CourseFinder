import logging

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from app import limiter
from app.forms import EmailOnlyForm, LoginForm, RegisterForm, ResetPasswordForm
from app.security import clear_session, safe_next_url
from app.store.errors import DuplicateError, EmailNotConfirmedError, StoreError

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
logger = logging.getLogger(__name__)


def store():
    return current_app.extensions["store"]


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("8 per minute")
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        try:
            if store().get_user_by_email(form.email.data):
                raise DuplicateError("An account with this email already exists.")
            user = store().create_user(form.email.data, form.password.data, form.display_name.data)
            try:
                signed_in_user = store().authenticate(form.email.data, form.password.data)
            except EmailNotConfirmedError:
                signed_in_user = None
            if signed_in_user:
                session.clear()
                session["user_id"] = signed_in_user["id"]
                session.permanent = True
                flash("Welcome to CourseFinder.", "success")
                return redirect(url_for("account.library"))
            flash(
                "Your account was created. If email confirmation is required, confirm your address before signing in. "
                "You can request another confirmation email below.",
                "info",
            )
            return redirect(url_for("auth.login"))
        except DuplicateError as exc:
            form.email.errors.append(str(exc))
        except StoreError as exc:
            form.password.errors.append(str(exc))
        except Exception as exc:
            logger.warning("Registration could not be completed (%s)", type(exc).__name__)
            form.password.errors.append("Account creation could not be completed. Please try again.")
    return render_template("auth/register.html", form=form)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    form = LoginForm()
    if form.validate_on_submit():
        try:
            user = store().authenticate(form.email.data, form.password.data)
        except EmailNotConfirmedError:
            form.password.errors.append("Confirm your email address before signing in. You can request a new confirmation email below.")
            user = None
        except Exception as exc:
            logger.warning("Sign-in could not be completed (%s)", type(exc).__name__)
            form.password.errors.append("Sign-in is temporarily unavailable. Please try again.")
            user = None
        if not user:
            form.password.errors.append("Email or password is incorrect.")
        else:
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            next_url = safe_next_url(request.args.get("next"), url_for("public.home"))
            flash("Signed in.", "success")
            return redirect(next_url)
    return render_template("auth/login.html", form=form)


@auth_bp.route("/resend-confirmation", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def resend_confirmation():
    form = EmailOnlyForm()
    if form.validate_on_submit():
        if getattr(store(), "backend", "local") != "supabase":
            flash("Email confirmation is available when CourseFinder uses Supabase Auth.", "error")
            return render_template("auth/email_action.html", form=form, mode="confirm")
        try:
            redirect_to = current_app.config["PUBLIC_BASE_URL"].rstrip("/") + url_for("auth.login")
            store().resend_confirmation(form.email.data, redirect_to=redirect_to)
        except Exception as exc:
            logger.warning("Confirmation resend request failed (%s)", type(exc).__name__)
        flash("If that address can receive a confirmation message, one has been requested.", "info")
        return redirect(url_for("auth.login"))
    return render_template("auth/email_action.html", form=form, mode="confirm")


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def forgot_password():
    form = EmailOnlyForm()
    if form.validate_on_submit():
        if getattr(store(), "backend", "local") != "supabase":
            flash("Password reset email is available when CourseFinder uses Supabase Auth.", "error")
            return render_template("auth/email_action.html", form=form, mode="reset")
        try:
            redirect_to = current_app.config["PUBLIC_BASE_URL"].rstrip("/") + url_for("auth.reset_password")
            store().request_password_reset(form.email.data, redirect_to)
        except Exception as exc:
            logger.warning("Password reset request failed (%s)", type(exc).__name__)
        flash("If an account can receive a reset link at that address, one has been requested.", "info")
        return redirect(url_for("auth.login"))
    return render_template("auth/email_action.html", form=form, mode="reset")


@auth_bp.route("/reset-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def reset_password():
    form = ResetPasswordForm()
    if form.validate_on_submit():
        if getattr(store(), "backend", "local") != "supabase":
            form.access_token.errors.append("Password recovery is unavailable for this local database.")
        elif store().update_password_from_recovery(form.access_token.data, form.password.data):
            session.clear()
            flash("Password updated. Sign in with your new password.", "success")
            return redirect(url_for("auth.login"))
        else:
            form.access_token.errors.append("That reset link is invalid or expired. Request a new one.")
    return render_template("auth/reset_password.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    clear_session()
    flash("Signed out.", "success")
    return redirect(url_for("public.home"))
