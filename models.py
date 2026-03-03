"""BridgeSpace - Database Models"""
from datetime import datetime
import secrets, json
from extensions import db
from flask_login import UserMixin

SUPPORTED_LANGUAGES = {
    'en': 'English', 'pt': 'Portuguese', 'es': 'Spanish',
    'fr': 'French', 'de': 'German', 'it': 'Italian',
    'zh': 'Chinese', 'ar': 'Arabic', 'hi': 'Hindi',
    'ja': 'Japanese', 'ru': 'Russian', 'nl': 'Dutch',
}

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    display_name = db.Column(db.String(128), nullable=False)
    preferred_language = db.Column(db.String(8), default='en')
    phone = db.Column(db.String(32), nullable=True)
    is_verified = db.Column(db.Boolean, default=False)
    verification_token = db.Column(db.String(128), nullable=True)
    reset_token = db.Column(db.String(128), nullable=True)
    reset_token_expiry = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    allow_case_sharing = db.Column(db.Boolean, default=False)
    anonymous_alias = db.Column(db.String(64), nullable=True)
    participations = db.relationship('MediationParticipant', back_populates='user', lazy='dynamic')
    posts = db.relationship('Post', back_populates='author', lazy='dynamic')

    def generate_verification_token(self):
        self.verification_token = secrets.token_urlsafe(32)

    def generate_reset_token(self):
        from datetime import timedelta
        self.reset_token = secrets.token_urlsafe(32)
        self.reset_token_expiry = datetime.utcnow() + timedelta(hours=1)


class Mediation(db.Model):
    __tablename__ = 'mediations'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text, nullable=True)
    creator_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    mode = db.Column(db.String(16), default='async')   # 'live' or 'async'
    status = db.Column(db.String(32), default='pending')  # pending|active|closed
    start_date = db.Column(db.DateTime, nullable=True)
    end_date = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_confidential = db.Column(db.Boolean, default=True)
    shared_publicly = db.Column(db.Boolean, default=False)
    invite_token = db.Column(db.String(64), unique=True,
                             default=lambda: secrets.token_urlsafe(32))
    creator = db.relationship('User', foreign_keys=[creator_id])
    participants = db.relationship('MediationParticipant', back_populates='mediation',
                                   lazy='dynamic', cascade='all, delete-orphan')
    posts = db.relationship('Post', back_populates='mediation',
                            lazy='dynamic', cascade='all, delete-orphan')
    invitations = db.relationship('MediationInvitation', back_populates='mediation',
                                  lazy='dynamic', cascade='all, delete-orphan')

    def get_participant(self, user):
        return self.participants.filter_by(user_id=user.id).first()


class MediationParticipant(db.Model):
    __tablename__ = 'mediation_participants'
    id = db.Column(db.Integer, primary_key=True)
    mediation_id = db.Column(db.Integer, db.ForeignKey('mediations.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    role = db.Column(db.String(32), default='respondent')  # requester|respondent|observer
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    mediation = db.relationship('Mediation', back_populates='participants')
    user = db.relationship('User', back_populates='participations')


class MediationInvitation(db.Model):
    __tablename__ = 'mediation_invitations'
    id = db.Column(db.Integer, primary_key=True)
    mediation_id = db.Column(db.Integer, db.ForeignKey('mediations.id'), nullable=False)
    invited_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    contact = db.Column(db.String(256), nullable=False)
    contact_type = db.Column(db.String(16), default='email')  # email|phone
    token = db.Column(db.String(128), unique=True,
                      default=lambda: secrets.token_urlsafe(32))
    status = db.Column(db.String(16), default='pending')  # pending|accepted|declined
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    responded_at = db.Column(db.DateTime, nullable=True)
    mediation = db.relationship('Mediation', back_populates='invitations')
    invited_by = db.relationship('User', foreign_keys=[invited_by_id])


class Post(db.Model):
    __tablename__ = 'posts'
    id = db.Column(db.Integer, primary_key=True)
    mediation_id = db.Column(db.Integer, db.ForeignKey('mediations.id'), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    original_content = db.Column(db.Text, nullable=False)
    reformulated_content = db.Column(db.Text, nullable=True)
    submitted_version = db.Column(db.String(16), default='original')
    translations = db.Column(db.Text, nullable=True)  # JSON {lang_code: text}
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_draft = db.Column(db.Boolean, default=False)
    input_method = db.Column(db.String(8), default='text')  # text|voice
    mediation = db.relationship('Mediation', back_populates='posts')
    author = db.relationship('User', back_populates='posts')

    def get_display_content(self):
        if self.submitted_version == 'reformulated' and self.reformulated_content:
            return self.reformulated_content
        return self.original_content

    def get_translation(self, lang_code):
        if not self.translations:
            return None
        try:
            return json.loads(self.translations).get(lang_code)
        except Exception:
            return None

    def set_translation(self, lang_code, text):
        try:
            t = json.loads(self.translations) if self.translations else {}
        except Exception:
            t = {}
        t[lang_code] = text
        self.translations = json.dumps(t)


class MediationAgreement(db.Model):
    """
    Stores the agreed outcome of a closed mediation.
    Only mediations where ALL participants have set allow_case_sharing=True
    (or the mediation is explicitly marked shared) are eligible for search indexing.
    Personal identifiers are stripped before storage.
    """
    __tablename__ = 'mediation_agreements'
    id = db.Column(db.Integer, primary_key=True)
    mediation_id = db.Column(db.Integer, db.ForeignKey('mediations.id'),
                             nullable=False, unique=True)
    # Privacy-masked summary — no PII, generated by AI at mediation close
    masked_summary = db.Column(db.Text, nullable=False)
    # Privacy-masked agreement text shown in search results
    masked_agreement = db.Column(db.Text, nullable=False)
    # Domain tags for filtering: e.g. ["workplace", "contract", "family"]
    tags = db.Column(db.String(512), nullable=True)
    # Embedding vector stored as JSON list of floats (for semantic search)
    embedding = db.Column(db.Text, nullable=True)
    # The language the masked content is stored in (always English for indexing)
    language = db.Column(db.String(8), default='en')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_indexed = db.Column(db.Boolean, default=True)

    mediation = db.relationship('Mediation', foreign_keys=[mediation_id])

    def get_tags_list(self):
        if not self.tags:
            return []
        try:
            return json.loads(self.tags)
        except Exception:
            return [t.strip() for t in self.tags.split(',') if t.strip()]

    def set_tags_list(self, tags: list):
        self.tags = json.dumps(tags)