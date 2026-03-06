# tests/test_mediation_dashboard.py
from extensions import db, bcrypt
from models import User, Mediation, MediationParticipant


def login(client, email, password):
    return client.post(
        "/auth/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


def test_dashboard_requires_login(client):
    # Do not log in
    resp = client.get("/dashboard", follow_redirects=False)

    # Accept either redirect to login or 401 unauthorized
    assert resp.status_code in (302, 303, 401)
    if resp.status_code in (302, 303):
        assert "/auth/login" in resp.headers["Location"]


def test_dashboard_shows_mediation_as_mediator(app, client):
    # Create a user and mediation within app context
    email = "mediator@example.com"
    password = "Password123!"
    with app.app_context():
        pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        u = User(
            username="mediator",
            email=email,
            display_name="Mediator",
            password_hash=pw_hash,
        )
        db.session.add(u)
        db.session.commit()
        user_id = u.id

        m = Mediation(
            title="Test Mediation",
            description="desc",
            mediation_type="structured",
            mediator_id=user_id,
            creator_id=user_id,
        )
        db.session.add(m)
        db.session.commit()

    # Login
    login(client, email, password)

    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert b"Test Mediation" in resp.data


def test_dashboard_shows_mediation_as_participant(app, client):
    email = "participant@example.com"
    password = "Password123!"
    with app.app_context():
        pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        u = User(
            username="participant",
            email=email,
            display_name="Participant",
            password_hash=pw_hash,
        )
        db.session.add(u)
        db.session.commit()
        user_id = u.id

        m = Mediation(
            title="Participant Mediation",
            description="desc",
            mediation_type="structured",
            mediator_id=user_id,
            creator_id=user_id,
        )
        db.session.add(m)
        db.session.commit()
        mediation_id = m.id

        p = MediationParticipant(
            mediation_id=mediation_id,
            user_id=user_id,
            role="requester",
            display_name=u.display_name,
            is_active=True,
        )
        db.session.add(p)
        db.session.commit()

    login(client, email, password)

    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert b"Participant Mediation" in resp.data