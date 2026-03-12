"""
BridgeSpace - Notification Service
Handles email and SMS for invitations, verification, and status alerts.
"""
import logging
import re
from flask import current_app, url_for, request
from flask_mail import Message
from extensions import mail

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────


def _external_url(endpoint: str, **values) -> str:
    """
    Build an absolute URL for emails.

    Priority:
    1. If PUBLIC_BASE_URL is set (e.g. https://mediador.diogocordeiro.pt),
       always use that as base, regardless of current request host.
    2. Otherwise, if inside a request context, rely on url_for(_external=True)
       together with ProxyFix + X-Forwarded-* from nginx.
    3. As a last resort, fall back to url_for(_external=True) even without a request,
       which will use SERVER_NAME / default host.
    """
    base = (current_app.config.get("PUBLIC_BASE_URL") or "").strip()
    if base:
        path = url_for(endpoint, _external=False, **values)
        return base.rstrip("/") + path
    try:
        # Normal case: we are inside a request and ProxyFix + nginx give the right host/scheme
        return url_for(endpoint, _external=True, **values)
    except RuntimeError:
        # No request context; fall back to app-level URL building
        return url_for(endpoint, _external=True, **values)

def _send_email(subject, recipients, html_body, text_body=""):
    try:
        plain = text_body or re.sub(r"<[^>]+>", "", html_body).strip()
        msg = Message(
            subject=subject, recipients=recipients,
            html=html_body, body=plain,
            sender=current_app.config.get("MAIL_DEFAULT_SENDER",
                                          "noreply@bridgespace.app"),
        )
        mail.send(msg)
        logger.info("Email sent to %s", recipients)
        return True
    except Exception as exc:
        logger.error("Email failed: %s", exc)
        return False


def _send_sms(phone, body):
    """
    SMS stub — uncomment and configure Twilio to activate.
    pip install twilio
    Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER in .env
    """
    # from twilio.rest import Client
    # c = Client(current_app.config["TWILIO_ACCOUNT_SID"],
    #            current_app.config["TWILIO_AUTH_TOKEN"])
    # c.messages.create(body=body,
    #                   from_=current_app.config["TWILIO_FROM_NUMBER"],
    #                   to=phone)
    logger.info("[SMS stub] To %s: %s", phone, body)
    return True


def _send_whatsapp(contact, body):
    """Send via WhatsApp (stub until API is wired in backoffice)."""
    try:
        from services.settings_service import whatsapp_enabled, whatsapp_api_key
        if not whatsapp_enabled() or not whatsapp_api_key():
            logger.debug("WhatsApp not configured; skip.")
            return True
        # TODO: wire to WhatsApp Business API or Twilio WhatsApp
        logger.info("[WhatsApp stub] To %s: %s", contact, body[:80])
        return True
    except Exception as e:
        logger.warning("WhatsApp send failed: %s", e)
        return False


def _send_telegram(contact, body):
    """Send via Telegram (stub until bot token is wired)."""
    try:
        from services.settings_service import telegram_enabled, telegram_bot_token
        if not telegram_enabled() or not telegram_bot_token():
            logger.debug("Telegram not configured; skip.")
            return True
        # TODO: use python-telegram-bot or requests to send to contact (chat_id or @username)
        logger.info("[Telegram stub] To %s: %s", contact, body[:80])
        return True
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)
        return False


def _send_signal(contact, body):
    """Send via Signal (stub until API is wired)."""
    try:
        from services.settings_service import signal_enabled, signal_api_url
        if not signal_enabled() or not signal_api_url():
            logger.debug("Signal not configured; skip.")
            return True
        # TODO: wire to signal-cli REST or third-party API
        logger.info("[Signal stub] To %s: %s", contact, body[:80])
        return True
    except Exception as e:
        logger.warning("Signal send failed: %s", e)
        return False


def dispatch_to_user_channels(user, subject, html_body, plain_body=None, link_url=None):
    """
    Send notification to all configured channels for this user:
    email (always if present), then WhatsApp / Telegram / Signal if user has them and backoffice enabled.
    """
    import re
    plain = plain_body or re.sub(r"<[^>]+>", "", html_body).strip()
    if link_url:
        plain += "\n\n" + link_url
    success = True
    if user.email:
        if not _send_email(subject, [user.email], html_body, plain):
            success = False
    if getattr(user, "whatsapp", None) and user.whatsapp.strip():
        if not _send_whatsapp(user.whatsapp.strip(), plain):
            success = False
    if getattr(user, "telegram", None) and user.telegram.strip():
        if not _send_telegram(user.telegram.strip(), plain):
            success = False
    if getattr(user, "signal", None) and user.signal.strip():
        if not _send_signal(user.signal.strip(), plain):
            success = False
    return success


def _wrap(title, content, lang=None):
    """Shared branded HTML email wrapper."""
    app_name = current_app.config.get("APP_NAME", "BridgeSpace")

    if lang is None:
        try:
            lang = _email_lang_default()
        except Exception:
            lang = "en"
    if lang == "pt":
        footer_text = (
            f"Esta mensagem foi enviada pelo {app_name}. "
            "Todo o conteúdo da mediação é estritamente confidencial."
        )
    else:
        footer_text = (
            f"This message was sent by {app_name}. "
            "All mediation content is strictly confidential."
        )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><style>
body{{font-family:Georgia,serif;background:#F5F0E8;margin:0;padding:0}}
.w{{max-width:560px;margin:40px auto;background:#FDFAF5;border-radius:12px;
    border:1px solid #DDD8CE;box-shadow:0 4px 24px rgba(0,0,0,.07)}}
.h{{background:#4A6E60;padding:28px 36px}}
.h h1{{color:#FDFAF5;font-size:1.3rem;margin:0}}
.h h1 span{{color:#E8C4A8}}
.b{{padding:32px 36px;color:#2C2C2C;line-height:1.7}}
.b h2{{font-size:1.1rem;margin:0 0 .8rem}}
.b p{{margin:0 0 1rem;font-size:.94rem}}
.btn{{display:inline-block;padding:12px 28px;background:#C4855A;color:#fff;
      border-radius:8px;text-decoration:none;font-size:.94rem;font-weight:600;margin:6px 0 16px}}
hr{{border:none;border-top:1px solid #DDD8CE;margin:1.2rem 0}}
.f{{padding:18px 36px;background:#F0EBE1;font-size:.76rem;color:#7A7A7A;
    border-top:1px solid #DDD8CE}}
    </style></head><body><div class="w">
<div class="h"><h1>{app_name}</h1></div>
<div class="b"><h2>{title}</h2>{content}</div>
<div class="f">{footer_text}</div>
</div></body></html>"""


# ── Public API ─────────────────────────────────────────────────────────────

def _email_lang_default() -> str:
    """
    Global default email language, configured in admin. Falls back to English.
    """
    try:
        from services.settings_service import email_language
        return email_language()
    except Exception:
        return "en"


def _lang_for_user(user) -> str:
    """
    Language for a registered user:
    - use user.preferred_language if supported
    - otherwise use global default email language.
    """
    try:
        from services.translations import LOCALES
        if user is not None:
            pref = getattr(user, "preferred_language", None)
            if pref in LOCALES:
                return pref
        return _email_lang_default()
    except Exception:
        return "en"


def _lang_for_contact(contact: str) -> str:
    """
    Language for an arbitrary email contact.
    If a user with this email exists, use their preferred_language.
    Otherwise use global default.
    """
    try:
        if "@" in (contact or ""):
            from models import User
            u = User.query.filter_by(email=contact).first()
            if u:
                return _lang_for_user(u)
        return _email_lang_default()
    except Exception:
        return _email_lang_default()

def send_verification_email(user):
    """Email address verification link for newly registered users."""
    url = _external_url("auth.verify_email", token=user.verification_token)
    app_name = current_app.config.get("APP_NAME", "BridgeSpace")
    lang = _lang_for_user(user)
    if lang == "pt":
        c = (
            f"<p>Bem-vindo(a), <strong>{user.display_name}</strong>!</p>"
            f"<p>Por favor confirme o seu e-mail para ativar a conta:</p>"
            f"<a href='{url}' class='btn'>Confirmar o meu e-mail</a><hr>"
            f"<p style='word-break:break-all;font-size:.82rem;color:#7A7A7A;'>{url}</p>"
            f"<p>Esta ligação é válida durante <strong>48 horas</strong>.</p>"
        )
        subject = f"Confirme a sua conta {app_name}"
        title = "Confirmar endereço de e-mail"
    else:
        c = (
            f"<p>Welcome, <strong>{user.display_name}</strong>!</p>"
            f"<p>Please verify your email to activate your account:</p>"
            f"<a href='{url}' class='btn'>Verify my email</a><hr>"
            f"<p style='word-break:break-all;font-size:.82rem;color:#7A7A7A;'>{url}</p>"
            f"<p>This link is valid for <strong>48 hours</strong>.</p>"
        )
        subject = f"Verify your {app_name} account"
        title = "Confirm your email address"
    return _send_email(subject, [user.email], _wrap(title, c, lang))


def send_password_reset_email(user):
    """Password-reset link."""
    url = _external_url("auth.reset_password", token=user.reset_token)
    app_name = current_app.config.get("APP_NAME", "BridgeSpace")
    lang = _lang_for_user(user)
    if lang == "pt":
        c = (
            f"<p>Olá <strong>{user.display_name}</strong>,</p>"
            f"<p>Reponha a sua palavra-passe do {app_name}:</p>"
            f"<a href='{url}' class='btn'>Repor palavra-passe</a><hr>"
            f"<p style='word-break:break-all;font-size:.82rem;color:#7A7A7A;'>{url}</p>"
            f"<p>A ligação expira em <strong>1 hora</strong>. "
            f"Ignore esta mensagem se não fez este pedido.</p>"
        )
        subject = f"Repor palavra-passe {app_name}"
        title = "Pedido de reposição de palavra-passe"
    else:
        c = (
            f"<p>Hi <strong>{user.display_name}</strong>,</p>"
            f"<p>Reset your {app_name} password:</p>"
            f"<a href='{url}' class='btn'>Reset my password</a><hr>"
            f"<p style='word-break:break-all;font-size:.82rem;color:#7A7A7A;'>{url}</p>"
            f"<p>Expires in <strong>1 hour</strong>. "
            f"Ignore this if you did not make this request.</p>"
        )
        subject = f"Reset your {app_name} password"
        title = "Password reset request"
    return _send_email(subject, [user.email], _wrap(title, c, lang))


def send_mediation_invitation_email(invitation, mediation, invited_by):
    """Invite an external party by email."""
    url = _external_url("mediation.join_via_invite", token=invitation.token)
    mode_en = ("Asynchronous — reply at your own pace"
               if mediation.mode == "async" else "Live — real-time session")
    mode_pt = ("Assíncrono — responder ao seu ritmo"
               if mediation.mode == "async" else "Ao vivo — sessão em tempo real")
    date_line = (
        f"<p><strong>Scheduled start:</strong> "
        f"{mediation.start_date.strftime('%d %b %Y at %H:%M UTC')}</p>"
        if mediation.start_date else ""
    )
    desc_line = (
        f"<p><strong>Description:</strong> {mediation.description}</p>"
        if mediation.description else ""
    )
    lang = _lang_for_contact(invitation.contact)
    if lang == "pt":
        c = (
            f"<p><strong>{invited_by.display_name}</strong> convidou-o(a) para uma "
            f"mediação confidencial no BridgeSpace.</p>"
            f"<p><strong>Mediação:</strong> {mediation.title}</p>"
            f"{desc_line}"
            f"<p><strong>Modo:</strong> {mode_pt}</p>"
            f"{date_line}<hr>"
            f"<p>Ser-lhe-á pedido que crie uma conta gratuita se ainda não tiver uma. "
            f"<strong>Todo o conteúdo é estritamente confidencial.</strong></p>"
            f"<a href='{url}' class='btn'>Aceitar convite →</a><hr>"
            f"<p style='word-break:break-all;font-size:.82rem;color:#7A7A7A;'>{url}</p>"
        )
        subject = f"Convite para mediação: {mediation.title}"
        title = "Convite para mediação"
    else:
        c = (
            f"<p><strong>{invited_by.display_name}</strong> has invited you to a "
            f"confidential mediation on BridgeSpace.</p>"
            f"<p><strong>Mediation:</strong> {mediation.title}</p>"
            f"{desc_line}"
            f"<p><strong>Mode:</strong> {mode_en}</p>"
            f"{date_line}<hr>"
            f"<p>You will be asked to create a free account if you do not "
            f"already have one. <strong>All content is strictly confidential.</strong></p>"
            f"<a href='{url}' class='btn'>Accept invitation →</a><hr>"
            f"<p style='word-break:break-all;font-size:.82rem;color:#7A7A7A;'>{url}</p>"
        )
        subject = f"Invitation to mediation: {mediation.title}"
        title = "Mediation invitation"
    return _send_email(subject, [invitation.contact], _wrap(title, c, lang))


def send_mediation_invitation_sms(invitation, mediation, invited_by):
    """Invite an external party by SMS."""
    url = _external_url("mediation.join_via_invite", token=invitation.token)
    body = (f"BridgeSpace: {invited_by.display_name} invited you to "
            f'mediation "{mediation.title}". Join: {url}')
    return _send_sms(invitation.contact, body)


def send_new_post_notification(post, mediation):
    """
    Notify other active participants that a new post was submitted.
    Skipped for live mediations — SocketIO handles real-time delivery there.
    """
    if mediation.mode == "live":
        return True

    from models import MediationParticipant
    participants = MediationParticipant.query.filter_by(
        mediation_id=mediation.id, is_active=True
    ).all()
    url = _external_url("mediation.session", mediation_id=mediation.id)
    success = True
    for p in participants:
        if p.user_id == post.author_id:
            continue
        lang = _lang_for_user(p.user)
        c = (f"<p>Hi <strong>{p.user.display_name}</strong>,</p>"
             f"<p>A new message was posted in "
             f"<strong>\"{mediation.title}\"</strong>.</p>"
             f"<p>Log in to read it and reply at your own pace.</p>"
             f"<a href='{url}' class='btn'>View mediation →</a>")
        if not _send_email(
            f"New message in: {mediation.title}",
            [p.user.email],
            _wrap("New message in your mediation", c, lang),
        ):
            success = False
    return success


def send_mediation_status_change(mediation, new_status):
    """Notify all participants when mediation status changes."""
    from models import MediationParticipant
    participants = MediationParticipant.query.filter_by(
        mediation_id=mediation.id, is_active=True
    ).all()
    url = _external_url("mediation.session", mediation_id=mediation.id)
    labels = {
        "active":  {
            "en": ("Mediation is now active",
                   "The session has started — you may now post your messages."),
            "pt": ("A mediação está agora ativa",
                   "A sessão começou — já pode publicar as suas mensagens."),
        },
        "closed":  {
            "en": ("Mediation has been closed",
                   "This mediation is closed. No further posts can be added."),
            "pt": ("A mediação foi encerrada",
                   "Esta mediação está encerrada. Não podem ser adicionadas novas publicações."),
        },
        "pending": {
            "en": ("Mediation scheduled",
                   "The mediation is scheduled and will open at the designated time."),
            "pt": ("Mediação agendada",
                   "A mediação está agendada e abrirá na data e hora definidas."),
        },
    }
    # Fallback English texts
    default_title_en = "Mediation update"
    default_desc_en = "The status of your mediation has changed."
    success = True
    for p in participants:
        lang = _lang_for_user(p.user)
        texts = labels.get(new_status, {}).get(lang)
        if not texts:
            texts = labels.get(new_status, {}).get("en", (default_title_en, default_desc_en))
        title, desc = texts
        if lang == "pt":
            c = (
                f"<p>Olá <strong>{p.user.display_name}</strong>,</p>"
                f"<p>{desc}</p>"
                f"<p><strong>{mediation.title}</strong></p>"
                f"<a href='{url}' class='btn'>Abrir mediação →</a>"
            )
            subj = f"BridgeSpace: {title}"
        else:
            c = (
                f"<p>Hi <strong>{p.user.display_name}</strong>,</p>"
                f"<p>{desc}</p>"
                f"<p><strong>{mediation.title}</strong></p>"
                f"<a href='{url}' class='btn'>Open mediation →</a>"
            )
            subj = f"BridgeSpace: {title}"
        if not _send_email(subj, [p.user.email], _wrap(title, c, lang)):
            success = False
    return success


def dispatch_invitation(invitation, mediation, invited_by):
    """
    Unified dispatcher — routes to email or SMS based on invitation.contact_type.
    Call this after persisting the MediationInvitation record to the database.
    """
    if invitation.contact_type == "email":
        return send_mediation_invitation_email(invitation, mediation, invited_by)
    if invitation.contact_type == "phone":
        return send_mediation_invitation_sms(invitation, mediation, invited_by)
    logger.warning("Unknown contact_type '%s' on invitation %s",
                   invitation.contact_type, invitation.id)
    return False


def send_ask_mediator_explanation_email(mediation, requested_by):
    """
    Notify the mediator on all configured channels that a participant requested an explanation.
    Link to the pre-mediation page so they can fill it in.
    """
    mediator = mediation.mediator
    if not mediator:
        return False
    url = _external_url("mediation.pre_mediation", mediation_id=mediation.id)
    requester_name = requested_by.display_name or requested_by.username or "A participant"
    lang = _lang_for_user(mediator)
    if lang == "pt":
        c = (
            f"<p>Olá <strong>{mediator.display_name}</strong>,</p>"
            f"<p><strong>{requester_name}</strong> pediu uma explicação sobre o processo de mediação "
          f"para a sessão <strong>\"{mediation.title}\"</strong>.</p>"
            f"<p>As partes só podem marcar a etapa de pré-mediação como lida depois de adicionar uma explicação. "
            f"Por favor adicione a sua explicação abaixo para que possam prosseguir.</p>"
            f"<a href='{url}' class='btn'>Adicionar explicação →</a><hr>"
            f"<p style='word-break:break-all;font-size:.82rem;color:#7A7A7A;'>{url}</p>"
        )
        subject = f"Pedido de explicação: {mediation.title}"
        title = "Participante pediu explicação do processo"
    else:
        c = (
            f"<p>Hi <strong>{mediator.display_name}</strong>,</p>"
            f"<p><strong>{requester_name}</strong> has asked for an explanation of the mediation process "
            f"for the session <strong>\"{mediation.title}\"</strong>.</p>"
            f"<p>Participants can only mark the pre-mediation step as read after you add an explanation. "
            f"Please add your explanation below so they can proceed.</p>"
            f"<a href='{url}' class='btn'>Add explanation →</a><hr>"
            f"<p style='word-break:break-all;font-size:.82rem;color:#7A7A7A;'>{url}</p>"
        )
        subject = f"Explanation requested: {mediation.title}"
        title = "Participant requested process explanation"
    html = _wrap(title, c, lang)
    return dispatch_to_user_channels(mediator, subject, html, link_url=url)


def send_pre_mediation_confirmation_request(mediation):
    """
    When the mediator adds or updates the pre-mediation explanation, email all required parties
    who have not yet acknowledged, asking them to confirm they have read the explanation.
    """
    from models import MediationParticipant
    url = _external_url("mediation.pre_mediation", mediation_id=mediation.id)
    # Required parties (non-mediator, is_required) who have not acknowledged yet
    participants = [
        p for p in mediation.participants
        if p.is_active
        and p.role != "mediator"
        and getattr(p, "is_required", True)
        and not p.pre_mediation_acknowledged
        and p.user_id
        and getattr(p.user, "email", None)
    ]
    # One email per user (in case of duplicate participant rows)
    seen_user_ids = set()
    success = True
    for p in participants:
        if p.user_id in seen_user_ids:
            continue
        seen_user_ids.add(p.user_id)
        user = p.user
        lang = _lang_for_user(user)
        if lang == "pt":
            c = (
                f"<p>Olá <strong>{user.display_name or user.username}</strong>,</p>"
                f"<p>O mediador adicionou ou atualizou a explicação do processo de mediação "
                f"para a sessão <strong>\"{mediation.title}\"</strong>.</p>"
                f"<p>Por favor leia a explicação e confirme que a leu para poder avançar.</p>"
                f"<a href='{url}' class='btn'>Ler e confirmar →</a><hr>"
                f"<p style='word-break:break-all;font-size:.82rem;color:#7A7A7A;'>{url}</p>"
            )
            subject = f"Confirmar leitura da explicação: {mediation.title}"
            title = "Explicação de pré-mediação disponível"
        else:
            c = (
                f"<p>Hi <strong>{user.display_name or user.username}</strong>,</p>"
                f"<p>The mediator has added or updated the mediation process explanation "
                f"for the session <strong>\"{mediation.title}\"</strong>.</p>"
                f"<p>Please read the explanation and confirm you have read it so you can proceed.</p>"
                f"<a href='{url}' class='btn'>Read and confirm →</a><hr>"
                f"<p style='word-break:break-all;font-size:.82rem;color:#7A7A7A;'>{url}</p>"
            )
            subject = f"Confirm you have read the explanation: {mediation.title}"
            title = "Pre-mediation explanation available"
        html = _wrap(title, c, lang)
        if not dispatch_to_user_channels(user, subject, html, link_url=url):
            success = False
    return success


def send_mediator_availability_request(mediation, mediator_user):
    """
    Notify the selected mediator on all their configured channels (email, WhatsApp, Telegram, Signal).
    They must confirm availability within 48 hours.
    """
    if not mediator_user:
        return False
    confirm_url = _external_url(
        "mediation.confirm_mediator_availability",
        mediation_id=mediation.id,
    )
    lang = _lang_for_user(mediator_user)
    if lang == "pt":
        c = (
            f"<p>Olá <strong>{mediator_user.display_name}</strong>,</p>"
            f"<p>Foi selecionado(a) como mediador(a) para a sessão "
            f"<strong>\"{mediation.title}\"</strong>.</p>"
            f"<p>Por favor <strong>confirme a sua disponibilidade nas próximas 48 horas</strong>. "
            f"Se não confirmar a tempo, outro mediador poderá ser atribuído e a sua classificação poderá ser afetada.</p>"
            f"<a href='{confirm_url}' class='btn'>Confirmar disponibilidade</a><hr>"
            f"<p style='word-break:break-all;font-size:.82rem;color:#7A7A7A;'>{confirm_url}</p>"
        )
        subject = f"Confirmar disponibilidade: {mediation.title}"
        title = "Confirmação de disponibilidade do mediador"
    else:
        c = (
            f"<p>Hi <strong>{mediator_user.display_name}</strong>,</p>"
            f"<p>You have been selected as mediator for the session "
            f"<strong>\"{mediation.title}\"</strong>.</p>"
            f"<p>Please <strong>confirm your availability within 48 hours</strong>. "
            f"If you do not confirm in time, another mediator may be assigned and your ranking may be affected.</p>"
            f"<a href='{confirm_url}' class='btn'>Confirm my availability</a><hr>"
            f"<p style='word-break:break-all;font-size:.82rem;color:#7A7A7A;'>{confirm_url}</p>"
        )
        subject = f"Confirm availability: {mediation.title}"
        title = "Mediator availability confirmation"
    html = _wrap(title, c, lang)
    return dispatch_to_user_channels(mediator_user, subject, html, link_url=confirm_url)


def send_mediator_unconfirmed_alert_to_admins(mediation):
    """
    Notify all admins and superadmins on all their configured channels when no mediator confirmed after 2nd tentative.
    """
    from models import User
    admins = User.query.filter(User.role.in_(("admin", "superadmin"))).all()
    if not admins:
        return True
    url = _external_url("mediation.session", mediation_id=mediation.id)
    lang = _email_lang_default()
    if lang == "pt":
        c = (
            f"<p>A mediação <strong>\"{mediation.title}\"</strong> (ID: {mediation.id}) teve "
            f"<strong>duas convites a mediadores expirados sem confirmação</strong> nas últimas 48 horas.</p>"
            f"<p>Por favor atribua um mediador ou tome uma ação a partir do backoffice.</p>"
            f"<a href='{url}' class='btn'>Abrir mediação</a><hr>"
            f"<p style='word-break:break-all;font-size:.82rem;color:#7A7A7A;'>{url}</p>"
        )
        subject = f"Ação necessária: nenhum mediador confirmou para \"{mediation.title}\""
        title = "Tempo limite de confirmação do mediador"
    else:
        c = (
            f"<p>The mediation <strong>\"{mediation.title}\"</strong> (ID: {mediation.id}) has had "
            f"<strong>two mediator invitations expire without confirmation</strong> within 48 hours.</p>"
            f"<p>Please assign a mediator or take action from the backoffice.</p>"
            f"<a href='{url}' class='btn'>Open mediation</a><hr>"
            f"<p style='word-break:break-all;font-size:.82rem;color:#7A7A7A;'>{url}</p>"
        )
        subject = f"Action required: no mediator confirmed for \"{mediation.title}\""
        title = "Mediator confirmation timeout"
    html = _wrap(title, c, lang)
    success = True
    for u in admins:
        if not dispatch_to_user_channels(u, subject, html, link_url=url):
            success = False
    return success


def send_payment_config_changed_notification():
    """
    Notify all admins and superadmins that Stripe (payment) configuration was changed.
    """
    from models import User
    admins = User.query.filter(User.role.in_(("admin", "superadmin"))).all()
    if not admins:
        return True
    url = _external_url("admin.payment_settings")
    lang = _email_lang_default()
    if lang == "pt":
        c = (
            "<p>A <strong>configuração de pagamentos Stripe</strong> no backoffice foi alterada.</p>"
            "<p>Se não efetuou esta alteração, reveja as definições e assegure-se de que apenas pessoas autorizadas têm acesso de superadministrador.</p>"
            f"<a href='{url}' class='btn'>Ver definições de pagamento</a><hr>"
            f"<p style='word-break:break-all;font-size:.82rem;color:#7A7A7A;'>{url}</p>"
        )
        subject = "BridgeSpace: Configuração de pagamentos atualizada"
        title = "Configuração de pagamentos alterada"
    else:
        c = (
            "<p>The <strong>Stripe payment configuration</strong> in the backoffice has been changed.</p>"
            "<p>If you did not make this change, please review the settings and ensure only authorised personnel have superadmin access.</p>"
            f"<a href='{url}' class='btn'>View payment settings</a><hr>"
            f"<p style='word-break:break-all;font-size:.82rem;color:#7A7A7A;'>{url}</p>"
        )
        subject = "BridgeSpace: Payment configuration updated"
        title = "Payment configuration changed"
    html = _wrap(title, c, lang)
    success = True
    for u in admins:
        if not dispatch_to_user_channels(u, subject, html, link_url=url):
            success = False
    return success