"""
BridgeSpace - Mediation Routes
"""
from datetime import datetime

from flask import (Blueprint, render_template, redirect,
                   url_for, flash, request, abort, jsonify)
from flask_login import login_required, current_user

from extensions import db, socketio
from models import (Mediation, MediationParticipant,
                    MediationInvitation, Post, User)
from flask_socketio import join_room, leave_room, emit

mediation_bp = Blueprint('mediation', __name__)


# ── Dashboard ──────────────────────────────────────────────────────────────

@mediation_bp.route('/')
@mediation_bp.route('/dashboard')
@login_required
def dashboard():
    participations = current_user.participations.filter_by(is_active=True).all()
    mediations = [p.mediation for p in participations]
    return render_template('dashboard.html', mediations=mediations)


# ── Create mediation ───────────────────────────────────────────────────────

@mediation_bp.route('/mediation/new', methods=['GET', 'POST'])
@login_required
def create_mediation():
    if request.method == 'POST':
        title        = request.form.get('title', '').strip()
        description  = request.form.get('description', '').strip()
        mode         = request.form.get('mode', 'async')
        start_date_str = request.form.get('start_date', '')
        invitees_raw = request.form.get('invitees', '')

        if not title:
            flash('Please provide a title for the mediation.', 'danger')
            return render_template('mediation/create.html')

        start_date = None
        if start_date_str:
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                pass

        status = 'pending' if start_date else 'active'
        med = Mediation(
            title=title,
            description=description,
            mode=mode,
            creator_id=current_user.id,
            start_date=start_date,
            status=status,
        )
        db.session.add(med)
        db.session.flush()   # get med.id before commit

        # Add creator as requesting party
        db.session.add(MediationParticipant(
            mediation_id=med.id,
            user_id=current_user.id,
            role='requester',
        ))

        # Create invitations
        if invitees_raw:
            contacts = [c.strip() for c in invitees_raw.split(',') if c.strip()]
            for contact in contacts:
                contact_type = 'email' if '@' in contact else 'phone'
                inv = MediationInvitation(
                    mediation_id=med.id,
                    invited_by_id=current_user.id,
                    contact=contact,
                    contact_type=contact_type,
                )
                db.session.add(inv)
                db.session.flush()
                try:
                    from services.notification import dispatch_invitation
                    dispatch_invitation(inv, med, current_user)
                except Exception:
                    pass   # mail not configured — invitation still created

        db.session.commit()
        flash('Mediation created successfully!', 'success')
        return redirect(url_for('mediation.view_mediation',
                                mediation_id=med.id))

    return render_template('mediation/create.html')


# ── View mediation ─────────────────────────────────────────────────────────

@mediation_bp.route('/mediation/<int:mediation_id>')
@login_required
def view_mediation(mediation_id):
    med = Mediation.query.get_or_404(mediation_id)
    participant = med.get_participant(current_user)
    if not participant:
        abort(403)

    posts = (Post.query
             .filter_by(mediation_id=med.id, is_draft=False)
             .order_by(Post.created_at.asc())
             .all())

    return render_template(
        'mediation/view.html',
        mediation=med,
        posts=posts,
        participant=participant,
        user_lang=current_user.preferred_language,
    )


# ── Invite page ────────────────────────────────────────────────────────────

@mediation_bp.route('/mediation/<int:mediation_id>/invite')
@login_required
def invite_page(mediation_id):
    med = Mediation.query.get_or_404(mediation_id)
    if not med.get_participant(current_user):
        abort(403)
    return render_template('mediation/invite.html', mediation=med)


# ── Send invites (form POST from invite page) ──────────────────────────────

@mediation_bp.route('/mediation/<int:mediation_id>/invite/send', methods=['POST'])
@login_required
def send_invites(mediation_id):
    """
    Handles the invite form submission from templates/mediation/invite.html.
    Creates MediationInvitation records and dispatches email/SMS.
    """
    med = Mediation.query.get_or_404(mediation_id)
    if not med.get_participant(current_user):
        abort(403)

    invitees_raw     = request.form.get('invitees', '')
    personal_message = request.form.get('personal_message', '').strip()

    if not invitees_raw.strip():
        flash('Please add at least one email address or phone number.', 'danger')
        return redirect(url_for('mediation.invite_page',
                                mediation_id=mediation_id))

    contacts = [c.strip() for c in invitees_raw.split(',') if c.strip()]
    sent = 0
    skipped = 0

    for contact in contacts:
        # Skip duplicates
        existing = MediationInvitation.query.filter_by(
            mediation_id=med.id,
            contact=contact,
        ).first()
        if existing:
            skipped += 1
            continue

        contact_type = 'email' if '@' in contact else 'phone'
        inv = MediationInvitation(
            mediation_id=med.id,
            invited_by_id=current_user.id,
            contact=contact,
            contact_type=contact_type,
        )
        db.session.add(inv)
        db.session.flush()

        try:
            from services.notification import dispatch_invitation
            dispatch_invitation(inv, med, current_user)
            sent += 1
        except Exception:
            sent += 1   # record saved even if email not configured

    db.session.commit()

    if skipped:
        flash(f'{sent} invitation(s) sent. {skipped} already invited.',
              'info')
    else:
        flash(f'{sent} invitation(s) sent successfully.', 'success')

    return redirect(url_for('mediation.invite_page',
                            mediation_id=mediation_id))


# ── Join via per-invitation token (from email/SMS link) ───────────────────

@mediation_bp.route('/join/<token>')
def join_via_invite(token):
    """
    Handles both:
      - Per-invitation tokens  (MediationInvitation.token)   from email/SMS
      - Mediation share tokens (Mediation.invite_token)       from the copy-link button
    """
    # 1. Try per-invitation token first
    inv = MediationInvitation.query.filter_by(token=token).first()
    if inv:
        med = inv.mediation
        return _do_join(med, inv=inv)

    # 2. Fall back to mediation-level share token
    med = Mediation.query.filter_by(invite_token=token).first()
    if med:
        return _do_join(med, inv=None)

    # 3. Nothing matched
    abort(404)


def _do_join(med, inv=None):
    """
    Common join logic used by both token types.
    Redirects to login first if the user is not authenticated.
    """
    if not current_user.is_authenticated:
        # After login, come back to the same join URL
        from flask import session as flask_session
        if inv:
            next_url = url_for('mediation.join_via_invite',
                               token=inv.token)
        else:
            next_url = url_for('mediation.join_via_invite',
                               token=med.invite_token)
        flash('Please log in (or register) to accept your invitation.', 'info')
        return redirect(url_for('auth.login', next=next_url))

    # Already a participant?
    existing = med.get_participant(current_user)
    if existing:
        flash('You are already a participant in this mediation.', 'info')
        return redirect(url_for('mediation.view_mediation',
                                mediation_id=med.id))

    # Add as respondent
    db.session.add(MediationParticipant(
        mediation_id=med.id,
        user_id=current_user.id,
        role='respondent',
    ))

    # Mark per-invitation token as accepted
    if inv:
        inv.status       = 'accepted'
        inv.responded_at = datetime.utcnow()

    db.session.commit()
    flash(f'You have joined the mediation: {med.title}', 'success')
    return redirect(url_for('mediation.view_mediation',
                            mediation_id=med.id))


# ── Close mediation ────────────────────────────────────────────────────────

@mediation_bp.route('/mediation/<int:mediation_id>/close', methods=['POST'])
@login_required
def close_mediation(mediation_id):
    med = Mediation.query.get_or_404(mediation_id)
    if med.creator_id != current_user.id:
        abort(403)

    agreement_text = request.form.get('agreement_text', '').strip()
    med.status   = 'closed'
    med.end_date = datetime.utcnow()
    db.session.commit()

    # Notify all participants
    try:
        from services.notification import send_mediation_status_change
        send_mediation_status_change(med, 'closed')
    except Exception:
        pass

    # Index for search if consent given and agreement provided
    if agreement_text:
        try:
            from services.search_service import index_closed_mediation
            index_closed_mediation(med, agreement_text)
        except Exception:
            pass

    flash('Mediation closed.', 'success')
    return redirect(url_for('mediation.view_mediation',
                            mediation_id=med.id))


# ── SocketIO events ────────────────────────────────────────────────────────

@socketio.on('join_mediation')
def on_join(data):
    room = f"mediation_{data['mediation_id']}"
    join_room(room)
    emit('status', {'msg': f'{current_user.display_name} joined.'}, room=room)


@socketio.on('leave_mediation')
def on_leave(data):
    room = f"mediation_{data['mediation_id']}"
    leave_room(room)


@socketio.on('typing')
def on_typing(data):
    room = f"mediation_{data['mediation_id']}"
    emit('user_typing',
         {'user': current_user.display_name},
         room=room,
         include_self=False)