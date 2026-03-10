"""
BridgeSpace - CAPTCHA verification (reCAPTCHA v2).
Used on public forms (login, register, forgot password, reset password) to reduce bot abuse.
"""
import logging
from flask import current_app
import requests

logger = logging.getLogger(__name__)

RECAPTCHA_VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"


def is_captcha_required():
    """Return True if reCAPTCHA is configured (both keys set)."""
    sk = current_app.config.get("RECAPTCHA_SITE_KEY") or ""
    sec = current_app.config.get("RECAPTCHA_SECRET_KEY") or ""
    return bool(sk.strip() and sec.strip())


def verify_recaptcha(response_token: str) -> bool:
    """
    Verify reCAPTCHA v2 response token with Google.
    Returns True if CAPTCHA is not configured (dev mode) or verification succeeds.
    """
    if not is_captcha_required():
        return True
    if not (response_token or response_token.strip()):
        return False
    secret = current_app.config.get("RECAPTCHA_SECRET_KEY", "").strip()
    if not secret:
        return True
    try:
        resp = requests.post(
            RECAPTCHA_VERIFY_URL,
            data={
                "secret": secret,
                "response": response_token.strip(),
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            logger.warning("reCAPTCHA verify failed: %s", data.get("error-codes", []))
            return False
        return True
    except Exception as e:
        logger.exception("reCAPTCHA verify error: %s", e)
        return False
