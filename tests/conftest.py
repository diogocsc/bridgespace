# tests/conftest.py
import pytest

import sys
import os

# Add the project root to sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Mock stripe (and optionally paypal) so app loads without installing them
from unittest.mock import MagicMock
if "stripe" not in sys.modules:
    _stripe_mock = MagicMock()
    _stripe_mock.checkout.Session.create.return_value = MagicMock(id="sess_xxx", url="https://checkout.stripe.com/xxx")
    sys.modules["stripe"] = _stripe_mock

from app import create_app
from extensions import db, bcrypt
from models import User
from flask import template_rendered
from contextlib import contextmanager

TEST_CONFIG = {
    "TESTING": True,
    "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    "SQLALCHEMY_TRACK_MODIFICATIONS": False,
    "WTF_CSRF_ENABLED": False,
    "MAIL_SUPPRESS_SEND": True,
}


@pytest.fixture
def app():
    app = create_app(TEST_CONFIG)

    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def runner(app):
    return app.test_cli_runner()


@pytest.fixture
def user(app):
    """Create a sample user for auth-related tests."""
    password = "Password123!"
    pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
    u = User(
        username="alice",
        email="alice@example.com",
        display_name="Alice",
        password_hash=pw_hash,
        preferred_language="en",
    )
    db.session.add(u)
    db.session.commit()
    # Expose the plaintext password for login helper
    u._plain_password = password
    return u




@contextmanager
def captured_templates(app):
    """
    Captures templates rendered via Flask's template_rendered signal.
    Produces a list of (template, context) tuples.
    """
    recorded = []

    def record(sender, template, context, **extra):
        # Record exactly (template, context)
        recorded.append((template, context))

    template_rendered.connect(record, app)

    try:
        yield recorded
    finally:
        template_rendered.disconnect(record, app)


@pytest.fixture
def templates(app):
    """
    A pytest fixture that RETURNS a context manager.
    Usage in tests:
        with templates as captured:
            client.get(...)
    """
    # DO NOT yield a list here — yield the context manager.
    return captured_templates(app)

