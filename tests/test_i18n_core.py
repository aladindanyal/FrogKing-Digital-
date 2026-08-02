import pytest
from bot.i18n.main import normalize_locale, localize
from bot.i18n.strings import TRANSLATIONS, DEFAULT_LOCALE

def test_normalize_locale():
    assert normalize_locale("en-US") == "en"
    assert normalize_locale("en_US") == "en"
    assert normalize_locale("RU") == "ru"
    assert normalize_locale("ar-JO") == "ar"
    assert normalize_locale("") is None
    assert normalize_locale(None) is None

def test_localize_fallback():
    # If key doesn't exist anywhere
    assert localize("non.existent.key") == "non.existent.key"
    
    # Existing key should resolve to text
    # Assuming 'menu.title' exists in English or Russian
    assert "menu.title" in TRANSLATIONS.get("en", {}) or "menu.title" in TRANSLATIONS.get("ru", {})
