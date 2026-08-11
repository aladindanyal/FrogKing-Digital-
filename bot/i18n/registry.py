from typing import Dict, List, Optional, Any

LOCALE_METADATA: Dict[str, Dict[str, Any]] = {
    "en": {"name": "English", "rtl": False, "enabled": True},
    "ar": {"name": "العربية", "rtl": True, "enabled": True},
    "ru": {"name": "Русский", "rtl": False, "enabled": True},
    "zh": {"name": "简体中文", "rtl": False, "enabled": False},
    "vi": {"name": "Tiếng Việt", "rtl": False, "enabled": False},
    "tr": {"name": "Türkçe", "rtl": False, "enabled": False},
    "es": {"name": "Español", "rtl": False, "enabled": False},
    "id": {"name": "Bahasa Indonesia", "rtl": False, "enabled": False},
    "hi": {"name": "हिन्दी", "rtl": False, "enabled": False},
    "bn": {"name": "বাংলা", "rtl": False, "enabled": False},
}

DEFAULT_LOCALE = "en"

LOCALE_ALIASES: Dict[str, str] = {
    "zh-hans": "zh",
    "zh-cn": "zh",
    "zh-sg": "zh",
    "id-id": "id",
    "in": "id",
    "in-id": "id",
    "en-us": "en",
    "en-gb": "en",
    "ar-ae": "ar",
    "ar-sa": "ar",
    "ar-eg": "ar",
    "ar-jo": "ar",
    "ru-ru": "ru",
    "vi-vn": "vi",
    "tr-tr": "tr",
    "es-es": "es",
    "es-mx": "es",
    "es-419": "es",
    "es-ar": "es",
    "hi-in": "hi",
    "bn-bd": "bn",
    "bn-in": "bn"
}

UNSUPPORTED_EXPLICIT = {
    "zh-hant", "zh-tw", "zh-hk", "zh-mo"
}

def canonicalize_locale(loc: Optional[str]) -> Optional[str]:
    """
    Safely canonicalize a raw locale string.
    - case-insensitive
    - accepts _ and -
    - strips whitespace
    - maps aliases
    - explicitly blocks zh-hant from becoming zh
    """
    if not loc:
        return None
    loc = loc.strip().lower().replace("_", "-")

    if loc in UNSUPPORTED_EXPLICIT:
        return loc

    if loc in LOCALE_ALIASES:
        return LOCALE_ALIASES[loc]

    if loc in LOCALE_METADATA:
        return loc

    if "-" in loc:
        base = loc.split("-")[0]
        if base == "zh" and loc not in LOCALE_ALIASES:
            return loc

        if base in LOCALE_METADATA:
            return base

    return loc

def get_canonical_locales() -> List[str]:
    return list(LOCALE_METADATA.keys())

def get_enabled_locales() -> List[str]:
    return [k for k, v in LOCALE_METADATA.items() if v["enabled"]]

def is_supported(loc: Optional[str]) -> bool:
    canon = canonicalize_locale(loc)
    if canon and canon in LOCALE_METADATA:
        return LOCALE_METADATA[canon]["enabled"]
    return False

def normalize_locale(loc: Optional[str]) -> Optional[str]:
    """
    Returns the canonical locale only if it's enabled.
    Otherwise returns None (which will fall back to DEFAULT_LOCALE).
    """
    canon = canonicalize_locale(loc)
    if canon and is_supported(canon):
        return canon
    return None
