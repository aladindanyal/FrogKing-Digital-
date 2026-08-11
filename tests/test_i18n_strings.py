import pytest
import string
import re
from bot.i18n.strings import TRANSLATIONS
from bot.i18n.registry import DEFAULT_LOCALE

def extract_placeholders(text: str) -> list[tuple[str, str, str]]:
    """Extracts (field_name, format_spec, conversion) from string templates."""
    placeholders = []
    try:
        # We only care about valid placeholder parts
        for literal_text, field_name, format_spec, conversion in string.Formatter().parse(text):
            if field_name is not None:
                placeholders.append((field_name, format_spec or "", conversion or ""))
    except ValueError:
        pass
    return placeholders

def test_translation_dictionaries_exist():
    assert "ru" in TRANSLATIONS
    assert "en" in TRANSLATIONS
    assert "ar" in TRANSLATIONS

def test_key_parity():
    ru_keys = set(TRANSLATIONS["ru"].keys())
    en_keys = set(TRANSLATIONS["en"].keys())
    ar_keys = set(TRANSLATIONS["ar"].keys())

    assert len(ru_keys) == 444, f"Expected 444 keys in ru, found {len(ru_keys)}"
    assert ru_keys == en_keys, "en keys do not match ru keys exactly"
    assert ru_keys == ar_keys, "ar keys do not match ru keys exactly"

def test_no_blank_values():
    for lang, dict_obj in TRANSLATIONS.items():
        for key, value in dict_obj.items():
            assert value.strip() != "", f"Empty or whitespace-only value found for {lang} key '{key}'"

def test_placeholder_parity():
    ru_dict = TRANSLATIONS["ru"]
    for lang in ["en", "ar"]:
        target_dict = TRANSLATIONS[lang]
        for key, ru_text in ru_dict.items():
            target_text = target_dict[key]

            ru_placeholders = extract_placeholders(ru_text)
            target_placeholders = extract_placeholders(target_text)

            # Sort them because order in translated text might differ
            assert sorted(ru_placeholders) == sorted(target_placeholders), \
                f"Placeholder mismatch for key '{key}' in '{lang}'"

def test_html_preservation():
    html_tags_re = re.compile(r'</?[a-z]+[> ]', re.IGNORECASE)
    ru_dict = TRANSLATIONS["ru"]

    for lang in ["en", "ar"]:
        target_dict = TRANSLATIONS[lang]
        for key, ru_text in ru_dict.items():
            ru_tags = sorted(html_tags_re.findall(ru_text))
            if ru_tags:
                target_tags = sorted(html_tags_re.findall(target_dict[key]))
                assert ru_tags == target_tags, f"HTML mismatch for key '{key}' in '{lang}'"

def test_newline_preservation():
    ru_dict = TRANSLATIONS["ru"]

    for lang in ["en", "ar"]:
        target_dict = TRANSLATIONS[lang]
        for key, ru_text in ru_dict.items():
            ru_newlines = ru_text.count('\n')
            target_newlines = target_dict[key].count('\n')
            # Some translations might adjust spacing, but we assert they both have newlines if original has them
            if ru_newlines > 0:
                assert target_newlines > 0, f"Missing newline for key '{key}' in '{lang}'"
