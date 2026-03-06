"""Tests for local UI translations and default language."""
from services.translations import (
    DEFAULT_LANGUAGE,
    get_translations,
    translate,
    LOCALES,
)


def test_default_language_is_portuguese():
    assert DEFAULT_LANGUAGE == "pt"


def test_get_translations_returns_dict():
    t = get_translations("en")
    assert isinstance(t, dict)
    assert "welcome_back" in t
    assert t["welcome_back"] == "Welcome back"


def test_get_translations_pt():
    t = get_translations("pt")
    assert t["welcome_back"] == "Bem-vindo de volta"
    assert t["dashboard"] == "Painel"


def test_get_translations_unknown_falls_back_to_english():
    t = get_translations("xx")
    assert t["welcome_back"] == "Welcome back"


def test_translate_key_en():
    assert translate("invalid_login", "en") == "Invalid email or password."


def test_translate_key_pt():
    assert translate("invalid_login", "pt") == "E-mail ou palavra-passe incorretos."


def test_translate_unknown_key_returns_key():
    assert translate("nonexistent_key", "en") == "nonexistent_key"
