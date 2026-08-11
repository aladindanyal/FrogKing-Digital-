from __future__ import annotations
from typing import Any
import contextvars

from bot.misc import EnvKeys
from bot.logger_mesh import logger
from .strings import TRANSLATIONS
from .registry import normalize_locale, is_supported, DEFAULT_LOCALE

current_locale = contextvars.ContextVar("current_locale", default=None)

# Re-export them so consumers don't break
__all__ = ["current_locale", "normalize_locale", "is_supported", "get_locale", "localize"]

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

    if TRANSLATIONS:
        return next(iter(TRANSLATIONS))
    return DEFAULT_LOCALE

def localize(key: str, /, **kwargs: Any) -> str:
    """
    Get translation by key.
    Fallback: selected enabled locale dictionary -> English dictionary -> raw translation key.
    """
    loc = get_locale()

    text = None

    # 1. Selected enabled locale dictionary
    if loc in TRANSLATIONS:
        text = TRANSLATIONS[loc].get(key)

    # 2. English dictionary
    if text is None:
        if "en" in TRANSLATIONS:
            text = TRANSLATIONS["en"].get(key)

    # 3. Raw translation key
    if text is None:
        text = key

    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Failed to format translation key '{key}' with kwargs {kwargs}: {e}")

    return str(text)
