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
from models import User, SUPPORTED_LANGUAGES

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
        return redirect(url_for('mediation.dashboard'))

    if request.method == 'POST':
        email        = request.form.get('email', '').strip().lower()
        username     = request.form.get('username', '').strip()
        display_name = request.form.get('display_name', '').strip()
        password     = request.form.get('password', '')
        confirm      = request.form.get('confirm_password', '')
        lang         = request.form.get('preferred_language', 'en')
        # Carry next through the POST submission via hidden field
        next_page    = request.form.get('next') or request.args.get('next')

        errors = []
        if not _is_valid_email(email):
            errors.append('Invalid email address.')
        if len(username) < 3:
            errors.append('Username must be at least 3 characters.')
        if len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        if password != confirm:
            errors.append('Passwords do not match.')
        if User.query.filter_by(email=email).first():
            errors.append('Email already registered.')
        if User.query.filter_by(username=username).first():
            errors.append('Username already taken.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('auth/register.html',
                                   languages=SUPPORTED_LANGUAGES,
                                   form_data=request.form,
                                   next=next_page)

        pw_hash = bcrypt.generate_password_hash(password).decode('utf-8')
        user = User(
            email=email,
            username=username,
            display_name=display_name,
            password_hash=pw_hash,
            preferred_language=lang,
        )
        user.generate_verification_token()
        db.session.add(user)
        db.session.commit()

        try:
            from services.notification import send_verification_email
            send_verification_email(user)
        except Exception:
            pass

        login_user(user)
        flash('Account created! Check your email to verify your address.', 'success')

        # Redirect to the invite link immediately after registration
        if next_page and next_page.startswith('/'):
            return redirect(next_page)
        return redirect(url_for('mediation.dashboard'))

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
        return redirect(url_for('mediation.dashboard'))

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
            return redirect(url_for('mediation.dashboard'))

        flash('Invalid email or password.', 'danger')

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
    return redirect(url_for('mediation.dashboard'))


# ── Forgot password ────────────────────────────────────────────────────────

@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('mediation.dashboard'))

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
        return redirect(url_for('mediation.dashboard'))

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
        current_user.preferred_language = request.form.get('preferred_language', 'en')
        current_user.phone             = request.form.get('phone', '').strip() or None
        current_user.anonymous_alias   = request.form.get('anonymous_alias', '').strip() or None
        current_user.allow_case_sharing = bool(request.form.get('allow_case_sharing'))

        # Password change (optional — only if fields are filled)
        current_pw  = request.form.get('current_password', '')
        new_pw      = request.form.get('new_password', '')
        confirm_pw  = request.form.get('confirm_new_password', '')

        if current_pw or new_pw:
            if not bcrypt.check_password_hash(current_user.password_hash, current_pw):
                flash('Current password is incorrect.', 'danger')
                return render_template('preferences.html',
                                       languages=SUPPORTED_LANGUAGES,
                                       user=current_user)
            if len(new_pw) < 8:
                flash('New password must be at least 8 characters.', 'danger')
                return render_template('preferences.html',
                                       languages=SUPPORTED_LANGUAGES,
                                       user=current_user)
            if new_pw != confirm_pw:
                flash('New passwords do not match.', 'danger')
                return render_template('preferences.html',
                                       languages=SUPPORTED_LANGUAGES,
                                       user=current_user)
            current_user.password_hash = bcrypt.generate_password_hash(new_pw).decode('utf-8')
            flash('Password updated successfully.', 'success')

        db.session.commit()
        flash('Preferences saved.', 'success')
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