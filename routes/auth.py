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
        from services.captcha_service import verify_recaptcha, is_captcha_required
        if is_captcha_required() and not verify_recaptcha(request.form.get('g-recaptcha-response', '')):
            lang = request.form.get('preferred_language', 'pt')
            flash(translate('captcha_required', lang), 'danger')
            return render_template('auth/register.html',
                                   languages=SUPPORTED_LANGUAGES,
                                   form_data=request.form,
                                   next=request.form.get('next') or request.args.get('next'))
        email        = request.form.get('email', '').strip().lower()
        username     = request.form.get('username', '').strip()
        display_name = request.form.get('display_name', '').strip()
        password     = request.form.get('password', '')
        confirm      = request.form.get('confirm_password', '')
        lang         = request.form.get('preferred_language', 'pt')
        register_as_mediator = request.form.get('register_as_mediator') == '1'
        mediator_plan = request.form.get('mediator_plan', 'free').strip() if register_as_mediator else 'free'
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
        if request.form.get('accept_terms') != '1':
            error_keys.append('terms_required')
        if register_as_mediator and request.form.get('accept_mediator_terms') != '1':
            error_keys.append('mediator_terms_required')

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
            from datetime import datetime as dt
            profile = MediatorProfile(
                user_id=user.id,
                is_active=True,
                terms_accepted_at=dt.utcnow(),
            )
            if mediator_plan in ('professional', 'enterprise'):
                profile.subscription_plan = mediator_plan
            db.session.add(profile)
            db.session.commit()

        try:
            from services.notification import send_verification_email
            send_verification_email(user)
        except Exception:
            pass

        login_user(user)
        flash(translate('account_created', lang), 'success')

        # If mediator chose a paid plan at registration, send them to subscribe flow
        if register_as_mediator and mediator_plan == 'professional':
            return redirect(url_for('billing.subscribe_pro'))
        if register_as_mediator and mediator_plan == 'enterprise':
            return redirect(url_for('billing.subscribe_enterprise'))

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
        from services.captcha_service import verify_recaptcha, is_captcha_required
        if is_captcha_required() and not verify_recaptcha(request.form.get('g-recaptcha-response', '')):
            req_lang = request.accept_languages.best_match(['pt', 'en']) or 'pt'
            flash(translate('captcha_required', req_lang), 'danger')
            return render_template('auth/login.html', next=request.form.get('next') or request.args.get('next'))
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember'))
        # Carry next through the POST submission via hidden field
        next_page = request.form.get('next') or request.args.get('next')

        user = User.query.filter_by(email=email).first()
        if user and not getattr(user, "deleted_at", None) and user.password_hash and bcrypt.check_password_hash(user.password_hash, password):
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
    # Capture language before logging out
    lang = getattr(current_user, 'preferred_language', 'pt') or 'pt'
    logout_user()
    flash(translate('logged_out', lang), 'info')
    return redirect(url_for('auth.login'))


# ── Email verification ─────────────────────────────────────────────────────

@auth_bp.route('/verify/<token>')
def verify_email(token):
    user = User.query.filter_by(verification_token=token).first()
    if not user:
        req_lang = request.accept_languages.best_match(['pt', 'en']) or 'pt'
        flash(translate('verification_link_invalid', req_lang), 'danger')
        return redirect(url_for('auth.login'))

    user.is_verified = True
    user.verification_token = None
    db.session.commit()
    lang = getattr(user, 'preferred_language', 'pt') or 'pt'
    flash(translate('email_verified', lang), 'success')
    return redirect(url_for('index'))


# ── Forgot password ────────────────────────────────────────────────────────

@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        from services.captcha_service import verify_recaptcha, is_captcha_required
        if is_captcha_required() and not verify_recaptcha(request.form.get('g-recaptcha-response', '')):
            req_lang = request.accept_languages.best_match(['pt', 'en']) or 'pt'
            flash(translate('captcha_required', req_lang), 'danger')
            return render_template('auth/forgot_password.html')
        email = request.form.get('email', '').strip().lower()
        user  = User.query.filter_by(email=email).first()

        # Always show the same message to prevent user enumeration
        req_lang = request.accept_languages.best_match(['pt', 'en']) or 'pt'
        flash(translate('reset_if_registered', req_lang), 'info')

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
        req_lang = request.accept_languages.best_match(['pt', 'en']) or 'pt'
        flash(translate('reset_link_invalid', req_lang), 'danger')
        return redirect(url_for('auth.forgot_password'))

    if user.reset_token_expiry and user.reset_token_expiry < datetime.utcnow():
        req_lang = request.accept_languages.best_match(['pt', 'en']) or 'pt'
        flash(translate('reset_link_expired', req_lang), 'danger')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        from services.captcha_service import verify_recaptcha, is_captcha_required
        if is_captcha_required() and not verify_recaptcha(request.form.get('g-recaptcha-response', '')):
            req_lang = request.accept_languages.best_match(['pt', 'en']) or 'pt'
            flash(translate('captcha_required', req_lang), 'danger')
            return render_template('auth/reset_password.html', token=token)
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm_password', '')

        req_lang = request.accept_languages.best_match(['pt', 'en']) or 'pt'
        if len(password) < 8:
            flash(translate('password_length', req_lang), 'danger')
            return render_template('auth/reset_password.html', token=token)

        if password != confirm:
            flash(translate('passwords_dont_match', req_lang), 'danger')
            return render_template('auth/reset_password.html', token=token)

        user.password_hash    = bcrypt.generate_password_hash(password).decode('utf-8')
        user.reset_token      = None
        user.reset_token_expiry = None
        db.session.commit()
        flash(translate('password_updated', req_lang), 'success')
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
    Permanently deletes the user's profile: contact details are cleared,
    mediation-related data is kept. User cannot log in again.
    """
    from services.user_deletion import anonymise_user

    user = current_user
    lang = getattr(user, "preferred_language", "pt") or "pt"

    anonymise_user(user)
    db.session.commit()
    logout_user()

    flash(translate("account_deleted", lang), "info")
    return redirect(url_for("auth.login"))