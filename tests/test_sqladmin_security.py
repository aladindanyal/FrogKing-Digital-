import pytest
from sqladmin import Admin
from bot.web.admin import create_admin_app, CheckoutIntakeDraftAdmin, ManualFulfillmentJobAdmin
from starlette.applications import Starlette

def test_admin_security_and_views():
    # 1. Verify OrderCustomerInputAdmin is NOT registered (should not exist)
    try:
        from bot.web.admin import OrderCustomerInputAdmin
        assert False, "OrderCustomerInputAdmin should not exist"
    except ImportError:
        pass
    
    # 2. Intake Drafts Technical Fields Hidden
    assert "encrypted_payload" not in CheckoutIntakeDraftAdmin.column_list
    assert "schema_fingerprint" not in CheckoutIntakeDraftAdmin.column_list
    assert CheckoutIntakeDraftAdmin.can_export is False
    assert CheckoutIntakeDraftAdmin.can_create is False
    assert CheckoutIntakeDraftAdmin.can_edit is False
    
    # 3. Manual Orders Name
    assert ManualFulfillmentJobAdmin.name == "Manual Order"
    assert "public_order_id" in ManualFulfillmentJobAdmin.column_list
    assert "product_name" in ManualFulfillmentJobAdmin.column_list
