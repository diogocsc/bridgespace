"""
BridgeSpace - Application Factory
"""
import os
from flask import Flask
from extensions import db, login_manager, bcrypt, socketio, mail


def create_app():
    app = Flask(__name__)

    # ── Config ────────────────────────────────────────────────────────────
    app.config['SECRET_KEY']                  = os.environ.get('SECRET_KEY', 'bridgespace-dev-secret-CHANGE-IN-PRODUCTION')
    app.config['SQLALCHEMY_DATABASE_URI']     = os.environ.get('DATABASE_URL', 'sqlite:///bridgespace.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['ANTHROPIC_API_KEY']           = os.environ.get('ANTHROPIC_API_KEY', '')
    app.config['OLLAMA_API_KEY']              = os.environ.get('OLLAMA_API_KEY', '')

    app.config['MAIL_SERVER']                 = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    app.config['MAIL_PORT']                   = int(os.environ.get('MAIL_PORT', 587))
    app.config['MAIL_USE_TLS']                = True
    app.config['MAIL_USERNAME']               = os.environ.get('MAIL_USERNAME', '')
    app.config['MAIL_PASSWORD']               = os.environ.get('MAIL_PASSWORD', '')
    app.config['MAIL_DEFAULT_SENDER']         = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@bridgespace.app')

    # ── Init extensions ───────────────────────────────────────────────────
    db.init_app(app)
    login_manager.init_app(app)
    bcrypt.init_app(app)
    socketio.init_app(app, cors_allowed_origins="*")
    mail.init_app(app)

    login_manager.login_view          = 'auth.login'
    login_manager.login_message       = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'

    # ── User loader ───────────────────────────────────────────────────────
    from models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # ── Blueprints ────────────────────────────────────────────────────────
    from routes.auth      import auth_bp
    from routes.mediation import mediation_bp
    from routes.api       import api_bp
    from routes.search    import search_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(mediation_bp)
    app.register_blueprint(api_bp,    url_prefix='/api')
    app.register_blueprint(search_bp)

    # ── Create tables ─────────────────────────────────────────────────────
    with app.app_context():
        db.create_all()

    return app


if __name__ == '__main__':
    app = create_app()
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)