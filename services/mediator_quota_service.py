from __future__ import annotations

from datetime import datetime

from extensions import db
from models import MediatorProfile
from services.settings_service import (
    free_quota_default,
    pro_plan_quota_per_month,
)


def _current_period_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Return (start, end) for the current calendar month."""
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def ensure_profile(user_id: int) -> MediatorProfile:
    """Get or create MediatorProfile with default free quota."""
    profile = MediatorProfile.query.filter_by(user_id=user_id).first()
    if not profile:
        profile = MediatorProfile(
            user_id=user_id,
            free_quota_per_month=free_quota_default(),
        )
        db.session.add(profile)
        db.session.flush()
    return profile


def ensure_period(profile: MediatorProfile, now: datetime | None = None) -> None:
    """Ensure current_period_* and carry-over are up to date for this month."""
    now = now or datetime.utcnow()
    if not profile.current_period_start or not profile.current_period_end or not (
        profile.current_period_start <= now < profile.current_period_end
    ):
        # New period → carry over unused quota from previous period
        monthly_quota = profile.free_quota_per_month
        if profile.subscription_plan == "professional" and profile.subscription_status == "active":
            monthly_quota += pro_plan_quota_per_month()
        unused = max(0, monthly_quota - (profile.used_in_period or 0))
        profile.carry_over_balance = (profile.carry_over_balance or 0) + unused
        profile.used_in_period = 0
        profile.current_period_start, profile.current_period_end = _current_period_bounds(now)


def available_mediations(profile: MediatorProfile) -> int | float:
    """Return how many mediations the mediator can still take this month."""
    ensure_period(profile)
    if profile.subscription_plan == "enterprise" and profile.subscription_status == "active":
        return float("inf")
    monthly_quota = profile.free_quota_per_month
    if profile.subscription_plan == "professional" and profile.subscription_status == "active":
        monthly_quota += pro_plan_quota_per_month()
    total_quota = (profile.carry_over_balance or 0) + monthly_quota
    return max(0, total_quota - (profile.used_in_period or 0))


def consume_mediation(profile: MediatorProfile, count: int = 1) -> bool:
    """
    Try to consume `count` mediation slots from the mediator's quota.
    Returns True if successful, False if there is not enough quota.
    Caller is responsible for committing the session.
    """
    ensure_period(profile)
    avail = available_mediations(profile)
    if avail != float("inf") and avail < count:
        return False
    if avail != float("inf"):
        profile.used_in_period = (profile.used_in_period or 0) + count
    return True

