def test_homepage_renders_and_has_no_fake_stats(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Find Courses Worth Your Time" in html
    assert "students enrolled" not in html.lower()
    assert "CourseFinder" in html


def test_legal_pages_exist(client):
    for path in ("/about", "/contact", "/privacy", "/terms", "/community-guidelines", "/disclaimer", "/how-it-works"):
        assert client.get(path).status_code == 200


def test_catalogue_search_uses_database(client, store):
    response = client.get("/courses?q=CS50")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "CS50" in html


def test_catalogue_empty_filters(client):
    response = client.get("/courses?q=zzzz-no-such-course")
    html = response.get_data(as_text=True)
    assert "No published courses match" in html


def test_unpublished_course_is_not_public(client, store):
    category = store.list_categories()[0]
    provider = store.get_or_create_provider("Example Provider", "https://example.com")
    course = store.create_course(
        {
            "title": "Hidden Draft Course",
            "official_url": "https://example.com/hidden",
            "provider_id": provider["id"],
            "category_id": category["id"],
            "short_description": "Should not appear",
            "description": "Draft only",
            "status": "draft",
        }
    )
    assert course["status"] == "draft"
    listing = client.get("/courses?q=Hidden+Draft+Course")
    assert "Hidden Draft Course" not in listing.get_data(as_text=True)
    assert client.get(f"/courses/{course['slug']}").status_code == 404


def test_api_lists_published_courses(client):
    response = client.get("/api/v1/courses")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total"] >= 1
    assert payload["items"][0]["coursefinder_hosts_course"] is False
