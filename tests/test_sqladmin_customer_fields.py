import pytest
from sqlalchemy import select
from bot.database.models.main import Goods, ProductCustomerField, Categories
from bot.database.main import Database
from bot.misc.customer_field_templates import get_field_templates
from starlette.testclient import TestClient
from bot.web.admin import create_admin_app

@pytest.fixture
def app():
    return create_admin_app()

@pytest.fixture
def mock_admin_auth(monkeypatch):
    """Bypasses admin login for test routes."""
    from bot.web.admin import AdminAuth
    async def mock_authenticate(self, request):
        request.session["authenticated"] = True
        return True
    monkeypatch.setattr(AdminAuth, "authenticate", mock_authenticate)

@pytest.fixture
async def db_session(setup_test_database):
    async with Database().session() as session:
        yield session

@pytest.fixture
async def test_manual_goods(db_session):
    category = Categories(name="TestCategory")
    db_session.add(category)
    await db_session.flush()
    goods = Goods(
        name="Test Manual Goods",
        price=100.0,
        description="test desc",
        category_id=category.id,
        fulfillment_mode="manual"
    )
    db_session.add(goods)
    await db_session.commit()
    return goods

@pytest.mark.asyncio
async def test_quick_field_set_service_templates():
    """Test backend field generation logic from template strings."""
    templates = get_field_templates()
    assert "Email Only" in templates
    assert "Email + Password" in templates
    assert "Username + Password" in templates
    assert "Account URL" in templates
    assert "Phone Number" in templates
    
    # Verify Secret forced sensitive
    for t_name, fields in templates.items():
        for f in fields:
            if f["field_type"] == "secret":
                assert f["is_sensitive"] is True

@pytest.mark.asyncio
async def test_quick_field_set_http_get_authenticated(app, mock_admin_auth):
    """Test authenticated GET request to Quick Field Set."""
    with TestClient(app) as client:
        response = client.get("/admin/quick-field-set")
        assert response.status_code == 200
        assert b"Quick Field Set" in response.content
        assert b"Email + Password" in response.content

@pytest.mark.asyncio
async def test_quick_field_set_http_get_unauthenticated(app):
    """Test unauthenticated GET request to Quick Field Set."""
    with TestClient(app) as client:
        response = client.get("/admin/quick-field-set", follow_redirects=False)
        assert response.status_code in (302, 303, 401, 403)

@pytest.mark.asyncio
async def test_quick_field_set_http_post_success(app, mock_admin_auth, db_session, test_manual_goods: Goods):
    """Test POST request successfully creates fields."""
    with TestClient(app) as client:
        data = {
            "product_id": test_manual_goods.id,
            "template": "Email + Password",
            "scope": "per_unit",
            "duplicate_handling": "strict",
            "sort_order": "5"
        }
        
        response = client.post("/admin/quick-field-set", data=data, follow_redirects=False)
        assert response.status_code == 303
        
        # Verify db
        result = await db_session.execute(
            select(ProductCustomerField).where(ProductCustomerField.goods_id == test_manual_goods.id).order_by(ProductCustomerField.sort_order)
        )
        fields = result.scalars().all()
        assert len(fields) == 2
        assert fields[0].field_key == "email"
        assert fields[0].sort_order == 5
        assert fields[0].scope == "per_unit"
        assert fields[1].field_key == "password"
        assert fields[1].sort_order == 6
        assert fields[1].scope == "per_unit"
        assert fields[1].is_sensitive is True

@pytest.mark.asyncio
async def test_quick_field_set_duplicate_strict(app, mock_admin_auth, db_session, test_manual_goods: Goods):
    """Test Strict duplicate handling blocks creation."""
    existing = ProductCustomerField(
        goods_id=test_manual_goods.id, field_key="email", field_type="email",
        label_i18n={"en": "Email"}, required=True, is_sensitive=True, scope="per_unit", sort_order=0, is_active=True
    )
    db_session.add(existing)
    await db_session.commit()
    
    with TestClient(app) as client:
        data = {
            "product_id": test_manual_goods.id,
            "template": "Email + Password",
            "scope": "per_unit",
            "duplicate_handling": "strict",
            "sort_order": "1"
        }
        response = client.post("/admin/quick-field-set", data=data, follow_redirects=False)
        assert response.status_code == 303
        
        result = await db_session.execute(
            select(ProductCustomerField).where(ProductCustomerField.goods_id == test_manual_goods.id)
        )
        fields = result.scalars().all()
        assert len(fields) == 1
        assert fields[0].field_key == "email"

@pytest.mark.asyncio
async def test_quick_field_set_duplicate_missing_only(app, mock_admin_auth, db_session, test_manual_goods: Goods):
    """Test Missing Only duplicate handling creates missing fields."""
    existing = ProductCustomerField(
        goods_id=test_manual_goods.id, field_key="email", field_type="email",
        label_i18n={"en": "Email"}, required=True, is_sensitive=True, scope="per_unit", sort_order=0, is_active=True
    )
    db_session.add(existing)
    await db_session.commit()
    
    with TestClient(app) as client:
        data = {
            "product_id": test_manual_goods.id,
            "template": "Email + Password",
            "scope": "per_order",
            "duplicate_handling": "missing_only",
            "sort_order": "5"
        }
        response = client.post("/admin/quick-field-set", data=data, follow_redirects=False)
        assert response.status_code == 303
        
        result = await db_session.execute(
            select(ProductCustomerField).where(ProductCustomerField.goods_id == test_manual_goods.id).order_by(ProductCustomerField.sort_order)
        )
        fields = result.scalars().all()
        assert len(fields) == 2
        assert fields[0].field_key == "email"
        assert fields[0].scope == "per_unit" 
        assert fields[0].sort_order == 0
        
        assert fields[1].field_key == "password"
        assert fields[1].scope == "per_order"
        assert fields[1].sort_order == 6

@pytest.mark.asyncio
async def test_quick_field_set_next_sort_order(app, mock_admin_auth, db_session, test_manual_goods: Goods):
    """Test next sort order endpoint."""
    with TestClient(app) as client:
        response = client.get(f"/admin/quick-field-set/next-sort-order?product_id={test_manual_goods.id}")
        assert response.status_code == 200
        assert response.json()["next_sort_order"] == 0
        
        existing = ProductCustomerField(
            goods_id=test_manual_goods.id, field_key="email", field_type="email",
            label_i18n={"en": "Email"}, required=True, is_sensitive=True, scope="per_unit", sort_order=4, is_active=True
        )
        db_session.add(existing)
        await db_session.commit()
        
        response = client.get(f"/admin/quick-field-set/next-sort-order?product_id={test_manual_goods.id}")
        assert response.status_code == 200
        assert response.json()["next_sort_order"] == 5

@pytest.mark.asyncio
async def test_username_preset_platform_neutral():
    """Test that the Username preset is platform-neutral and contains no Telegram strings."""
    templates = get_field_templates()
    username_fields = templates["Username + Password"]
    for field in username_fields:
        if field["field_type"] == "username":
            assert "Telegram" not in field["label_i18n"]["en"]
            assert "Telegram" not in field["help_text_i18n"]["en"]
            assert "تيليجرام" not in field["label_i18n"]["ar"]
            assert field["label_i18n"]["en"] == "Username"
            assert field["label_i18n"]["ar"] == "اسم المستخدم"

@pytest.mark.asyncio
async def test_customer_field_script_ui_strings():
    """Verify that UI Javascript contains the correct strings and logic."""
    import pathlib
    script_path = pathlib.Path("bot/web/templates/admin/customer_field_script.html")
    content = script_path.read_text(encoding="utf-8")
    
    # Select helper text
    assert "The customer will see these choices as Telegram buttons." in content
    assert "ستظهر هذه الخيارات للعميل كأزرار داخل تيليجرام." in content
    
    # Secret security notice
    assert "Never request OTP codes, recovery codes, temporary verification codes, or payment-card information." in content
    assert "لا تطلب رموز التحقق أو رموز الاسترداد أو الرموز المؤقتة أو معلومات البطاقة البنكية." in content

    # Select options editor conditionally appears
    assert "if (t === 'select') {" in content
    assert "ed.style.display = 'block'" in content
    assert "ed.style.display = 'none'" in content

@pytest.mark.asyncio
async def test_non_select_fields_cannot_retain_select_options(app, mock_admin_auth, db_session, test_manual_goods: Goods):
    """Test that if field_type is not select, any submitted select options are ignored."""
    from bot.web.admin import ProductCustomerFieldAdmin
    from sqlalchemy.ext.asyncio import AsyncSession
    from starlette.requests import Request
    
    admin_view = ProductCustomerFieldAdmin()
    
    mock_request = Request({"type": "http", "method": "POST"})
    data = {
        "field_type": "text",
        "select_options_raw": '[{"key": "test", "en": "Test"}]'
    }
    
    model = ProductCustomerField(goods_id=test_manual_goods.id, field_key="test", required=True, is_sensitive=False, is_active=True, scope="per_order")
    await admin_view.on_model_change(data, model, True, mock_request)
    
    assert model.select_options_i18n is None
    assert "select_options_raw" not in data

