from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from app import limiter
from app.forms import ContactForm, ReportForm, ReviewForm
from app.security import login_required, same_site_referrer
from app.store.errors import NotFoundError
from app.utils import parse_page, split_lines

public_bp = Blueprint("public", __name__)

SORTS = ("newest", "rating", "price_low", "price_high", "title", "editorial")
DIFFICULTY_CHOICES = ("beginner", "intermediate", "advanced")


def store():
    return current_app.extensions["store"]


def _filters():
    args = request.args
    sort = args.get("sort", "newest")
    return {
        "q": args.get("q", "").strip()[:100],
        "category_slug": args.get("category", "").strip()[:80],
        "provider_id": args.get("provider", "").strip()[:64],
        "pricing": args.get("pricing") if args.get("pricing") in ("free", "paid") else "",
        "difficulty": args.get("difficulty", "").strip()[:30] if args.get("difficulty") in DIFFICULTY_CHOICES else "",
        "language": args.get("language", "").strip()[:40],
        "min_rating": args.get("min_rating") if args.get("min_rating") in ("3", "4") else "",
        "featured": "1" if args.get("featured") in ("1", "true", "on") else "",
        "underrated": "1" if args.get("underrated") in ("1", "true", "on") else "",
        "sort": sort if sort in SORTS else "newest",
    }


@public_bp.route("/")
def home():
    s = store()
    featured = s.list_courses(featured_only=True, per_page=6)["items"]
    recent = s.list_courses(sort="newest", per_page=6)["items"]
    underrated = s.list_courses(underrated_only=True, per_page=6)["items"]
    free = s.list_courses(pricing="free", per_page=6)["items"]
    return render_template(
        "home.html",
        featured=featured,
        recent=recent,
        underrated=underrated,
        free_courses=free,
        categories=s.list_categories(active_only=True),
    )


@public_bp.route("/courses")
def courses():
    filters = _filters()
    s = store()
    result = s.list_courses(
        q=filters["q"] or None,
        category_slug=filters["category_slug"] or None,
        provider_id=filters["provider_id"] or None,
        pricing=filters["pricing"] or None,
        difficulty=filters["difficulty"] or None,
        language=filters["language"] or None,
        min_rating=filters["min_rating"] or None,
        featured_only=bool(filters["featured"]),
        underrated_only=bool(filters["underrated"]),
        sort=filters["sort"],
        page=parse_page(request.args.get("page")),
        per_page=current_app.config.get("PER_PAGE", 12),
    )
    return render_template(
        "courses/index.html",
        result=result,
        filters=filters,
        categories=s.list_categories(active_only=True),
        providers=s.list_providers(with_published_courses=True),
        languages=s.public_languages(),
        ratings_available=s.has_public_ratings(),
    )


@public_bp.route("/courses/<slug>", methods=["GET"])
def course_detail(slug):
    s = store()
    course = s.get_course(slug=slug)  # published only
    if not course:
        return render_template("errors/404.html"), 404
    saved = my_review = None
    if g.user:
        saved = s.is_saved(g.user["id"], course["id"])
        my_review = s.get_user_review(course["id"], g.user["id"])
    # Pre-fill the review form only when the user explicitly clicks Edit (?edit_review=1),
    # so pending review text is never echoed onto the public course page before moderation.
    prefill = my_review and request.args.get("edit_review") == "1"
    form = ReviewForm(
        data={
            "rating": str(my_review["rating"]) if prefill else "",
            "body": my_review["body"] if prefill else "",
        }
    )
    return render_template(
        "courses/detail.html",
        course=course,
        skills_list=split_lines(course.get("skills_taught")),
        form=form,
        saved=bool(saved),
        my_review=my_review,
        reviews=s.list_reviews(course_id=course["id"], status="approved"),
        similar=s.similar_courses(course),
        report_form=ReportForm("course"),
        review_report_form=ReportForm("review"),
        preview=False,
    )


@public_bp.route("/courses/<slug>", methods=["POST"])
@public_bp.route("/courses/<slug>/review", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def submit_review(slug):
    s = store()
    course = s.get_course(slug=slug)
    if not course:
        abort(404)
    form = ReviewForm()
    if form.validate_on_submit():
        try:
            _id, created = s.add_review(course["id"], g.user["id"], form.rating.data, form.body.data)
        except NotFoundError:
            abort(404)
        flash(
            "Your review was submitted and is waiting for moderation."
            if created
            else "Your review was updated and is waiting for moderation again.",
            "success",
        )
        return redirect(url_for("public.course_detail", slug=slug) + "#reviews")
    for field, errors in form.errors.items():
        for error in errors:
            flash(f"{form[field].label.text}: {error}", "error")
    return redirect(url_for("public.course_detail", slug=slug) + "#write-review")


@public_bp.route("/courses/<slug>/review/delete", methods=["POST"])
@login_required
def delete_my_review(slug):
    s = store()
    course = s.get_course(slug=slug, include_unpublished=True)
    review = s.get_user_review(course["id"], g.user["id"]) if course else None
    if review:
        s.delete_review(review["id"], user_id=g.user["id"])
        flash("Your review was deleted.", "success")
    return redirect(same_site_referrer(url_for("account.library")))


@public_bp.route("/courses/<slug>/report", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def report_course(slug):
    s = store()
    course = s.get_course(slug=slug)
    if not course:
        abort(404)
    form = ReportForm("course")
    if form.validate_on_submit():
        created = s.add_report("course", course["id"], g.user["id"], form.reason.data, form.details.data)
        flash(
            "Thank you. We will check this." if created else "You already have an open report on this course.",
            "success",
        )
    else:
        flash("Please choose a reason for the report.", "error")
    return redirect(url_for("public.course_detail", slug=slug))


@public_bp.route("/reviews/<review_id>/report", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def report_review(review_id):
    s = store()
    review = s.get_review(review_id)
    course = s.get_course(course_id=review["course_id"]) if review else None
    if not review or not course or review["status"] != "approved":
        abort(404)
    form = ReportForm("review")
    if form.validate_on_submit():
        created = s.add_report("review", review_id, g.user["id"], form.reason.data, form.details.data)
        flash("Thank you. We will check this review." if created else "You already reported this review.", "success")
    else:
        flash("Please choose a reason for the report.", "error")
    return redirect(url_for("public.course_detail", slug=course["slug"]) + "#reviews")


@public_bp.route("/categories")
def categories():
    return render_template("categories.html", categories=store().list_categories(active_only=True))


@public_bp.route("/categories/<slug>")
def category_detail(slug):
    s = store()
    category = s.get_category_by_slug(slug)
    if not category or not category.get("is_active"):
        return render_template("errors/404.html"), 404
    result = s.list_courses(
        category_slug=slug,
        page=parse_page(request.args.get("page")),
        per_page=current_app.config.get("PER_PAGE", 12),
    )
    return render_template("category_detail.html", category=category, result=result)


@public_bp.route("/how-it-works")
def how_it_works():
    return render_template("pages/how_it_works.html")


@public_bp.route("/about")
def about():
    return render_template("pages/about.html")


@public_bp.route("/contact", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def contact():
    form = ContactForm()
    if form.validate_on_submit():
        store().add_contact_message(form.name.data, form.email.data, form.message.data)
        flash("Message received. Thank you.", "success")
        return redirect(url_for("public.contact"))
    return render_template("pages/contact.html", form=form)


@public_bp.route("/privacy")
def privacy():
    return render_template("pages/privacy.html")


@public_bp.route("/terms")
def terms():
    return render_template("pages/terms.html")


@public_bp.route("/community-guidelines")
def guidelines():
    return render_template("pages/guidelines.html")


@public_bp.route("/disclaimer")
def disclaimer():
    return render_template("pages/disclaimer.html")
