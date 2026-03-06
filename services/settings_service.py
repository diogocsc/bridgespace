"""
services/settings_service.py

Centralized access to admin-configurable settings.
Environment variables act as a fallback for local/dev.
"""

from __future__ import annotations

import os
from typing import Optional

from extensions import db
from models import SiteSetting, MediationPayment


def get_setting(key: str, default: str = "") -> str:
    # Env wins (useful for deploys)
    env_val = os.environ.get(key)
    if env_val is not None and env_val != "":
        return env_val

    try:
        row = SiteSetting.query.get(key)
        if row and row.value is not None:
            return str(row.value)
    except Exception:
        pass

    return default


def set_setting(key: str, value: str) -> None:
    row = SiteSetting.query.get(key)
    if row:
        row.value = value
    else:
        row = SiteSetting(key=key, value=value)
        db.session.add(row)
    db.session.commit()


def stripe_secret_key() -> str:
    return get_setting("STRIPE_SECRET_KEY", "")


def stripe_public_key() -> str:
    return get_setting("STRIPE_PUBLIC_KEY", "")


def paypal_client_id() -> str:
    return get_setting("PAYPAL_CLIENT_ID", "")


def paypal_client_secret() -> str:
    return get_setting("PAYPAL_CLIENT_SECRET", "")


def platform_commission_percent() -> float:
    """Platform commission as percentage of each paid mediation (e.g. 10 for 10%)."""
    raw = get_setting("PLATFORM_COMMISSION_PERCENT", "10")
    try:
        return max(0.0, min(100.0, float(raw)))
    except ValueError:
        return 10.0


def whatsapp_enabled() -> bool:
    return get_setting("WHATSAPP_ENABLED", "").lower() in ("1", "true", "yes")


def whatsapp_api_key() -> str:
    return get_setting("WHATSAPP_API_KEY", "")


def telegram_enabled() -> bool:
    return get_setting("TELEGRAM_ENABLED", "").lower() in ("1", "true", "yes")


def telegram_bot_token() -> str:
    return get_setting("TELEGRAM_BOT_TOKEN", "")


def signal_enabled() -> bool:
    return get_setting("SIGNAL_ENABLED", "").lower() in ("1", "true", "yes")


def signal_api_url() -> str:
    return get_setting("SIGNAL_API_URL", "")


def payments_enabled_for_mediation(mediation) -> bool:
    """
    Payments are considered enabled if:
    - mediation.payment_required is True
    - at least one provider is configured
    """
    if not getattr(mediation, "payment_required", True):
        return False
    return bool(stripe_secret_key() or (paypal_client_id() and paypal_client_secret()))


def is_participant_paid(mediation_id: int, participant_id: int) -> bool:
    paid = (
        MediationPayment.query
        .filter_by(mediation_id=mediation_id, participant_id=participant_id, status="paid")
        .first()
    )
    return bool(paid)

