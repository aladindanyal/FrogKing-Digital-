import pytest
import os
import json
from decimal import Decimal
from sqlalchemy import select
from bot.database.models.main import Goods, ProductCustomerField, ItemValues
from bot.database.main import Database
from bot.misc.customer_fields import validate_field_key, get_schema_fingerprint, get_localized_label
from bot.misc.encryption import encrypt_text, decrypt_text, encrypt_json, decrypt_json, is_configured, get_active_version, EncryptionUnavailableError, DecryptionError, UnsupportedEncryptionVersionError
from bot.misc.masking import mask_email, mask_phone, mask_username, mask_secret, mask_generic

def test_goods_fulfillment_config():
    """Test Goods model default and manual fulfillment fields."""
    goods = Goods(
        name="Test Instant", price=Decimal("10.00"), description="desc", category_id=1
    )
    
    # Defaults handled by DB are not present on uncommitted objects, but we can set them
    goods.fulfillment_mode = "instant"
    assert goods.fulfillment_mode == "instant"
    assert goods.fulfillment_eta_minutes is None
    
    goods.fulfillment_mode = "manual"
    goods.fulfillment_eta_minutes = 60
    goods.customer_input_intro_i18n = {"en": "Hello"}
    
    assert goods.fulfillment_mode == "manual"
    assert goods.fulfillment_eta_minutes == 60
    assert goods.customer_input_intro_i18n == {"en": "Hello"}

def test_product_customer_field():
    """Test ProductCustomerField creation."""
    field = ProductCustomerField(
        goods_id=1,
        field_key="test_field",
        field_type="text",
        label_i18n={"en": "Test Label"},
        required=True,
        scope="per_order",
        sort_order=1
    )
    
    assert field.field_key == "test_field"
    assert field.required is True
    assert field.scope == "per_order"

def test_localization():
    assert get_localized_label({"en": "English", "ar": "Arabic"}, "ar") == "Arabic"
    assert get_localized_label({"en": "English", "ar": "Arabic"}, "fr") == "English"
    assert get_localized_label({"fr": "French"}, "en") == "French"
    assert get_localized_label({}, "en") == ""

def test_fingerprint():
    field1 = {"field_key": "email", "field_type": "email", "required": True, "sort_order": 0, "is_active": True}
    field2 = {"field_key": "email", "field_type": "email", "required": True, "sort_order": 0, "is_active": True, "label_i18n": {"en": "Different"}}
    assert get_schema_fingerprint([field1]) == get_schema_fingerprint([field2])
    
    field3 = {"field_key": "email", "field_type": "email", "required": False, "sort_order": 0, "is_active": True}
    assert get_schema_fingerprint([field1]) != get_schema_fingerprint([field3])
    
    # Test select options
    field_select = {"field_key": "sel", "field_type": "select", "select_options_i18n": {"opt1": {"en": "A"}, "opt2": {"en": "B"}}}
    field_select_diff_labels = {"field_key": "sel", "field_type": "select", "select_options_i18n": {"opt1": {"en": "C"}, "opt2": {"en": "D"}}}
    field_select_diff_keys = {"field_key": "sel", "field_type": "select", "select_options_i18n": {"opt1": {"en": "A"}, "opt3": {"en": "B"}}}
    
    assert get_schema_fingerprint([field_select]) == get_schema_fingerprint([field_select_diff_labels])
    assert get_schema_fingerprint([field_select]) != get_schema_fingerprint([field_select_diff_keys])

def test_encryption(monkeypatch):
    monkeypatch.setenv("DATA_ENCRYPTION_ACTIVE_VERSION", "1")
    monkeypatch.setenv("DATA_ENCRYPTION_KEY_V1", "4Snh_6a79YVjH3i3bU7k7Xk5ZlG9F2hL5Jk=") # Invalid length but wait, fernet needs 32 url-safe base64
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode('utf-8')
    monkeypatch.setenv("DATA_ENCRYPTION_KEY_V1", key)
    
    assert is_configured() is True
    
    enc = encrypt_text("hello")
    assert enc["ciphertext"] != "hello"
    assert enc["version"] == 1
    
    dec = decrypt_text(enc["ciphertext"], enc["version"])
    assert dec == "hello"
    
    enc_json = encrypt_json({"test": 123})
    dec_json = decrypt_json(enc_json["ciphertext"], enc_json["version"])
    assert dec_json == {"test": 123}

def test_masking():
    assert mask_email("aladin@example.com") == "a***n@example.com"
    assert mask_phone("+962791234567") == "+962******567"
    assert mask_username("customer_name") == "c***e"
    assert mask_secret("password123") == "***********"
    assert mask_generic("hello") == "h***o"
