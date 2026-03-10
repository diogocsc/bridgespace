"""
Mediator metrics: mediations opened, agreements reached,
explanation response average time, confirmation response time,
total value received from mediations, total expended (billing).
"""
from typing import Optional

from extensions import db
from models import Mediation, Agreement, MediationPayment, MediatorBillingTransaction


def get_mediator_metrics(mediator_id: int) -> dict:
    """
    Return metrics for one mediator.
    - mediations_opened: count of Mediation where mediator_id = mediator_id
    - agreements_reached: count where phase == 'agreement' and agreement exists
    - explanation_response_avg_hours: avg (explanation_added_at - explanation_requested_at) in hours, or None
    - confirmation_response_avg_hours: avg (mediator_confirmed_at - mediator_invited_at) in hours, or None
    - total_value_received_cents: sum of mediator_payout_cents for paid mediation payments
    - total_expended_cents: sum of MediatorBillingTransaction.amount_cents (subscriptions + bulk packs)
    """
    base = Mediation.query.filter(Mediation.mediator_id == mediator_id)
    mediations_opened = base.count()

    agreements_reached = (
        Mediation.query.filter(Mediation.mediator_id == mediator_id)
        .join(Agreement, Agreement.mediation_id == Mediation.id)
        .count()
    )

    # Explanation: average of (explanation_added_at - explanation_requested_at) where both set
    expl_rows = (
        Mediation.query.filter(
            Mediation.mediator_id == mediator_id,
            Mediation.explanation_requested_at.isnot(None),
            Mediation.explanation_added_at.isnot(None),
        )
        .with_entities(
            Mediation.explanation_requested_at,
            Mediation.explanation_added_at,
        )
        .all()
    )
    if expl_rows:
        total_seconds = sum(
            (added - requested).total_seconds()
            for requested, added in expl_rows
            if requested and added and added >= requested
        )
        n = len([1 for r in expl_rows if r[0] and r[1] and r[1] >= r[0]])
        explanation_response_avg_hours = (total_seconds / n / 3600.0) if n else None
    else:
        explanation_response_avg_hours = None

    # Confirmation: average of (mediator_confirmed_at - mediator_invited_at) where both set
    conf_rows = (
        Mediation.query.filter(
            Mediation.mediator_id == mediator_id,
            Mediation.mediator_invited_at.isnot(None),
            Mediation.mediator_confirmed_at.isnot(None),
        )
        .with_entities(
            Mediation.mediator_invited_at,
            Mediation.mediator_confirmed_at,
        )
        .all()
    )
    if conf_rows:
        total_seconds = sum(
            (confirmed - invited).total_seconds()
            for invited, confirmed in conf_rows
            if invited and confirmed and confirmed >= invited
        )
        n = len([1 for r in conf_rows if r[0] and r[1] and r[1] >= r[0]])
        confirmation_response_avg_hours = (total_seconds / n / 3600.0) if n else None
    else:
        confirmation_response_avg_hours = None

    # Total value received: sum of mediator_payout_cents for payments with status=paid
    received_row = (
        MediationPayment.query.join(Mediation, MediationPayment.mediation_id == Mediation.id)
        .filter(Mediation.mediator_id == mediator_id, MediationPayment.status == "paid")
        .with_entities(db.func.coalesce(db.func.sum(MediationPayment.mediator_payout_cents), 0))
        .scalar()
    )
    total_value_received_cents = int(received_row) if received_row is not None else 0

    # Total expended: sum of MediatorBillingTransaction.amount_cents
    expended_row = (
        MediatorBillingTransaction.query.filter(MediatorBillingTransaction.user_id == mediator_id)
        .with_entities(db.func.coalesce(db.func.sum(MediatorBillingTransaction.amount_cents), 0))
        .scalar()
    )
    total_expended_cents = int(expended_row) if expended_row is not None else 0

    return {
        "mediations_opened": mediations_opened,
        "agreements_reached": agreements_reached,
        "explanation_response_avg_hours": explanation_response_avg_hours,
        "confirmation_response_avg_hours": confirmation_response_avg_hours,
        "total_value_received_cents": total_value_received_cents,
        "total_expended_cents": total_expended_cents,
    }


def get_all_mediator_ids() -> list:
    """User IDs of all users who have mediated at least one session."""
    ids = (
        Mediation.query.with_entities(Mediation.mediator_id)
        .distinct()
        .all()
    )
    return [x[0] for x in ids]


def format_duration_hours(hours: Optional[float]) -> str:
    if hours is None:
        return "—"
    if hours < 1:
        return f"{int(hours * 60)} min"
    if hours < 24:
        return f"{hours:.1f} h"
    days = hours / 24.0
    return f"{days:.1f} days"


def format_currency_cents(cents: int, currency: str = "EUR") -> str:
    """Format cents as money string."""
    return f"{cents / 100:.2f} {currency}"
