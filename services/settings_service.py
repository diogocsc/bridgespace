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


def _env_get(key: str) -> str:
    """
    Get value from os.environ, with Windows-safe handling: CRLF/BOM can leave
    keys like 'STRIPE_WEBHOOK_SECRET\\r' or '\\ufeffKEY', so we try exact key
    then stripped key, then match any env key that strips to our key.
    """
    val = os.environ.get(key)
    if val is not None and str(val).strip():
        return str(val).strip()
    val = os.environ.get(key.strip())
    if val is not None and str(val).strip():
        return str(val).strip()
    key_clean = key.strip()
    for k, v in os.environ.items():
        if k.strip() == key_clean and v and str(v).strip():
            return str(v).strip()
    return ""


def get_setting(key: str, default: str = "") -> str:
    # Env wins (useful for deploys). Use Windows-safe env lookup.
    env_val = _env_get(key)
    if env_val:
        return env_val

    try:
        row = SiteSetting.query.get(key)
        if row and row.value is not None:
            return str(row.value).strip()
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


def stripe_webhook_secret() -> str:
    """Signing secret for Stripe webhooks (starts with whsec_). From Stripe Dashboard → Developers → Webhooks."""
    return get_setting("STRIPE_WEBHOOK_SECRET", "")


def platform_commission_percent() -> float:
    """Platform commission as percentage of each paid mediation (e.g. 5 for 5%)."""
    raw = get_setting("PLATFORM_COMMISSION_PERCENT", "5")
    try:
        return max(0.0, min(100.0, float(raw)))
    except ValueError:
        return 5.0


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
    - Stripe is configured (Stripe is the only payment provider)
    """
    if not getattr(mediation, "payment_required", True):
        return False
    return bool(stripe_secret_key())


# ---------------------------------------------------------------------------
# Mediator plan settings (quotas and prices)
# ---------------------------------------------------------------------------

def free_quota_default() -> int:
    return int(get_setting("FREE_QUOTA_DEFAULT_PER_MONTH", "3") or 3)


def pro_plan_price_eur() -> float:
    return float(get_setting("PRO_PLAN_PRICE_EUR", "50") or 50.0)


def pro_plan_quota_per_month() -> int:
    return int(get_setting("PRO_PLAN_QUOTA_PER_MONTH", "15") or 15)


def enterprise_plan_price_eur() -> float:
    return float(get_setting("ENTERPRISE_PLAN_PRICE_EUR", "100") or 100.0)


def bulk_price_per_mediation_eur() -> float:
    return float(get_setting("BULK_PRICE_PER_MEDIATION_EUR", "10") or 10.0)


def bulk_pack_size() -> int:
    """Number of mediations included in one bulk pack purchase (admin-configurable, default 3)."""
    try:
        return int(get_setting("BULK_PACK_SIZE", "3") or 3)
    except ValueError:
        return 3


def is_participant_paid(mediation_id: int, participant_id: int) -> bool:
    paid = (
        MediationPayment.query
        .filter_by(mediation_id=mediation_id, participant_id=participant_id, status="paid")
        .first()
    )
    return bool(paid)


# ---------------------------------------------------------------------------
# Company / legal data (privacy policy, terms)
# ---------------------------------------------------------------------------

def company_name() -> str:
    return get_setting("COMPANY_NAME", "")


def company_address() -> str:
    return get_setting("COMPANY_ADDRESS", "")


def company_contact_email() -> str:
    return get_setting("COMPANY_CONTACT_EMAIL", "")


def company_contact_phone() -> str:
    return get_setting("COMPANY_CONTACT_PHONE", "")


def company_fiscal_number() -> str:
    return get_setting("COMPANY_FISCAL_NUMBER", "")


def get_company_placeholders() -> dict:
    """Return dict of placeholder -> value for legal documents (e.g. {{COMPANY_NAME}} -> value)."""
    return {
        "{{COMPANY_NAME}}": company_name(),
        "{{COMPANY_ADDRESS}}": company_address(),
        "{{COMPANY_CONTACT_EMAIL}}": company_contact_email(),
        "{{COMPANY_CONTACT_PHONE}}": company_contact_phone(),
        "{{COMPANY_FISCAL_NUMBER}}": company_fiscal_number(),
    }


def email_language() -> str:
    """
    Default language for outgoing emails, configured in the admin panel.
    Falls back to the UI default language if unset or invalid.
    """
    try:
        from services.translations import DEFAULT_LANGUAGE, LOCALES
        default_lang = DEFAULT_LANGUAGE
        allowed = set(LOCALES.keys())
    except Exception:
        default_lang = "en"
        allowed = {"en", "pt"}
    lang = get_setting("EMAIL_LANGUAGE", default_lang)
    return lang if lang in allowed else default_lang

