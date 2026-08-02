from __future__ import annotations
from typing import Any
import contextvars

from bot.misc import EnvKeys
from .strings import TRANSLATIONS, DEFAULT_LOCALE
from bot.logger_mesh import logger

current_locale = contextvars.ContextVar("current_locale", default=None)

def normalize_locale(loc: str | None) -> str | None:
    """
    Normalize locale strings like 'en-US' or 'en_US' or 'RU' to canonical 'en', 'ru'.
    Returns None for empty strings or None.
    """
    if not loc:
        return None
    loc = loc.strip().lower()
    if '-' in loc:
        loc = loc.split('-')[0]
    elif '_' in loc:
        loc = loc.split('_')[0]
    return loc if loc else None

def is_supported(loc: str | None) -> bool:
    """Check if the normalized locale is supported by our translations."""
    norm = normalize_locale(loc)
    return norm in TRANSLATIONS if norm else False

def get_locale() -> str:
    """
    Get the currently resolved locale.
    Fallback: current_locale -> BOT_LOCALE -> DEFAULT_LOCALE -> a safe available catalog.
    """
    loc = current_locale.get()
    norm_loc = normalize_locale(loc)
    
    if norm_loc and norm_loc in TRANSLATIONS:
        return norm_loc
        
    env_loc = normalize_locale(EnvKeys.BOT_LOCALE)
    if env_loc and env_loc in TRANSLATIONS:
        return env_loc
        
    norm_default = normalize_locale(DEFAULT_LOCALE)
    if norm_default and norm_default in TRANSLATIONS:
        return norm_default
        
    # Safe fallback if DEFAULT_LOCALE is misconfigured
    if TRANSLATIONS:
        return next(iter(TRANSLATIONS))
    return DEFAULT_LOCALE

def localize(key: str, /, **kwargs: Any) -> str:
    """
    Get translation by key.
    Fallback: current locale -> DEFAULT_LOCALE -> safe fallback -> the key itself.
    """
    loc = get_locale()

    text = TRANSLATIONS.get(loc, {}).get(key)
    
    if text is None:
        norm_default = normalize_locale(DEFAULT_LOCALE)
        if norm_default in TRANSLATIONS:
            text = TRANSLATIONS.get(norm_default, {}).get(key)
            
    if text is None:
        for catalog in TRANSLATIONS.values():
            if key in catalog:
                text = catalog[key]
                break

    if text is None:
        text = key

    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Failed to format translation key '{key}' with kwargs {kwargs}: {e}")

    return str(text)
