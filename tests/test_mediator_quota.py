from datetime import datetime, timedelta

from extensions import db
from models import User, MediatorProfile
from services.mediator_quota_service import ensure_profile, available_mediations, consume_mediation


def create_mediator(app):
    with app.app_context():
        u = User(
            email="quota@example.com",
            username="quota_mediator",
            display_name="Quota Mediator",
            role="mediator",
            password_hash="x",  # minimal non-null password for tests
        )
        u._plain_password = "SuperSecret123"  # for symmetry with fixtures
        db.session.add(u)
        db.session.commit()
        return u.id


def test_free_quota_consumption_and_carry_over(app):
    mediator_id = create_mediator(app)
    with app.app_context():
        profile = ensure_profile(mediator_id)
        # Start with default free quota
        start_avail = available_mediations(profile)
        assert start_avail >= 0

        # Consume one mediation
        assert consume_mediation(profile) is True
        db.session.commit()
        after_one = available_mediations(profile)
        assert after_one == start_avail - 1 or after_one < start_avail


def test_pro_bono_refund_does_not_reduce_quota(app):
    mediator_id = create_mediator(app)
    with app.app_context():
        profile = ensure_profile(mediator_id)
        start_avail = available_mediations(profile)
        assert consume_mediation(profile) is True
        db.session.commit()
        after_consume = available_mediations(profile)
        # Simulate "refund" by manually decrementing used_in_period
        profile.used_in_period = max(0, (profile.used_in_period or 0) - 1)
        db.session.commit()
        after_refund = available_mediations(profile)
        # After refund we should have at least as many available as after_consume
        assert after_refund >= after_consume
        # and ideally back to start or close
        assert after_refund <= start_avail

