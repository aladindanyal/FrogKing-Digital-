from __future__ import annotations
from typing import Any
import contextvars

from bot.misc import EnvKeys
from .strings import TRANSLATIONS, DEFAULT_LOCALE
from bot.logger_mesh import logger

current_locale = contextvars.ContextVar("current_locale", default=None)

def get_locale() -> str:
    loc = current_locale.get()
    if not loc:
        loc = EnvKeys.BOT_LOCALE.lower().strip()
    return loc if loc in TRANSLATIONS else DEFAULT_LOCALE

def localize(key: str, /, **kwargs: Any) -> str:
    """
    Get translation by key.
    Fallback: current locale -> DEFAULT_LOCALE -> the key itself.
    """
    loc = get_locale()

    text = TRANSLATIONS.get(loc, {}).get(key)
    if text is None:
        text = TRANSLATIONS.get(DEFAULT_LOCALE, {}).get(key)
    if text is None:
        text = key

    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Failed to format translation key '{key}' with kwargs {kwargs}: {e}")

    return str(text)
