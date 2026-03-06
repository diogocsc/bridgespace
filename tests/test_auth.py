# tests/test_auth.py
from extensions import db
from models import User, MediatorProfile


def test_register_success(client, app):
    data = {
        "email": "bob@example.com",
        "username": "bob",
        "display_name": "Bob",
        "password": "SuperSecret123",
        "confirm_password": "SuperSecret123",
        "preferred_language": "en",
    }

    resp = client.post("/auth/register", data=data, follow_redirects=True)
    assert resp.status_code == 200

    # Check user was created
    with app.app_context():
        user = User.query.filter_by(email="bob@example.com").first()
        assert user is not None
        assert user.username == "bob"


def test_register_default_language_is_portuguese(client, app):
    """New users get Portuguese as default when not specified."""
    data = {
        "email": "defaultlang@example.com",
        "username": "defaultlang",
        "display_name": "Default",
        "password": "SuperSecret123",
        "confirm_password": "SuperSecret123",
        # omit preferred_language to test default
    }
    resp = client.post("/auth/register", data=data, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        user = User.query.filter_by(email="defaultlang@example.com").first()
        assert user is not None
        assert user.preferred_language == "pt"


def test_register_as_mediator(client, app):
    """Mediators can self-register and get role=mediator + MediatorProfile."""
    data = {
        "email": "mediator@example.com",
        "username": "mediator1",
        "display_name": "Mediator One",
        "password": "SuperSecret123",
        "confirm_password": "SuperSecret123",
        "preferred_language": "en",
        "register_as_mediator": "1",
    }
    resp = client.post("/auth/register", data=data, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        user = User.query.filter_by(email="mediator@example.com").first()
        assert user is not None
        assert user.role == "mediator"
        profile = MediatorProfile.query.filter_by(user_id=user.id).first()
        assert profile is not None
        assert profile.is_active is True


def test_preferences_become_mediator(client, app, user):
    """Existing user can register as mediator via preferences."""
    assert user.role == "user"
    client.post(
        "/auth/login",
        data={"email": user.email, "password": user._plain_password},
        follow_redirects=True,
    )
    resp = client.post(
        "/auth/preferences",
        data={
            "display_name": user.display_name,
            "preferred_language": "en",
            "register_as_mediator": "1",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        u = User.query.get(user.id)
        assert u.role == "mediator"
        profile = MediatorProfile.query.filter_by(user_id=u.id).first()
        assert profile is not None
        assert profile.is_active is True
        # New fields have defaults
        assert getattr(profile, "selection_count", 0) is not None
        assert getattr(profile, "ranking", 100) is not None


def test_register_invalid_email(client, app):
    data = {
        "email": "not-an-email",
        "username": "bob",
        "display_name": "Bob",
        "password": "SuperSecret123",
        "confirm_password": "SuperSecret123",
        "preferred_language": "en",
    }

    resp = client.post("/auth/register", data=data, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Invalid email address." in resp.data

    with app.app_context():
        assert User.query.filter_by(username="bob").first() is None


def test_login_success(client, app, user):
    # user fixture already created a valid user with _plain_password
    data = {
        "email": user.email,
        "password": user._plain_password,
        "remember": "y",
    }

    resp = client.post("/auth/login", data=data, follow_redirects=True)
    assert resp.status_code == 200
    # after login, user should be redirected to mediation.dashboard
    assert b"Dashboard" in resp.data or b"Your mediations" in resp.data


def test_login_invalid_password(client):
    data = {
        "email": "nonexistent@example.com",
        "password": "wrong",
    }

    resp = client.post("/auth/login", data=data, follow_redirects=True)
    assert resp.status_code == 200
    # Message may be in English or Portuguese depending on Accept-Language
    assert b"Invalid email or password." in resp.data or b"E-mail ou palavra-passe incorretos." in resp.data


def test_logout(client, app, user):
    # Log in first
    login_data = {
        "email": user.email,
        "password": user._plain_password,
    }
    client.post("/auth/login", data=login_data, follow_redirects=True)

    # Then logout
    resp = client.get("/auth/logout", follow_redirects=True)
    assert resp.status_code == 200
    assert b"You have been logged out." in resp.data