# tests/test_search_query.py
import json

from extensions import db, bcrypt
from models import User


def create_user(app, email="searchquery@example.com", password="Password123!"):
    """
    Create a user in the test DB and return only primitive values
    (no ORM instances) to avoid DetachedInstanceError.
    """
    with app.app_context():
        pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        u = User(
            username="searchquery",
            email=email,
            display_name="Search Query User",
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


def test_search_query_requires_login(client):
    resp = client.post(
        "/search/query",
        data=json.dumps({"query": "some text"}),
        content_type="application/json",
        follow_redirects=False,
    )

    # Unauthenticated users should either be redirected to login or get 401
    assert resp.status_code in (302, 303, 401)


def test_search_query_rejects_empty_query(app, client):
    user = create_user(app)
    login(client, user["email"], user["password"])

    resp = client.post(
        "/search/query",
        data=json.dumps({"query": ""}),
        content_type="application/json",
    )

    assert resp.status_code == 400
    data = resp.get_json()
    assert "Please describe your case to search." in data.get("error", "")


def test_search_query_rejects_too_short_query(app, client):
    user = create_user(app)
    login(client, user["email"], user["password"])

    resp = client.post(
        "/search/query",
        data=json.dumps({"query": "short text"}),  # < 20 chars
        content_type="application/json",
    )

    assert resp.status_code == 400
    data = resp.get_json()
    assert "more detailed description" in data.get("error", "")


def test_search_query_calls_search_service_and_returns_results(app, client, monkeypatch):
    user = create_user(app)
    login(client, user["email"], user["password"])

    # Patch the function as imported by routes.search
    import routes.search as search_routes

    captured = {}

    def fake_search_similar_cases(query, user_lang, dispute_type_filter, tag_filter, limit):
        captured["query"] = query
        captured["user_lang"] = user_lang
        captured["dispute_type_filter"] = dispute_type_filter
        captured["tag_filter"] = tag_filter
        captured["limit"] = limit
        return [
            {"id": 1, "score": 0.9, "title": "Case 1"},
            {"id": 2, "score": 0.8, "title": "Case 2"},
        ]

    monkeypatch.setattr(search_routes, "search_similar_cases", fake_search_similar_cases)

    payload = {
        "query": "This is a sufficiently long case description to pass validation.",
        "dispute_type": "typeA",
        "tag": "tagA",
        "limit": 5,
    }

    resp = client.post(
        "/search/query",
        data=json.dumps(payload),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 2
    assert len(data["results"]) == 2

    # Check our fake was called with correct args
    assert captured["query"] == payload["query"]
    assert captured["user_lang"] == user["preferred_language"]
    assert captured["dispute_type_filter"] == "typeA"
    assert captured["tag_filter"] == "tagA"
    assert captured["limit"] == 5


def test_search_query_handles_service_exception(app, client, monkeypatch):
    user = create_user(app)
    login(client, user["email"], user["password"])

    import routes.search as search_routes

    def fake_raise(*args, **kwargs):
        raise RuntimeError("Boom")

    monkeypatch.setattr(search_routes, "search_similar_cases", fake_raise)

    payload = {
        "query": "This is a sufficiently long case description to pass validation.",
    }

    resp = client.post(
        "/search/query",
        data=json.dumps(payload),
        content_type="application/json",
    )

    assert resp.status_code == 500
    data = resp.get_json()
    assert "Search failed: Boom" in data.get("error", "")