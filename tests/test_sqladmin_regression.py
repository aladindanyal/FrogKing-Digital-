import pytest
from sqlalchemy import select
from bot.database.models.main import Goods, Categories
from bot.database.main import Database
from starlette.testclient import TestClient
from bot.web.admin import create_admin_app
from bot.misc.env import EnvKeys
import os

@pytest.fixture
def app():
    return create_admin_app()

@pytest.fixture
def mock_admin_auth(monkeypatch):
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
async def test_category(db_session):
    category = Categories(name="RegressionCategory")
    db_session.add(category)
    await db_session.flush()
    await db_session.commit()
    return category

@pytest.fixture
def managed_root():
    path = os.path.abspath(EnvKeys.PRODUCT_IMAGES_ROOT)
    os.makedirs(path, exist_ok=True)
    yield path
    for f in os.listdir(path):
        os.remove(os.path.join(path, f))

@pytest.mark.asyncio
async def test_product_without_image_saves_normally(app, mock_admin_auth, db_session, test_category):
    with TestClient(app) as client:
        data = {
            "name": "New Product No Image",
            "price": "100",
            "category": str(test_category.id),
            "description": "desc",
            "fulfillment_mode": "instant"
        }
        resp = client.post("/admin/goods/create", data=data, follow_redirects=False)
        assert resp.status_code in (302, 303)
        
        result = await db_session.execute(select(Goods).where(Goods.name == "New Product No Image"))
        goods = result.scalars().first()
        assert goods is not None
        assert goods.image_path is None

@pytest.mark.asyncio
async def test_save_with_no_changes_succeeds_with_existing_image(app, mock_admin_auth, db_session, test_category):
    goods = Goods(name="Existing Image Product", price=100.0, description="desc", category_id=test_category.id, fulfillment_mode="instant", image_path="product_images/old.jpg")
    db_session.add(goods)
    await db_session.commit()

    with TestClient(app) as client:
        data = {
            "name": "Existing Image Product",
            "price": "100",
            "category": str(test_category.id),
            "description": "desc",
            "fulfillment_mode": "instant",
            # Simulated empty file upload
            "image_upload": ""
        }
        resp = client.post(f"/admin/goods/edit/{goods.id}", data=data, follow_redirects=False)
        assert resp.status_code in (302, 303)
        
        await db_session.refresh(goods)
        assert goods.image_path == "product_images/old.jpg"

@pytest.mark.asyncio
async def test_name_only_edit_succeeds_and_preserves_image(app, mock_admin_auth, db_session, test_category):
    goods = Goods(name="Old Name", price=100.0, description="desc", category_id=test_category.id, fulfillment_mode="instant", image_path="product_images/old.jpg")
    db_session.add(goods)
    await db_session.commit()

    with TestClient(app) as client:
        data = {
            "name": "New Name",
            "price": "100",
            "category": str(test_category.id),
            "description": "desc",
            "fulfillment_mode": "instant",
            "image_upload": ""
        }
        resp = client.post(f"/admin/goods/edit/{goods.id}", data=data, follow_redirects=False)
        assert resp.status_code in (302, 303)
        
        await db_session.refresh(goods)
        assert goods.name == "New Name"
        assert goods.image_path == "product_images/old.jpg"

@pytest.mark.asyncio
async def test_price_only_edit_succeeds_and_preserves_image(app, mock_admin_auth, db_session, test_category):
    goods = Goods(name="Price Edit", price=100.0, description="desc", category_id=test_category.id, fulfillment_mode="instant", image_path="product_images/old.jpg")
    db_session.add(goods)
    await db_session.commit()

    with TestClient(app) as client:
        data = {
            "name": "Price Edit",
            "price": "200",
            "category": str(test_category.id),
            "description": "desc",
            "fulfillment_mode": "instant",
            "image_upload": ""
        }
        resp = client.post(f"/admin/goods/edit/{goods.id}", data=data, follow_redirects=False)
        assert resp.status_code in (302, 303)
        
        await db_session.refresh(goods)
        assert goods.price == 200.0
        assert goods.image_path == "product_images/old.jpg"

@pytest.mark.asyncio
async def test_remove_image_succeeds(app, mock_admin_auth, db_session, test_category):
    goods = Goods(name="Remove Image Prod", price=100.0, description="desc", category_id=test_category.id, fulfillment_mode="instant", image_path="product_images/old.jpg")
    db_session.add(goods)
    await db_session.commit()

    with TestClient(app) as client:
        data = {
            "name": "Remove Image Prod",
            "price": "100",
            "category": str(test_category.id),
            "description": "desc",
            "fulfillment_mode": "instant",
            "remove_image": "y"
        }
        resp = client.post(f"/admin/goods/edit/{goods.id}", data=data, follow_redirects=False)
        assert resp.status_code in (302, 303)
        
        await db_session.refresh(goods)
        assert goods.image_path is None

@pytest.mark.asyncio
async def test_replacement_succeeds(app, mock_admin_auth, db_session, test_category, managed_root):
    goods = Goods(name="Replace Image Prod", price=100.0, description="desc", category_id=test_category.id, fulfillment_mode="instant", image_path="product_images/old.jpg")
    db_session.add(goods)
    await db_session.commit()

    from PIL import Image
    import io
    
    img = Image.new('RGB', (10, 10), color='red')
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='JPEG')
    img_bytes = img_byte_arr.getvalue()

    with open("dummy.jpg", "wb") as f:
        f.write(img_bytes)

    with TestClient(app) as client:
        data = {
            "name": "Replace Image Prod",
            "price": "100",
            "category": str(test_category.id),
            "description": "desc",
            "fulfillment_mode": "instant",
        }
        files = {"image_upload": ("dummy.jpg", img_bytes, "image/jpeg")}
        resp = client.post(f"/admin/goods/edit/{goods.id}", data=data, files=files, follow_redirects=False)
        
        assert resp.status_code in (302, 303)
        
        await db_session.refresh(goods)
        assert goods.image_path is not None
        assert goods.image_path != "product_images/old.jpg"
        assert goods.image_path.endswith(".jpg")
    
    os.remove("dummy.jpg")

@pytest.mark.asyncio
async def test_failed_persistence_preserves_previous_image(app, mock_admin_auth, db_session, test_category, monkeypatch):
    goods = Goods(name="Fail Persist", price=100.0, description="desc", category_id=test_category.id, fulfillment_mode="instant", image_path="product_images/old.jpg")
    db_session.add(goods)
    await db_session.commit()
    
    from bot.web.admin import GoodsAdmin
    original_update = GoodsAdmin.update_model
    
    async def failing_update(*args, **kwargs):
        raise ValueError("Simulated DB failure")
        
    monkeypatch.setattr(GoodsAdmin, "update_model", failing_update)

    from PIL import Image
    import io
    
    img = Image.new('RGB', (10, 10), color='blue')
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='JPEG')
    img_bytes = img_byte_arr.getvalue()

    with open("dummy_fail.jpg", "wb") as f:
        f.write(img_bytes)

    with TestClient(app) as client:
        data = {
            "name": "Fail Persist",
            "price": "100",
            "category": str(test_category.id),
            "description": "desc",
            "fulfillment_mode": "instant",
        }
        files = {"image_upload": ("dummy_fail.jpg", img_bytes, "image/jpeg")}
        try:
            client.post(f"/admin/goods/edit/{goods.id}", data=data, files=files, follow_redirects=False)
        except ValueError:
            pass
        
        await db_session.refresh(goods)
        assert goods.image_path == "product_images/old.jpg"
        
    os.remove("dummy_fail.jpg")
