"""
services/schema_migrations.py

Very small, idempotent schema helpers for this project.
We don't use Alembic yet, so we apply minimal safe migrations at startup.
"""

from __future__ import annotations

import logging
from sqlalchemy import inspect, text

from extensions import db

logger = logging.getLogger(__name__)


def ensure_schema():
    """
    Apply tiny, additive migrations if needed.
    Safe to run on every startup.
    """
    engine = db.engine
    insp = inspect(engine)

    # --- user.role (additive) ---
    if insp.has_table("user"):
        cols = {c["name"] for c in insp.get_columns("user")}
        if "role" not in cols:
            logger.warning("Applying migration: add user.role")
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE user ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'"))
        for col in ("whatsapp", "telegram", "signal"):
            if col not in cols:
                logger.warning("Applying migration: add user.%s", col)
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE user ADD COLUMN {col} VARCHAR(80)"))

    # --- mediator_profile table (new) + additive ---
    if not insp.has_table("mediator_profile"):
        logger.warning("Applying migration: create mediator_profile")
        from models import MediatorProfile  # noqa: F401
        db.metadata.create_all(engine, tables=[db.metadata.tables["mediator_profile"]])
    else:
        mp_cols = {c["name"] for c in insp.get_columns("mediator_profile")}
        for col, typ, default in (
            ("selection_count", "INTEGER NOT NULL DEFAULT 0", 0),
            ("times_confirmed", "INTEGER NOT NULL DEFAULT 0", 0),
            ("ranking", "FLOAT NOT NULL DEFAULT 100.0", 100.0),
        ):
            if col not in mp_cols:
                logger.warning("Applying migration: add mediator_profile.%s", col)
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE mediator_profile ADD COLUMN {col} {typ}"))

    # --- mediation additive fields ---
    if insp.has_table("mediation"):
        cols = {c["name"] for c in insp.get_columns("mediation")}
        with engine.begin() as conn:
            if "pre_mediation_text" not in cols:
                logger.warning("Applying migration: add mediation.pre_mediation_text")
                conn.execute(text("ALTER TABLE mediation ADD COLUMN pre_mediation_text TEXT NOT NULL DEFAULT ''"))
            if "price_per_party_cents" not in cols:
                logger.warning("Applying migration: add mediation.price_per_party_cents")
                conn.execute(text("ALTER TABLE mediation ADD COLUMN price_per_party_cents INTEGER NOT NULL DEFAULT 5000"))
            if "pricing_type" not in cols:
                logger.warning("Applying migration: add mediation.pricing_type")
                conn.execute(text("ALTER TABLE mediation ADD COLUMN pricing_type VARCHAR(20) NOT NULL DEFAULT 'fixed'"))
            if "currency" not in cols:
                logger.warning("Applying migration: add mediation.currency")
                conn.execute(text("ALTER TABLE mediation ADD COLUMN currency VARCHAR(3) NOT NULL DEFAULT 'EUR'"))
            if "payment_required" not in cols:
                logger.warning("Applying migration: add mediation.payment_required")
                conn.execute(text("ALTER TABLE mediation ADD COLUMN payment_required BOOLEAN NOT NULL DEFAULT 1"))
            if "mediator_invited_at" not in cols:
                logger.warning("Applying migration: add mediation.mediator_invited_at")
                conn.execute(text("ALTER TABLE mediation ADD COLUMN mediator_invited_at DATETIME"))
            if "mediator_confirmed_at" not in cols:
                logger.warning("Applying migration: add mediation.mediator_confirmed_at")
                conn.execute(text("ALTER TABLE mediation ADD COLUMN mediator_confirmed_at DATETIME"))
            if "mediator_attempt" not in cols:
                logger.warning("Applying migration: add mediation.mediator_attempt")
                conn.execute(text("ALTER TABLE mediation ADD COLUMN mediator_attempt INTEGER NOT NULL DEFAULT 1"))
            if "mediator_escalated_at" not in cols:
                logger.warning("Applying migration: add mediation.mediator_escalated_at")
                conn.execute(text("ALTER TABLE mediation ADD COLUMN mediator_escalated_at DATETIME"))
            if "explanation_requested_at" not in cols:
                logger.warning("Applying migration: add mediation.explanation_requested_at")
                conn.execute(text("ALTER TABLE mediation ADD COLUMN explanation_requested_at DATETIME"))
            if "explanation_added_at" not in cols:
                logger.warning("Applying migration: add mediation.explanation_added_at")
                conn.execute(text("ALTER TABLE mediation ADD COLUMN explanation_added_at DATETIME"))
            if "mediation_type" not in cols:
                logger.warning("Applying migration: add mediation.mediation_type")
                conn.execute(text("ALTER TABLE mediation ADD COLUMN mediation_type VARCHAR(20) NOT NULL DEFAULT 'structured'"))
            if "agreement_post_id" not in cols:
                logger.warning("Applying migration: add mediation.agreement_post_id")
                conn.execute(text("ALTER TABLE mediation ADD COLUMN agreement_post_id INTEGER"))
            if "close_outcome" not in cols:
                logger.warning("Applying migration: add mediation.close_outcome")
                conn.execute(text("ALTER TABLE mediation ADD COLUMN close_outcome VARCHAR(30)"))
            if "close_justification" not in cols:
                logger.warning("Applying migration: add mediation.close_justification")
                conn.execute(text("ALTER TABLE mediation ADD COLUMN close_justification TEXT"))

    # --- perspective additive fields ---
    if insp.has_table("perspective"):
        cols = {c["name"] for c in insp.get_columns("perspective")}
        if "used_ai_reformulation" not in cols:
            logger.warning("Applying migration: add perspective.used_ai_reformulation")
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE perspective ADD COLUMN used_ai_reformulation BOOLEAN NOT NULL DEFAULT 0"
                    )
                )

    # --- mediation_participant additive fields ---
    if insp.has_table("mediation_participant"):
        cols = {c["name"] for c in insp.get_columns("mediation_participant")}
        if "pre_mediation_acknowledged" not in cols:
            logger.warning("Applying migration: add mediation_participant.pre_mediation_acknowledged")
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE mediation_participant "
                        "ADD COLUMN pre_mediation_acknowledged BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
        if "consent_search_share" not in cols:
            logger.warning("Applying migration: add mediation_participant.consent_search_share")
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE mediation_participant "
                        "ADD COLUMN consent_search_share BOOLEAN"
                    )
                )

    # --- site_setting table (new) ---
    if not insp.has_table("site_setting"):
        logger.warning("Applying migration: create site_setting")
        from models import SiteSetting  # noqa: F401
        db.metadata.create_all(engine, tables=[db.metadata.tables["site_setting"]])

    # --- mediator_payout_config table (new) ---
    if not insp.has_table("mediator_payout_config"):
        logger.warning("Applying migration: create mediator_payout_config")
        from models import MediatorPayoutConfig  # noqa: F401
        db.metadata.create_all(engine, tables=[db.metadata.tables["mediator_payout_config"]])

    # --- mediation_payment table (new) ---
    if not insp.has_table("mediation_payment"):
        logger.warning("Applying migration: create mediation_payment")
        from models import MediationPayment  # noqa: F401
        db.metadata.create_all(engine, tables=[db.metadata.tables["mediation_payment"]])
    else:
        cols = {c["name"] for c in insp.get_columns("mediation_payment")}
        if "kind" not in cols:
            logger.warning("Applying migration: add mediation_payment.kind")
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE mediation_payment "
                        "ADD COLUMN kind VARCHAR(20) NOT NULL DEFAULT 'standard'"
                    )
                )
        if "platform_commission_cents" not in cols:
            logger.warning("Applying migration: add mediation_payment.platform_commission_cents")
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE mediation_payment "
                        "ADD COLUMN platform_commission_cents INTEGER NOT NULL DEFAULT 0"
                    )
                )
        if "mediator_payout_cents" not in cols:
            logger.warning("Applying migration: add mediation_payment.mediator_payout_cents")
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE mediation_payment "
                        "ADD COLUMN mediator_payout_cents INTEGER"
                    )
                )

    # --- mediation_deletion_log table (new) ---
    if not insp.has_table("mediation_deletion_log"):
        logger.warning("Applying migration: create mediation_deletion_log")
        from models import MediationDeletionLog  # noqa: F401
        db.metadata.create_all(engine, tables=[db.metadata.tables["mediation_deletion_log"]])

