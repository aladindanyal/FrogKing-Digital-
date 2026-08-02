import pytest
from bot.i18n.main import get_locale, localize, normalize_locale, current_locale
from bot.i18n.strings import DEFAULT_LOCALE

def test_default_locale_is_english():
    assert DEFAULT_LOCALE == "en", "The default locale must be strictly English (en)"

def test_normalize_locale():
    assert normalize_locale("en-US") == "en"
    assert normalize_locale("RU") == "ru"
    assert normalize_locale(None) is None
    assert normalize_locale("") is None
    assert normalize_locale("   ") is None

def test_get_locale_missing_resolves_to_english():
    token = current_locale.set(None)
    try:
        assert get_locale() == "en"
    finally:
        current_locale.reset(token)

def test_get_locale_unsupported_resolves_to_english():
    token = current_locale.set("fr")
    try:
        assert get_locale() == "en"
    finally:
        current_locale.reset(token)

def test_get_locale_supported():
    token = current_locale.set("ar")
    try:
        assert get_locale() == "ar"
    finally:
        current_locale.reset(token)

    token = current_locale.set("ru")
    try:
        assert get_locale() == "ru"
    finally:
        current_locale.reset(token)

def test_localize_fallback_arabic_to_english(monkeypatch):
    # If a key is missing in Arabic, it should fall back to English
    from bot.i18n.strings import TRANSLATIONS
    test_key = "test.missing.ar"

    # Mock translations just for this test
    mock_translations = {
        "en": {test_key: "English Fallback"},
        "ar": {},
        "ru": {test_key: "Russian text"}
    }
    monkeypatch.setattr("bot.i18n.main.TRANSLATIONS", mock_translations)
    monkeypatch.setattr("bot.i18n.main.DEFAULT_LOCALE", "en")

    token = current_locale.set("ar")
    try:
        assert localize(test_key) == "English Fallback"
    finally:
        current_locale.reset(token)

def test_localize_fallback_missing_everywhere(monkeypatch):
    test_key = "test.missing.everywhere"

    mock_translations = {
        "en": {},
        "ar": {},
        "ru": {}
    }
    monkeypatch.setattr("bot.i18n.main.TRANSLATIONS", mock_translations)

    token = current_locale.set("ar")
    try:
        assert localize(test_key) == test_key
    finally:
        current_locale.reset(token)
