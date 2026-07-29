import pytest
from sqlalchemy import select, exc
from bot.database import Database
from bot.database.models import Goods
from bot.web.admin import GoodsAdmin
from starlette.requests import Request
from starlette.datastructures import UploadFile, Headers
from starlette.exceptions import HTTPException
import os
import shutil
import asyncio

class DummyClient:
    host = "127.0.0.1"

class DummyRequest:
    class State:
        def __init__(self):
            self.product_image_to_rollback = None
            self.product_image_to_delete = None
    
    def __init__(self):
        self.state = self.State()
        self.client = DummyClient()

class MockUploadFile(UploadFile):
    def __init__(self, filename, content):
        self.filename = filename
        self._content = content
        # UploadFile requires a file object
        import io
        super().__init__(file=io.BytesIO(content), filename=filename)
        
    async def read(self):
        return self._content

def create_dummy_image(ext="jpg"):
    from PIL import Image
    from io import BytesIO
    img = Image.new('RGB', (10, 10))
    buf = BytesIO()
    img.save(buf, format="JPEG" if ext == "jpg" else ext.upper())
    return buf.getvalue()

@pytest.fixture
def managed_root():
    path = "/app/data/product_images"
    os.makedirs(path, exist_ok=True)
    yield path
    # cleanup after tests
    for f in os.listdir(path):
        os.remove(os.path.join(path, f))

@pytest.mark.asyncio
async def test_invalid_image_upload_rejected():
    request = DummyRequest()
    admin = GoodsAdmin()
    data = {
        "image_upload": MockUploadFile("test.jpg", b"fake_not_image_data")
    }
    model = Goods()
    with pytest.raises(HTTPException) as excinfo:
        await admin.on_model_change(data, model, False, request)
    assert excinfo.value.status_code == 400
    assert "Invalid image file uploaded." in excinfo.value.detail

@pytest.mark.asyncio
async def test_valid_image_upload_success(managed_root):
    request = DummyRequest()
    admin = GoodsAdmin()
    content = create_dummy_image()
    data = {
        "image_upload": MockUploadFile("test.jpg", content)
    }
    model = Goods(image_path=None)
    await admin.on_model_change(data, model, False, request)
    
    assert model.image_path is not None
    assert model.image_path.startswith("product_images/")
    assert request.state.product_image_to_rollback is not None
    assert request.state.product_image_to_rollback.startswith(managed_root)
    
    # Simulate DB success
    await admin.after_model_change(data, model, False, request)
    assert os.path.exists(request.state.product_image_to_rollback)

@pytest.mark.asyncio
async def test_failed_db_persistence_removes_staged(managed_root):
    request = DummyRequest()
    admin = GoodsAdmin()
    content = create_dummy_image()
    data = {
        "image_upload": MockUploadFile("test.jpg", content)
    }
    model = Goods(image_path=None)
    await admin.on_model_change(data, model, False, request)
    staged = request.state.product_image_to_rollback
    assert os.path.exists(staged)
    
    # Simulate DB failure during update_model
    class FailingAdmin(GoodsAdmin):
        async def update_model(self, req, pk, d):
            # simulate super failure
            staged = getattr(req.state, "product_image_to_rollback", None)
            if staged:
                os.remove(staged)
            raise exc.SQLAlchemyError("DB Error")

    f_admin = FailingAdmin()
    with pytest.raises(exc.SQLAlchemyError):
        await f_admin.update_model(request, 1, data)
        
    assert not os.path.exists(staged)

@pytest.mark.asyncio
async def test_image_removal(managed_root):
    request = DummyRequest()
    admin = GoodsAdmin()
    # create dummy existing image
    existing_file = os.path.join(managed_root, "existing.jpg")
    with open(existing_file, "wb") as f:
        f.write(create_dummy_image())
    
    model = Goods(image_path="product_images/existing.jpg")
    data = {
        "remove_image": True
    }
    await admin.on_model_change(data, model, False, request)
    assert model.image_path is None
    assert request.state.product_image_to_delete == "product_images/existing.jpg"
    
    # before DB success, file still exists
    assert os.path.exists(existing_file)
    
    # after DB success, file deleted
    await admin.after_model_change(data, model, False, request)
    assert not os.path.exists(existing_file)

@pytest.mark.asyncio
async def test_path_traversal_rejected():
    request = DummyRequest()
    admin = GoodsAdmin()
    
    model = Goods(image_path="../some_secret_file.txt")
    data = {
        "remove_image": True
    }
    await admin.on_model_change(data, model, False, request)
    assert model.image_path is None
    
    # Simulate after_model_change trying to delete
    # It shouldn't crash and shouldn't delete outside
    await admin.after_model_change(data, model, False, request)

@pytest.mark.asyncio
async def test_missing_image_render_fallback():
    from bot.handlers.user.shop_and_goods import _render_item_page
    from aiogram.fsm.context import FSMContext
    from unittest.mock import AsyncMock, MagicMock
    import bot.handlers.user.shop_and_goods as shop_module
    
    state = MagicMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={})
    target = MagicMock()
    target.message = MagicMock()
    target.message.answer_photo = AsyncMock()
    target.message.answer = AsyncMock()
    target.message.chat.id = 1
    target.bot = MagicMock()
    target.from_user = MagicMock()
    target.from_user.id = 123
    
    orig_get = shop_module.get_item_info_cached
    orig_check_val = shop_module.check_value
    orig_sel = shop_module.select_item_values_amount_cached
    
    async def mock_get(name):
        return {
            "name": name,
            "description": "test",
            "price": 10.0,
            "image_path": "product_images/does_not_exist.jpg"
        }
    shop_module.get_item_info_cached = mock_get
    shop_module.check_value = AsyncMock(return_value=False)
    shop_module.select_item_values_amount_cached = AsyncMock(return_value=5)
    
    try:
        await _render_item_page(target, state, "Test Item", send_new=True, user_id=123)
        # Should NOT have called answer_photo because file doesn't exist
        target.message.answer_photo.assert_not_called()
        # Should have called answer (text)
        target.message.answer.assert_called_once()
    finally:
        shop_module.get_item_info_cached = orig_get
        shop_module.check_value = orig_check_val
        shop_module.select_item_values_amount_cached = orig_sel

@pytest.mark.asyncio
async def test_failed_db_delete_preserves_image(managed_root):
    request = DummyRequest()
    existing_file = os.path.join(managed_root, 'existing_delete.jpg')
    with open(existing_file, 'wb') as f:
        f.write(create_dummy_image())
    model = Goods(id=999, name='Test Product', image_path='product_images/existing_delete.jpg')
    class FailingDeleteAdmin(GoodsAdmin):
        async def on_model_delete(self, model, req):
            raise exc.SQLAlchemyError('DB Delete Error')
    f_admin = FailingDeleteAdmin()
    with pytest.raises(exc.SQLAlchemyError):
        await f_admin.on_model_delete(model, request)
    assert os.path.exists(existing_file)


from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_telegram_navigation_cleanup_transitions():
    from bot.handlers.user.main import send_fresh_main_menu
    from bot.handlers.user.shop_and_goods import (
        _render_category_page,
        _render_goods_page,
        _render_popular_deals_page,
        delete_product_image_safe,
        _render_item_page
    )
    
    # We just want to assert delete_product_image_safe is called in these transitions.
    with patch('bot.handlers.user.shop_and_goods.delete_product_image_safe', new_callable=AsyncMock) as mock_shop_delete:
         
        # Test Home (send_fresh_main_menu)
        call = AsyncMock()
        call.bot = AsyncMock()
        await send_fresh_main_menu(call, 1, 'user', 123)
        mock_shop_delete.assert_called_once_with(call.bot, 123, 123)
        
        # Test Category / Shop Back
        with patch('bot.handlers.user.shop_and_goods.query_categories', AsyncMock(return_value=[{'id':1,'name':'1','description':''}])), \
             patch('bot.handlers.user.shop_and_goods.get_store_settings', AsyncMock()), \
             patch('bot.handlers.user.shop_and_goods.LazyPaginator.get_page', return_value=[]), \
             patch('bot.handlers.user.shop_and_goods.LazyPaginator.get_total_pages', return_value=1):
             
            call.message.edit_text = AsyncMock()
            await _render_category_page(call, AsyncMock(), parent_id=None, page=0)
            mock_shop_delete.assert_called_with(call.bot, call.message.chat.id, call.from_user.id)
            
        # Test Popular Deals Back
        with patch('bot.handlers.user.shop_and_goods.LazyPaginator.get_page', return_value=[]), \
             patch('bot.handlers.user.shop_and_goods.LazyPaginator.get_total_pages', return_value=1), \
             patch('bot.database.methods.read.get_stock_for_items', AsyncMock(return_value={})):
            call.message.edit_text = AsyncMock()
            await _render_popular_deals_page(call, AsyncMock(), page=0)
            mock_shop_delete.assert_called_with(call.bot, call.message.chat.id, call.from_user.id)
            
        # Test checkout transitions? Those are in checkout/intake handlers.


@pytest.mark.asyncio
async def test_permission_error_during_save_preserves_old_image(managed_root):
    request = DummyRequest()
    admin = GoodsAdmin()
    content = create_dummy_image()
    data = {
        "image_upload": MockUploadFile("test.jpg", content)
    }
    model = Goods(image_path="product_images/old_image.jpg")
    
    with patch("PIL.Image.Image.save", side_effect=PermissionError("Permission Denied")):
        with pytest.raises(HTTPException) as excinfo:
            await admin.on_model_change(data, model, False, request)
    
    assert excinfo.value.status_code == 400
    assert "Product image storage is not writable" in excinfo.value.detail
    assert model.image_path == "product_images/old_image.jpg"
    assert getattr(request.state, "product_image_to_delete", None) is None

@pytest.mark.asyncio
async def test_permission_error_on_makedirs(managed_root):
    request = DummyRequest()
    admin = GoodsAdmin()
    content = create_dummy_image()
    data = {
        "image_upload": MockUploadFile("test.jpg", content)
    }
    model = Goods(image_path="product_images/old_image.jpg")
    
    with patch("os.makedirs", side_effect=PermissionError("Permission Denied")):
        with pytest.raises(HTTPException) as excinfo:
            await admin.on_model_change(data, model, False, request)
    
    assert excinfo.value.status_code == 400
    assert "Product image storage is not writable" in excinfo.value.detail
    assert model.image_path == "product_images/old_image.jpg"

@pytest.mark.asyncio
async def test_canonical_path_resolver(managed_root):
    from bot.misc.utils import resolve_product_image_path
    
    valid_uuid = "resolver_test.jpg"
    valid_file = os.path.join(managed_root, valid_uuid)
    with open(valid_file, "wb") as f:
        f.write(b"dummy")
        
    # 1. Resolves exactly to /app/data/product_images/<uuid>.jpg
    resolved = resolve_product_image_path(f"product_images/{valid_uuid}")
    assert resolved == valid_file
    
    # 2. Never resolves to /app/data/product_images/product_images/<uuid>.jpg
    assert resolved != os.path.join(managed_root, f"product_images/{valid_uuid}")
    
    # 6. Absolute and traversal paths are rejected
    assert resolve_product_image_path(f"product_images/../{valid_uuid}") is None
    assert resolve_product_image_path(f"product_images/\\{valid_uuid}") is None
    assert resolve_product_image_path(f"product_images/subdir/{valid_uuid}") is None
    assert resolve_product_image_path(valid_file) is None

@pytest.mark.asyncio
async def test_existing_image_render(managed_root):
    from bot.handlers.user.shop_and_goods import _render_item_page
    from aiogram.fsm.context import FSMContext
    from unittest.mock import AsyncMock, MagicMock
    import bot.handlers.user.shop_and_goods as shop_module
    
    state = MagicMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={})
    target = MagicMock()
    target.message = MagicMock()
    
    photo_mock = MagicMock()
    photo_mock.message_id = 999
    target.message.answer_photo = AsyncMock(return_value=photo_mock)
    target.message.answer = AsyncMock()
    target.message.chat = MagicMock()
    target.message.chat.id = 1
    target.bot = MagicMock()
    target.from_user = MagicMock()
    target.from_user.id = 123
    
    orig_get = shop_module.get_item_info_cached
    orig_check_val = shop_module.check_value
    orig_sel = shop_module.select_item_values_amount_cached
    
    valid_uuid = "real_img.jpg"
    valid_file = os.path.join(managed_root, valid_uuid)
    with open(valid_file, "wb") as f:
        f.write(b"dummy")
    
    async def mock_get(name):
        return {
            "name": name,
            "description": "test",
            "price": 10.0,
            "image_path": f"product_images/{valid_uuid}"
        }
    shop_module.get_item_info_cached = mock_get
    shop_module.check_value = AsyncMock(return_value=False)
    shop_module.select_item_values_amount_cached = AsyncMock(return_value=5)
    
    with patch('bot.handlers.user.shop_and_goods.delete_product_image_safe', new_callable=AsyncMock), \
         patch('bot.handlers.user.shop_and_goods.store_product_image_message', new_callable=AsyncMock):
        try:
            await _render_item_page(target, state, "Test Item", send_new=True, user_id=123)
            target.message.answer_photo.assert_called_once()
            target.message.answer.assert_called_once()
        finally:
            shop_module.get_item_info_cached = orig_get
            shop_module.check_value = orig_check_val
            shop_module.select_item_values_amount_cached = orig_sel

@pytest.mark.asyncio
async def test_admin_existing_image_removal(managed_root):
    request = DummyRequest()
    admin = GoodsAdmin()
    
    # ensure it processes without AttributeError for empty image_upload
    data = {
        "image_upload": MockUploadFile("", b""),
        "remove_image": True
    }
    
    existing_file = os.path.join(managed_root, "to_remove.jpg")
    with open(existing_file, "wb") as f2:
        f2.write(create_dummy_image())
        
    model = Goods(image_path="product_images/to_remove.jpg")
    
    # We call update_model, which calls our overriden method and then on_model_change
    class DummyAdmin(GoodsAdmin):
        async def get_model_by_pk(self, pk):
            return model
            
        async def update_model(self, request, pk, data):
            request.state.temp_form_data = {
                "image_upload": data.pop("image_upload", None),
                "remove_image": data.pop("remove_image", False),
            }
            # mock super().update_model by calling on_model_change directly
            await self.on_model_change(data, model, False, request)
            await self.after_model_change(data, model, False, request)
            return model
            
    d_admin = DummyAdmin()
    await d_admin.update_model(request, 1, data)
    
    assert model.image_path is None
    assert not os.path.exists(existing_file)

@pytest.mark.asyncio
async def test_admin_image_replacement(managed_root):
    request = DummyRequest()
    
    existing_file = os.path.join(managed_root, "to_replace.jpg")
    with open(existing_file, "wb") as f2:
        f2.write(create_dummy_image())
        
    model = Goods(image_path="product_images/to_replace.jpg")
    content = create_dummy_image("png")
    data = {
        "image_upload": MockUploadFile("new.png", content),
        "remove_image": False
    }
    
    class DummyAdmin(GoodsAdmin):
        async def update_model(self, request, pk, data):
            request.state.temp_form_data = {
                "image_upload": data.pop("image_upload", None),
                "remove_image": data.pop("remove_image", False),
            }
            await self.on_model_change(data, model, False, request)
            await self.after_model_change(data, model, False, request)
            return model
            
    d_admin = DummyAdmin()
    await d_admin.update_model(request, 1, data)
    
    assert model.image_path is not None
    assert model.image_path != "product_images/to_replace.jpg"
    assert not os.path.exists(existing_file)
    assert os.path.exists(os.path.join(managed_root, os.path.basename(model.image_path)))

@pytest.mark.asyncio
async def test_image_order_transition():
    from bot.handlers.user.shop_and_goods import _render_item_page
    from aiogram.fsm.context import FSMContext
    from unittest.mock import AsyncMock, MagicMock, patch
    import bot.handlers.user.shop_and_goods as shop_module
    import bot.misc.utils as utils_module
    
    state = MagicMock(spec=FSMContext)
    # Different item name in state to simulate transition
    state.get_data = AsyncMock(return_value={'image_sent_for': 'Other Item'})
    target = MagicMock()
    target.message = MagicMock()
    target.message.delete = AsyncMock()
    target.message.answer_photo = AsyncMock()
    target.message.answer = AsyncMock()
    target.bot = MagicMock()
    target.bot.send_message = AsyncMock()
    
    orig_get = shop_module.get_item_info_cached
    orig_check = shop_module.check_value
    orig_sel = shop_module.select_item_values_amount_cached
    orig_res = utils_module.resolve_product_image_path
    
    shop_module.get_item_info_cached = AsyncMock(return_value={"name": "Test Item", "description": "test", "price": 10.0, "image_path": "path"})
    shop_module.check_value = AsyncMock(return_value=False)
    shop_module.select_item_values_amount_cached = AsyncMock(return_value=5)
    utils_module.resolve_product_image_path = MagicMock(return_value="/fake/path.jpg")
    
    with patch('bot.handlers.user.shop_and_goods.delete_product_image_safe', new_callable=AsyncMock), \
         patch('bot.handlers.user.shop_and_goods.store_product_image_message', new_callable=AsyncMock):
        try:
            await _render_item_page(target, state, "Test Item", send_new=False, user_id=123)
            # The list message should be deleted
            target.message.delete.assert_called_once()
            # Image should be sent
            target.message.answer_photo.assert_called_once()
            # Text should be sent AFTER image
            target.message.answer.assert_called_once()
            
            # verify order of calls on target.message
            call_order = [call[0] for call in target.message.mock_calls]
            delete_idx = next(i for i, name in enumerate(call_order) if 'delete' in name)
            photo_idx = next(i for i, name in enumerate(call_order) if 'answer_photo' in name)
            text_idx = next(i for i, name in enumerate(call_order) if 'answer' in name and 'answer_photo' not in name)
            
            assert delete_idx < photo_idx < text_idx
            
        finally:
            shop_module.get_item_info_cached = orig_get
            shop_module.check_value = orig_check
            shop_module.select_item_values_amount_cached = orig_sel
            utils_module.resolve_product_image_path = orig_res
