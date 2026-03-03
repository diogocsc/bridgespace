"""
BridgeSpace - Extensions
Instantiated here with no app context.
Call init_app(app) on each inside create_app().
"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
from flask_socketio import SocketIO
from flask_mail import Mail

db           = SQLAlchemy()
login_manager = LoginManager()
bcrypt        = Bcrypt()
socketio      = SocketIO()
mail          = Mail()