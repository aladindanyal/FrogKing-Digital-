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
def managed_root(tmp_path, monkeypatch):
    from bot.misc.env import EnvKeys
    path = str(tmp_path / "product_images")
    os.makedirs(path, exist_ok=True)
    monkeypatch.setattr(EnvKeys, "PRODUCT_IMAGES_ROOT", path)

    # Fail immediately if the resolved path starts with /app/data
    if EnvKeys.PRODUCT_IMAGES_ROOT.startswith("/app/data"):
        raise RuntimeError("Test path still points to /app/data!")

    yield path

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


@pytest.mark.asyncio
async def test_category_parent_string_persistence(app, mock_admin_auth, db_session, test_category):
    with TestClient(app) as client:
        # 1. Create with Parent submitted as numeric string
        data = {
            "name": "Sub of Test",
            "parent": str(test_category.id),
        }
        res = client.post("/admin/categories/create", data=data, follow_redirects=False)
        assert res.status_code in (302, 303)

        cats = (await db_session.execute(select(Categories).where(Categories.name == "Sub of Test"))).scalars().all()
        assert len(cats) == 1
        assert cats[0].parent_id == test_category.id

@pytest.mark.asyncio
async def test_failed_create_does_not_persist(app, mock_admin_auth, db_session):
    with TestClient(app) as client:
        # Invalid parent string
        data = {
            "name": "Should Fail",
            "parent": "abc",
        }
        try:
            client.post("/admin/categories/create", data=data, follow_redirects=False)
        except ValueError:
            pass

        cats = (await db_session.execute(select(Categories).where(Categories.name == "Should Fail"))).scalars().all()
        assert len(cats) == 0

@pytest.mark.asyncio
async def test_single_row_delete_protected_product(app, mock_admin_auth, db_session, test_category):
    from bot.database.models.main import Goods, OrderItem, Order

    goods = Goods(name="Delete HTML Test Single", description="Test product description", price=10, category_id=test_category.id, fulfillment_mode="instant")
    db_session.add(goods)
    await db_session.flush()

    order = Order(public_id="TEST_HTML_ORDER_SINGLE")
    db_session.add(order)
    await db_session.flush()

    order_item = OrderItem(order_id=order.id, item_id=goods.id, product_name_snapshot="X", unit_price=10, subtotal=10, total=10)
    db_session.add(order_item)
    await db_session.commit()

    with TestClient(app) as client:
        res = client.delete(f"/admin/goods/delete?pks={goods.id}")

        assert res.status_code == 400
        assert "text/plain" in res.headers["content-type"]
        assert "<!DOCTYPE html>" not in res.text
        assert "Cannot delete this product because it is referenced by historical orders" in res.text

    surviving_goods = (await db_session.execute(select(Goods).where(Goods.id == goods.id))).scalars().one_or_none()
    assert surviving_goods is not None

@pytest.mark.asyncio
async def test_bulk_delete_protected_product_atomic(app, mock_admin_auth, db_session, test_category):
    from bot.database.models.main import Goods, OrderItem, Order

    goods1 = Goods(name="Bulk HTML Test 1 (Unprotected)", description="Test product description", price=10, category_id=test_category.id, fulfillment_mode="instant")
    goods2 = Goods(name="Bulk HTML Test 2 (Protected)", description="Test product description", price=10, category_id=test_category.id, fulfillment_mode="instant")
    db_session.add_all([goods1, goods2])
    await db_session.flush()

    order = Order(public_id="TEST_HTML_ORDER_BULK")
    db_session.add(order)
    await db_session.flush()

    order_item = OrderItem(order_id=order.id, item_id=goods2.id, product_name_snapshot="X", unit_price=10, subtotal=10, total=10)
    db_session.add(order_item)
    await db_session.commit()

    with TestClient(app) as client:
        res = client.delete(f"/admin/goods/delete?pks={goods1.id},{goods2.id}")

        assert res.status_code == 400
        assert "text/plain" in res.headers["content-type"]
        assert "<!DOCTYPE html>" not in res.text
        assert "Cannot delete products: at least one selected product is referenced" in res.text

    # BOTH must survive because it's atomic
    surviving_goods1 = (await db_session.execute(select(Goods).where(Goods.id == goods1.id))).scalars().one_or_none()
    surviving_goods2 = (await db_session.execute(select(Goods).where(Goods.id == goods2.id))).scalars().one_or_none()
    assert surviving_goods1 is not None
    assert surviving_goods2 is not None

@pytest.mark.asyncio
async def test_single_row_delete_unprotected_succeeds(app, mock_admin_auth, db_session, test_category):
    from bot.database.models.main import Goods
    goods = Goods(name="Delete Success Single", description="Test product description", price=10, category_id=test_category.id, fulfillment_mode="instant")
    db_session.add(goods)
    await db_session.commit()

    with TestClient(app) as client:
        res = client.delete(f"/admin/goods/delete?pks={goods.id}")
        assert res.status_code == 200

    surviving = (await db_session.execute(select(Goods).where(Goods.id == goods.id))).scalars().one_or_none()
    assert surviving is None

@pytest.mark.asyncio
async def test_bulk_delete_unprotected_succeeds(app, mock_admin_auth, db_session, test_category):
    from bot.database.models.main import Goods
    goods1 = Goods(name="Delete Success Bulk 1", description="Test product description", price=10, category_id=test_category.id, fulfillment_mode="instant")
    goods2 = Goods(name="Delete Success Bulk 2", description="Test product description", price=10, category_id=test_category.id, fulfillment_mode="instant")
    db_session.add_all([goods1, goods2])
    await db_session.commit()

    with TestClient(app) as client:
        res = client.delete(f"/admin/goods/delete?pks={goods1.id},{goods2.id}")
        assert res.status_code == 200

    surviving = (await db_session.execute(select(Goods).where(Goods.name.in_([goods1.name, goods2.name])))).scalars().all()
    assert len(surviving) == 0

@pytest.mark.asyncio
async def test_media_persistence_when_deletion_blocked(app, mock_admin_auth, db_session, test_category, managed_root):
    from bot.database.models.main import Goods, OrderItem, Order
    import io
    from PIL import Image
    import os

    img = Image.new('RGB', (10, 10), color='red')
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='JPEG')

    # Write a dummy image to the managed root
    img_name = "protected_media.jpg"
    img_path = os.path.join(managed_root, img_name)
    with open(img_path, "wb") as f:
        f.write(img_byte_arr.getvalue())

    goods = Goods(name="Media Protected", description="Test product description", price=10, category_id=test_category.id, fulfillment_mode="instant", image_path=f"product_images/{img_name}")
    db_session.add(goods)
    await db_session.flush()

    order = Order(public_id="TEST_MEDIA_ORDER")
    db_session.add(order)
    await db_session.flush()

    order_item = OrderItem(order_id=order.id, item_id=goods.id, product_name_snapshot="X", unit_price=10, subtotal=10, total=10)
    db_session.add(order_item)
    await db_session.commit()

    with TestClient(app) as client:
        res = client.delete(f"/admin/goods/delete?pks={goods.id}")
        assert res.status_code == 400

    # Verify the image still exists
    assert os.path.exists(img_path)

@pytest.mark.asyncio
async def test_media_deleted_when_deletion_succeeds(app, mock_admin_auth, db_session, test_category, managed_root):
    from bot.database.models.main import Goods
    import io
    from PIL import Image
    import os

    img = Image.new('RGB', (10, 10), color='green')
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='JPEG')

    # Write a dummy image to the managed root
    img_name = "unprotected_media.jpg"
    img_path = os.path.join(managed_root, img_name)
    with open(img_path, "wb") as f:
        f.write(img_byte_arr.getvalue())

    goods = Goods(name="Media Unprotected", description="Test product description", price=10, category_id=test_category.id, fulfillment_mode="instant", image_path=f"product_images/{img_name}")
    db_session.add(goods)
    await db_session.commit()

    with TestClient(app) as client:
        res = client.delete(f"/admin/goods/delete?pks={goods.id}")
        assert res.status_code == 200

    # Verify the image was deleted
    assert not os.path.exists(img_path)

@pytest.mark.asyncio
async def test_db_cleanup_isolation(db_session):
    import uuid
    from bot.database.models.main import Goods, OrderItem, Order, CartItems, CheckoutIntakeDraft, Categories, ManualFulfillmentJob
    from sqlalchemy import select, delete
    from tests.conftest import db_cleanup

    unique_name = f"__pytest_goods_{uuid.uuid4()}"
    cat = Categories(name=f"__pytest_cat_{uuid.uuid4()}")
    db_session.add(cat)
    await db_session.flush()

    goods = Goods(name=unique_name, description="Test product description", price=10, category_id=cat.id, fulfillment_mode="instant")
    db_session.add(goods)
    await db_session.flush()

    order = Order(public_id=f"__pytest_order_{uuid.uuid4()}")
    db_session.add(order)
    await db_session.flush()

    order_item = OrderItem(order_id=order.id, item_id=goods.id, product_name_snapshot="X", unit_price=10, subtotal=10, total=10)
    cart_item = CartItems(user_id=1, item_name=goods.name)
    import datetime
    draft = CheckoutIntakeDraft(
        user_id=1,
        goods_id=goods.id,
        quantity=1,
        status="pending",
        public_token=f"__pytest_token_{uuid.uuid4()}",
        schema_fingerprint="dummy",
        encrypted_payload="dummy",
        encryption_version=1,
        expires_at=(
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(minutes=30)
        )
    )

    db_session.add_all([order_item, cart_item, draft])
    await db_session.flush()

    mf_job = ManualFulfillmentJob(order_item_id=order_item.id, status="queued")
    db_session.add(mf_job)
    await db_session.commit()

    # Run the cleanup logic manually to prove it wipes our fixture
    # We can invoke the fixture generator
    # But db_cleanup uses the singleton db session.
    # Since we are inside a test, the db_cleanup fixture has already run its BEFORE phase.
    # We can just run the inner code of db_cleanup
    from bot.database.main import Database
    from bot.database.models.main import (
        ReferralEarnings, BoughtGoods, Operations, Payments, Reviews,
        ItemValues, Goods as G, Categories as C, User, Role, ProductCustomerField,
        OrderItem as OI, Order as O, CartItems as CI, CheckoutIntakeDraft as CID, ProductRestockSubscription,
        ManualFulfillmentJob as MFJ
    )
    db = Database()
    async with db.session() as s:
        await s.execute(delete(ReferralEarnings))
        await s.execute(delete(BoughtGoods))
        await s.execute(delete(Operations))
        await s.execute(delete(Payments))
        await s.execute(delete(Reviews))
        await s.execute(delete(OI))
        await s.execute(delete(O))
        await s.execute(delete(MFJ))
        await s.execute(delete(CI))
        await s.execute(delete(CID))
        await s.execute(delete(ProductRestockSubscription))
        await s.execute(delete(ItemValues))
        await s.execute(delete(ProductCustomerField))
        await s.execute(delete(G))
        await s.execute(delete(C))
        await s.commit()

    # Prove it's gone
    surviving = (await db_session.execute(select(Goods).where(Goods.name == unique_name))).scalars().all()
    assert len(surviving) == 0
    surviving_orders = (await db_session.execute(select(Order).where(Order.public_id == order.public_id))).scalars().all()
    assert len(surviving_orders) == 0
    surviving_mf_jobs = (await db_session.execute(select(ManualFulfillmentJob).where(ManualFulfillmentJob.order_item_id == order_item.id))).scalars().all()
    assert len(surviving_mf_jobs) == 0
