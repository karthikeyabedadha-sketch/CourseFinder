def register(client, email="member@example.com"):
    return client.post(
        "/auth/register",
        data={"display_name": "Member", "email": email, "password": "correcthorse"},
        follow_redirects=True,
    )


def test_register_and_login(client):
    response = register(client)
    assert response.status_code == 200
    assert b"My library" in response.data


def test_public_registration_cannot_set_admin_role(client, store):
    register(client, "normal@example.com")
    user = store.get_user_by_email("normal@example.com")
    assert user["role"] == "user"


def test_admin_routes_require_admin(client):
    register(client, "user@example.com")
    response = client.get("/admin/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/studio")
    client.post("/auth/logout")
    login_page = client.get("/admin/", follow_redirects=False)
    assert login_page.status_code == 302
    assert login_page.headers["Location"].endswith("/admin/studio")


def test_promote_script_logic(store):
    store.create_user("owner@example.com", "correcthorse", "Owner")
    store.set_user_role("owner@example.com", "admin")
    assert store.get_user_by_email("owner@example.com")["role"] == "admin"


def test_admin_dashboard_uses_real_counts(client, store):
    store.create_user("owner@example.com", "correcthorse", "Owner")
    store.set_user_role("owner@example.com", "admin")
    client.post(
        "/admin/studio",
        data={"owner_secret": "test-owner-access-secret-at-least-32"},
    )
    response = client.get("/admin/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    counts = store.dashboard_counts()
    assert str(counts["published_courses"]) in html
    assert str(counts["total_users"]) in html


def test_reviews_are_hidden_until_approved(client, store):
    register(client, "reviewer@example.com")
    course = store.list_courses(public_only=True)["items"][0]
    client.post(
        f"/courses/{course['slug']}",
        data={"rating": "5", "body": "I completed the problem sets and found them demanding but fair."},
        follow_redirects=True,
    )
    public = client.get(f"/courses/{course['slug']}")
    assert b"I completed the problem sets" not in public.data
    reviews = store.list_reviews(course_id=course["id"], status="pending")
    assert len(reviews) == 1
    store.set_review_status(reviews[0]["id"], "approved")
    approved = client.get(f"/courses/{course['slug']}")
    assert b"I completed the problem sets" in approved.data
