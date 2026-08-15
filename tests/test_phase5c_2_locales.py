
import pytest
from bot.i18n.registry import LOCALE_METADATA, is_supported, get_enabled_locales, normalize_locale
from bot.i18n.dynamic import get_localized_field, get_localized_jsonb

def test_seven_locale_static_integrity():
    for loc in ["en", "ar", "ru", "zh", "vi", "tr", "es"]:
        assert loc in LOCALE_METADATA

def test_translation_formatting_and_placeholders():
    from bot.i18n.strings import TRANSLATIONS
    en_pack = TRANSLATIONS['en']
    import re
    for loc in ["ru", "zh", "vi", "tr", "es", "ar"]:
        pack = TRANSLATIONS[loc]
        for k, v in en_pack.items():
            assert k in pack
            val = pack[k]
            assert val.strip() != ""
            en_ph = set(re.findall(r'\{[a-zA-Z_]+\}', v))
            loc_ph = set(re.findall(r'\{[a-zA-Z_]+\}', val))
            assert en_ph == loc_ph, f"Placeholder mismatch on {k} for {loc}"

def test_scalar_fallback():
    class DummyModel:
        name_en = "Name EN"
        name_ar = None
        name_ru = "Name RU"
        name_zh = None
        name_vi = "Name VI"
        name_tr = None
        name_es = "Name ES"
        name = "Fallback Name"

    m = DummyModel()
    assert get_localized_field(m, "name", "en") == "Name EN"
    assert get_localized_field(m, "name", "vi") == ("Name VI" if LOCALE_METADATA["vi"]["enabled"] else "Name EN")
    assert get_localized_field(m, "name", "tr") == "Name EN" # falls back to EN
    assert get_localized_field(m, "name", "ar") == "Name EN"

def test_jsonb_fallback():
    jsonb = {"en": "EN val", "ru": "RU val"}
    assert get_localized_jsonb(jsonb, "en") == "EN val"
    assert get_localized_jsonb(jsonb, "zh") == "EN val"
    assert get_localized_jsonb(jsonb, "ar") == "EN val"

def test_jsonb_null():
    assert get_localized_jsonb(None, "zh") == ""
    assert get_localized_jsonb({}, "es") == ""

def test_traditional_chinese_isolation():
    # normalize_locale returns None if disabled, so we test canonicalize_locale directly
    from bot.i18n.registry import canonicalize_locale
    assert canonicalize_locale("zh") == "zh"
    assert canonicalize_locale("zh-CN") == "zh"
    assert canonicalize_locale("zh-TW") == "zh-tw"
