# tests/test_api.py
import json
from extensions import db, bcrypt
from models import User, Mediation, MediationParticipant, Post


def login(client, email, password):
    return client.post(
        "/auth/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


def create_user_and_mediation(app):
    """
    Create a user + mediation + participation in the test DB.

    Returns a dict of simple values, not ORM instances, to avoid DetachedInstanceError.
    """
    email = "apiuser@example.com"
    password = "Password123!"

    with app.app_context():
        # Create user
        pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        u = User(
            username="apiuser",
            email=email,
            display_name="API User",
            password_hash=pw_hash,
        )
        db.session.add(u)
        db.session.commit()
        user_id = u.id

        # Create mediation
        m = Mediation(
            title="API Mediation",
            description="desc",
            mediation_type="structured",
            mediator_id=user_id,
            creator_id=user_id,
            status="open",
        )
        db.session.add(m)
        db.session.commit()
        mediation_id = m.id

        # Create participation
        p = MediationParticipant(
            mediation_id=mediation_id,
            user_id=user_id,
            role="requester",
            display_name=u.display_name,
            is_active=True,
        )
        db.session.add(p)
        db.session.commit()

    # Return only plain values
    return {
        "email": email,
        "password": password,
        "user_id": user_id,
        "mediation_id": mediation_id,
    }


def test_api_submit_post_success(app, client):
    data = create_user_and_mediation(app)
    # Login with simple values
    login(client, data["email"], data["password"])

    payload = {
        "mediation_id": data["mediation_id"],
        "original": "Hello from API test",
        "reformulated": "",
        "submitted_version": "original",
        "input_method": "text",
    }

    resp = client.post(
        "/api/post/submit",
        data=json.dumps(payload),
        content_type="application/json",
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    post_id = body["post_id"]

    # Verify post exists in DB using a fresh context and query
    with app.app_context():
        post = Post.query.get(post_id)
        assert post is not None
        assert post.original_content == "Hello from API test"
        assert post.mediation_id == data["mediation_id"]
        assert post.author_id == data["user_id"]


def test_api_submit_post_missing_fields(app, client):
    data = create_user_and_mediation(app)
    login(client, data["email"], data["password"])

    payload = {
        # "mediation_id" missing
        "original": "",
    }

    resp = client.post(
        "/api/post/submit",
        data=json.dumps(payload),
        content_type="application/json",
    )

    assert resp.status_code == 400
    body = resp.get_json()
    assert "error" in body