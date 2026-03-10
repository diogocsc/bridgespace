"""
routes/stripe_webhook.py

Stripe webhook endpoint. Handles checkout.session.completed to confirm payments.
Do not use @login_required; Stripe posts with a signature. Verify using STRIPE_WEBHOOK_SECRET.
"""

from datetime import datetime
from flask import Blueprint, request, current_app

from extensions import db
from models import MediationPayment, MediatorProfile
from services.settings_service import stripe_secret_key, stripe_webhook_secret
from services.mediator_quota_service import ensure_profile, ensure_period


stripe_webhook_bp = Blueprint("stripe_webhook", __name__)


@stripe_webhook_bp.route("/webhooks/stripe", methods=["POST"])
def handle_stripe_webhook():
    """
    Handle Stripe webhook events. Verifies signature and processes checkout.session.completed
    to mark the corresponding MediationPayment as paid (reliable confirmation alongside redirect).
    """
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")
    secret = stripe_webhook_secret()

    if not secret:
        current_app.logger.warning("Stripe webhook received but STRIPE_WEBHOOK_SECRET is not set")
        return "", 200  # 200 to avoid Stripe retries when webhook is not configured

    try:
        import stripe
        stripe.api_key = stripe_secret_key()
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except ValueError as e:
        current_app.logger.warning("Stripe webhook invalid payload: %s", e)
        return "Invalid payload", 400
    except Exception as e:
        current_app.logger.warning("Stripe webhook signature verification failed: %s", e)
        return "Invalid signature", 400

    if event.type == "checkout.session.completed":
        session = event.data.object
        session_id = getattr(session, "id", None)
        if session_id:
            # 1) Mediation one-time payments (existing behaviour)
            pay = MediationPayment.query.filter_by(provider="stripe", external_id=session_id).first()
            if pay and pay.status != "paid":
                pay.status = "paid"
                pay.paid_at = datetime.utcnow()
                db.session.commit()
                current_app.logger.info("MediationPayment %s marked paid via webhook session %s", pay.id, session_id)

        # 2) Subscriptions and bulk packs for mediators
        metadata = getattr(session, "metadata", {}) or {}
        kind = metadata.get("kind")
        mediator_id = metadata.get("mediator_user_id")
        if mediator_id:
            try:
                mediator_id_int = int(mediator_id)
            except (TypeError, ValueError):
                mediator_id_int = None
        else:
            mediator_id_int = None

        if kind == "subscription" and mediator_id_int:
            profile = ensure_profile(mediator_id_int)
            plan = metadata.get("plan", "free")
            profile.subscription_plan = plan
            profile.subscription_status = "active"
            profile.subscription_stripe_id = getattr(session, "subscription", None)
            profile.subscription_stripe_customer_id = getattr(session, "customer", None)
            profile.used_in_period = 0
            ensure_period(profile)
            db.session.commit()
            current_app.logger.info("Mediator %s subscription activated for plan %s", mediator_id_int, plan)
        elif kind == "bulk_pack" and mediator_id_int:
            profile = ensure_profile(mediator_id_int)
            try:
                pack_size = int(metadata.get("pack_size", "0"))
            except (TypeError, ValueError):
                pack_size = 0
            if pack_size > 0:
                profile.carry_over_balance = (profile.carry_over_balance or 0) + pack_size
                db.session.commit()
                current_app.logger.info("Mediator %s carry_over_balance increased by %s (bulk pack)", mediator_id_int, pack_size)

    return "", 200
