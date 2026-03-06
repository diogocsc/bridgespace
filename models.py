from datetime import datetime
import json
import secrets
from extensions import db
from flask_login import UserMixin


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Dict — templates use languages.items(); ai_service uses dict lookup
SUPPORTED_LANGUAGES = {
    'en': 'English',
    'pt': 'Português',
    'es': 'Español',
    'fr': 'Français',
    'de': 'Deutsch',
    'it': 'Italiano',
    'nl': 'Nederlands',
    'pl': 'Polski',
    'ru': 'Русский',
    'zh': '中文',
    'ar': 'العربية',
}


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

class User(UserMixin, db.Model):
    id                   = db.Column(db.Integer, primary_key=True)
    username             = db.Column(db.String(80), unique=True, nullable=False)
    email                = db.Column(db.String(120), unique=True, nullable=False)
    password_hash        = db.Column(db.String(256), nullable=False)
    display_name         = db.Column(db.String(100))
    preferred_language   = db.Column(db.String(10), default='pt')
    # user | mediator | admin | superadmin
    role                 = db.Column(db.String(20), default='user', nullable=False)
    phone                = db.Column(db.String(30))
    whatsapp             = db.Column(db.String(50))   # phone or handle for notifications
    telegram             = db.Column(db.String(80))   # @username or phone
    signal               = db.Column(db.String(50))   # phone for Signal
    anonymous_alias      = db.Column(db.String(80))
    allow_case_sharing   = db.Column(db.Boolean, default=False)
    is_verified          = db.Column(db.Boolean, default=False)
    verification_token   = db.Column(db.String(100))
    reset_token          = db.Column(db.String(100))
    reset_token_expiry   = db.Column(db.DateTime)
    last_seen            = db.Column(db.DateTime)
    created_at           = db.Column(db.DateTime, default=datetime.utcnow)

    def generate_verification_token(self):
        self.verification_token = secrets.token_urlsafe(32)

    def generate_reset_token(self):
        from datetime import timedelta
        self.reset_token = secrets.token_urlsafe(32)
        self.reset_token_expiry = datetime.utcnow() + timedelta(hours=1)

    @property
    def is_admin(self) -> bool:
        return self.role in ('admin', 'superadmin')

    @property
    def is_superadmin(self) -> bool:
        return self.role == 'superadmin'

    @property
    def is_mediator(self) -> bool:
        return self.role == 'mediator'


# ---------------------------------------------------------------------------
# Mediation session
# ---------------------------------------------------------------------------

class Mediation(db.Model):
    """
    mediation_type:
        structured   – Phased process (pre_mediation → perspectives → agenda → proposals → agreement)
        unstructured – Free-flow posts; mediator marks agreement post and closes with outcome
    """
    MEDIATION_TYPES = ['structured', 'unstructured']
    PHASES = ['pre_mediation', 'perspectives', 'agenda', 'proposals', 'agreement']

    id           = db.Column(db.Integer, primary_key=True)
    title        = db.Column(db.String(200), nullable=False)
    description  = db.Column(db.Text)
    mediation_type = db.Column(db.String(20), default='structured', nullable=False)  # 'structured' | 'unstructured'
    mediator_id          = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    mediator_invited_at  = db.Column(db.DateTime)   # when current mediator was offered
    mediator_confirmed_at = db.Column(db.DateTime) # when they confirmed availability (None = pending)
    mediator_attempt       = db.Column(db.Integer, default=1)  # 1 = first choice, 2 = after first timeout
    mediator_escalated_at  = db.Column(db.DateTime)  # when admins were notified after 2nd timeout
    creator_id             = db.Column(db.Integer, db.ForeignKey('user.id'))   # alias used by search.py
    is_live      = db.Column(db.Boolean, default=False)
    status       = db.Column(db.String(20), default='open')          # open / closed
    phase        = db.Column(db.String(20), default='pre_mediation', nullable=False)
    mode         = db.Column(db.String(10), default='async')     # 'async' | 'live'
    start_date   = db.Column(db.DateTime)
    end_date     = db.Column(db.DateTime)
    invite_token = db.Column(db.String(100), unique=True,
                             default=lambda: __import__('secrets').token_urlsafe(24))
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    # Pre-mediation
    pre_mediation_text     = db.Column(db.Text, default="")
    explanation_requested_at = db.Column(db.DateTime)  # when a participant first asked for explanation
    explanation_added_at     = db.Column(db.DateTime)  # when mediator first saved non-empty explanation
    # Pricing (payments implemented separately)
    price_per_party_cents = db.Column(db.Integer, default=5000)  # 50.00 EUR (used when pricing_type=fixed)
    pricing_type = db.Column(db.String(20), default='fixed')  # fixed | donation | probono (set by mediator in pre-mediation)
    currency = db.Column(db.String(3), default="EUR")
    payment_required = db.Column(db.Boolean, default=True)
    # Unstructured: agreement is a specific post; close outcome + justification
    agreement_post_id   = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=True)
    close_outcome       = db.Column(db.String(30), nullable=True)   # 'agreement_reached' | 'agreement_not_reached'
    close_justification  = db.Column(db.Text, nullable=True)

    mediator        = db.relationship('User', backref='mediated_sessions', foreign_keys=[mediator_id])
    participants    = db.relationship('MediationParticipant', back_populates='mediation', cascade='all, delete-orphan')
    posts           = db.relationship(
        'Post',
        back_populates='mediation',
        cascade='all, delete-orphan',
        foreign_keys='Post.mediation_id',
    )
    agreement_post  = db.relationship('Post', foreign_keys=[agreement_post_id], uselist=False)
    perspectives  = db.relationship(
        'Perspective',
        back_populates='mediation',
        order_by='Perspective.created_at.desc()',
        cascade='all, delete-orphan'
    )
    agenda_points = db.relationship(
        'AgendaPoint',
        back_populates='mediation',
        order_by='AgendaPoint.order',
        cascade='all, delete-orphan'
    )
    agreement     = db.relationship('Agreement', back_populates='mediation', uselist=False, cascade='all, delete-orphan')
    payments      = db.relationship('MediationPayment', back_populates='mediation', cascade='all, delete-orphan')

    def get_participant(self, user):
        return next((p for p in self.participants if p.user_id == user.id), None)

    @property
    def phase_index(self):
        return self.PHASES.index(self.phase)

    def advance_phase(self):
        idx = self.phase_index
        if idx < len(self.PHASES) - 1:
            self.phase = self.PHASES[idx + 1]
            return True
        return False

    def can_advance(self):
        if self.phase == 'pre_mediation':
            # All active participants should acknowledge the pre-mediation explanation
            all_ack = all(p.pre_mediation_acknowledged for p in self.participants if p.is_active)
            if not all_ack:
                return False
            # Payments are enforced only if enabled/configured; see services.settings_service
            try:
                from services.settings_service import payments_enabled_for_mediation, is_participant_paid
                if payments_enabled_for_mediation(self):
                    return all(
                        (not p.is_active)
                        or (p.role == "mediator")
                        or is_participant_paid(self.id, p.id)
                        for p in self.participants
                    )
            except Exception:
                # Never block phase advancement due to settings lookup failure
                return all_ack
            return True
        if self.phase == 'perspectives':
            return len(self.perspectives) >= 2
        if self.phase == 'agenda':
            return len(self.agenda_points) >= 1
        if self.phase == 'proposals':
            return all(len(ap.proposals) >= 1 for ap in self.agenda_points)
        return False


class MediationParticipant(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    mediation_id  = db.Column(db.Integer, db.ForeignKey('mediation.id'), nullable=False)
    user_id       = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    email         = db.Column(db.String(120))
    display_name  = db.Column(db.String(100))
    role          = db.Column(db.String(20), default='participant')  # requester | respondent | participant
    is_active     = db.Column(db.Boolean, default=True)
    pre_mediation_acknowledged = db.Column(db.Boolean, default=False)
    consent_search_share = db.Column(db.Boolean, nullable=True)  # None=not asked; True/False=consent for this mediation in search (when closed with agreement)
    joined_at     = db.Column(db.DateTime, default=datetime.utcnow)

    mediation = db.relationship('Mediation', back_populates='participants')
    user      = db.relationship('User', backref='participations')

    payments  = db.relationship('MediationPayment', back_populates='participant', cascade='all, delete-orphan')


# ---------------------------------------------------------------------------
# Post  (free-form messaging — used by api.py and auth.py)
# ---------------------------------------------------------------------------

class Post(db.Model):
    id                   = db.Column(db.Integer, primary_key=True)
    mediation_id         = db.Column(db.Integer, db.ForeignKey('mediation.id'), nullable=False)
    author_id            = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    original_content     = db.Column(db.Text, nullable=False)
    reformulated_content = db.Column(db.Text)
    submitted_version    = db.Column(db.String(20), default='original')
    input_method         = db.Column(db.String(20), default='text')
    translations         = db.Column(db.Text)   # JSON {lang_code: text}
    is_draft             = db.Column(db.Boolean, default=False)
    created_at           = db.Column(db.DateTime, default=datetime.utcnow)

    mediation = db.relationship('Mediation', back_populates='posts', foreign_keys=[mediation_id])
    author    = db.relationship('User', backref='posts')

    def get_display_content(self):
        if self.submitted_version == 'reformulated' and self.reformulated_content:
            return self.reformulated_content
        return self.original_content

    def get_translation(self, lang_code: str):
        if not self.translations:
            return None
        try:
            return json.loads(self.translations).get(lang_code)
        except (json.JSONDecodeError, TypeError):
            return None

    def set_translation(self, lang_code: str, text: str):
        try:
            data = json.loads(self.translations) if self.translations else {}
        except (json.JSONDecodeError, TypeError):
            data = {}
        data[lang_code] = text
        self.translations = json.dumps(data)


# ---------------------------------------------------------------------------
# Phase 1 – Perspectives
# ---------------------------------------------------------------------------

class Perspective(db.Model):
    id                    = db.Column(db.Integer, primary_key=True)
    mediation_id          = db.Column(db.Integer, db.ForeignKey('mediation.id'), nullable=False)
    author_id             = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content               = db.Column(db.Text, nullable=False)
    reformulated          = db.Column(db.Text)
    used_ai_reformulation = db.Column(db.Boolean, default=False)  # user submitted the AI-reformulated version
    translated            = db.Column(db.Text)
    created_at            = db.Column(db.DateTime, default=datetime.utcnow)

    mediation = db.relationship('Mediation', back_populates='perspectives')
    author    = db.relationship('User', backref='perspectives')


# ---------------------------------------------------------------------------
# Phase 2 – Agenda
# ---------------------------------------------------------------------------

class AgendaPoint(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    mediation_id = db.Column(db.Integer, db.ForeignKey('mediation.id'), nullable=False)
    title        = db.Column(db.String(200), nullable=False)
    description  = db.Column(db.Text)
    order        = db.Column(db.Integer, default=0)
    ai_generated = db.Column(db.Boolean, default=False)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    mediation = db.relationship('Mediation', back_populates='agenda_points')
    proposals = db.relationship('Proposal', back_populates='agenda_point', cascade='all, delete-orphan')


# ---------------------------------------------------------------------------
# Phase 3 – Proposals
# ---------------------------------------------------------------------------

class Proposal(db.Model):
    STATUS = ['pending', 'accepted', 'rejected', 'modified']

    id              = db.Column(db.Integer, primary_key=True)
    agenda_point_id = db.Column(db.Integer, db.ForeignKey('agenda_point.id'), nullable=False)
    author_id       = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content         = db.Column(db.Text, nullable=False)
    reformulated    = db.Column(db.Text)
    status          = db.Column(db.String(20), default='pending')
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    agenda_point = db.relationship('AgendaPoint', back_populates='proposals')
    author       = db.relationship('User', backref='proposals')


# ---------------------------------------------------------------------------
# Phase 4 – Agreement
# ---------------------------------------------------------------------------

class Agreement(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    mediation_id = db.Column(db.Integer, db.ForeignKey('mediation.id'), nullable=False)
    content      = db.Column(db.Text, nullable=False)
    signed_at    = db.Column(db.DateTime)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    mediation  = db.relationship('Mediation', back_populates='agreement')
    signatures = db.relationship('AgreementSignature', back_populates='agreement', cascade='all, delete-orphan')

    @property
    def is_signed_by_all(self):
        participant_ids = {p.user_id for p in self.mediation.participants if p.user_id and getattr(p, 'role', None) != 'mediator'}
        signed_ids      = {s.user_id for s in self.signatures}
        return participant_ids.issubset(signed_ids)


class AgreementSignature(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    agreement_id = db.Column(db.Integer, db.ForeignKey('agreement.id'), nullable=False)
    user_id      = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    signed_at    = db.Column(db.DateTime, default=datetime.utcnow)

    agreement = db.relationship('Agreement', back_populates='signatures')
    user      = db.relationship('User', backref='signatures')


# ---------------------------------------------------------------------------
# Invitation  (restored from original)
# ---------------------------------------------------------------------------

import secrets as _secrets

class MediationInvitation(db.Model):
    """
    An invitation sent to a party (by email or SMS) to join a mediation.
    The token is embedded in the join link.
    """
    id             = db.Column(db.Integer, primary_key=True)
    mediation_id   = db.Column(db.Integer, db.ForeignKey('mediation.id'), nullable=False)
    invited_by_id  = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    contact        = db.Column(db.String(200), nullable=False)   # email or phone
    contact_type   = db.Column(db.String(10), default='email')   # 'email' | 'phone'
    token          = db.Column(db.String(100), unique=True, nullable=False,
                               default=lambda: _secrets.token_urlsafe(32))
    status         = db.Column(db.String(20), default='pending')  # pending | accepted | declined
    personal_message = db.Column(db.Text)
    responded_at   = db.Column(db.DateTime)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    mediation  = db.relationship('Mediation', backref='invitations')
    invited_by = db.relationship('User', backref='sent_invitations')


class MediationAgreement(db.Model):
    __tablename__ = "mediation_agreement"

    id = db.Column(db.Integer, primary_key=True)
    mediation_id = db.Column(db.Integer, db.ForeignKey('mediation.id'), nullable=False)

    # Masked, anonymized text
    masked_summary = db.Column(db.Text, default="")
    masked_agreement = db.Column(db.Text, default="")

    # JSON tags (stored as comma‑separated or JSON string)
    tags = db.Column(db.Text, default="[]")

    dispute_type = db.Column(db.String(50), default="other")

    # Search index flag
    is_indexed = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    mediation = db.relationship("Mediation", backref="indexed_agreement", uselist=False)

    # Helpers
    def set_tags_list(self, tag_list):
        import json
        self.tags = json.dumps(tag_list)

    def get_tags_list(self):
        import json
        try:
            return json.loads(self.tags)
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Mediator profile
# ---------------------------------------------------------------------------

class MediatorProfile(db.Model):
    id               = db.Column(db.Integer, primary_key=True)
    user_id          = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    bio              = db.Column(db.Text, default="")
    is_active        = db.Column(db.Boolean, default=True)
    selection_count  = db.Column(db.Integer, default=0)   # times offered a mediation
    times_confirmed  = db.Column(db.Integer, default=0) # times accepted within 48h
    ranking          = db.Column(db.Float, default=100.0) # higher = more likely to be chosen on 2nd tentative
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('mediator_profile', uselist=False))


# ---------------------------------------------------------------------------
# Settings (admin-configurable)
# ---------------------------------------------------------------------------

class SiteSetting(db.Model):
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text, default="")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

class MediatorPayoutConfig(db.Model):
    """
    Per-mediator payout account: Stripe Connect and/or PayPal for receiving payments.
    One row per mediator (user_id unique).
    """
    __tablename__ = "mediator_payout_config"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True)
    stripe_connect_account_id = db.Column(db.String(120), nullable=True)  # Stripe Connect Express account id
    paypal_merchant_id = db.Column(db.String(120), nullable=True)  # or PayPal merchant/email for payouts
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("payout_config", uselist=False))


class MediationPayment(db.Model):
    """
    One payment transaction for a single participant in a mediation.
    All payments (fixed price, donation, pro-bono) are registered.
    Platform commission is applied to paid amounts; mediator_payout_cents is the remainder.
    """
    id = db.Column(db.Integer, primary_key=True)
    mediation_id = db.Column(db.Integer, db.ForeignKey('mediation.id'), nullable=False)
    participant_id = db.Column(db.Integer, db.ForeignKey('mediation_participant.id'), nullable=False)
    payer_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    provider = db.Column(db.String(20), nullable=False)  # stripe | paypal
    status = db.Column(db.String(20), default="pending")  # pending | paid | failed | cancelled
    # standard = fixed price, donation = party pays what they state, probono = fee waived
    kind = db.Column(db.String(20), default="standard")
    amount_cents = db.Column(db.Integer, nullable=False)
    platform_commission_cents = db.Column(db.Integer, default=0)  # platform share
    mediator_payout_cents = db.Column(db.Integer, nullable=True)  # amount - commission (for mediator)
    currency = db.Column(db.String(3), default="EUR")
    external_id = db.Column(db.String(200))  # session id / order id
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    paid_at = db.Column(db.DateTime)

    mediation = db.relationship('Mediation', back_populates='payments')
    participant = db.relationship('MediationParticipant', back_populates='payments')
    payer = db.relationship('User', backref='payments', foreign_keys=[payer_user_id])


# ---------------------------------------------------------------------------
# Admin audit log (mediation deletions)
# ---------------------------------------------------------------------------

class MediationDeletionLog(db.Model):
    """
    Record of a mediation deleted by an admin. Kept for audit; no FK to mediation.
    """
    __tablename__ = "mediation_deletion_log"
    id = db.Column(db.Integer, primary_key=True)
    mediation_id = db.Column(db.Integer, nullable=False)  # id at time of deletion (no FK)
    deleted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    deleted_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    snapshot = db.Column(db.Text, default="{}")  # JSON: title, phase, status, mediator_id, participant_count, created_at

    deleted_by = db.relationship("User", backref="mediation_deletions_log")