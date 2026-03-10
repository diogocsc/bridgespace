"""
BridgeSpace - Legal pages (privacy policy, terms for mediators).
Serves the markdown documents from legal/ as HTML.
"""
import os
from flask import Blueprint, render_template, current_app

legal_bp = Blueprint("legal", __name__, url_prefix="/legal")


def _legal_path(filename):
    root = current_app.root_path
    return os.path.join(root, "legal", filename)


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
    """Terms for mediators (PT)."""
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
        return markdown.markdown(
            text,
            extensions=["extra", "nl2br"],
            extension_configs={"extra": {}},
        )
    except Exception:
        return "<p>Erro ao carregar o documento.</p>"
