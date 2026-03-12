# tests/test_mediation_type_and_view.py
"""Tests for structured vs unstructured mediation type and view (posts) flow."""
from extensions import db, bcrypt
from models import User, Mediation, MediationParticipant, Post


def login(client, email, password):
    return client.post(
        "/auth/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


def create_user(app, email="viewuser@example.com", username="viewuser", password="Password123!", role="user"):
    with app.app_context():
        pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        u = User(
            username=username,
            email=email,
            display_name=username.title(),
            password_hash=pw_hash,
            role=role,
        )
        db.session.add(u)
        db.session.commit()
        return {"email": email, "password": password, "user_id": u.id}


def create_mediation_with_participant(app, mediator_id, creator_id, mediation_type="structured", add_post=False):
    """Create mediation and add mediator as participant; optionally add one post."""
    with app.app_context():
        m = Mediation(
            title="Test Mediation",
            description="desc",
            mediation_type=mediation_type,
            mediator_id=mediator_id,
            creator_id=creator_id,
            status="open",
            phase="pre_mediation",
            payment_required=False,
        )
        db.session.add(m)
        db.session.flush()
        mid = m.id
        db.session.add(MediationParticipant(
            mediation_id=mid,
            user_id=mediator_id,
            role="mediator",
            display_name="Mediator",
            is_active=True,
        ))
        db.session.add(MediationParticipant(
            mediation_id=mid,
            user_id=creator_id,
            role="requester",
            display_name="Requester",
            is_active=True,
        ))
        if add_post:
            p = Post(
                mediation_id=mid,
                author_id=creator_id,
                original_content="A test post",
                submitted_version="original",
            )
            db.session.add(p)
            db.session.flush()
            post_id = p.id
        else:
            post_id = None
        db.session.commit()
    return {"mediation_id": mid, "post_id": post_id}


def test_session_redirect_structured_to_pre_mediation(app, client):
    """Structured mediation: opening session redirects to current phase (pre_mediation)."""
    user = create_user(app, email="struct@example.com", username="struct", role="mediator")
    med = create_mediation_with_participant(app, user["user_id"], user["user_id"], mediation_type="structured")
    login(client, user["email"], user["password"])

    resp = client.get(f"/mediation/{med['mediation_id']}", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/pre_mediation" in resp.headers["Location"] or "pre_mediation" in resp.headers["Location"]


def test_session_redirect_unstructured_to_view(app, client):
    """Unstructured mediation: opening session redirects to current phase (pre_mediation when in pre_mediation)."""
    user = create_user(app, email="unstruct@example.com", username="unstruct", role="mediator")
    med = create_mediation_with_participant(app, user["user_id"], user["user_id"], mediation_type="unstructured")
    login(client, user["email"], user["password"])

    resp = client.get(f"/mediation/{med['mediation_id']}", follow_redirects=False)
    assert resp.status_code in (302, 303)
    # For unstructured mediations in pre_mediation phase we now redirect to pre_mediation
    assert "/pre_mediation" in resp.headers["Location"] or "pre_mediation" in resp.headers["Location"]


def test_view_mediation_unstructured_returns_200(app, client):
    """Unstructured view page loads for participant."""
    user = create_user(app, email="part@example.com", username="part")
    mediator = create_user(app, email="med@example.com", username="med", role="mediator")
    med = create_mediation_with_participant(
        app, mediator["user_id"], user["user_id"], mediation_type="unstructured"
    )
    login(client, user["email"], user["password"])

    resp = client.get(f"/mediation/{med['mediation_id']}/view")
    assert resp.status_code == 200
    assert b"Test Mediation" in resp.data
    # Unstructured badge may be translated (e.g. EN "Unstructured", PT "Não estruturada")
    assert (
        b"Unstructured" in resp.data
        or b"unstructured" in resp.data.lower()
        or b"N\xc3\xa3o estruturada" in resp.data  # PT badge
    )


def test_view_mediation_structured_redirects_to_session(app, client):
    """Accessing /view for a structured mediation redirects to session (then to phase)."""
    user = create_user(app, email="s2@example.com", username="s2", role="mediator")
    med = create_mediation_with_participant(app, user["user_id"], user["user_id"], mediation_type="structured")
    login(client, user["email"], user["password"])

    resp = client.get(f"/mediation/{med['mediation_id']}/view", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert str(med["mediation_id"]) in resp.headers["Location"]


def test_view_mediation_set_agreement_mediator_only(app, client):
    """Mediator can mark a post as the agreement (unstructured)."""
    mediator = create_user(app, email="med2@example.com", username="med2", role="mediator")
    part = create_user(app, email="p2@example.com", username="p2")
    med = create_mediation_with_participant(
        app, mediator["user_id"], part["user_id"], mediation_type="unstructured", add_post=True
    )
    login(client, mediator["email"], mediator["password"])

    resp = client.post(
        f"/mediation/{med['mediation_id']}/view",
        data={"action": "set_agreement", "post_id": med["post_id"]},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        m = Mediation.query.get(med["mediation_id"])
        assert m.agreement_post_id == med["post_id"]


def test_view_mediation_close_with_outcome(app, client):
    """Mediator can close unstructured mediation with outcome and justification."""
    mediator = create_user(app, email="med3@example.com", username="med3", role="mediator")
    med = create_mediation_with_participant(
        app, mediator["user_id"], mediator["user_id"], mediation_type="unstructured"
    )
    login(client, mediator["email"], mediator["password"])

    resp = client.post(
        f"/mediation/{med['mediation_id']}/view",
        data={
            "action": "close_mediation",
            "close_outcome": "agreement_reached",
            "close_justification": "Parties agreed on terms.",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        m = Mediation.query.get(med["mediation_id"])
        assert m.status == "closed"
        assert m.close_outcome == "agreement_reached"
        assert m.close_justification == "Parties agreed on terms."


def test_mediation_model_defaults_to_structured(app):
    """Mediation without explicit mediation_type is structured."""
    with app.app_context():
        pw_hash = bcrypt.generate_password_hash("dummy").decode("utf-8")
        u = User(
            username="u",
            email="u@example.com",
            password_hash=pw_hash,
        )
        db.session.add(u)
        db.session.commit()
        m = Mediation(
            title="T",
            mediator_id=u.id,
            creator_id=u.id,
        )
        db.session.add(m)
        db.session.commit()
        assert getattr(m, "mediation_type", "structured") == "structured"
