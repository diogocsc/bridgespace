"""
BridgeSpace - Authentication Routes
Covers: register, login, logout, email verification,
        forgot/reset password, preferences, delete account.
"""
import re
from datetime import datetime

from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, session)
from flask_login import (login_user, logout_user,
                         login_required, current_user)

from extensions import db, bcrypt
from models import User, SUPPORTED_LANGUAGES, MediatorProfile
from services.translations import translate

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


# ── Helpers ────────────────────────────────────────────────────────────────

def _is_valid_email(email: str) -> bool:
    return bool(re.match(r'^[^@]+@[^@]+\.[^@]+$', email))


# ── Register ───────────────────────────────────────────────────────────────

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        next_page = request.args.get('next')
        if next_page and next_page.startswith('/'):
            return redirect(next_page)
        return redirect(url_for('index'))

    if request.method == 'POST':
        email        = request.form.get('email', '').strip().lower()
        username     = request.form.get('username', '').strip()
        display_name = request.form.get('display_name', '').strip()
        password     = request.form.get('password', '')
        confirm      = request.form.get('confirm_password', '')
        lang         = request.form.get('preferred_language', 'pt')
        register_as_mediator = request.form.get('register_as_mediator') == '1'
        # Carry next through the POST submission via hidden field
        next_page    = request.form.get('next') or request.args.get('next')

        error_keys = []
        if not _is_valid_email(email):
            error_keys.append('invalid_email')
        if len(username) < 3:
            error_keys.append('username_length')
        if len(password) < 8:
            error_keys.append('password_length')
        if password != confirm:
            error_keys.append('passwords_dont_match')
        if User.query.filter_by(email=email).first():
            error_keys.append('email_taken')
        if User.query.filter_by(username=username).first():
            error_keys.append('username_taken')

        if error_keys:
            for key in error_keys:
                flash(translate(key, lang), 'danger')
            return render_template('auth/register.html',
                                   languages=SUPPORTED_LANGUAGES,
                                   form_data=request.form,
                                   next=next_page)

        pw_hash = bcrypt.generate_password_hash(password).decode('utf-8')
        role = 'mediator' if register_as_mediator else 'user'
        user = User(
            email=email,
            username=username,
            display_name=display_name,
            password_hash=pw_hash,
            preferred_language=lang,
            role=role,
        )
        user.generate_verification_token()
        db.session.add(user)
        db.session.commit()

        if register_as_mediator:
            from models import MediatorProfile
            profile = MediatorProfile(user_id=user.id, is_active=True)
            db.session.add(profile)
            db.session.commit()

        try:
            from services.notification import send_verification_email
            send_verification_email(user)
        except Exception:
            pass

        login_user(user)
        flash(translate('account_created', lang), 'success')

        # Redirect to the invite link immediately after registration
        if next_page and next_page.startswith('/'):
            return redirect(next_page)
        return redirect(url_for('index'))

    next_page = request.args.get('next', '')
    return render_template('auth/register.html',
                           languages=SUPPORTED_LANGUAGES,
                           form_data={},
                           next=next_page)


# ── Login ──────────────────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        # Honour next even if already logged in
        next_page = request.args.get('next')
        if next_page and next_page.startswith('/'):
            return redirect(next_page)
        return redirect(url_for('index'))

    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember'))
        # Carry next through the POST submission via hidden field
        next_page = request.form.get('next') or request.args.get('next')

        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user, remember=remember)
            user.last_seen = datetime.utcnow()
            db.session.commit()
            if next_page and next_page.startswith('/'):
                return redirect(next_page)
            return redirect(url_for('index'))

        req_lang = request.accept_languages.best_match(['pt', 'en']) or 'pt'
        flash(translate('invalid_login', req_lang), 'danger')

    # Pass next into the template so the form can carry it
    next_page = request.args.get('next', '')
    return render_template('auth/login.html', next=next_page)


# ── Logout ─────────────────────────────────────────────────────────────────

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


# ── Email verification ─────────────────────────────────────────────────────

@auth_bp.route('/verify/<token>')
def verify_email(token):
    user = User.query.filter_by(verification_token=token).first()
    if not user:
        flash('Verification link is invalid or has already been used.', 'danger')
        return redirect(url_for('auth.login'))

    user.is_verified = True
    user.verification_token = None
    db.session.commit()
    flash('Email verified! Your account is now fully active.', 'success')
    return redirect(url_for('index'))


# ── Forgot password ────────────────────────────────────────────────────────

@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user  = User.query.filter_by(email=email).first()

        # Always show the same message to prevent user enumeration
        flash('If that address is registered you will receive a reset link shortly.', 'info')

        if user:
            user.generate_reset_token()
            db.session.commit()
            try:
                from services.notification import send_password_reset_email
                send_password_reset_email(user)
            except Exception:
                pass

        return redirect(url_for('auth.login'))

    return render_template('auth/forgot_password.html')


# ── Reset password ─────────────────────────────────────────────────────────

@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    user = User.query.filter_by(reset_token=token).first()

    if not user:
        flash('Reset link is invalid.', 'danger')
        return redirect(url_for('auth.forgot_password'))

    if user.reset_token_expiry and user.reset_token_expiry < datetime.utcnow():
        flash('Reset link has expired. Please request a new one.', 'danger')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm_password', '')

        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'danger')
            return render_template('auth/reset_password.html', token=token)

        if password != confirm:
            flash('Passwords do not match.', 'danger')
            return render_template('auth/reset_password.html', token=token)

        user.password_hash    = bcrypt.generate_password_hash(password).decode('utf-8')
        user.reset_token      = None
        user.reset_token_expiry = None
        db.session.commit()
        flash('Password updated. You can now log in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/reset_password.html', token=token)


# ── Preferences ────────────────────────────────────────────────────────────

@auth_bp.route('/preferences', methods=['GET', 'POST'])
@login_required
def preferences():
    if request.method == 'POST':
        current_user.display_name      = request.form.get('display_name', '').strip() or current_user.display_name
        current_user.preferred_language = request.form.get('preferred_language', 'pt')
        current_user.phone             = request.form.get('phone', '').strip() or None
        current_user.whatsapp          = request.form.get('whatsapp', '').strip() or None
        current_user.telegram          = request.form.get('telegram', '').strip() or None
        current_user.signal            = request.form.get('signal', '').strip() or None
        current_user.anonymous_alias   = request.form.get('anonymous_alias', '').strip() or None
        current_user.allow_case_sharing = bool(request.form.get('allow_case_sharing'))

        # Become a mediator (existing user can self-register as mediator via preferences)
        became_mediator = False
        if request.form.get('register_as_mediator') == '1' and current_user.role != 'mediator':
            current_user.role = 'mediator'
            if not getattr(current_user, 'mediator_profile', None):
                db.session.add(MediatorProfile(user_id=current_user.id, is_active=True))
            became_mediator = True

        # Password change only when user actually provides a new password (leave fields blank to skip)
        current_pw  = request.form.get('current_password', '').strip()
        new_pw      = request.form.get('new_password', '').strip()
        confirm_pw  = request.form.get('confirm_new_password', '').strip()

        pref_lang = request.form.get('preferred_language') or getattr(current_user, 'preferred_language', None) or 'pt'
        if new_pw:
            # User wants to change password: require current password and validate new one
            if not current_pw:
                flash(translate('current_password_incorrect', pref_lang), 'danger')
                return render_template('preferences.html',
                                       languages=SUPPORTED_LANGUAGES,
                                       user=current_user)
            if not bcrypt.check_password_hash(current_user.password_hash, current_pw):
                flash(translate('current_password_incorrect', pref_lang), 'danger')
                return render_template('preferences.html',
                                       languages=SUPPORTED_LANGUAGES,
                                       user=current_user)
            if len(new_pw) < 8:
                flash(translate('new_password_length', pref_lang), 'danger')
                return render_template('preferences.html',
                                       languages=SUPPORTED_LANGUAGES,
                                       user=current_user)
            if new_pw != confirm_pw:
                flash(translate('new_passwords_dont_match', pref_lang), 'danger')
                return render_template('preferences.html',
                                       languages=SUPPORTED_LANGUAGES,
                                       user=current_user)
            current_user.password_hash = bcrypt.generate_password_hash(new_pw).decode('utf-8')
            flash(translate('password_updated', pref_lang), 'success')

        db.session.commit()
        flash(translate('preferences_saved', pref_lang), 'success')
        if became_mediator:
            flash(translate('now_registered_as_mediator', pref_lang), 'info')
        return redirect(url_for('auth.preferences'))

    return render_template('preferences.html',
                           languages=SUPPORTED_LANGUAGES,
                           user=current_user)


# ── Delete account ─────────────────────────────────────────────────────────

@auth_bp.route('/delete-account', methods=['POST'])
@login_required
def delete_account():
    """
    Permanently deletes the user's personal data.
    Posts in active mediations are anonymised rather than deleted
    so mediation continuity is preserved for other parties.
    """
    from models import Post, MediationParticipant

    user = current_user

    # Anonymise the user's posts instead of deleting them
    posts = Post.query.filter_by(author_id=user.id).all()
    for post in posts:
        alias = user.anonymous_alias or 'Deleted User'
        # We blank PII from stored content; the post record stays for mediation integrity
        post.original_content      = f'[Content removed — {alias}]'
        post.reformulated_content  = None
        post.translations          = None

    # Deactivate participations
    participations = MediationParticipant.query.filter_by(user_id=user.id).all()
    for p in participations:
        p.is_active = False

    db.session.flush()

    # Now delete the user record
    logout_user()
    db.session.delete(user)
    db.session.commit()

    flash('Your account has been permanently deleted.', 'info')
    return redirect(url_for('auth.login'))