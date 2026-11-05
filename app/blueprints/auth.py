from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from app import limiter
from app.forms import LoginForm, RegisterForm
from app.security import clear_session, safe_next_url

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def store():
    return current_app.extensions["store"]


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("8 per minute")
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        if store().get_user_by_email(form.email.data):
            form.email.errors.append("An account with this email already exists.")
        else:
            user = store().create_user(form.email.data, form.password.data, form.display_name.data)
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            flash("Welcome to CourseFinder.", "success")
            return redirect(url_for("account.library"))
    return render_template("auth/register.html", form=form)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = store().authenticate(form.email.data, form.password.data)
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


@auth_bp.route("/logout", methods=["POST"])
def logout():
    clear_session()
    flash("Signed out.", "success")
    return redirect(url_for("public.home"))
