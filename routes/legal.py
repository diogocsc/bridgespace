"""
BridgeSpace - Legal pages (privacy policy, terms of use).
Serves the markdown documents from legal/ as HTML.
Company data (name, address, email, phone, fiscal number) is replaced from admin settings.
"""
import os
from flask import Blueprint, render_template, current_app

legal_bp = Blueprint("legal", __name__, url_prefix="/legal")


def _legal_path(filename):
    root = current_app.root_path
    return os.path.join(root, "legal", filename)


def _apply_company_placeholders(text: str) -> str:
    """Replace {{COMPANY_*}} and {{APP_NAME}} placeholders with values from settings/config."""
    from services.settings_service import get_company_placeholders

    out = text
    # Company identity placeholders
    for placeholder, value in get_company_placeholders().items():
        out = out.replace(placeholder, value)

    # App name placeholder for legal docs (terms, privacy)
    app_name = current_app.config.get("APP_NAME", "BridgeSpace")
    out = out.replace("{{APP_NAME}}", app_name)
    return out


@legal_bp.route("/privacidade")
def privacy():
    """Privacy policy (PT)."""
    path = _legal_path("politica-privacidade-pt.md")
    html = _md_to_html(path)
    return render_template(
        "legal/page.html",
        title_key="footer_privacy",
        content_html=html,
    )


@legal_bp.route("/termos-mediadores")
def terms_mediators():
    """Terms of use (PT) – all users and mediators."""
    path = _legal_path("termos-mediadores-pt.md")
    html = _md_to_html(path)
    return render_template(
        "legal/page.html",
        title_key="footer_terms",
        content_html=html,
    )


def _md_to_html(path):
    if not os.path.isfile(path):
        return "<p>Documento não disponível.</p>"
    try:
        import markdown
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        text = _apply_company_placeholders(text)
        return markdown.markdown(
            text,
            extensions=["extra", "nl2br"],
            extension_configs={"extra": {}},
        )
    except Exception:
        return "<p>Erro ao carregar o documento.</p>"
