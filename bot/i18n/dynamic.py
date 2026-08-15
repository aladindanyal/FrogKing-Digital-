from typing import Any, Mapping
from bot.i18n.main import current_locale, normalize_locale

class NormalizedDynamicItem(tuple):
    """
    A small reusable adapter that acts like a legacy tuple but also exposes
    dictionary-like attribute access for localization fields from an ORM object or mapping.
    """
    def __new__(cls, tuple_vals, mapping=None):
        obj = super().__new__(cls, tuple_vals)
        obj._mapping = mapping or {}
        return obj

    def get(self, key, default=None):
        if isinstance(self._mapping, dict):
            return self._mapping.get(key, default)
        return getattr(self._mapping, key, default)

    def __getattr__(self, item):
        if isinstance(self._mapping, dict):
            if item in self._mapping:
                return self._mapping[item]
            raise AttributeError(item)
        return getattr(self._mapping, item)

def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False

def get_localized_field(obj: Any, base_field: str, locale: str = None) -> str:
    """
    Resolves a localized field based on the fallback contract:
    - Arabic: _ar -> _en -> base
    - English: _en -> base
    - Other: base
    """
    if obj is None:
        return ""

    if not locale:
        locale = current_locale.get()

    norm_loc = normalize_locale(locale)

    is_dict = isinstance(obj, Mapping)

    def get_val(suffix: str) -> Any:
        field = f"{base_field}{suffix}"
        if is_dict:
            return obj.get(field)
        return getattr(obj, field, None)

    val_loc = get_val(f"_{norm_loc}") if norm_loc not in ("en", "") else get_val("_en")
    val_en = get_val("_en")
    val_base = get_val("")

    if not _is_missing(val_loc):
        return str(val_loc)

    if not _is_missing(val_en):
        return str(val_en)

    if not _is_missing(val_base):
        return str(val_base)

    return ""

def get_localized_jsonb(json_obj: Any, locale: str = None) -> Any:
    """
    Resolves a localized value from a JSONB dictionary (e.g. {'en': '...', 'ar': '...'}).
    If json_obj is not a dictionary, returns it directly (for primitive options).
    """
    if json_obj is None:
        return ""
    if not isinstance(json_obj, Mapping):
        return json_obj

    if not locale:
        locale = current_locale.get()

    norm_loc = normalize_locale(locale)

    val_loc = json_obj.get(norm_loc)
    val_en = json_obj.get("en")

    if not _is_missing(val_loc):
        return val_loc

    if not _is_missing(val_en):
        return val_en

    return ""
