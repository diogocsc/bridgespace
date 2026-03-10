"""
routes/billing.py

Mediator billing flows:
 - Subscribe to Professional / Enterprise plans (Stripe Checkout, mode=subscription)
 - Buy bulk mediation packs (one-time payment that increases carry_over_balance)
"""

from flask import Blueprint, redirect, url_for, flash
from flask_login import login_required, current_user

from services.payment_service import (
    start_subscription_checkout,
    start_bulk_pack_checkout,
)
from services.translations import translate


billing_bp = Blueprint("billing", __name__, url_prefix="/billing")


def _require_mediator():
    if not getattr(current_user, "is_mediator", False):
        from flask import abort
        abort(403)


@billing_bp.route("/subscribe/pro")
@login_required
def subscribe_pro():
    _require_mediator()
    url = start_subscription_checkout(current_user, "professional")
    if not url:
        lang = getattr(current_user, "preferred_language", "en")
        flash(translate("billing_checkout_error", lang), "danger")
        return redirect(url_for("mediation.payout_settings"))
    return redirect(url)


@billing_bp.route("/subscribe/enterprise")
@login_required
def subscribe_enterprise():
    _require_mediator()
    url = start_subscription_checkout(current_user, "enterprise")
    if not url:
        lang = getattr(current_user, "preferred_language", "en")
        flash(translate("billing_checkout_error", lang), "danger")
        return redirect(url_for("mediation.payout_settings"))
    return redirect(url)


@billing_bp.route("/buy-pack/<int:pack_size>")
@login_required
def buy_pack(pack_size: int):
    _require_mediator()
    if pack_size <= 0:
        return redirect(url_for("mediation.payout_settings"))
    url = start_bulk_pack_checkout(current_user, pack_size)
    if not url:
        lang = getattr(current_user, "preferred_language", "en")
        flash(translate("billing_checkout_error", lang), "danger")
        return redirect(url_for("mediation.payout_settings"))
    return redirect(url)

