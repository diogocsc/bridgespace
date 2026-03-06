"""
services/payment_service.py

Lightweight helpers to start payment flows for mediations.
Stripe: uses hosted Checkout if keys are configured.
PayPal: placeholder URL (to be wired with JS SDK).
"""

from __future__ import annotations

import stripe  # type: ignore
from flask import current_app, url_for

from extensions import db
from models import MediationPayment
from services.settings_service import (
    stripe_secret_key,
    stripe_public_key,
    paypal_client_id,
    platform_commission_percent,
)


def _init_stripe():
    secret = stripe_secret_key()
    if not secret:
        return False
    stripe.api_key = secret
    return True


def create_payment_record(mediation, participant, provider: str, kind: str, amount_cents: int, currency: str):
    """
    Create a payment record. For paid amounts, compute platform commission and mediator payout.
    Pro-bono: amount_cents 0, platform_commission_cents 0, mediator_payout_cents 0.
    """
    platform_commission_cents = 0
    mediator_payout_cents = None
    if amount_cents > 0 and getattr(mediation, "mediator_id", None):
        pct = platform_commission_percent()
        platform_commission_cents = int(round(amount_cents * pct / 100.0))
        mediator_payout_cents = amount_cents - platform_commission_cents

    pay = MediationPayment(
        mediation_id=mediation.id,
        participant_id=participant.id,
        payer_user_id=participant.user_id,
        provider=provider,
        kind=kind,
        amount_cents=amount_cents,
        platform_commission_cents=platform_commission_cents,
        mediator_payout_cents=mediator_payout_cents,
        currency=currency,
        status="pending",
    )
    db.session.add(pay)
    db.session.commit()
    return pay


def start_stripe_checkout(mediation, participant, kind: str, donation_extra_cents: int = 0) -> str:
    """
    Returns Stripe Checkout URL, or empty string if Stripe not configured.
    """
    if not _init_stripe():
        return ""

    base = mediation.price_per_party_cents
    if kind == "probono":
        amount = 0
    elif kind == "donation":
        # By-donation: amount is what the participant states (no base fee)
        amount = max(donation_extra_cents, 0)
    else:
        amount = max(base + donation_extra_cents, 0)

    pay = create_payment_record(mediation, participant, "stripe", kind, amount, mediation.currency)

    success_url = url_for(
        "mediation.payment_success",
        mediation_id=mediation.id,
        payment_id=pay.id,
        _external=True,
    )
    cancel_url = url_for(
        "mediation.pre_mediation",
        mediation_id=mediation.id,
        _external=True,
    )

    if amount <= 0:
        # Pro bono: mark as paid immediately, no external checkout
        pay.status = "paid"
        db.session.commit()
        return success_url

    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[
            {
                "price_data": {
                    "currency": mediation.currency.lower(),
                    "unit_amount": amount,
                    "product_data": {
                        "name": f"Mediation {mediation.id} – {kind}",
                    },
                },
                "quantity": 1,
            }
        ],
        success_url=success_url + "&session_id={CHECKOUT_SESSION_ID}",
        cancel_url=cancel_url,
    )

    pay.external_id = session.id
    db.session.commit()
    return session.url


def mark_stripe_paid(payment_id: int):
    pay = MediationPayment.query.get(payment_id)
    if not pay:
        return
    pay.status = "paid"
    db.session.commit()


def start_paypal_flow(mediation, participant, kind: str, donation_extra_cents: int = 0) -> str:
    """
    Placeholder: in a full implementation you would create an order via PayPal API
    and return its approval URL. For now, we simply mark probono as paid and
    return an empty string for others if PayPal is not configured.
    """
    client_id = paypal_client_id()
    base = mediation.price_per_party_cents
    if kind == "probono":
        amount = 0
    elif kind == "donation":
        amount = max(donation_extra_cents, 0)
    else:
        amount = max(base + donation_extra_cents, 0)
    if kind == "probono":
        pay = create_payment_record(mediation, participant, "paypal", kind, 0, mediation.currency)
        pay.status = "paid"
        db.session.commit()
        return url_for("mediation.payment_success", mediation_id=mediation.id, payment_id=pay.id, _external=True)

    if not client_id:
        return ""

    # Minimal stub – real integration would use JS SDK on the frontend.
    pay = create_payment_record(mediation, participant, "paypal", kind, amount, mediation.currency)
    db.session.commit()
    # For now, just return pre_mediation URL; admin can manually mark as paid.
    return url_for("mediation.pre_mediation", mediation_id=mediation.id, _external=True)

