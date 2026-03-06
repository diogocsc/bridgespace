# tests/test_search_page.py
from extensions import db, bcrypt
from models import User


def create_user(app, email="searchuser@example.com", password="Password123!"):
    """
    Create a user in the test DB and return only primitive values
    (no ORM instances) to avoid DetachedInstanceError.
    """
    with app.app_context():
        pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        u = User(
            username="searchuser",
            email=email,
            display_name="Search User",
            password_hash=pw_hash,
            preferred_language="en",
        )
        db.session.add(u)
        db.session.commit()
        user_id = u.id

    return {
        "email": email,
        "password": password,
        "user_id": user_id,
        "preferred_language": "en",
    }


def login(client, email, password):
    return client.post(
        "/auth/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


def test_search_page_requires_login(client):
    resp = client.get("/search/", follow_redirects=False)
    assert resp.status_code in (302, 303, 401)
    if resp.status_code in (302, 303):
        assert "/auth/login" in resp.headers["Location"]


def test_search_page_renders_template_with_context(app, client, templates, monkeypatch):
    user = create_user(app)
    login(client, user["email"], user["password"])

    import routes.search as search_routes

    monkeypatch.setattr(search_routes, "get_all_tags", lambda: ["tag1", "tag2"])

    monkeypatch.setattr(search_routes, "get_dispute_types",
                        lambda: [("type1", "Type 1"), ("type2", "Type 2")])

    with templates as recorded:
        resp = client.get("/search/")
        assert resp.status_code == 200

    assert len(recorded) >= 1

    # ---- FIX: normalize entry to avoid unpacking error ----
    entry = recorded[0]
    template = entry[0]
    context = entry[1]
    # -------------------------------------------------------

    assert template.name == "search/search.html"
    assert context["tags"] == ["tag1", "tag2"]
    assert context["dispute_types"] == [("type1", "Type 1"), ("type2", "Type 2")]
    assert context["user_lang"] == user["preferred_language"]