"""
BridgeSpace - Application Factory
"""
import os
from pathlib import Path

from dotenv import load_dotenv, dotenv_values

# Load .env from the directory containing this file (project root), so it works
# regardless of the current working directory when starting the app.
_project_root = Path(__file__).resolve().parent
_env_path = _project_root / ".env"
load_dotenv(_env_path)

# Windows: ensure Stripe (and other) keys from .env are in os.environ even if
# load_dotenv left keys with CRLF/BOM (e.g. "STRIPE_WEBHOOK_SECRET\r").
# Re-apply from parsed .env using stripped keys/values.
if _env_path.exists():
    _raw = dotenv_values(_env_path)
    for _k, _v in (_raw or {}).items():
        if _k is None or _v is None:
            continue
        _kc = _k.strip().strip("\r\n")
        _vc = str(_v).strip().strip("\r\n")
        if _kc and _vc and not _kc.startswith("#"):
            os.environ[_kc] = _vc

from flask import Flask, redirect, url_for, request, jsonify
from flask_login import current_user
from extensions import db, login_manager, bcrypt, socketio, mail


def create_app(test_config=None):
    app = Flask(__name__)

    # default config
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'bridgespace-dev-secret-CHANGE-IN-PRODUCTION')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///bridgespace.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    app.config['ANTHROPIC_API_KEY'] = os.environ.get('ANTHROPIC_API_KEY', '')
    app.config['OLLAMA_API_KEY'] = os.environ.get('OLLAMA_API_KEY', '')
    app.config['OLLAMA_API_URL'] = os.environ.get('OLLAMA_API_URL', 'https://ollama.com')
    app.config['OLLAMA_MODEL'] = os.environ.get('OLLAMA_MODEL', 'gpt-oss:120b')

    app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
    app.config['MAIL_USE_TLS'] = True
    app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', '')
    app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', '')
    app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@bridgespace.app')

    # reCAPTCHA v2 for public forms (login, register, forgot/reset password)
    app.config['RECAPTCHA_SITE_KEY'] = os.environ.get('RECAPTCHA_SITE_KEY', '')
    app.config['RECAPTCHA_SECRET_KEY'] = os.environ.get('RECAPTCHA_SECRET_KEY', '')

    # ⭐ Apply overrides for testing
    if test_config is not None:
        app.config.update(test_config)

    # init extensions
    db.init_app(app)
    login_manager.init_app(app)
    # Redirect unauthenticated browser users to login
    login_manager.login_view = "auth.login"
    bcrypt.init_app(app)
    socketio.init_app(app, cors_allowed_origins="*")
    mail.init_app(app)

    from models import User
    @login_manager.user_loader
    def load_user(user_id):
        u = User.query.get(int(user_id))
        if u and getattr(u, "deleted_at", None):
            return None  # deleted users cannot stay logged in
        return u

    @login_manager.unauthorized_handler
    def _unauthorized():
        # For API calls, return JSON 401 (fetch callers expect JSON, not HTML redirects)
        if request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json":
            return jsonify({"error": "Authentication required"}), 401
        # For browser navigation, redirect to login page
        return redirect(url_for("auth.login", next=request.full_path))

    # blueprints
    from routes.auth import auth_bp
    from routes.mediation import mediation_bp
    from routes.api import api_bp
    from routes.search import search_bp
    from routes.admin import admin_bp
    from routes.legal import legal_bp
    from routes.stripe_webhook import stripe_webhook_bp
    from routes.billing import billing_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(mediation_bp)
    app.register_blueprint(stripe_webhook_bp)
    app.register_blueprint(billing_bp)
    app.register_blueprint(legal_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(search_bp)
    app.register_blueprint(admin_bp)

    # Inject translations, current language and public config into all templates
    from services.translations import get_translations, DEFAULT_LANGUAGE, LOCALES
    from services.settings_service import company_contact_email, company_address, company_contact_phone
    @app.context_processor
    def inject_translations():
        lang = getattr(current_user, "preferred_language", None) if current_user.is_authenticated else DEFAULT_LANGUAGE
        if not lang or lang not in LOCALES:
            lang = DEFAULT_LANGUAGE if DEFAULT_LANGUAGE in LOCALES else "en"
        recaptcha_site_key = app.config.get("RECAPTCHA_SITE_KEY", "")
        return {
            "t": get_translations(lang),
            "current_lang": lang,
            "recaptcha_site_key": recaptcha_site_key,
            "company_contact_email": company_contact_email(),
            "company_address": company_address(),
            "company_contact_phone": company_contact_phone(),
        }

    # root route
    @app.route('/')
    def index():
        if current_user.is_authenticated:
            return redirect(url_for('mediation.dashboard'))
        return redirect(url_for('auth.login'))

    # create tables
    with app.app_context():
        db.create_all()
        try:
            from services.schema_migrations import ensure_schema
            ensure_schema()
        except Exception:
            # Never prevent startup due to a best-effort additive migration
            pass

        # Seed a superadmin if none exists (dev-friendly).
        # Configure via env vars for predictable credentials.
        try:
            from models import User
            from extensions import bcrypt as _bcrypt
            super_count = User.query.filter(User.role == "superadmin").count()
            if super_count == 0:
                admin_email = os.environ.get("SUPERADMIN_EMAIL", "admin@bridgespace.local")
                admin_username = os.environ.get("SUPERADMIN_USERNAME", "admin")
                admin_pw = os.environ.get("SUPERADMIN_PASSWORD")
                if not admin_pw:
                    import secrets as _secrets
                    admin_pw = _secrets.token_urlsafe(12)
                    app.logger.warning("Generated SUPERADMIN_PASSWORD=%s", admin_pw)

                existing = User.query.filter_by(email=admin_email).first()
                if not existing:
                    pw_hash = _bcrypt.generate_password_hash(admin_pw).decode("utf-8")
                    u = User(
                        username=admin_username,
                        email=admin_email,
                        display_name="Super Admin",
                        password_hash=pw_hash,
                        role="superadmin",
                        is_verified=True,
                    )
                    db.session.add(u)
                    db.session.commit()
        except Exception:
            pass

    @app.cli.command('process-mediator-timeouts')
    def process_mediator_timeouts():
        """Run 48h mediator confirmation timeout job (reassign or escalate to admins)."""
        from services.mediator_availability_job import process_mediator_confirmation_timeouts
        n = process_mediator_confirmation_timeouts()
        print(f"Processed {n} mediation(s) with expired mediator confirmation.")

    @app.cli.command('reset-superadmin-password')
    def reset_superadmin_password():
        """Set the superadmin password to SUPERADMIN_PASSWORD from .env (user matched by SUPERADMIN_EMAIL)."""
        load_dotenv()
        email = os.environ.get('SUPERADMIN_EMAIL', 'admin@bridgespace.local')
        pw = os.environ.get('SUPERADMIN_PASSWORD', '')
        if not pw:
            print('SUPERADMIN_PASSWORD is not set in .env. Set it and try again.')
            return
        from models import User
        user = User.query.filter_by(email=email).first()
        if not user:
            print(f'No user with email {email!r} found.')
            return
        user.password_hash = bcrypt.generate_password_hash(pw).decode('utf-8')
        db.session.commit()
        print(f'Password for {email} updated to match .env.')

    return app


if __name__ == '__main__':
    app = create_app()
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
