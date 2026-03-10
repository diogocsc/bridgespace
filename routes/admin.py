"""
routes/admin.py

Simple admin/backoffice for:
 - Payment settings (Stripe / PayPal keys, default price)
 - User management (roles, mediator flag)
 - Mediation overview (basic list, delete with audit log)
"""

import json
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from extensions import db
from models import User, Mediation, MediationDeletionLog
from services.settings_service import (
    stripe_public_key,
    stripe_secret_key,
    stripe_webhook_secret,
    paypal_client_id,
    paypal_client_secret,
    platform_commission_percent,
    email_language,
    set_setting,
    whatsapp_enabled,
    whatsapp_api_key,
    telegram_enabled,
    telegram_bot_token,
    signal_enabled,
    signal_api_url,
    free_quota_default,
    pro_plan_quota_per_month,
    pro_plan_price_eur,
    enterprise_plan_price_eur,
    bulk_price_per_mediation_eur,
)


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _require_admin():
    if not getattr(current_user, "is_admin", False):
        from flask import abort
        abort(403)


def _require_superadmin():
    if not getattr(current_user, "is_superadmin", False):
        from flask import abort
        abort(403)


@admin_bp.route("/")
@login_required
def dashboard():
    _require_admin()
    stats = {
        "users": User.query.count(),
        "mediations": Mediation.query.count(),
    }
    return render_template("admin/dashboard.html", stats=stats)


@admin_bp.route("/settings/payments", methods=["GET", "POST"])
@login_required
def payment_settings():
    _require_admin()
    _require_superadmin()

    if request.method == "POST":
        set_setting("STRIPE_PUBLIC_KEY", request.form.get("stripe_public_key", "").strip())
        set_setting("STRIPE_SECRET_KEY", request.form.get("stripe_secret_key", "").strip())
        set_setting("STRIPE_WEBHOOK_SECRET", request.form.get("stripe_webhook_secret", "").strip())
        set_setting("PAYPAL_CLIENT_ID", request.form.get("paypal_client_id", "").strip())
        set_setting("PAYPAL_CLIENT_SECRET", request.form.get("paypal_client_secret", "").strip())
        set_setting("PLATFORM_COMMISSION_PERCENT", request.form.get("platform_commission_percent", "5").strip())
        set_setting("FREE_QUOTA_DEFAULT_PER_MONTH", request.form.get("free_quota_default_per_month", "3").strip())
        set_setting("PRO_PLAN_QUOTA_PER_MONTH", request.form.get("pro_plan_quota_per_month", "15").strip())
        set_setting("PRO_PLAN_PRICE_EUR", request.form.get("pro_plan_price_eur", "50").strip())
        set_setting("ENTERPRISE_PLAN_PRICE_EUR", request.form.get("enterprise_plan_price_eur", "100").strip())
        set_setting("BULK_PRICE_PER_MEDIATION_EUR", request.form.get("bulk_price_per_mediation_eur", "10").strip())
        lang = (request.form.get("email_language", "") or "").strip().lower()
        if lang not in ("en", "pt"):
            lang = "en"
        set_setting("EMAIL_LANGUAGE", lang)
        try:
            from services.notification import send_payment_config_changed_notification
            send_payment_config_changed_notification()
        except Exception:
            pass
        flash("Payment settings saved. All admins have been notified.", "success")
        return redirect(url_for("admin.payment_settings"))

    return render_template(
        "admin/payment_settings.html",
        stripe_pub=stripe_public_key(),
        stripe_sec=stripe_secret_key(),
        stripe_webhook_secret=stripe_webhook_secret(),
        paypal_id=paypal_client_id(),
        paypal_sec=paypal_client_secret(),
        platform_commission_percent=platform_commission_percent(),
        email_language=email_language(),
        free_quota_default_per_month=free_quota_default(),
        pro_plan_quota_per_month=pro_plan_quota_per_month(),
        pro_plan_price_eur=pro_plan_price_eur(),
        enterprise_plan_price_eur=enterprise_plan_price_eur(),
        bulk_price_per_mediation_eur=bulk_price_per_mediation_eur(),
    )


@admin_bp.route("/users", methods=["GET", "POST"])
@login_required
def users():
    _require_admin()

    if request.method == "POST":
        user_id = request.form.get("user_id", type=int)
        role = request.form.get("role", "").strip()
        if user_id and role in ("user", "mediator", "admin", "superadmin"):
            u = User.query.get_or_404(user_id)
            # Only superadmin can grant superadmin
            if role == "superadmin" and not current_user.is_superadmin:
                flash("Only superadmin can assign superadmin role.", "danger")
            else:
                u.role = role
                db.session.commit()
                flash("User role updated.", "success")
        return redirect(url_for("admin.users"))

    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=users)


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
def delete_user(user_id):
    """Superadmin-only: delete (anonymise) a user profile. Contact details removed; mediation data kept."""
    _require_admin()
    if not current_user.is_superadmin:
        from services.translations import translate
        lang = getattr(current_user, "preferred_language", "en")
        flash(translate("only_superadmin_can_delete_users", lang), "danger")
        return redirect(url_for("admin.users"))

    target = User.query.get_or_404(user_id)
    if getattr(target, "deleted_at", None):
        return redirect(url_for("admin.users"))

    from services.user_deletion import anonymise_user
    anonymise_user(target)
    db.session.commit()

    from services.translations import translate
    lang = getattr(current_user, "preferred_language", "en")
    flash(translate("user_deleted", lang), "success")

    if target.id == current_user.id:
        from flask_login import logout_user
        logout_user()
        return redirect(url_for("auth.login"))
    return redirect(url_for("admin.users"))


@admin_bp.route("/mediations")
@login_required
def mediations():
    _require_admin()
    meds = Mediation.query.order_by(Mediation.created_at.desc()).limit(200).all()
    return render_template("admin/mediations.html", mediations=meds)


@admin_bp.route("/mediations/<int:mediation_id>/delete", methods=["POST"])
@login_required
def delete_mediation(mediation_id):
    """Admin-only: delete a mediation and record the deletion in the log."""
    _require_admin()
    med = Mediation.query.get_or_404(mediation_id)
    snapshot = {
        "title": med.title,
        "phase": med.phase,
        "status": med.status,
        "mediation_type": getattr(med, "mediation_type", "structured"),
        "mediator_id": med.mediator_id,
        "participant_count": len(med.participants) if med.participants else 0,
        "created_at": med.created_at.isoformat() if med.created_at else None,
    }
    log_entry = MediationDeletionLog(
        mediation_id=med.id,
        deleted_by_user_id=current_user.id,
        snapshot=json.dumps(snapshot, default=str),
    )
    db.session.add(log_entry)
    med.agreement_post_id = None  # avoid FK constraint when cascade-deleting posts
    db.session.delete(med)
    db.session.commit()
    flash("Mediation deleted. The deletion has been recorded in the audit log.", "success")
    return redirect(url_for("admin.mediations"))


@admin_bp.route("/mediator-metrics")
@login_required
def mediator_metrics_overview():
    """Admins/superadmins: list all mediators with their metrics."""
    _require_admin()
    from services.mediator_metrics_service import (
        get_all_mediator_ids,
        get_mediator_metrics,
        format_duration_hours,
    )
    mediator_ids = get_all_mediator_ids()
    mediators_with_metrics = []
    for uid in mediator_ids:
        u = User.query.get(uid)
        if not u:
            continue
        m = get_mediator_metrics(uid)
        m["user"] = u
        m["explanation_response_display"] = format_duration_hours(m["explanation_response_avg_hours"])
        m["confirmation_response_display"] = format_duration_hours(m["confirmation_response_avg_hours"])
        mediators_with_metrics.append(m)
    # Sort by mediations_opened desc
    mediators_with_metrics.sort(key=lambda x: x["mediations_opened"], reverse=True)
    return render_template(
        "admin/mediator_metrics.html",
        mediators_with_metrics=mediators_with_metrics,
    )


@admin_bp.route("/mediator-metrics/<int:user_id>")
@login_required
def mediator_metrics_detail(user_id):
    """Admins/superadmins: view one mediator's metrics."""
    _require_admin()
    u = User.query.get_or_404(user_id)
    if u.role != "mediator" and not Mediation.query.filter_by(mediator_id=u.id).first():
        from flask import abort
        abort(404)
    from services.mediator_metrics_service import get_mediator_metrics, format_duration_hours
    metrics = get_mediator_metrics(u.id)
    metrics["explanation_response_display"] = format_duration_hours(metrics["explanation_response_avg_hours"])
    metrics["confirmation_response_display"] = format_duration_hours(metrics["confirmation_response_avg_hours"])
    return render_template(
        "admin/mediator_metrics_detail.html",
        mediator_user=u,
        metrics=metrics,
    )


@admin_bp.route("/settings/integrations", methods=["GET", "POST"])
@login_required
def integration_settings():
    """Superadmin-only: WhatsApp, Telegram, Signal integration config."""
    _require_admin()
    _require_superadmin()

    if request.method == "POST":
        set_setting("WHATSAPP_ENABLED", "1" if request.form.get("whatsapp_enabled") else "0")
        set_setting("WHATSAPP_API_KEY", request.form.get("whatsapp_api_key", "").strip())
        set_setting("TELEGRAM_ENABLED", "1" if request.form.get("telegram_enabled") else "0")
        set_setting("TELEGRAM_BOT_TOKEN", request.form.get("telegram_bot_token", "").strip())
        set_setting("SIGNAL_ENABLED", "1" if request.form.get("signal_enabled") else "0")
        set_setting("SIGNAL_API_URL", request.form.get("signal_api_url", "").strip())
        flash("Integration settings saved.", "success")
        return redirect(url_for("admin.integration_settings"))

    return render_template(
        "admin/integration_settings.html",
        whatsapp_enabled=whatsapp_enabled(),
        whatsapp_api_key=whatsapp_api_key(),
        telegram_enabled=telegram_enabled(),
        telegram_bot_token=telegram_bot_token(),
        signal_enabled=signal_enabled(),
        signal_api_url=signal_api_url(),
    )

