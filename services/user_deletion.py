"""
BridgeSpace - User profile deletion (anonymisation).
Clears contact details and prevents login; keeps mediation-related data for referential integrity.
"""
import secrets
from datetime import datetime
from extensions import db, bcrypt
from models import User, Post, MediationParticipant, MediatorPayoutConfig, MediatorProfile


def anonymise_user(user: User) -> None:
    """
    Permanently anonymise a user: clear all contact details and break login.
    Mediation data (participations, mediator/creator references, posts with anonymised content) is kept.
    Caller must commit the session after this.
    """
    if getattr(user, "deleted_at", None):
        return  # already anonymised

    uid = user.id
    alias = user.anonymous_alias or "Deleted User"

    # Anonymise posts (keep record for mediation continuity)
    for post in Post.query.filter_by(author_id=uid).all():
        post.original_content = f"[Content removed — {alias}]"
        post.reformulated_content = None
        post.translations = None

    # Deactivate participations
    for p in MediationParticipant.query.filter_by(user_id=uid).all():
        p.is_active = False

    # Clear mediator payout config (contact/payment details)
    config = MediatorPayoutConfig.query.filter_by(user_id=uid).first()
    if config:
        config.iban = None
        config.mobile_phone = None
        config.stripe_connect_account_id = None

    # Clear mediator profile bio (personal info)
    profile = MediatorProfile.query.filter_by(user_id=uid).first()
    if profile:
        profile.bio = ""

    # Clear all contact details and break login; keep id, role, created_at for FKs
    user.email = f"deleted_{uid}@deleted.local"
    user.username = f"deleted_{uid}"
    user.password_hash = bcrypt.generate_password_hash(secrets.token_urlsafe(32)).decode("utf-8")  # unknown; login will fail
    user.display_name = None
    user.phone = None
    user.whatsapp = None
    user.telegram = None
    user.signal = None
    user.anonymous_alias = None
    user.verification_token = None
    user.reset_token = None
    user.reset_token_expiry = None
    user.is_verified = False
    user.deleted_at = datetime.utcnow()
