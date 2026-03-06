"""
Process mediator 48h confirmation timeouts:
- 1st timeout: lower ranking of current mediator, assign next by ranking, send new request.
- 2nd timeout: notify all admins/superadmins, set mediator_escalated_at.
"""
import logging
from datetime import datetime, timedelta

from extensions import db
from models import Mediation, User, MediatorProfile, MediationParticipant

logger = logging.getLogger(__name__)

CONFIRMATION_DEADLINE_HOURS = 48


def _active_mediators_list():
    q = (
        User.query.filter(User.role == "mediator")
        .outerjoin(MediatorProfile, MediatorProfile.user_id == User.id)
        .filter((MediatorProfile.is_active == True) | (MediatorProfile.id == None))  # noqa: E712
    )
    return q.all()


def _ranking(mediator_user):
    profile = MediatorProfile.query.filter_by(user_id=mediator_user.id).first()
    return (profile.ranking if profile is not None and profile.ranking is not None else 100.0)


def _pick_next_by_ranking(mediators, exclude_user_id):
    candidates = [m for m in mediators if m.id != exclude_user_id]
    if not candidates:
        return None
    return max(candidates, key=lambda m: _ranking(m))


def _invite_mediator(med, mediator_user):
    from routes.mediation import _invite_mediator_and_notify
    _invite_mediator_and_notify(med, mediator_user)


def process_mediator_confirmation_timeouts():
    """
    Run this periodically (e.g. every hour via cron). Finds mediations where
    the mediator was invited 48h+ ago and has not confirmed; either reassigns
    (attempt 1) or escalates to admins (attempt 2).
    """
    deadline = datetime.utcnow() - timedelta(hours=CONFIRMATION_DEADLINE_HOURS)
    pending = (
        Mediation.query.filter(
            Mediation.mediator_invited_at != None,  # noqa: E711
            Mediation.mediator_confirmed_at == None,  # noqa: E711
            Mediation.mediator_escalated_at == None,  # noqa: E711
            Mediation.mediator_invited_at < deadline,
        )
        .all()
    )
    for med in pending:
        try:
            if med.mediator_attempt == 1:
                # Lower current mediator's ranking
                profile = MediatorProfile.query.filter_by(user_id=med.mediator_id).first()
                if profile:
                    profile.ranking = max(0.0, (profile.ranking or 100.0) - 10.0)
                mediators = _active_mediators_list()
                next_mediator = _pick_next_by_ranking(mediators, med.mediator_id)
                if next_mediator:
                    med.mediator_attempt = 2
                    _invite_mediator(med, next_mediator)
                    # Update participant record so the new mediator has access
                    for p in med.participants:
                        if p.role == "mediator":
                            p.user_id = next_mediator.id
                            p.display_name = next_mediator.display_name or next_mediator.username
                            break
                    logger.info("Mediation %s: reassigned to mediator %s (2nd tentative)", med.id, next_mediator.id)
                else:
                    # No other mediator — escalate immediately
                    med.mediator_escalated_at = datetime.utcnow()
                    try:
                        from services.notification import send_mediator_unconfirmed_alert_to_admins
                        send_mediator_unconfirmed_alert_to_admins(med)
                    except Exception as e:
                        logger.exception("Failed to send admin alert: %s", e)
                    logger.warning("Mediation %s: no alternative mediator; escalated to admins", med.id)
            else:
                # attempt == 2: escalate to admins
                med.mediator_escalated_at = datetime.utcnow()
                try:
                    from services.notification import send_mediator_unconfirmed_alert_to_admins
                    send_mediator_unconfirmed_alert_to_admins(med)
                except Exception as e:
                    logger.exception("Failed to send admin alert: %s", e)
                logger.info("Mediation %s: 2nd timeout; admins notified", med.id)
            db.session.commit()
        except Exception as e:
            logger.exception("Error processing mediation %s: %s", med.id, e)
            db.session.rollback()
    return len(pending)
