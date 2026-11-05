from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from app import limiter
from app.forms import PasswordChangeForm, ProfileForm, SubmissionForm
from app.security import login_required, same_site_referrer
from app.store.errors import NotFoundError

account_bp = Blueprint("account", __name__, url_prefix="/account")


def store():
    return current_app.extensions["store"]


@account_bp.route("/")
@login_required
def library():
    s = store()
    return render_template(
        "account/library.html",
        saved=s.list_saved(g.user["id"]),
        reviews=s.list_reviews(user_id=g.user["id"]),
        submissions=[x for x in s.list_submissions(status=None) if x["submitter_id"] == g.user["id"]],
    )


@account_bp.route("/profile", methods=["GET", "POST"])
@login_required
@limiter.limit("15 per hour", methods=["POST"])
def profile():
    action = request.form.get("action", "profile")
    form = ProfileForm(data={"display_name": g.user.get("display_name")})
    password_form = PasswordChangeForm()

    if request.method == "POST":
        if action == "password":
            password_form = PasswordChangeForm()
            if password_form.validate_on_submit():
                ok = store().change_password(
                    g.user["id"],
                    password_form.current_password.data,
                    password_form.new_password.data,
                )
                if ok:
                    flash("Password updated.", "success")
                    return redirect(url_for("account.profile"))
                password_form.current_password.errors.append("Current password is incorrect.")
        else:
            form = ProfileForm()
            if form.validate_on_submit():
                store().update_display_name(g.user["id"], form.display_name.data)
                flash("Profile updated.", "success")
                return redirect(url_for("account.profile"))

    return render_template("account/profile.html", form=form, password_form=password_form)


@account_bp.route("/submit", methods=["GET", "POST"])
@login_required
@limiter.limit("10 per day", methods=["POST"])
def submit_course():
    form = SubmissionForm()
    if form.validate_on_submit():
        s = store()
        if s.count_recent_submissions(g.user["id"]) >= 5:
            flash("You have reached today's limit for suggestions. Please try again tomorrow.", "error")
        else:
            s.add_submission(
                g.user["id"],
                form.title.data,
                form.official_url.data,
                form.provider_name.data,
                form.note.data,
            )
            flash(
                "Thank you. Your suggestion will be checked by an editor. It is not published automatically.",
                "success",
            )
            return redirect(url_for("account.library"))
    return render_template("account/submit.html", form=form)


@account_bp.route("/save/<course_id>", methods=["POST"])
@login_required
def save(course_id):
    try:
        store().save_course_for_user(g.user["id"], course_id)
    except NotFoundError:
        flash("That course is not available.", "error")
        return redirect(url_for("public.courses"))
    course = store().get_course(course_id=course_id)
    flash("Course saved to your library.", "success")
    fallback = url_for("public.course_detail", slug=course["slug"]) if course else url_for("account.library")
    return redirect(same_site_referrer(fallback))


@account_bp.route("/unsave/<course_id>", methods=["POST"])
@login_required
def unsave(course_id):
    s = store()
    course = s.get_course(course_id=course_id)
    s.unsave_course_for_user(g.user["id"], course_id)
    flash("Removed from your library.", "success")
    fallback = url_for("public.course_detail", slug=course["slug"]) if course else url_for("account.library")
    return redirect(same_site_referrer(fallback))
