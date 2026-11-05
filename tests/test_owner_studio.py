"""Automated tests for CourseFinder Owner Studio:

1. Studio Dashboard: total courses, published courses, drafts, free/paid courses, and recent activity.
2. Course Manager: add, edit, delete, search, filter (status, category, pricing, difficulty, flag) and organise/sort.
3. Course Editor: titles, providers, URLs, descriptions, thumbnails (upload & remove), categories, pricing, difficulty, duration, skills, and personal recommendations.
4. Publishing Workflow: every new course defaults to draft; only authorized admins can publish or unpublish.
5. AI Content Assistant: generate and improve course descriptions, skills, tags/keywords, and editorial outlines (via both /admin/api/ai-assist and /admin/ai-assistant), enforcing draft-only creation.
"""

import io


def _make_admin(client, store, email="owner@example.com", password="ownerpassword123"):
    store.create_user(email, password, "Studio Owner")
    store.set_user_role(email, "admin")
    client.post(
        "/admin/studio",
        data={"owner_secret": "test-owner-access-secret-at-least-32"},
    )
    return store.get_user_by_email(email)


def test_owner_studio_dashboard_shows_total_published_draft_free_paid_and_activity(client, store):
    _make_admin(client, store)
    cat = store.list_categories()[0]
    prov = store.get_or_create_provider("Paid Academy", "https://paid.example.com")
    store.save_course(
        {
            "title": "Quantitative Portfolio Management",
            "official_url": "https://paid.example.com/quant-portfolio",
            "provider_id": prov["id"],
            "category_id": cat["id"],
            "short_description": "Paid quantitative finance course.",
            "description": "Covers factor models, risk parity, and portfolio optimization.",
            "is_free": False,
            "price": 199.00,
            "currency": "USD",
            "difficulty": "advanced",
        }
    )

    counts = store.dashboard_counts()
    assert counts["total_courses"] >= 6
    assert counts["published_courses"] >= 1
    assert counts["draft_courses"] >= 5
    assert counts["free_courses"] >= 5
    assert counts["paid_courses"] == 1

    for path in ("/admin/",):
        res = client.get(path)
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        assert "CourseFinder Owner Studio" in html
        assert "Free courses" in html
        assert "Paid courses" in html
        assert "Recent activity" in html
        assert "Quantitative Portfolio Management" in html

    studio_redirect = client.get("/admin/studio")
    assert studio_redirect.status_code == 302
    assert studio_redirect.headers["Location"].endswith("/admin/")


def test_course_manager_search_filter_pricing_difficulty_and_sort(client, store):
    _make_admin(client, store)
    cats = {c["slug"]: c["id"] for c in store.list_categories()}
    prov = store.get_or_create_provider("Systems Institute", "https://sys.example.com")

    store.save_course(
        {
            "title": "Advanced Rust Kernel Systems",
            "official_url": "https://sys.example.com/rust-kernel",
            "provider_id": prov["id"],
            "category_id": cats["programming"],
            "short_description": "Paid advanced Rust course.",
            "description": "Low-level kernel programming in Rust.",
            "is_free": False,
            "price": 149.00,
            "currency": "USD",
            "difficulty": "advanced",
        }
    )

    # Filter by pricing=paid in Course Manager
    paid_res = client.get("/admin/courses?pricing=paid")
    assert paid_res.status_code == 200
    paid_html = paid_res.get_data(as_text=True)
    assert "Advanced Rust Kernel Systems" in paid_html
    assert "CS50: Introduction to Computer Science" not in paid_html

    # Filter by difficulty=advanced in Course Manager
    adv_res = client.get("/admin/courses?difficulty=advanced")
    assert adv_res.status_code == 200
    assert "Advanced Rust Kernel Systems" in adv_res.get_data(as_text=True)

    # Sort by title A-Z in Course Manager
    sort_res = client.get("/admin/courses?sort=title")
    assert sort_res.status_code == 200
    sort_html = sort_res.get_data(as_text=True)
    assert sort_html.index("Advanced Rust Kernel Systems") < sort_html.index("Elements of AI")


def test_course_editor_manages_all_fields_thumbnails_and_personal_recommendations(client, store):
    _make_admin(client, store)
    cat = store.list_categories()[0]

    # Tiny valid 1x1 PNG
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    res = client.post(
        "/admin/courses/new",
        data={
            "title": "Modern Product Design Systems",
            "official_url": "https://design.example.com/systems",
            "new_provider_name": "Design Guild",
            "new_provider_url": "https://design.example.com",
            "category_id": cat["id"],
            "subcategory": "UI Systems",
            "short_description": "Practical component architecture and design tokens.",
            "description": "Deep dive into typography scales, accessibility, and multi-brand design systems.",
            "language": "English",
            "is_free": "false",
            "price": "79.00",
            "currency": "USD",
            "pricing_notes": "One-time access fee.",
            "difficulty": "intermediate",
            "prerequisites": "Basic Figma familiarity.",
            "duration": "6 weeks (4 hours/week)",
            "duration_verified": "y",
            "skills_taught": "Design Systems\nTypography\nAccessibility\nFigma Tokens",
            "learning_outcomes": "Design accessible components.\nApply tokens across products.",
            "intended_audience": "Product designers and frontend engineers.",
            "recommendation_status": "recommended",
            "editorial_score": "9.0",
            "editorial_score_public": "y",
            "editorial_approved": "y",
            "editorial_assessment": "Standout practical coverage of design tokens and handoff.",
            "editorial_why_recommend": "Bridges the gap between Figma components and production CSS.",
            "editorial_who_should_take": "Designers building shared component libraries.",
            "editorial_who_should_avoid": "Beginners looking for general graphic illustration.",
            "editorial_value_for_money": "Well worth the modest one-time price.",
            "strengths": "Real-world token architecture.",
            "limitations": "Focuses primarily on web interfaces.",
            "keywords": "design systems, figma, tokens, ui, accessibility",
            "admin_notes": "Personal owner note: verified syllabus on 2026-11-01.",
            "thumbnail": (io.BytesIO(png_bytes), "thumb.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert res.status_code == 200
    created = store.list_courses(q="Modern Product Design Systems", include_unpublished=True)["items"][0]
    assert created["status"] == "draft"
    assert created["thumbnail_path"] is not None and created["thumbnail_path"].startswith("uploads/")
    assert created["recommendation_status"] == "recommended"
    assert created["editorial_score"] == 9.0
    assert created["learning_outcomes"] == "Design accessible components.\nApply tokens across products."

    # Remove thumbnail in Course Editor
    edit_res = client.post(
        f"/admin/courses/{created['id']}/edit",
        data={
            "title": "Modern Product Design Systems",
            "official_url": "https://design.example.com/systems",
            "provider_id": created["provider_id"],
            "category_id": cat["id"],
            "short_description": "Practical component architecture and design tokens.",
            "description": "Deep dive into typography scales, accessibility, and multi-brand design systems.",
            "is_free": "false",
            "price": "79.00",
            "currency": "USD",
            "difficulty": "intermediate",
            "recommendation_status": "recommended",
            "remove_thumbnail": "1",
        },
        follow_redirects=True,
    )
    assert edit_res.status_code == 200
    updated = store.get_course(course_id=created["id"], include_unpublished=True)
    assert updated["thumbnail_path"] is None


def test_ai_content_assistant_api_and_studio_workspace_enforce_draft_rules(client, store):
    # Non-admin user cannot call AI Content Assistant
    client.post(
        "/auth/register",
        data={"display_name": "Regular", "email": "reg@example.com", "password": "password1234"},
        follow_redirects=True,
    )
    assert client.get("/admin/ai-assistant").status_code == 302
    assert client.post("/admin/api/ai-assist", json={"title": "Test"}).status_code == 302
    client.post("/auth/logout")

    # Admin uses JSON endpoint (/admin/api/ai-assist)
    _make_admin(client, store, "ai_owner@example.com")
    cat = store.get_category_by_slug("artificial-intelligence")
    api_res = client.post(
        "/admin/api/ai-assist",
        json={
            "title": "Practical Transformer Architectures with PyTorch",
            "category_id": cat["id"],
            "difficulty": "intermediate",
            "raw_notes": "- Self-attention and multi-head attention\n- Training loops in PyTorch\n- Tokenization and inference",
            "mode": "all",
        },
    )
    assert api_res.status_code == 200
    payload = api_res.get_json()
    assert "Transformer" in payload["short_description"] or "artificial intelligence" in payload["short_description"].lower()
    assert "CourseFinder lists this as an external course" in payload["description"]
    assert "PyTorch" in payload["skills_taught"]
    assert "PyTorch" in payload["learning_outcomes"]
    assert payload["category_suggestion_slug"] == "artificial-intelligence"
    assert "pytorch" in payload["keywords"]
    assert "transformer" in payload["keywords"]

    # Improve mode polishes existing short description
    imp_res = client.post(
        "/admin/api/ai-assist",
        json={
            "title": "SQL Performance Tuning",
            "short_description": "hands-on guide to indexing and query execution plans",
            "raw_notes": "Covers B-tree indexes, EXPLAIN ANALYZE, and join strategies in PostgreSQL",
            "mode": "improve",
        },
    )
    assert imp_res.status_code == 200
    imp_data = imp_res.get_json()
    assert imp_data["short_description"].startswith("Hands-on guide to indexing")
    assert "PostgreSQL" in imp_data["skills_taught"]

    # Standalone /admin/ai-assistant page: generate then save as draft
    gen_page = client.post(
        "/admin/ai-assistant",
        data={
            "action": "generate",
            "title": "Applied Cryptography and Zero-Knowledge Proofs",
            "official_url": "https://crypto.example.edu/zkp",
            "new_provider_name": "Crypto University",
            "category_id": cat["id"],
            "difficulty": "advanced",
            "raw_notes": "- Hash functions and symmetric encryption\n- Elliptic curves\n- Zero-knowledge proofs",
        },
        follow_redirects=True,
    )
    assert gen_page.status_code == 200
    assert "Applied cryptography" in gen_page.get_data(as_text=True)

    save_page = client.post(
        "/admin/ai-assistant",
        data={
            "action": "save_draft",
            "title": "Applied Cryptography and Zero-Knowledge Proofs",
            "official_url": "https://crypto.example.edu/zkp",
            "new_provider_name": "Crypto University",
            "category_id": cat["id"],
            "difficulty": "advanced",
            "short_description": "An advanced course covering applied cryptography and zero-knowledge proofs.",
            "description": "Full cryptography syllabus covering hash functions, elliptic curves, and ZKPs.",
            "skills_taught": "Applied cryptography\nZero-knowledge proofs\nElliptic curves",
            "learning_outcomes": "Explain core cryptographic primitives.\nApply zero-knowledge proofs.",
            "keywords": "cryptography, zkp, security, elliptic-curves",
            "prerequisites": "Discrete mathematics and probability.",
            "intended_audience": "Advanced computer science students and security engineers.",
            "editorial_assessment": "Draft outline for review.",
        },
        follow_redirects=True,
    )
    assert save_page.status_code == 200
    assert "Saved as an unverified draft" in save_page.get_data(as_text=True)

    # Verify the AI-assisted draft is strictly 'draft', origin='ai_suggestion', unfeatured, unapproved, and hidden publicly
    saved = store.list_courses(q="Zero-Knowledge Proofs", include_unpublished=True)["items"]
    assert len(saved) == 1
    draft = saved[0]
    assert draft["status"] == "draft"
    assert draft["origin"] == "ai_suggestion"
    assert draft["is_featured"] is False
    assert draft["is_underrated"] is False
    assert draft["editorial_approved"] is False
    assert draft["needs_verification"] is True
    assert draft["learning_outcomes"] == "Explain core cryptographic primitives.\nApply zero-knowledge proofs."
    assert client.get(f"/courses/{draft['slug']}").status_code == 404
