import re
import json
import hashlib
from typing import Any

# Canonical regex for field_key
FIELD_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

def validate_field_key(key: str) -> bool:
    """Validate that field_key is stable, safe, and lowercase."""
    if not key:
        return False
    return bool(FIELD_KEY_PATTERN.match(key))


def get_schema_fingerprint(fields: list[dict[str, Any]]) -> str:
    """
    Calculate SHA-256 fingerprint over canonical normalized JSON.
    Fields should be a list of dictionaries representing the fields.
    """
    normalized_fields = []
    
    for f in fields:
        # Extract stable select option keys, ignoring translations
        select_keys = []
        if f.get('field_type') == 'select' and f.get('select_options_i18n'):
            options = f.get('select_options_i18n', {})
            if isinstance(options, dict):
                select_keys = sorted(options.keys())
                
        normalized_fields.append({
            "field_key": f.get("field_key"),
            "field_type": f.get("field_type"),
            "is_active": bool(f.get("is_active", True)),
            "is_sensitive": bool(f.get("is_sensitive", False)),
            "max_length": f.get("max_length"),
            "min_length": f.get("min_length"),
            "required": bool(f.get("required", True)),
            "scope": f.get("scope", "per_order"),
            "select_keys": select_keys,
            "sort_order": int(f.get("sort_order", 0)),
        })
        
    # Sort fields deterministically by field_key
    normalized_fields.sort(key=lambda x: x["field_key"])
    
    # Canonical JSON string
    canonical_json = json.dumps(normalized_fields, separators=(',', ':'), sort_keys=True, ensure_ascii=False)
    
    return hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()


def get_localized_label(i18n_dict: dict, locale: str) -> str:
    """
    Fallback: requested locale -> English -> first non-empty safe value.
    """
    if not isinstance(i18n_dict, dict) or not i18n_dict:
        return ""
        
    if locale in i18n_dict and i18n_dict[locale]:
        return str(i18n_dict[locale])
        
    if "en" in i18n_dict and i18n_dict["en"]:
        return str(i18n_dict["en"])
        
    for k, v in i18n_dict.items():
        if v:
            return str(v)
            
    return ""
