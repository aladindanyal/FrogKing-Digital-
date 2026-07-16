import hashlib
import json
import re
from typing import List

from bot.database.models.main import ProductCustomerField


class IntakeValidationError(Exception):
    """Raised when customer input fails structured validation."""
    pass


def compute_schema_fingerprint(fields: List[ProductCustomerField]) -> str:
    """
    Computes a canonical SHA-256 fingerprint for a set of active fields.
    Includes only properties that affect validity and order.
    Deterministically sorted by sort_order, then id.
    """
    sorted_fields = sorted(fields, key=lambda f: (f.sort_order, f.id))
    
    payload = []
    for f in sorted_fields:
        if not f.is_active:
            continue
            
        # Normalize select option keys
        option_keys = []
        if f.field_type == 'select' and f.select_options_i18n:
            # We assume select_options_i18n is a list of dicts with 'key'
            if isinstance(f.select_options_i18n, list):
                for opt in f.select_options_i18n:
                    if isinstance(opt, dict) and 'key' in opt:
                        option_keys.append(opt['key'])
        
        payload.append({
            "id": f.id,
            "field_key": f.field_key,
            "field_type": f.field_type,
            "scope": f.scope,
            "required": f.required,
            "is_sensitive": f.is_sensitive,
            "sort_order": f.sort_order,
            "min_length": f.min_length,
            "max_length": f.max_length,
            "option_keys": sorted(option_keys),
            "is_active": f.is_active
        })
        
    canonical_json = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    return hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()


def validate_field_input(field: ProductCustomerField, value: str) -> str:
    """
    Validates structured input against a field's requirements.
    Returns the normalized string value.
    Raises IntakeValidationError if invalid.
    """
    if not value:
        if field.required:
            raise IntakeValidationError("This field is required.")
        return ""
        
    value = value.strip()
    
    if field.min_length and len(value) < field.min_length:
        raise IntakeValidationError(f"Input is too short (minimum {field.min_length} characters).")
        
    if field.max_length and len(value) > field.max_length:
        raise IntakeValidationError(f"Input is too long (maximum {field.max_length} characters).")
        
    if field.field_type == 'email':
        if '@' not in value or '.' not in value:
            raise IntakeValidationError("Please enter a valid email address.")
            
    elif field.field_type == 'phone':
        # Basic digits/plus check
        cleaned = re.sub(r'[\s\-\(\)]', '', value)
        if not re.match(r'^\+?[0-9]{7,15}$', cleaned):
            raise IntakeValidationError("Please enter a valid phone number.")
            
    elif field.field_type == 'username':
        if ' ' in value:
            raise IntakeValidationError("Usernames cannot contain spaces.")
            
    elif field.field_type == 'url':
        if not value.startswith('http://') and not value.startswith('https://'):
            raise IntakeValidationError("Please enter a valid URL starting with http:// or https://.")
            
    elif field.field_type == 'select':
        valid_keys = []
        if isinstance(field.select_options_i18n, list):
            valid_keys = [opt.get('key') for opt in field.select_options_i18n if isinstance(opt, dict)]
        if value not in valid_keys:
            raise IntakeValidationError("Please select a valid option.")
            
    # For text, textarea, secret, we just rely on length checks.
    
    return value
