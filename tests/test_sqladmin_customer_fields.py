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
        assert response.status_code == 200, response.text
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
        if response.status_code == 400:
            open('error_output.html', 'w', encoding='utf-8').write(response.text)
            assert False, 'Validation failed. See error_output.html'
        assert response.status_code in (302, 303)

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
        if response.status_code == 400:
            open('error_output.html', 'w', encoding='utf-8').write(response.text)
            assert False, 'Validation failed. See error_output.html'
        assert response.status_code in (302, 303)

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
        if response.status_code == 400:
            open('error_output.html', 'w', encoding='utf-8').write(response.text)
            assert False, 'Validation failed. See error_output.html'
        assert response.status_code in (302, 303)

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
        assert response.status_code == 200, response.text
        assert response.json()["next_sort_order"] == 0

        existing = ProductCustomerField(
            goods_id=test_manual_goods.id, field_key="email", field_type="email",
            label_i18n={"en": "Email"}, required=True, is_sensitive=True, scope="per_unit", sort_order=4, is_active=True
        )
        db_session.add(existing)
        await db_session.commit()

        response = client.get(f"/admin/quick-field-set/next-sort-order?product_id={test_manual_goods.id}")
        assert response.status_code == 200, response.text
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


@pytest.mark.asyncio
async def test_customer_field_create_post(app, mock_admin_auth, db_session, test_manual_goods: Goods):
    with TestClient(app) as client:
        data = {
            "goods": str(test_manual_goods.id),
            "preset": "email",
            "field_key": "email",
            "field_type": "email",
            "scope": "per_unit",
            "label_en": "Test Label EN",
            "label_ar": "Test Label AR",
            "min_length": "1",
            "max_length": "254",
            "is_active": "y",
            "required": "y"
        }

        response = client.post("/admin/product-customer-field/create", data=data, follow_redirects=False)
        if response.status_code == 400:
            open('error_output.html', 'w', encoding='utf-8').write(response.text)
            assert False, 'Validation failed. See error_output.html'
        assert response.status_code in (302, 303)

        result = await db_session.execute(select(ProductCustomerField).where(ProductCustomerField.goods_id == test_manual_goods.id))
        fields = result.scalars().all()
        assert len(fields) == 1
        assert fields[0].field_key == "email"
        assert fields[0].max_length == 254
        assert fields[0].label_i18n == {"en": "Test Label EN", "ar": "Test Label AR"}

@pytest.mark.asyncio
async def test_customer_field_edit_post(app, mock_admin_auth, db_session, test_manual_goods: Goods):
    field = ProductCustomerField(
        goods_id=test_manual_goods.id,
        field_key='custom_edit',
        field_type='text',
        label_i18n={'en': 'Old', 'ar': 'Old AR', 'es': 'Preserved ES'},
        sort_order=1
    )
    db_session.add(field)
    await db_session.commit()

    with TestClient(app) as client:
        data = {
            "goods": str(test_manual_goods.id),
            "field_key": "custom_edit",
            "field_type": "text",
            "scope": "per_order",
            "label_en": "New EN",
            "label_ar": "New AR",
            "is_active": "y",
            "required": "y"
        }
        response = client.post(f"/admin/product-customer-field/edit/{field.id}", data=data, follow_redirects=False)
        if response.status_code == 400:
            open('error_output.html', 'w', encoding='utf-8').write(response.text)
            assert False, 'Validation failed. See error_output.html'
        assert response.status_code in (302, 303)

        await db_session.refresh(field)
        assert field.label_i18n["en"] == "New EN"
        assert field.label_i18n["es"] == "Preserved ES"

@pytest.mark.asyncio
async def test_customer_field_invalid_create_post(app, mock_admin_auth, db_session, test_manual_goods: Goods):
    with TestClient(app) as client:
        data = {
            "goods": str(test_manual_goods.id),
            "label_en": "Missing key"
        }
        response = client.post("/admin/product-customer-field/create", data=data, follow_redirects=False)
        assert response.status_code == 400, response.text
        assert b"Missing key" in response.content
        assert b"Internal Server Error" not in response.content

@pytest.mark.asyncio
async def test_customer_field_invalid_edit_post(app, mock_admin_auth, db_session, test_manual_goods: Goods):
    field = ProductCustomerField(
        goods_id=test_manual_goods.id,
        field_key='custom_edit_inv',
        field_type='text',
        label_i18n={'en': 'Old'}
    )
    db_session.add(field)
    await db_session.commit()

    with TestClient(app) as client:
        data = {
            "goods": str(test_manual_goods.id),
            "field_key": "custom_edit_inv",
            "field_type": "select",
            "label_en": "New EN"
        }
        response = client.post(f"/admin/product-customer-field/edit/{field.id}", data=data, follow_redirects=False)
        assert response.status_code == 400, response.text
        assert b"New EN" in response.content

        await db_session.refresh(field)
        assert field.field_type == "text"

@pytest.mark.asyncio
async def test_customer_field_direct_form_lifecycle(app):
    from bot.web.admin import ProductCustomerFieldAdmin
    from sqladmin import Admin
    admin_inst = Admin(app, engine=Database().engine)
    admin_inst.add_view(ProductCustomerFieldAdmin)
    admin = admin_inst.views[0]
    form_class = await admin.scaffold_form()

    model = ProductCustomerField(field_key="test", label_i18n={"en": "test"})
    form1 = form_class()
    assert form1.label_en.data is None

    form2 = form_class(obj=model)
    assert form2.label_en.data == "test"

    from starlette.datastructures import FormData
    form3 = form_class(formdata=FormData([("label_en", "post_create")]))
    assert form3.label_en.data == "post_create"

    form4 = form_class(formdata=FormData([("label_en", "post_edit")]), obj=None)
    assert form4.label_en.data == "post_edit"

@pytest.mark.asyncio
async def test_goods_create_edit_post(app, mock_admin_auth, db_session):
    cat = Categories(name="C1")
    db_session.add(cat)
    await db_session.commit()

    with TestClient(app) as client:
        data = {
            "name": "Goods Post",
            "price": "10.00",
            "description": "Test Desc",
            "category": str(cat.id),
            "fulfillment_mode": "manual",
            "eta_preset": "",
            "is_active": "y"
        }
        resp = client.post("/admin/goods/create", data=data, follow_redirects=False)
        if resp.status_code == 400:
            open('error_output2.html', 'w', encoding='utf-8').write(resp.text)
            assert False, 'Validation failed. See error_output2.html'
        assert resp.status_code in (302, 303)

        goods = (await db_session.execute(select(Goods).where(Goods.name == "Goods Post"))).scalars().first()
        assert goods is not None

        data_edit = {
            "name": "Goods Post Edit",
            "price": "15.00",
            "description": "Test Desc 2",
            "category": str(cat.id),
            "fulfillment_mode": "manual",
            "eta_preset": "",
            "is_active": "y"
        }
        resp2 = client.post(f"/admin/goods/edit/{goods.id}", data=data_edit, follow_redirects=False)
        assert resp2.status_code in (302, 303)

        await db_session.refresh(goods)
        assert goods.name == "Goods Post Edit"
