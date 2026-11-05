from flask import Blueprint, current_app, jsonify, request

from app.utils import parse_page

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


def store():
    return current_app.extensions["store"]


def public_course(course):
    if not course:
        return None
    editorial = None
    if course.get("editorial_approved"):
        editorial = {
            "assessment": course.get("editorial_assessment"),
            "score": course.get("editorial_score") if course.get("editorial_score_public") else None,
            "why_recommend": course.get("editorial_why_recommend"),
            "who_should_take": course.get("editorial_who_should_take"),
            "who_should_avoid": course.get("editorial_who_should_avoid"),
            "value_for_money": course.get("editorial_value_for_money"),
            "label": "CourseFinder editorial assessment",
        }
    return {
        "id": course["id"],
        "slug": course["slug"],
        "title": course["title"],
        "provider": course.get("provider_name"),
        "provider_url": course.get("provider_url"),
        "official_url": course.get("official_url"),
        "category": course.get("category_name"),
        "category_slug": course.get("category_slug"),
        "subcategory": course.get("subcategory"),
        "language": course.get("language"),
        "is_featured": bool(course.get("is_featured")),
        "is_underrated": bool(course.get("is_underrated")),
        "short_description": course.get("short_description"),
        "description": course.get("description"),
        "is_free": course.get("is_free"),
        "price": course.get("price"),
        "currency": course.get("currency"),
        "pricing_notes": course.get("pricing_notes"),
        "difficulty": course.get("difficulty"),
        "prerequisites": course.get("prerequisites"),
        "duration": course.get("duration"),
        "duration_verified": bool(course.get("duration_verified")),
        "certificate_available": course.get("certificate_available"),
        "certificate_verified": bool(course.get("certificate_verified")),
        "teaching_format": course.get("teaching_format"),
        "skills_taught": course.get("skills_taught"),
        "intended_audience": course.get("intended_audience"),
        "strengths": course.get("strengths") if course.get("editorial_approved") else None,
        "limitations": course.get("limitations") if course.get("editorial_approved") else None,
        "recommendation_status": course.get("recommendation_status"),
        "last_verified_at": course.get("last_verified_at"),
        "editorial": editorial,
        "user_rating": course.get("user_rating"),
        "review_count": course.get("review_count"),
        "thumbnail_path": course.get("thumbnail_path"),
        "coursefinder_hosts_course": False,
    }


@api_bp.get("/health")
def health():
    try:
        store().ping()
    except Exception:
        current_app.logger.warning("Health check: data store unreachable", exc_info=True)
        return jsonify({"ok": False, "service": "coursefinder"}), 503
    return jsonify({"ok": True, "service": "coursefinder"})


@api_bp.get("/categories")
def categories():
    items = [
        {
            "slug": c["slug"],
            "name": c["name"],
            "description": c.get("description"),
            "published_count": c.get("published_count", 0),
        }
        for c in store().list_categories(active_only=True)
    ]
    return jsonify({"items": items})


@api_bp.get("/courses")
def courses():
    result = store().list_courses(
        q=(request.args.get("q") or "").strip()[:100] or None,
        category_slug=(request.args.get("category") or "").strip()[:80] or None,
        provider_id=(request.args.get("provider") or "").strip()[:64] or None,
        pricing=request.args.get("pricing") if request.args.get("pricing") in ("free", "paid") else None,
        difficulty=request.args.get("difficulty") if request.args.get("difficulty") in ("beginner", "intermediate", "advanced") else None,
        language=(request.args.get("language") or "").strip()[:40] or None,
        min_rating=request.args.get("min_rating") if request.args.get("min_rating") in ("3", "4") else None,
        featured_only=request.args.get("featured") in ("1", "true", "on"),
        underrated_only=request.args.get("underrated") in ("1", "true", "on"),
        sort=request.args.get("sort") or "newest",
        page=parse_page(request.args.get("page")),
    )
    return jsonify(
        {
            "items": [public_course(item) for item in result["items"]],
            "total": result["total"],
            "page": result["page"],
            "pages": result["pages"],
        }
    )


@api_bp.get("/courses/<slug>")
def course_detail(slug):
    course = store().get_course(slug=slug, include_unpublished=False)
    if not course:
        return jsonify({"error": "not_found"}), 404
    reviews = [
        {
            "rating": r["rating"],
            "body": r.get("body"),
            "display_name": r.get("display_name"),
            "created_at": r.get("created_at"),
        }
        for r in store().list_reviews(course_id=course["id"], status="approved")
    ]
    payload = public_course(course)
    payload["reviews"] = reviews
    return jsonify(payload)
