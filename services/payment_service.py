"""
services/payment_service.py

Payment flows for mediations. Stripe is the only provider.
When the mediator has a Stripe Connect account, Checkout uses a destination charge
so payment goes to the mediator's account and the platform keeps the commission.
"""

from __future__ import annotations

import stripe  # type: ignore
from flask import url_for

from extensions import db
from models import MediationPayment, MediatorPayoutConfig, MediatorProfile
from services.settings_service import (
    stripe_secret_key,
    stripe_public_key,
    platform_commission_percent,
    pro_plan_price_eur,
    enterprise_plan_price_eur,
    bulk_price_per_mediation_eur,
)


def _init_stripe():
    secret = stripe_secret_key()
    if not secret:
        return False
    stripe.api_key = secret
    return True


def create_payment_record(mediation, participant, provider: str, kind: str, amount_cents: int, currency: str):
    """
    Create a payment record. Commission logic has been removed; the full amount is recorded,
    and platform_commission_cents / mediator_payout_cents are no longer used.
    """
    platform_commission_cents = 0
    mediator_payout_cents = None
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
    Deprecated for party payments. Mediation participants no longer pay via platform Checkout;
    mediators handle payments directly. Kept only for backwards compatibility.
    """
    return ""


def mark_stripe_paid(payment_id: int):
    pay = MediationPayment.query.get(payment_id)
    if not pay:
        return
    pay.status = "paid"
    db.session.commit()


# ---------------------------------------------------------------------------
# Product and price (blueprint: create product with default_price for one-time payment)
# ---------------------------------------------------------------------------

def ensure_mediation_stripe_price(mediation) -> bool:
    """
    Create a Stripe Product with default price for this mediation (fixed price per party).
    Persists stripe_product_id and stripe_price_id on the mediation. Used so Checkout
    can use line_items[].price (blueprint) instead of only price_data.
    Returns True if product/price are ready, False on error or not applicable.
    """
    if not _init_stripe():
        return False
    if (mediation.pricing_type or "fixed") != "fixed" or (mediation.price_per_party_cents or 0) <= 0:
        return False
    if (mediation.stripe_price_id or "").strip():
        return True
    try:
        product = stripe.Product.create(
            name=mediation.title[:250] or f"Mediation {mediation.id}",
            default_price_data={
                "currency": (mediation.currency or "eur").lower(),
                "unit_amount": int(mediation.price_per_party_cents),
            },
        )
        price_id = product.default_price
        if hasattr(price_id, "id"):
            price_id = price_id.id
        if not price_id:
            return False
        mediation.stripe_product_id = product.id
        mediation.stripe_price_id = price_id
        db.session.commit()
        return True
    except stripe.StripeError:
        return False


# ---------------------------------------------------------------------------
# Subscription and bulk purchase Checkout for mediators (single platform Stripe account)
# ---------------------------------------------------------------------------

def start_subscription_checkout(user, plan: str) -> str:
    """
    Create a Stripe Checkout Session for a mediator subscription.
    plan: 'professional' | 'enterprise'
    """
    if not _init_stripe():
        return ""

    if plan == "professional":
        amount_eur = pro_plan_price_eur()
        name = "Professional mediation plan"
    elif plan == "enterprise":
        amount_eur = enterprise_plan_price_eur()
        name = "Enterprise mediation plan"
    else:
        return ""

    amount_cents = int(round(amount_eur * 100))

    # Build success/cancel URLs using same PUBLIC_BASE_URL-aware helper as emails.
    from services.notification import _external_url  # lazy import to avoid cycles
    base_success = _external_url("mediation.payout_settings")
    success_url = base_success + ("&" if "?" in base_success else "?") + "billing=success"
    cancel_url = _external_url("mediation.payout_settings")

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[
                {
                    "price_data": {
                        "currency": "eur",
                        "unit_amount": amount_cents,
                        "product_data": {"name": name},
                        "recurring": {"interval": "month"},
                    },
                    "quantity": 1,
                }
            ],
            success_url=success_url + "&session_id={CHECKOUT_SESSION_ID}",
            cancel_url=cancel_url,
            metadata={
                "kind": "subscription",
                "plan": plan,
                "mediator_user_id": str(user.id),
            },
        )
    except stripe.StripeError:
        return ""

    return session.url


def start_bulk_pack_checkout(user, pack_size: int) -> str:
    """
    Create a Stripe Checkout Session for a one-time bulk mediation pack.
    pack_size: number of mediations to add to carry_over_balance.
    """
    if not _init_stripe() or pack_size <= 0:
        return ""

    price_per_med = bulk_price_per_mediation_eur()
    amount_cents = int(round(price_per_med * pack_size * 100))

    from services.notification import _external_url  # lazy import to avoid cycles
    base_success = _external_url("mediation.payout_settings")
    success_url = base_success + ("&" if "?" in base_success else "?") + "billing=success"
    cancel_url = _external_url("mediation.payout_settings")

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[
                {
                    "price_data": {
                        "currency": "eur",
                        "unit_amount": amount_cents,
                        "product_data": {
                            "name": f"Mediation bulk pack ({pack_size} sessions)",
                        },
                    },
                    "quantity": 1,
                }
            ],
            success_url=success_url + "&session_id={CHECKOUT_SESSION_ID}",
            cancel_url=cancel_url,
            metadata={
                "kind": "bulk_pack",
                "pack_size": str(pack_size),
                "mediator_user_id": str(user.id),
            },
            invoice_creation={"enabled": True},
        )
    except stripe.StripeError:
        return ""

    return session.url

