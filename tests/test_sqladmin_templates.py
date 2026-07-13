import pytest
from unittest.mock import patch, AsyncMock
from starlette.testclient import TestClient
from bot.web.admin import create_admin_app

@pytest.fixture
def client():
    with patch('bot.web.admin.AdminAuth.authenticate', new_callable=AsyncMock) as mock_auth:
        mock_auth.return_value = True
        app = create_admin_app()
        with TestClient(app) as client:
            yield client

def test_admin_list_page_returns_200(client):
    response = client.get("/admin/goods/list")
    assert response.status_code == 200

def test_goods_create_page_renders(client):
    response = client.get("/admin/goods/create")
    assert response.status_code == 200
    assert "Save" in response.text
    assert "eta_preset" in response.text or "fulfillment_mode" in response.text
    assert "manual_instr_en" in response.text
    assert "Internal Server Error" not in response.text

def test_customer_field_create_page_renders(client):
    response = client.get("/admin/product-customer-field/create")
    assert response.status_code == 200
    assert "Save" in response.text
    assert "field_type" in response.text
    assert "Internal Server Error" not in response.text
