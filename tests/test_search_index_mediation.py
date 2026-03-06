# tests/test_search_index_mediation.py
import json

from extensions import db, bcrypt
from models import User, Mediation


def login(client, email, password):
    return client.post(
        "/auth/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


def create_user(app, email="creator@example.com", username="creator", password="Password123!"):
    """
    Create a user in the test DB and return only primitive values
    to avoid DetachedInstanceError.
    """
    with app.app_context():
        pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        u = User(
            username=username,
            email=email,
            display_name=username.title(),
            password_hash=pw_hash,
        )
        db.session.add(u)
        db.session.commit()
        user_id = u.id

    return {
        "email": email,
        "password": password,
        "username": username,
        "user_id": user_id,
    }


def create_mediation(app, creator_id, status="closed"):
    """
    Create a mediation with the given creator_id and return its ID.
    """
    with app.app_context():
        m = Mediation(
            title="Indexed Mediation",
            description="desc",
            mediation_type="structured",
            mediator_id=creator_id,
            creator_id=creator_id,
            status=status,
        )
        db.session.add(m)
        db.session.commit()
        mediation_id = m.id

    return mediation_id


def test_index_mediation_requires_login(client, app):
    creator = create_user(app)
    mediation_id = create_mediation(app, creator["user_id"])

    resp = client.post(
        f"/search/index-mediation/{mediation_id}",
        data=json.dumps({"agreement_text": "Agreement"}),
        content_type="application/json",
        follow_redirects=False,
    )

    # Either redirect to login or 401 unauthorized is acceptable
    assert resp.status_code in (302, 303, 401)


def test_index_mediation_only_creator_can_index(app, client):
    creator = create_user(app, email="creator@example.com", username="creator")
    other = create_user(app, email="other@example.com", username="other")
    mediation_id = create_mediation(app, creator["user_id"], status="closed")

    # Login as non-creator
    login(client, other["email"], other["password"])

    resp = client.post(
        f"/search/index-mediation/{mediation_id}",
        data=json.dumps({"agreement_text": "Agreement"}),
        content_type="application/json",
    )

    assert resp.status_code == 403
    data = resp.get_json()
    assert "Only the mediation creator" in data.get("error", "")


def test_index_mediation_must_be_closed(app, client):
    creator = create_user(app)
    # Create an OPEN mediation — should not be indexable
    mediation_id = create_mediation(app, creator["user_id"], status="open")

    login(client, creator["email"], creator["password"])

    resp = client.post(
        f"/search/index-mediation/{mediation_id}",
        data=json.dumps({"agreement_text": "Agreement"}),
        content_type="application/json",
    )

    assert resp.status_code == 400
    data = resp.get_json()
    assert "Mediation must be closed" in data.get("error", "")


def test_index_mediation_requires_agreement_text(app, client):
    creator = create_user(app)
    mediation_id = create_mediation(app, creator["user_id"], status="closed")

    login(client, creator["email"], creator["password"])

    resp = client.post(
        f"/search/index-mediation/{mediation_id}",
        data=json.dumps({"agreement_text": ""}),
        content_type="application/json",
    )

    assert resp.status_code == 400
    data = resp.get_json()
    assert "Agreement text is required" in data.get("error", "")


def test_index_mediation_success_and_skip_flow(app, client, monkeypatch):
    creator = create_user(app)
    mediation_id = create_mediation(app, creator["user_id"], status="closed")

    login(client, creator["email"], creator["password"])

    # Patch the symbol used in routes.search, not the underlying service module
    import routes.search as search_routes

    # Case 1: index_closed_mediation returns True (success)
    monkeypatch.setattr(search_routes, "index_closed_mediation", lambda med, text: True)

    resp = client.post(
        f"/search/index-mediation/{mediation_id}",
        data=json.dumps({"agreement_text": "Final Agreement Text"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "Agreement indexed for future search." in data["message"]

    # Case 2: index_closed_mediation returns False (skipped, e.g. no consent)
    monkeypatch.setattr(search_routes, "index_closed_mediation", lambda med, text: False)

    resp = client.post(
        f"/search/index-mediation/{mediation_id}",
        data=json.dumps({"agreement_text": "Final Agreement Text"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is False
    assert "Indexing skipped" in data["message"]