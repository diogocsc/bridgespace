"""
routes/mediation.py — Full mediation workflow
  - Create with upfront invitations
  - Invite more parties at any time
  - Join via token link
  - Four facilitative mediation phases
  - SocketIO live events
"""
from datetime import datetime

from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, abort, jsonify, send_file)
from flask_login import login_required, current_user
from flask_socketio import join_room, leave_room, emit

from extensions import db, socketio
from models import (
    Mediation, MediationParticipant, MediationInvitation,
    Perspective, AgendaPoint, Proposal, Agreement, AgreementSignature,
    Post, User, MediatorPayoutConfig, MediationPayment, MediationSession,
    MediatorProfile,
)
from services.ai_service import reformulate_nvc, extract_agenda_points, draft_agreement
from services.mediator_quota_service import (
    ensure_profile as ensure_mediator_profile,
    consume_mediation,
    available_mediations,
)
from services.settings_service import payments_enabled_for_mediation, is_participant_paid, platform_commission_percent

mediation_bp = Blueprint('mediation', __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_med(mediation_id):
    return Mediation.query.get_or_404(mediation_id)

def _require_participant(med):
    if not med.get_participant(current_user):
        abort(403)

def _maybe_start_pre_mediation(med):
    """
    If mediation is in 'new' state and mediator + all needed invitees have accepted,
    move it to 'open' (pre-mediation can effectively start).
    """
    # Only transition from explicit "new" state
    if getattr(med, "status", None) != "new":
        return
    # Mediator must have confirmed availability
    if not getattr(med, "mediator_confirmed_at", None):
        return
    # All required invitations must have a non-pending response (accepted/declined)
    # Optional invitations do not block the start of pre-mediation.
    invitations = getattr(med, "invitations", []) or []
    required_invitations = [
        inv for inv in invitations
        if getattr(inv, "is_required", True)
    ]
    has_pending = any(
        getattr(inv, "status", "pending") == "pending"
        for inv in required_invitations
    )
    if has_pending:
        return
    med.status = "open"

def _send_invites(med, invitees_raw, personal_message='', required=True):
    """Create MediationInvitation records and dispatch notifications."""
    contacts = [c.strip() for c in invitees_raw.split(',') if c.strip()]
    sent = skipped = 0
    for contact in contacts:
        existing = MediationInvitation.query.filter_by(
            mediation_id=med.id, contact=contact).first()
        if existing:
            skipped += 1
            continue
        contact_type = 'email' if '@' in contact else 'phone'
        inv = MediationInvitation(
            mediation_id=med.id,
            invited_by_id=current_user.id,
            contact=contact,
            contact_type=contact_type,
            is_required=required,
            personal_message=personal_message,
        )
        db.session.add(inv)
        db.session.flush()
        try:
            from services.notification import dispatch_invitation
            dispatch_invitation(inv, med, current_user)
        except Exception:
            pass
        sent += 1
    return sent, skipped


def _active_mediators():
    """
    Returns active mediator users.
    Mediators are users with role='mediator' and (optionally) an active MediatorProfile.
    """
    try:
        from models import MediatorProfile
        q = (
            User.query
            .filter(User.role == "mediator")
            .outerjoin(MediatorProfile, MediatorProfile.user_id == User.id)
            .filter((MediatorProfile.is_active == True) | (MediatorProfile.id == None))  # noqa: E712
            .order_by(User.display_name, User.username)
        )
        return q.all()
    except Exception:
        return User.query.filter(User.role == "mediator").all()


def _selection_count(mediator_user):
    """Return selection_count for mediator (0 if no profile)."""
    from models import MediatorProfile
    profile = MediatorProfile.query.filter_by(user_id=mediator_user.id).first()
    return (profile.selection_count or 0) if profile else 0


def _ranking(mediator_user):
    """Return ranking for mediator (100 if no profile)."""
    from models import MediatorProfile
    profile = MediatorProfile.query.filter_by(user_id=mediator_user.id).first()
    return (profile.ranking if profile is not None and profile.ranking is not None else 100.0)


def _pick_first_mediator(mediators):
    """
    First tentative: random choice favoring mediators who have been selected fewer times.
    Returns one User or None.
    """
    import random
    if not mediators:
        return None
    counts = [(_selection_count(m), m) for m in mediators]
    min_count = min(c[0] for c in counts)
    candidates = [m for c, m in counts if c == min_count]
    return random.choice(candidates)


def _mediators_with_available_quota(mediators):
    """Return list of mediators who have at least one available mediation slot (for request flow)."""
    result = []
    for m in mediators:
        if not getattr(m, "is_mediator", True):
            continue
        profile = ensure_mediator_profile(m.id)
        avail = available_mediations(profile)
        if avail == float("inf") or (isinstance(avail, (int, float)) and avail >= 1):
            result.append(m)
    return result


def _pick_next_mediator_by_ranking(mediators, exclude_user_id):
    """
    Second tentative: choose by highest ranking, excluding exclude_user_id.
    Returns one User or None.
    """
    candidates = [m for m in mediators if m.id != exclude_user_id]
    if not candidates:
        return None
    best = max(candidates, key=lambda m: _ranking(m))
    return best


def _invite_mediator_and_notify(med, mediator_user):
    """Set mediator on mediation, set invited_at, increment selection_count, send notification."""
    from datetime import datetime as dt
    from models import MediatorProfile
    med.mediator_id = mediator_user.id
    med.mediator_invited_at = dt.utcnow()
    med.mediator_confirmed_at = None
    profile = MediatorProfile.query.filter_by(user_id=mediator_user.id).first()
    if profile:
        profile.selection_count = (profile.selection_count or 0) + 1
    else:
        profile = MediatorProfile(user_id=mediator_user.id, is_active=True, selection_count=1)
        db.session.add(profile)
    try:
        from services.notification import send_mediator_availability_request
        send_mediator_availability_request(med, mediator_user)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@mediation_bp.route('/dashboard')
@login_required
def dashboard():
    # sessions where the user participates (as a party, not as mediator)
    participations = MediationParticipant.query.filter_by(
        user_id=current_user.id, is_active=True
    ).filter(MediationParticipant.role != 'mediator').all()
    as_participant = [p.mediation for p in participations]

    # sessions where the user is the mediator
    as_mediator = Mediation.query.filter_by(
        mediator_id=current_user.id
    ).all()

    # Mediator plan / quota overview (only for mediators)
    profile = None
    remaining = None
    total_quota = None
    is_unlimited = False
    bulk_pack_size_value = None
    if getattr(current_user, "is_mediator", False):
        profile = ensure_mediator_profile(current_user.id)
        remaining = available_mediations(profile)
        is_unlimited = remaining == float("inf")
        from services.settings_service import bulk_pack_size
        bulk_pack_size_value = bulk_pack_size()
        if not is_unlimited:
            from services.settings_service import pro_plan_quota_per_month
            monthly_quota = profile.free_quota_per_month
            if profile.subscription_plan == "professional" and profile.subscription_status == "active":
                monthly_quota += pro_plan_quota_per_month()
            total_quota = (profile.carry_over_balance or 0) + monthly_quota

    return render_template(
        'mediation/dashboard.html',
        as_mediator=as_mediator,
        as_participant=as_participant,
        mediator_profile=profile,
        mediator_remaining_quota=remaining,
        mediator_total_quota=total_quota,
        mediator_quota_unlimited=is_unlimited if profile else False,
        bulk_pack_size_value=bulk_pack_size_value,
    )


@mediation_bp.route('/mediation/metrics')
@login_required
def mediator_metrics():
    """Mediators see their own metrics. Non-mediators get 403."""
    if not current_user.is_mediator:
        abort(403)
    from services.mediator_metrics_service import get_mediator_metrics, format_duration_hours, format_currency_cents
    metrics = get_mediator_metrics(current_user.id)
    metrics["explanation_response_display"] = format_duration_hours(metrics["explanation_response_avg_hours"])
    metrics["confirmation_response_display"] = format_duration_hours(metrics["confirmation_response_avg_hours"])
    metrics["total_value_received_display"] = format_currency_cents(metrics["total_value_received_cents"])
    metrics["total_expended_display"] = format_currency_cents(metrics["total_expended_cents"])
    return render_template('mediation/mediator_metrics.html', metrics=metrics)


@mediation_bp.route('/mediation/payout-settings', methods=['GET', 'POST'])
@login_required
def payout_settings():
    """Mediators configure their payout accounts (IBAN / phone / Stripe Connect) and view transactions."""
    if not current_user.is_mediator:
        abort(403)
    config = MediatorPayoutConfig.query.filter_by(user_id=current_user.id).first()
    if request.method == 'POST':
        if not config:
            config = MediatorPayoutConfig(user_id=current_user.id)
            db.session.add(config)
        config.stripe_connect_account_id = request.form.get('stripe_connect_account_id', '').strip() or None
        config.iban = request.form.get('iban', '').strip() or None
        config.mobile_phone = request.form.get('mobile_phone', '').strip() or None
        db.session.commit()
        from services.translations import translate
        lang = getattr(current_user, 'preferred_language', 'en')
        flash(translate('payout_settings_saved', lang), 'success')
        return redirect(url_for('mediation.payout_settings'))
    # List payments for mediations where current user is the mediator
    transactions = (
        MediationPayment.query
        .join(Mediation, MediationPayment.mediation_id == Mediation.id)
        .filter(Mediation.mediator_id == current_user.id)
        .order_by(MediationPayment.created_at.desc())
        .limit(200)
        .all()
    )
    # List Stripe billing transactions (subscriptions, bulk packs) paid by this mediator
    from models import MediatorBillingTransaction
    billing_transactions = (
        MediatorBillingTransaction.query.filter_by(user_id=current_user.id)
        .order_by(MediatorBillingTransaction.created_at.desc())
        .limit(100)
        .all()
    )
    return render_template(
        'mediation/payout_settings.html',
        config=config,
        transactions=transactions,
        billing_transactions=billing_transactions,
    )


@mediation_bp.route('/mediation/payout-settings/mark-received/<int:payment_id>', methods=['POST'])
@login_required
def mark_payment_received(payment_id):
    """Mark a mediation payment as received by the mediator (payout done)."""
    if not current_user.is_mediator:
        abort(403)
    payment = MediationPayment.query.get_or_404(payment_id)
    mediation = payment.mediation
    if not mediation or mediation.mediator_id != current_user.id:
        abort(404)
    payment.mediator_received_at = datetime.utcnow()
    db.session.commit()
    next_url = request.form.get('next') or request.args.get('next')
    if next_url and next_url.startswith('/') and not next_url.startswith('//'):
        return redirect(next_url)
    return redirect(url_for('mediation.payout_settings'))


@mediation_bp.route('/mediation/<int:mediation_id>/payments')
@login_required
def mediation_payments(mediation_id):
    """Per-mediation payments list for the mediator to mark payments as received (any phase, structured or unstructured)."""
    med = _get_med(mediation_id)
    if med.mediator_id != current_user.id:
        abort(403)
    payments = (
        MediationPayment.query
        .filter_by(mediation_id=mediation_id)
        .order_by(MediationPayment.created_at.desc())
        .all()
    )
    # Participant-level view: all active non-mediator participants with paid flag
    from services.settings_service import is_participant_paid
    participant_rows = []
    for p in med.participants:
        if p.role == "mediator" or not getattr(p, "is_active", True):
            continue
        participant_rows.append({
            "id": p.id,
            "display_name": p.display_name or (p.user.display_name if p.user else (p.user.username if p.user else "")),
            "is_required": getattr(p, "is_required", True),
            "paid": is_participant_paid(med.id, p.id),
        })
    return render_template(
        'mediation/mediation_payments.html',
        mediation=med,
        payments=payments,
        participant_rows=participant_rows,
    )


@mediation_bp.route('/mediation/<int:mediation_id>/payments/mark', methods=['POST'])
@login_required
def mark_participant_payment(mediation_id):
    """Mediator toggles whether a given participant has paid (manual tracking)."""
    med = _get_med(mediation_id)
    if med.mediator_id != current_user.id:
        abort(403)
    participant_id = request.form.get('participant_id', type=int)
    paid_flag = request.form.get('paid', '0') == '1'
    if not participant_id:
        return redirect(url_for('mediation.mediation_payments', mediation_id=mediation_id))
    participant = MediationParticipant.query.filter_by(id=participant_id, mediation_id=med.id).first()
    if not participant or participant.role == "mediator":
        return redirect(url_for('mediation.mediation_payments', mediation_id=mediation_id))
    from datetime import datetime as dt
    from services.settings_service import is_participant_paid
    already_paid = is_participant_paid(med.id, participant.id)
    if paid_flag and not already_paid:
        # Create a manual payment record marked as paid
        amount_cents = med.price_per_party_cents or 0
        kind = med.pricing_type or "standard"
        if kind not in ("fixed", "donation", "probono"):
            kind = "standard"
        if kind == "fixed":
            kind = "standard"
        pay = MediationPayment(
            mediation_id=med.id,
            participant_id=participant.id,
            payer_user_id=participant.user_id,
            provider="manual",
            status="paid",
            kind=kind,
            amount_cents=amount_cents,
            platform_commission_cents=0,
            mediator_payout_cents=amount_cents,
            currency=med.currency or "EUR",
            created_at=dt.utcnow(),
            paid_at=dt.utcnow(),
        )
        db.session.add(pay)
        db.session.commit()
    elif (not paid_flag) and already_paid:
        # Mark any manual payments for this participant as cancelled
        manual_payments = MediationPayment.query.filter_by(
            mediation_id=med.id,
            participant_id=participant.id,
            provider="manual",
            status="paid",
        ).all()
        if manual_payments:
            for pay in manual_payments:
                pay.status = "cancelled"
            db.session.commit()
    return redirect(url_for('mediation.mediation_payments', mediation_id=mediation_id))


# ---------------------------------------------------------------------------
# Request mediation (default landing for regular users)
# ---------------------------------------------------------------------------

@mediation_bp.route('/request-mediation', methods=['GET', 'POST'])
@login_required
def request_mediation():
    # Mediators/admins can still request, but their primary flow is "create"
    mediators = _active_mediators()

    if request.method == 'POST':
        title        = request.form.get('title', '').strip()
        description  = request.form.get('description', '').strip()
        mode         = request.form.get('mode', 'async')
        # Mediation type (structured vs unstructured) is set by the mediator in pre-mediation
        mediation_type = 'structured'
        start_str    = request.form.get('start_date', '')
        invitees_raw = request.form.get('invitees', '')
        personal_msg = request.form.get('personal_message', '').strip()
        mediator_choice = request.form.get('mediator_choice', 'auto')

        if not title:
            flash('Please provide a title.', 'danger')
            return render_template('mediation/request.html', mediators=mediators)

        start_date = None
        if start_str:
            try:
                start_date = datetime.strptime(start_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                pass

        mediator_user = None
        if mediator_choice == 'auto':
            mediators_with_quota = _mediators_with_available_quota(mediators)
            mediator_user = _pick_first_mediator(mediators_with_quota) if mediators_with_quota else None
        else:
            try:
                mediator_id = int(mediator_choice)
                mediator_user = next((m for m in mediators if m.id == mediator_id), None)
            except (TypeError, ValueError):
                pass

        if not mediator_user:
            from services.translations import translate
            lang = getattr(current_user, 'preferred_language', 'en')
            try:
                from services.notification import send_no_mediators_quota_alert_to_admins
                send_no_mediators_quota_alert_to_admins(
                    title or request.form.get('title', '')[:200],
                    current_user.display_name or current_user.username,
                    no_mediators_at_all=len(mediators) == 0,
                )
            except Exception:
                pass
            flash(translate('no_mediators_available_try_later', lang), 'warning')
            return render_template('mediation/request.html', mediators=mediators)

        # When manual choice, ensure the chosen mediator has available quota
        if mediator_choice != 'auto' and mediator_user.is_mediator:
            mediators_with_quota = _mediators_with_available_quota(mediators)
            if not any(m.id == mediator_user.id for m in mediators_with_quota):
                from services.translations import translate
                lang = getattr(current_user, 'preferred_language', 'en')
                try:
                    from services.notification import send_no_mediators_quota_alert_to_admins
                    send_no_mediators_quota_alert_to_admins(
                        title or request.form.get('title', '')[:200],
                        current_user.display_name or current_user.username,
                        no_mediators_at_all=False,
                    )
                except Exception:
                    pass
                flash(translate('no_mediators_available_try_later', lang), 'warning')
                return render_template('mediation/request.html', mediators=mediators)

        # Enforce mediator quota before creating mediation
        if mediator_user.is_mediator:
            from services.translations import translate
            lang = getattr(current_user, 'preferred_language', 'en')
            profile = ensure_mediator_profile(mediator_user.id)
            if not consume_mediation(profile):
                flash(translate('mediator_quota_reached', lang), 'warning')
                return redirect(url_for('mediation.dashboard'))

        med = Mediation(
            title=title,
            description=description,
            mode=mode,
            mediation_type=mediation_type,
            mediator_id=mediator_user.id,
            creator_id=current_user.id,
            start_date=start_date,
            # When a user requests a mediation, it starts in state "new"
            # until mediator and all parties have accepted.
            status='new',
            phase='pre_mediation',
            mediator_attempt=1,
        )
        db.session.add(med)
        db.session.flush()

        _invite_mediator_and_notify(med, mediator_user)

        # Requester joins immediately (only if they are not also the mediator)
        if mediator_user.id != current_user.id:
            db.session.add(MediationParticipant(
                mediation_id=med.id,
                user_id=current_user.id,
                role='requester',
                display_name=current_user.display_name or current_user.username,
            ))

        # Mediator is also a participant for access (always)
        db.session.add(MediationParticipant(
            mediation_id=med.id,
            user_id=mediator_user.id,
            role='mediator',
            display_name=mediator_user.display_name or mediator_user.username,
            pre_mediation_acknowledged=True,
        ))

        required_raw = request.form.get('invitees_required', '') or ''
        optional_raw = request.form.get('invitees_optional', '') or ''
        total_sent = 0
        if required_raw.strip():
            sent_req, _ = _send_invites(med, required_raw, personal_msg, required=True)
            total_sent += sent_req
        if optional_raw.strip():
            sent_opt, _ = _send_invites(med, optional_raw, personal_msg, required=False)
            total_sent += sent_opt
        if total_sent:
            flash(f'{total_sent} invitation(s) sent.', 'info')

        db.session.commit()
        from services.translations import translate
        lang = getattr(current_user, 'preferred_language', 'en')
        flash(translate('mediation_requested_48h', lang), 'success')
        return redirect(url_for('mediation.session', mediation_id=med.id))

    return render_template('mediation/request.html', mediators=mediators)



# ---------------------------------------------------------------------------
# Create  (with optional upfront invitations)
# ---------------------------------------------------------------------------

@mediation_bp.route('/mediation/new', methods=['GET', 'POST'])
@login_required
def create_mediation():
    if not (current_user.is_mediator or current_user.is_admin):
        from services.translations import translate
        lang = getattr(current_user, 'preferred_language', 'en')
        flash(translate('only_mediators_can_create', lang), 'warning')
        return redirect(url_for('mediation.request_mediation'))

    mediators = _active_mediators()

    if request.method == 'POST':
        title        = request.form.get('title', '').strip()
        description  = request.form.get('description', '').strip()
        mode         = request.form.get('mode', 'async')
        # Mediation type (structured vs unstructured) is set by the mediator in pre-mediation
        mediation_type = 'structured'
        start_str    = request.form.get('start_date', '')
        invitees_raw = request.form.get('invitees', '')
        personal_msg = request.form.get('personal_message', '').strip()
        mediator_choice = request.form.get('mediator_choice', 'self')

        if not title:
            flash('Please provide a title.', 'danger')
            return render_template('mediation/create.html', mediators=mediators)

        start_date = None
        if start_str:
            try:
                start_date = datetime.strptime(start_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                pass

        mediator_user = None
        if mediator_choice in ('self', '', None):
            mediator_user = current_user
        elif mediator_choice == 'auto':
            mediator_user = _pick_first_mediator(mediators)
        else:
            try:
                mediator_id = int(mediator_choice)
                mediator_user = next((m for m in mediators if m.id == mediator_id), None)
            except (TypeError, ValueError):
                pass

        if not mediator_user:
            flash('No mediator is available yet. Please contact support or try again later.', 'danger')
            return render_template('mediation/create.html', mediators=mediators)

        # Enforce mediator quota before creating mediation when mediator is a real mediator
        if mediator_user.is_mediator:
            from services.translations import translate
            lang = getattr(current_user, 'preferred_language', 'en')
            profile = ensure_mediator_profile(mediator_user.id)
            if not consume_mediation(profile):
                flash(translate('mediator_quota_reached', lang), 'warning')
                return redirect(url_for('mediation.dashboard'))

        med = Mediation(
            title=title,
            description=description,
            mode=mode,
            mediation_type=mediation_type,
            mediator_id=mediator_user.id,
            creator_id=current_user.id,
            start_date=start_date,
            # Mediator-created mediations start directly as active ("open")
            status='open',
            phase='pre_mediation',
            mediator_attempt=1,
        )
        db.session.add(med)
        db.session.flush()

        if mediator_user.id != current_user.id:
            _invite_mediator_and_notify(med, mediator_user)

        # Creator joins as requester only if they are not also the mediator
        if mediator_user.id != current_user.id:
            db.session.add(MediationParticipant(
                mediation_id=med.id,
                user_id=current_user.id,
                role='requester',
                display_name=current_user.display_name or current_user.username,
            ))

        # Mediator is always present as a participant with role 'mediator'
        db.session.add(MediationParticipant(
            mediation_id=med.id,
            user_id=mediator_user.id,
            role='mediator',
            display_name=mediator_user.display_name or mediator_user.username,
            pre_mediation_acknowledged=True,
        ))

        required_raw = request.form.get('invitees_required', '') or ''
        optional_raw = request.form.get('invitees_optional', '') or ''
        total_sent = 0
        if required_raw.strip():
            sent_req, _ = _send_invites(med, required_raw, personal_msg, required=True)
            total_sent += sent_req
        if optional_raw.strip():
            sent_opt, _ = _send_invites(med, optional_raw, personal_msg, required=False)
            total_sent += sent_opt
        if total_sent:
            flash(f'{total_sent} invitation(s) sent.', 'info')

        db.session.commit()

        if mediator_user.id != current_user.id:
            flash('Mediation created. The assigned mediator has been notified and must confirm within 48 hours.', 'success')
        elif mediation_type == 'unstructured':
            flash('Mediation created! Pre-mediation has started. After everyone is ready, advance to the conversation.', 'success')
        else:
            flash('Mediation created! Pre-mediation has started.', 'success')
        return redirect(url_for('mediation.pre_mediation', mediation_id=med.id))

    return render_template('mediation/create.html', mediators=mediators)


# ---------------------------------------------------------------------------
# Invite page  (available at any phase)
# ---------------------------------------------------------------------------

@mediation_bp.route('/mediation/<int:mediation_id>/invite', methods=['GET', 'POST'])
@login_required
def invite_page(mediation_id):
    med = _get_med(mediation_id)
    _require_participant(med)

    if request.method == 'POST':
        invitees_raw = request.form.get('invitees', '')
        personal_msg = request.form.get('personal_message', '').strip()

        required_raw = request.form.get('invitees_required', '') or ''
        optional_raw = request.form.get('invitees_optional', '') or ''

        if not required_raw.strip() and not optional_raw.strip():
            flash('Please add at least one email or phone number.', 'danger')
        else:
            total_sent = total_skipped = 0
            if required_raw.strip():
                sent_req, skipped_req = _send_invites(med, required_raw, personal_msg, required=True)
                total_sent += sent_req
                total_skipped += skipped_req
            if optional_raw.strip():
                sent_opt, skipped_opt = _send_invites(med, optional_raw, personal_msg, required=False)
                total_sent += sent_opt
                total_skipped += skipped_opt
            db.session.commit()
            msg = f'{total_sent} invitation(s) sent.'
            if total_skipped:
                msg += f' {total_skipped} already invited.'
            flash(msg, 'success' if total_sent else 'info')
        return redirect(url_for('mediation.invite_page', mediation_id=mediation_id))

    return render_template('mediation/invite.html', mediation=med)


# ---------------------------------------------------------------------------
# Join via token link
# ---------------------------------------------------------------------------

@mediation_bp.route('/join/<token>')
def join_via_invite(token):
    inv = MediationInvitation.query.filter_by(token=token).first()
    if inv:
        return _do_join(inv.mediation, inv=inv)

    med = Mediation.query.filter_by(invite_token=token).first()
    if med:
        return _do_join(med, inv=None)

    abort(404)


def _do_join(med, inv=None):
    if not current_user.is_authenticated:
        next_url = url_for('mediation.join_via_invite',
                           token=inv.token if inv else med.invite_token)
        # If this invite was sent to an email that already belongs to a user, ask to log in.
        # Otherwise, redirect to registration so a new user can sign up and then join.
        target_endpoint = 'auth.login'
        try:
            from models import User
            if inv and '@' in (inv.contact or ''):
                existing = User.query.filter_by(email=inv.contact.lower()).first()
                if not existing:
                    target_endpoint = 'auth.register'
        except Exception:
            target_endpoint = 'auth.login'
        if target_endpoint == 'auth.login':
            flash('Please log in to accept your invitation.', 'info')
        else:
            flash('Please register to accept your invitation.', 'info')
        return redirect(url_for(target_endpoint, next=next_url))

    if med.get_participant(current_user):
        flash('You are already a participant in this mediation.', 'info')
        return redirect(url_for('mediation.session', mediation_id=med.id))

    db.session.add(MediationParticipant(
        mediation_id=med.id,
        user_id=current_user.id,
        role='respondent',
        display_name=current_user.display_name or current_user.username,
        is_required=getattr(inv, 'is_required', True) if inv else True,
    ))
    if inv:
        inv.status = 'accepted'
        inv.responded_at = datetime.utcnow()

    # Joining as a party may complete the acceptance loop for starting pre-mediation
    _maybe_start_pre_mediation(med)
    db.session.commit()
    flash(f'You have joined: {med.title}', 'success')
    return redirect(url_for('mediation.session', mediation_id=med.id))


# ---------------------------------------------------------------------------
# Session overview — redirect by type/phase: unstructured in pre_mediation → pre_mediation; unstructured else → view; structured → current phase
# ---------------------------------------------------------------------------

@mediation_bp.route('/mediation/<int:mediation_id>')
@login_required
def session(mediation_id):
    med = _get_med(mediation_id)
    _require_participant(med)
    if getattr(med, 'mediation_type', 'structured') == 'unstructured':
        if med.phase == 'pre_mediation':
            return redirect(url_for('mediation.pre_mediation', mediation_id=mediation_id))
        return redirect(url_for('mediation.view_mediation', mediation_id=mediation_id))
    return redirect(url_for(f'mediation.{med.phase}', mediation_id=mediation_id))


# ---------------------------------------------------------------------------
# Unstructured mediation — posts view (free-flow)
# ---------------------------------------------------------------------------

@mediation_bp.route('/mediation/<int:mediation_id>/view', methods=['GET', 'POST'])
@login_required
def view_mediation(mediation_id):
    """Unstructured mediation: posts feed; mediator can mark agreement post and close with outcome."""
    med = _get_med(mediation_id)
    _require_participant(med)
    if getattr(med, 'mediation_type', 'structured') != 'unstructured':
        return redirect(url_for('mediation.session', mediation_id=mediation_id))

    # POST: mark post as agreement or close mediation
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'set_agreement' and med.mediator_id == current_user.id and med.status != 'closed':
            post_id = request.form.get('post_id', type=int)
            if post_id:
                post = Post.query.filter_by(id=post_id, mediation_id=med.id).first()
                if post:
                    med.agreement_post_id = post.id
                    db.session.commit()
                    flash('Post marked as the agreement.', 'success')
        elif action == 'close_mediation' and med.mediator_id == current_user.id and med.status != 'closed':
            outcome = request.form.get('close_outcome', '').strip()
            justification = request.form.get('close_justification', '').strip()
            if outcome in ('agreement_reached', 'agreement_not_reached'):
                if not med.required_payments_complete():
                    from services.translations import translate
                    lang = getattr(current_user, 'preferred_language', 'en')
                    flash(translate('cannot_close_payment_pending', lang), 'warning')
                    return redirect(url_for('mediation.view_mediation', mediation_id=mediation_id))
                med.status = 'closed'
                med.end_date = datetime.utcnow()
                med.close_outcome = outcome
                med.close_justification = justification or None
                db.session.commit()
                try:
                    from services.notification import send_mediation_status_change
                    send_mediation_status_change(med, 'closed')
                except Exception:
                    pass
                flash('Mediation closed.', 'success')
            else:
                flash('Please select an outcome (agreement reached or not reached).', 'danger')
        return redirect(url_for('mediation.view_mediation', mediation_id=mediation_id))

    # Order posts by created_at ascending for chronological feed
    posts = sorted(med.posts, key=lambda p: p.created_at or datetime(1970, 1, 1))
    participant = med.get_participant(current_user)
    show_consent_search = (
        med.status == 'closed'
        and getattr(med, 'close_outcome', None) == 'agreement_reached'
        and participant
        and getattr(participant, 'role', None) != 'mediator'
    )
    return render_template(
        'mediation/view.html',
        mediation=med,
        posts=posts,
        participant=participant,
        show_consent_search=show_consent_search,
        user_lang=getattr(current_user, 'preferred_language', 'en'),
        is_unstructured=True,
    )


# ---------------------------------------------------------------------------
# Phase 1 – Perspectives  (newest → oldest)
# ---------------------------------------------------------------------------

@mediation_bp.route('/mediation/<int:mediation_id>/perspectives', methods=['GET', 'POST'])
@login_required
def perspectives(mediation_id):
    med = _get_med(mediation_id)
    _require_participant(med)

    if request.method == 'POST':
        content = request.form.get('content', '').strip()
        if not content:
            flash('Perspective cannot be empty.', 'danger')
        else:
            used_ai = request.form.get('used_ai_reformulation') == '1'
            try:
                reformulated = reformulate_nvc(content, med.description or '')
            except Exception:
                reformulated = None
            db.session.add(Perspective(
                mediation_id=med.id,
                author_id=current_user.id,
                content=content,
                reformulated=reformulated,
                used_ai_reformulation=used_ai,
            ))
            db.session.commit()
            flash('Your perspective has been added.', 'success')

    return render_template('mediation/perspectives.html', mediation=med)


# ---------------------------------------------------------------------------
# Phase 0 – Pre-mediation (process explanation + acknowledgement)
# ---------------------------------------------------------------------------

@mediation_bp.route('/mediation/<int:mediation_id>/pre_mediation', methods=['GET', 'POST'])
@login_required
def pre_mediation(mediation_id):
    med = _get_med(mediation_id)
    _require_participant(med)
    participant = med.get_participant(current_user)

    if request.method == 'POST':
        action = request.form.get('action')

        # Mediator can edit the pre-mediation text and pricing
        if current_user.id == med.mediator_id and action == 'save_text':
            new_text = request.form.get('pre_mediation_text', '').strip()
            med.pre_mediation_text = new_text
            if new_text and not med.explanation_added_at:
                from datetime import datetime as dt
                med.explanation_added_at = dt.utcnow()
            db.session.commit()
            flash('Pre-mediation explanation updated.', 'success')
            if new_text:
                try:
                    from services.notification import send_pre_mediation_confirmation_request
                    send_pre_mediation_confirmation_request(med)
                except Exception:
                    pass

        elif current_user.id == med.mediator_id and action == 'set_price':
            old_pricing_type = getattr(med, 'pricing_type', None) or 'fixed'
            pricing_type = (request.form.get('pricing_type') or 'fixed').strip().lower()
            if pricing_type not in ('fixed', 'donation', 'probono'):
                pricing_type = 'fixed'
            med.pricing_type = pricing_type
            if pricing_type == 'probono':
                med.price_per_party_cents = 0
                med.stripe_product_id = None
                med.stripe_price_id = None
            else:
                raw = (request.form.get('price_per_party', '') or '').strip().replace(',', '.')
                try:
                    value = float(raw)
                    if value < 0:
                        raise ValueError()
                    med.price_per_party_cents = int(round(value * 100))
                except Exception:
                    flash('Invalid price. Example: 50 or 50.00', 'danger')
                    return redirect(url_for('mediation.pre_mediation', mediation_id=med.id))
                med.stripe_product_id = None
                med.stripe_price_id = None
            db.session.commit()
            # If mediator switches to pro bono, refund one mediation slot (pro bono does not count to quota)
            if old_pricing_type != 'probono' and med.pricing_type == 'probono':
                try:
                    profile = MediatorProfile.query.filter_by(user_id=med.mediator_id).first()
                    if profile and (profile.used_in_period or 0) > 0:
                        profile.used_in_period = max(0, (profile.used_in_period or 0) - 1)
                        db.session.commit()
                except Exception:
                    pass
            if (med.pricing_type or '') == 'fixed' and (med.price_per_party_cents or 0) > 0:
                try:
                    ensure_mediation_stripe_price(med)
                except Exception:
                    pass
            flash('Price and payment type updated.', 'success')

        elif current_user.id == med.mediator_id and action == 'set_mediation_type':
            new_type = (request.form.get('mediation_type') or 'structured').strip().lower()
            if new_type in ('structured', 'unstructured'):
                med.mediation_type = new_type
                db.session.commit()
                from services.translations import translate
                lang = getattr(current_user, 'preferred_language', 'en')
                flash(translate('mediation_type_updated', lang) or 'Mediation type updated.', 'success')
            else:
                flash('Invalid mediation type.', 'danger')
            return redirect(url_for('mediation.pre_mediation', mediation_id=med.id))

        # Parties acknowledge that they read the process (only when mediator has added explanation)
        elif action == 'ack' and participant:
            if not (med.pre_mediation_text and med.pre_mediation_text.strip()):
                flash('You can only mark as read after the mediator has added an explanation.', 'warning')
            else:
                participant.pre_mediation_acknowledged = True
                db.session.commit()
                flash('Thanks — you acknowledged the pre-mediation explanation.', 'success')

        # Mediator (live mode) creates a scheduled session with start and optional end
        elif current_user.id == med.mediator_id and action == 'create_session' and med.mode == 'live':
            title = (request.form.get('session_title') or '').strip()
            start_str = (request.form.get('session_start') or '').strip()
            end_str = (request.form.get('session_end') or '').strip()
            from datetime import datetime as dt
            try:
                start_at = dt.strptime(start_str, '%Y-%m-%dT%H:%M')
                end_at = None
                if end_str:
                    end_at = dt.strptime(end_str, '%Y-%m-%dT%H:%M')
                    if end_at <= start_at:
                        raise ValueError()
                sess = MediationSession(
                    mediation_id=med.id,
                    created_by_id=current_user.id,
                    title=title or None,
                    start_at=start_at,
                    end_at=end_at,
                )
                db.session.add(sess)
                db.session.commit()
                flash('Live session scheduled.', 'success')
            except Exception:
                flash('Invalid session dates. Please check start and end time.', 'danger')
                return redirect(url_for('mediation.pre_mediation', mediation_id=med.id))

        # Mediator (live mode) updates an existing scheduled session
        elif current_user.id == med.mediator_id and action == 'update_session' and med.mode == 'live':
            session_id = request.form.get('session_id', type=int)
            if not session_id:
                flash('Invalid session.', 'danger')
                return redirect(url_for('mediation.pre_mediation', mediation_id=med.id))
            sess = MediationSession.query.filter_by(id=session_id, mediation_id=med.id).first()
            if not sess:
                flash('Session not found.', 'danger')
                return redirect(url_for('mediation.pre_mediation', mediation_id=med.id))
            title = (request.form.get('session_title') or '').strip()
            start_str = (request.form.get('session_start') or '').strip()
            end_str = (request.form.get('session_end') or '').strip()
            from datetime import datetime as dt
            try:
                start_at = dt.strptime(start_str, '%Y-%m-%dT%H:%M')
                end_at = None
                if end_str:
                    end_at = dt.strptime(end_str, '%Y-%m-%dT%H:%M')
                    if end_at <= start_at:
                        raise ValueError()
                sess.title = title or None
                sess.start_at = start_at
                sess.end_at = end_at
                db.session.commit()
                flash('Session updated.', 'success')
            except Exception:
                flash('Invalid session dates. Please check start and end time.', 'danger')
                return redirect(url_for('mediation.pre_mediation', mediation_id=med.id))

        return redirect(url_for('mediation.pre_mediation', mediation_id=med.id))

    # Compute simple flags for template
    paid = False
    if participant:
        paid = is_participant_paid(med.id, participant.id)

    # Sessions (for live mode): order by start time
    sessions = sorted(
        getattr(med, "sessions", []) or [],
        key=lambda s: s.start_at or datetime(1970, 1, 1),
    )

    # Build participant list for display: one row per user, with roles and requester in parallel
    seen_user_ids = set()
    participants_display = []
    for p in med.participants:
        if p.user_id in seen_user_ids:
            continue
        seen_user_ids.add(p.user_id)
        roles = []
        ack = False
        for q in med.participants:
            if q.user_id != p.user_id:
                continue
            roles.append(q.role)
            if q.pre_mediation_acknowledged:
                ack = True
        u = p.user
        display_name = p.display_name or (u.display_name if u else (u.username if u else ""))
        participants_display.append({
            "user_id": p.user_id,
            "display_name": display_name,
            "roles": roles,
            "is_creator": med.creator_id == p.user_id,
            "pre_mediation_acknowledged": ack,
        })

    # Pending invitations (contacts who have not yet accepted or declined)
    pending_invitations = [
        {
            "contact": inv.contact,
            "contact_type": getattr(inv, "contact_type", "email"),
            "is_required": getattr(inv, "is_required", True),
            "status": getattr(inv, "status", "pending"),
        }
        for inv in getattr(med, "invitations", []) or []
        if getattr(inv, "status", "pending") == "pending"
    ]

    # Number of parties obliged to pay (required only); commission/total are based on this
    required_user_ids = {
        p.user_id for p in med.participants
        if p.role != 'mediator' and getattr(p, 'is_required', True)
    }
    n_required_parties = len(required_user_ids)
    participant_is_required = (
        participant and participant.role != 'mediator'
        and getattr(participant, 'is_required', True)
    )

    # Mediator payment details (IBAN / mobile) to show to parties for bank/mobile transfer
    mediator_payout_details = None
    if med.mediator_id:
        payout_config = MediatorPayoutConfig.query.filter_by(user_id=med.mediator_id).first()
        if payout_config and (payout_config.iban or payout_config.mobile_phone):
            mediator_payout_details = {
                "iban": (payout_config.iban or "").strip() or None,
                "mobile_phone": (payout_config.mobile_phone or "").strip() or None,
            }

    return render_template(
        'mediation/pre_mediation.html',
        mediation=med,
        participant=participant,
        participants_display=participants_display,
        pending_invitations=pending_invitations,
        n_required_parties=n_required_parties,
        participant_is_required=participant_is_required,
        payments_enabled=payments_enabled_for_mediation(med),
        is_paid=paid,
        sessions=sessions,
        platform_commission_percent=platform_commission_percent(),
        mediator_payout_details=mediator_payout_details,
    )


@mediation_bp.route('/mediation/<int:mediation_id>/ask-explanation', methods=['POST'])
@login_required
def ask_mediator_explanation(mediation_id):
    """Participant requests the mediator to add a process explanation; mediator receives an email."""
    from services.translations import translate
    lang = getattr(current_user, 'preferred_language', 'en')
    med = _get_med(mediation_id)
    _require_participant(med)
    participant = med.get_participant(current_user)
    if not participant or participant.role == 'mediator':
        flash(translate('ask_explanation_only_parties', lang), 'warning')
        return redirect(url_for('mediation.pre_mediation', mediation_id=mediation_id))
    if med.pre_mediation_text and med.pre_mediation_text.strip():
        flash(translate('ask_explanation_already_available', lang), 'info')
        return redirect(url_for('mediation.pre_mediation', mediation_id=mediation_id))
    if not med.explanation_requested_at:
        from datetime import datetime as dt
        med.explanation_requested_at = dt.utcnow()
        db.session.commit()
    try:
        from services.notification import send_ask_mediator_explanation_email
        send_ask_mediator_explanation_email(med, current_user)
        flash(translate('ask_explanation_notified', lang), 'success')
    except Exception:
        flash(translate('ask_explanation_send_error', lang), 'danger')
    return redirect(url_for('mediation.pre_mediation', mediation_id=mediation_id))


@mediation_bp.route('/mediation/<int:mediation_id>/confirm-availability', methods=['GET', 'POST'])
@login_required
def confirm_mediator_availability(mediation_id):
    """Mediator confirms availability within 48h (linked from email)."""
    med = _get_med(mediation_id)
    if med.mediator_id != current_user.id:
        flash('Only the assigned mediator can confirm availability.', 'danger')
        return redirect(url_for('mediation.session', mediation_id=mediation_id))
    if med.mediator_confirmed_at:
        flash('You have already confirmed your availability for this mediation.', 'info')
        return redirect(url_for('mediation.pre_mediation', mediation_id=mediation_id))
    if request.method == 'POST':
        from datetime import datetime as dt
        med.mediator_confirmed_at = dt.utcnow()
        profile = getattr(current_user, 'mediator_profile', None)
        if profile:
            profile.times_confirmed = (profile.times_confirmed or 0) + 1
        # Mediator confirmation may complete the acceptance loop for starting pre-mediation
        _maybe_start_pre_mediation(med)
        db.session.commit()
        flash('Thank you — your availability is confirmed.', 'success')
        return redirect(url_for('mediation.pre_mediation', mediation_id=mediation_id))
    return render_template('mediation/confirm_availability.html', mediation=med)


@mediation_bp.route('/mediation/<int:mediation_id>/payment/success')
@login_required
def payment_success(mediation_id):
    med = _get_med(mediation_id)
    _require_participant(med)
    payment_id = request.args.get('payment_id', type=int)
    session_id = request.args.get('session_id')  # for Stripe; currently not re-validated
    if payment_id:
        # For now we trust the redirect; in production you would verify via Stripe webhooks.
        mark_stripe_paid(payment_id)
        flash('Payment recorded. Thank you.', 'success')
    return redirect(url_for('mediation.pre_mediation', mediation_id=med.id))


# ---------------------------------------------------------------------------
# Phase 2 – Agenda
# ---------------------------------------------------------------------------

@mediation_bp.route('/mediation/<int:mediation_id>/agenda', methods=['GET', 'POST'])
@login_required
def agenda(mediation_id):
    med = _get_med(mediation_id)
    _require_participant(med)

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'ai_suggest':
            try:
                lang = getattr(current_user, "preferred_language", "en") or "en"
                suggestions = extract_agenda_points([p.content for p in med.perspectives], lang=lang)
                for i, s in enumerate(suggestions):
                    db.session.add(AgendaPoint(
                        mediation_id=med.id,
                        title=s['title'],
                        description=s.get('description', ''),
                        order=i,
                        ai_generated=True,
                    ))
                db.session.commit()
                flash(f'{len(suggestions)} agenda point(s) suggested by AI.', 'info')
            except Exception as e:
                flash(f'AI suggestion failed: {e}', 'danger')

        elif action == 'add_manual':
            title = request.form.get('title', '').strip()
            if title:
                count = AgendaPoint.query.filter_by(mediation_id=med.id).count()
                db.session.add(AgendaPoint(
                    mediation_id=med.id,
                    title=title,
                    description=request.form.get('description', '').strip(),
                    order=count,
                ))
                db.session.commit()
                flash('Agenda point added.', 'success')

        elif action == 'delete':
            ap = AgendaPoint.query.get_or_404(request.form.get('agenda_point_id', type=int))
            db.session.delete(ap)
            db.session.commit()

    return render_template('mediation/agenda.html', mediation=med)


# ---------------------------------------------------------------------------
# Phase 3 – Proposals
# ---------------------------------------------------------------------------

@mediation_bp.route('/mediation/<int:mediation_id>/proposals', methods=['GET', 'POST'])
@login_required
def proposals(mediation_id):
    med = _get_med(mediation_id)
    _require_participant(med)

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add_proposal':
            ap_id   = request.form.get('agenda_point_id', type=int)
            content = request.form.get('content', '').strip()
            if content and ap_id:
                try:
                    reformulated = reformulate_nvc(content)
                except Exception:
                    reformulated = None
                db.session.add(Proposal(
                    agenda_point_id=ap_id,
                    author_id=current_user.id,
                    content=content,
                    reformulated=reformulated,
                ))
                db.session.commit()
                flash('Proposal submitted.', 'success')

        elif action == 'update_status':
            if med.mediator_id != current_user.id:
                flash('Only the mediator can change proposal status.', 'danger')
            else:
                prop = Proposal.query.get_or_404(request.form.get('proposal_id', type=int))
                status = request.form.get('status')
                if status in Proposal.STATUS:
                    prop.status = status
                    db.session.commit()

    return render_template('mediation/proposals.html', mediation=med)


# ---------------------------------------------------------------------------
# Phase 4 – Agreement
# ---------------------------------------------------------------------------

@mediation_bp.route('/mediation/<int:mediation_id>/agreement', methods=['GET', 'POST'])
@login_required
def agreement(mediation_id):
    med = _get_med(mediation_id)
    _require_participant(med)
    agr = med.agreement

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'ai_draft':
            try:
                accepted = [
                    {'agenda_point': ap.title,
                     'proposals': [p.content for p in ap.proposals if p.status == 'accepted']}
                    for ap in med.agenda_points
                    if any(p.status == 'accepted' for p in ap.proposals)
                ]
                lang = getattr(current_user, "preferred_language", "en") or "en"
                content = draft_agreement(med.title, accepted, lang=lang)
                if agr:
                    agr.content = content
                else:
                    agr = Agreement(mediation_id=med.id, content=content)
                    db.session.add(agr)
                db.session.commit()
                flash('Agreement drafted by AI.', 'info')
            except Exception as e:
                flash(f'AI drafting failed: {e}', 'danger')

        elif action == 'save_draft':
            content = request.form.get('content', '').strip()
            if content:
                if agr:
                    agr.content = content
                else:
                    agr = Agreement(mediation_id=med.id, content=content)
                    db.session.add(agr)
                db.session.commit()
                flash('Agreement saved.', 'success')

        elif action == 'sign':
            if not agr:
                flash('No agreement to sign yet.', 'danger')
            else:
                already = AgreementSignature.query.filter_by(
                    agreement_id=agr.id, user_id=current_user.id).first()
                if already:
                    flash('You have already signed this agreement.', 'info')
                else:
                    db.session.add(AgreementSignature(
                        agreement_id=agr.id, user_id=current_user.id))
                    db.session.commit()
                    flash('You have signed the agreement.', 'success')

    # For consent UI: mediation closed with agreement, and current user is a party
    participant = med.get_participant(current_user)
    ends_in_agreement = (
        med.status == 'closed'
        and (
            getattr(med, 'close_outcome', None) == 'agreement_reached'
            or (agr and (agr.content or '').strip())
        )
    )
    show_consent_search = (
        ends_in_agreement
        and participant
        and getattr(participant, 'role', None) != 'mediator'
    )
    return render_template(
        'mediation/agreement.html',
        mediation=med,
        agreement=med.agreement,
        participant=participant,
        show_consent_search=show_consent_search,
    )


@mediation_bp.route('/mediation/<int:mediation_id>/agreement.pdf')
@login_required
def download_agreement_pdf(mediation_id):
    """Export the current agreement text to a simple PDF."""
    med = _get_med(mediation_id)
    _require_participant(med)
    agr = med.agreement
    # Determine agreement text:
    # - Structured: use Agreement.content when available
    # - Unstructured: if no Agreement record, use the marked agreement post content
    content_text = None
    if agr and (agr.content or "").strip():
        content_text = (agr.content or "").strip()
    elif getattr(med, "mediation_type", "structured") == "unstructured" and med.agreement_post:
        try:
            body = med.agreement_post.get_display_content()
        except Exception:
            body = med.agreement_post.original_content or ""
        if body:
            content_text = body.strip()
    if not content_text:
        flash("No agreement drafted yet.", "warning")
        return redirect(url_for("mediation.agreement", mediation_id=mediation_id))
    try:
        from io import BytesIO
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet

        def _normalize_for_pdf(text: str) -> str:
            """
            Normalize Unicode punctuation so it renders correctly in basic PDF fonts.
            In particular, replace various dash characters with a standard hyphen-minus.
            """
            if not text:
                return ""
            replacements = {
                "\u2010": "-",  # hyphen
                "\u2011": "-",  # non-breaking hyphen
                "\u2012": "-",  # figure dash
                "\u2013": "-",  # en dash
                "\u2014": "-",  # em dash
                "\u2212": "-",  # minus sign
                "\u00A0": " ",  # non-breaking space
            }
            for src, dst in replacements.items():
                text = text.replace(src, dst)
            return text

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=40,
            rightMargin=40,
            topMargin=60,
            bottomMargin=40,
        )
        styles = getSampleStyleSheet()
        title_style = styles["Heading1"]
        title_style.fontName = "Helvetica-Bold"
        title_style.fontSize = 16
        title_style.leading = 20

        body_style = styles["Normal"]
        body_style.fontName = "Helvetica"
        body_style.fontSize = 10
        body_style.leading = 14

        story = []
        title_text = med.title or "Mediation agreement"
        story.append(Paragraph(_normalize_for_pdf(title_text), title_style))
        story.append(Spacer(1, 12))

        content = _normalize_for_pdf(content_text)
        # Split paragraphs by blank lines and preserve line breaks within each paragraph
        paragraphs = [p for p in content.split("\n\n") if p.strip()] or [""]
        for para in paragraphs:
            safe_para = para.replace("\n", "<br/>")
            story.append(Paragraph(safe_para, body_style))
            story.append(Spacer(1, 8))

        doc.build(story)
        buffer.seek(0)
        # Build a filename that includes a slugified version of the mediation title
        import re
        raw_title = med.title or ""
        slug = re.sub(r"[^A-Za-z0-9]+", "-", raw_title).strip("-") or "agreement"
        filename = f"agreement-{slug}-{med.id}.pdf"
        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype="application/pdf",
        )
    except Exception as exc:
        flash(f"Could not generate PDF: {exc}", "danger")
        return redirect(url_for("mediation.agreement", mediation_id=mediation_id))


@mediation_bp.route('/mediation/<int:mediation_id>/consent-search', methods=['POST'])
@login_required
def consent_search_share(mediation_id):
    """Party gives or revokes consent for this mediation to be shared in search (anonymised)."""
    med = _get_med(mediation_id)
    _require_participant(med)
    participant = med.get_participant(current_user)
    if not participant or getattr(participant, 'role', None) == 'mediator':
        flash('Only parties can give consent for search sharing.', 'warning')
        return redirect(url_for('mediation.session', mediation_id=mediation_id))
    ends_in_agreement = (
        med.status == 'closed'
        and (
            getattr(med, 'close_outcome', None) == 'agreement_reached'
            or (med.agreement and (med.agreement.content or '').strip())
        )
    )
    if not ends_in_agreement:
        flash('Consent for search applies only to mediations closed with an agreement.', 'warning')
        return redirect(url_for('mediation.session', mediation_id=mediation_id))

    consent = request.form.get('consent')
    if consent in ('1', 'yes', 'true'):
        participant.consent_search_share = True
    elif consent in ('0', 'no', 'false'):
        participant.consent_search_share = False
    else:
        flash('Invalid consent value.', 'warning')
        return redirect(url_for('mediation.session', mediation_id=mediation_id))
    db.session.commit()
    from services.translations import translate
    lang = getattr(current_user, 'preferred_language', 'en')
    flash(translate('consent_search_saved', lang), 'success')
    return redirect(url_for('mediation.session', mediation_id=mediation_id))


# ---------------------------------------------------------------------------
# Advance phase (mediator only)
# ---------------------------------------------------------------------------

@mediation_bp.route('/mediation/<int:mediation_id>/advance', methods=['POST'])
@login_required
def advance_phase(mediation_id):
    med = _get_med(mediation_id)
    if med.mediator_id != current_user.id:
        flash('Only the mediator can advance the phase.', 'danger')
        return redirect(url_for('mediation.session', mediation_id=mediation_id))

    if not med.can_advance():
        flash('Requirements for the next phase are not met yet.', 'warning')
    else:
        old = med.phase
        med.advance_phase()
        db.session.commit()
        flash(f'Advanced from "{old}" to "{med.phase}".', 'success')
        if not med.required_payments_complete():
            from services.translations import translate
            lang = getattr(current_user, 'preferred_language', 'en')
            flash(translate('advance_warning_payment_pending', lang), 'warning')

    return redirect(url_for('mediation.session', mediation_id=mediation_id))


# ---------------------------------------------------------------------------
# Close mediation
# ---------------------------------------------------------------------------

@mediation_bp.route('/mediation/<int:mediation_id>/close', methods=['POST'])
@login_required
def close_mediation(mediation_id):
    med = _get_med(mediation_id)
    if med.creator_id != current_user.id:
        abort(403)
    if not med.required_payments_complete():
        from services.translations import translate
        lang = getattr(current_user, 'preferred_language', 'en')
        flash(translate('cannot_close_payment_pending', lang), 'warning')
        return redirect(url_for('mediation.session', mediation_id=mediation_id))
    med.status   = 'closed'
    med.end_date = datetime.utcnow()
    db.session.commit()
    try:
        from services.notification import send_mediation_status_change
        send_mediation_status_change(med, 'closed')
    except Exception:
        pass
    flash('Mediation closed.', 'success')
    return redirect(url_for('mediation.session', mediation_id=mediation_id))


# ---------------------------------------------------------------------------
# SocketIO live events
# ---------------------------------------------------------------------------

@socketio.on('join_mediation')
def on_join(data):
    room = f"mediation_{data['mediation_id']}"
    join_room(room)
    emit('status', {'msg': f"{current_user.display_name} joined."}, room=room)

@socketio.on('leave_mediation')
def on_leave(data):
    leave_room(f"mediation_{data['mediation_id']}")

@socketio.on('typing')
def on_typing(data):
    room = f"mediation_{data['mediation_id']}"
    emit('user_typing', {'user': current_user.display_name},
         room=room, include_self=False)
