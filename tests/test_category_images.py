import pytest
from sqlalchemy import select, exc
from bot.database import Database
from bot.database.models import Goods, Categories
from bot.web.admin import CategoryAdmin
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
            self.category_image_to_rollback = None
            self.category_image_to_delete = None

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
def managed_root(monkeypatch):
    import tempfile
    from bot.misc.env import EnvKeys

    with tempfile.TemporaryDirectory() as tmpdirname:
        # Patch the environment key where the app reads the path
        monkeypatch.setattr(EnvKeys, "CATEGORY_IMAGES_ROOT", tmpdirname)

        if EnvKeys.CATEGORY_IMAGES_ROOT.startswith("/app/data"):
            raise RuntimeError("Test path still points to /app/data!")

        yield tmpdirname


@pytest.mark.asyncio
async def test_invalid_image_upload_rejected():
    request = DummyRequest()
    admin = CategoryAdmin()
    data = {
        "image_upload": MockUploadFile("test.jpg", b"fake_not_image_data")
    }
    model = Categories()
    with pytest.raises(HTTPException) as excinfo:
        await admin.on_model_change(data, model, False, request)
    assert excinfo.value.status_code == 400
    assert "Invalid image file uploaded." in excinfo.value.detail

@pytest.mark.asyncio
async def test_valid_image_upload_success(managed_root):
    request = DummyRequest()
    admin = CategoryAdmin()
    content = create_dummy_image()
    data = {
        "image_upload": MockUploadFile("test.jpg", content)
    }
    model = Categories(image_path=None)
    await admin.on_model_change(data, model, False, request)

    assert model.image_path is not None
    assert model.image_path.startswith("category_images/")
    assert request.state.category_image_to_rollback is not None
    assert request.state.category_image_to_rollback.startswith(managed_root)

    # Simulate DB success
    await admin.after_model_change(data, model, False, request)
    assert os.path.exists(request.state.category_image_to_rollback)

@pytest.mark.asyncio
async def test_failed_db_persistence_removes_staged(managed_root):
    request = DummyRequest()
    admin = CategoryAdmin()
    content = create_dummy_image()
    data = {
        "image_upload": MockUploadFile("test.jpg", content)
    }
    model = Categories(image_path=None)
    await admin.on_model_change(data, model, False, request)
    staged = request.state.category_image_to_rollback
    assert os.path.exists(staged)

    # Simulate DB failure during update_model
    class FailingAdmin(CategoryAdmin):
        async def update_model(self, req, pk, d):
            # simulate super failure
            staged = getattr(req.state, "category_image_to_rollback", None)
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
    admin = CategoryAdmin()
    # create dummy existing image
    existing_file = os.path.join(managed_root, "existing.jpg")
    with open(existing_file, "wb") as f:
        f.write(create_dummy_image())

    model = Categories(image_path="category_images/existing.jpg")
    data = {
        "remove_image": True
    }
    await admin.on_model_change(data, model, False, request)
    assert model.image_path is None
    assert request.state.category_image_to_delete == "category_images/existing.jpg"

    # before DB success, file still exists
    assert os.path.exists(existing_file)

    # after DB success, file deleted
    await admin.after_model_change(data, model, False, request)
    assert not os.path.exists(existing_file)

@pytest.mark.asyncio
async def test_path_traversal_rejected():
    request = DummyRequest()
    admin = CategoryAdmin()

    model = Categories(image_path="../some_secret_file.txt")
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
    from bot.handlers.user.shop_and_goods import _render_category_page
    from aiogram.fsm.context import FSMContext
    from unittest.mock import AsyncMock, MagicMock, patch

    state = MagicMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={})
    state.get_state = AsyncMock(return_value="state")
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    call = MagicMock()
    call.answer = AsyncMock()
    call.message = MagicMock()
    call.message.chat.id = 1
    call.message.message_id = 999
    call.message.photo = None
    call.message.video = None
    call.message.document = None
    call.message.edit_text = AsyncMock()
    call.bot = MagicMock()
    call.bot.send_message = AsyncMock()
    call.bot.delete_message = AsyncMock()
    call.from_user = MagicMock()
    call.from_user.id = 123

    # mock get_category_by_id and LazyPaginator to avoid DB errors
    with patch('bot.handlers.user.shop_and_goods.get_category_by_id', new_callable=AsyncMock) as mock_get_cat, \
         patch('bot.handlers.user.shop_and_goods.LazyPaginator.get_page', new_callable=AsyncMock) as mock_paginator, \
         patch('bot.handlers.user.shop_and_goods.LazyPaginator.get_total_count', new_callable=AsyncMock, return_value=1), \
         patch('bot.handlers.user.shop_and_goods.get_store_settings', new_callable=AsyncMock), \
         patch('bot.handlers.user.shop_and_goods.get_category_parent_id', new_callable=AsyncMock), \
         patch('bot.handlers.user.shop_and_goods.delete_product_image_safe', new_callable=AsyncMock) as mock_del_img:

        mock_get_cat.return_value = {
            "name": "Test Category",
            "description": "desc",
            "image_path": "category_images/does_not_exist.jpg"
        }
        mock_paginator.return_value = []

        await _render_category_page(call, state, parent_id=1, page=0)

        # Should edit message because no image exists
        call.message.edit_text.assert_called_once()
        mock_del_img.assert_called_once()

@pytest.mark.asyncio
async def test_failed_db_delete_preserves_image(managed_root):
    request = DummyRequest()
    existing_file = os.path.join(managed_root, 'existing_delete.jpg')
    with open(existing_file, 'wb') as f:
        f.write(create_dummy_image())
    model = Categories(id=999, name='Test Product', image_path='category_images/existing_delete.jpg')
    class FailingDeleteAdmin(CategoryAdmin):
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
        _render_category_page
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
    admin = CategoryAdmin()
    content = create_dummy_image()
    data = {
        "image_upload": MockUploadFile("test.jpg", content)
    }
    model = Categories(image_path="category_images/old_image.jpg")

    with patch("PIL.Image.Image.save", side_effect=PermissionError("Permission Denied")):
        with pytest.raises(HTTPException) as excinfo:
            await admin.on_model_change(data, model, False, request)

    assert excinfo.value.status_code == 400
    assert "Category image storage is not writable." in excinfo.value.detail
    assert model.image_path == "category_images/old_image.jpg"
    assert getattr(request.state, "category_image_to_delete", None) is None

@pytest.mark.asyncio
async def test_permission_error_on_makedirs(managed_root):
    request = DummyRequest()
    admin = CategoryAdmin()
    content = create_dummy_image()
    data = {
        "image_upload": MockUploadFile("test.jpg", content)
    }
    model = Categories(image_path="category_images/old_image.jpg")

    with patch("os.makedirs", side_effect=PermissionError("Permission Denied")):
        with pytest.raises(HTTPException) as excinfo:
            await admin.on_model_change(data, model, False, request)

    assert excinfo.value.status_code == 400
    assert "Category image storage is not writable." in excinfo.value.detail
    assert model.image_path == "category_images/old_image.jpg"

@pytest.mark.asyncio
async def test_canonical_path_resolver(managed_root):
    from bot.misc.utils import resolve_category_image_path

    valid_uuid = "resolver_test.jpg"
    valid_file = os.path.join(managed_root, valid_uuid)
    with open(valid_file, "wb") as f:
        f.write(b"dummy")

    # 1. Resolves exactly to /app/data/category_images/<uuid>.jpg
    resolved = resolve_category_image_path(f"category_images/{valid_uuid}")
    assert resolved == valid_file

    # 2. Never resolves to /app/data/category_images/category_images/<uuid>.jpg
    assert resolved != os.path.join(managed_root, f"category_images/{valid_uuid}")

    # 6. Absolute and traversal paths are rejected
    assert resolve_category_image_path(f"category_images/../{valid_uuid}") is None
    assert resolve_category_image_path(f"category_images/\\{valid_uuid}") is None
    assert resolve_category_image_path(f"category_images/subdir/{valid_uuid}") is None
    assert resolve_category_image_path(valid_file) is None

@pytest.mark.asyncio
async def test_existing_image_render(managed_root):
    from bot.handlers.user.shop_and_goods import _render_category_page
    from aiogram.fsm.context import FSMContext
    from unittest.mock import AsyncMock, MagicMock, patch
    import os

    valid_uuid = "real_img.jpg"
    valid_file = os.path.join(managed_root, valid_uuid)
    with open(valid_file, "wb") as f:
        f.write(b"dummy")

    state = MagicMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={})
    state.get_state = AsyncMock(return_value="state")
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    call = MagicMock()
    call.answer = AsyncMock()
    call.message = MagicMock()
    call.message.chat.id = 1
    call.message.message_id = 999
    call.message.photo = None
    call.message.video = None
    call.message.document = None
    call.message.answer_photo = AsyncMock()
    call.message.answer_photo.return_value = MagicMock(message_id=9999)
    call.message.answer = AsyncMock()
    call.bot = MagicMock()
    call.bot.send_message = AsyncMock()
    call.bot.delete_message = AsyncMock()
    call.from_user = MagicMock()
    call.from_user.id = 123

    with patch('bot.handlers.user.shop_and_goods.get_category_by_id', new_callable=AsyncMock) as mock_get_cat, \
         patch('bot.handlers.user.shop_and_goods.LazyPaginator.get_page', new_callable=AsyncMock) as mock_paginator, \
         patch('bot.handlers.user.shop_and_goods.LazyPaginator.get_total_count', new_callable=AsyncMock, return_value=1), \
         patch('bot.handlers.user.shop_and_goods.get_store_settings', new_callable=AsyncMock), \
         patch('bot.handlers.user.shop_and_goods.get_category_parent_id', new_callable=AsyncMock), \
         patch('bot.handlers.user.shop_and_goods.delete_product_image_safe', new_callable=AsyncMock) as mock_del_img, \
         patch('bot.handlers.user.shop_and_goods.store_product_image_message', new_callable=AsyncMock) as mock_store_img, \
         patch('bot.misc.utils.resolve_category_image_path', return_value=valid_file):

        mock_get_cat.return_value = {
            "name": "Test Category",
            "description": "desc",
            "image_path": f"category_images/{valid_uuid}"
        }
        mock_paginator.return_value = []

        await _render_category_page(call, state, parent_id=1, page=0)

        call.message.answer_photo.assert_called_once()
        call.message.answer.assert_called_once()
        mock_store_img.assert_called_once()

@pytest.mark.asyncio
async def test_admin_existing_image_removal(managed_root):
    request = DummyRequest()
    admin = CategoryAdmin()

    # ensure it processes without AttributeError for empty image_upload
    data = {
        "image_upload": MockUploadFile("", b""),
        "remove_image": True
    }

    existing_file = os.path.join(managed_root, "to_remove.jpg")
    with open(existing_file, "wb") as f2:
        f2.write(create_dummy_image())

    model = Categories(image_path="category_images/to_remove.jpg")

    # We call update_model, which calls our overriden method and then on_model_change
    class DummyAdmin(CategoryAdmin):
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

    model = Categories(image_path="category_images/to_replace.jpg")
    content = create_dummy_image("png")
    data = {
        "image_upload": MockUploadFile("new.png", content),
        "remove_image": False
    }

    class DummyAdmin(CategoryAdmin):
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
    assert model.image_path != "category_images/to_replace.jpg"
    assert not os.path.exists(existing_file)
    assert os.path.exists(os.path.join(managed_root, os.path.basename(model.image_path)))

@pytest.mark.asyncio
async def test_image_order_transition():
    from bot.handlers.user.shop_and_goods import _render_category_page
    from aiogram.fsm.context import FSMContext
    from unittest.mock import AsyncMock, MagicMock, patch

    state = MagicMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={'image_sent_for': 'cat_2'})
    state.get_state = AsyncMock(return_value="state")
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    call = MagicMock()
    call.answer = AsyncMock()
    call.message = MagicMock()
    call.message.chat.id = 1
    call.message.message_id = 999
    call.message.photo = None
    call.message.video = None
    call.message.document = None
    call.message.answer_photo = AsyncMock()
    call.message.answer_photo.return_value = MagicMock(message_id=9999)
    call.message.answer = AsyncMock()
    call.bot = MagicMock()
    call.bot.send_message = AsyncMock()
    call.bot.delete_message = AsyncMock()
    call.from_user = MagicMock()
    call.from_user.id = 123

    with patch('bot.handlers.user.shop_and_goods.get_category_by_id', new_callable=AsyncMock) as mock_get_cat, \
         patch('bot.handlers.user.shop_and_goods.LazyPaginator.get_page', new_callable=AsyncMock) as mock_paginator, \
         patch('bot.handlers.user.shop_and_goods.LazyPaginator.get_total_count', new_callable=AsyncMock, return_value=1), \
         patch('bot.handlers.user.shop_and_goods.get_store_settings', new_callable=AsyncMock), \
         patch('bot.handlers.user.shop_and_goods.get_category_parent_id', new_callable=AsyncMock), \
         patch('bot.handlers.user.shop_and_goods.delete_product_image_safe', new_callable=AsyncMock) as mock_del_img, \
         patch('bot.handlers.user.shop_and_goods.store_product_image_message', new_callable=AsyncMock) as mock_store_img, \
         patch('bot.misc.utils.resolve_category_image_path', return_value="/fake/path.jpg"):

        mock_get_cat.return_value = {
            "name": "Test Category",
            "description": "desc",
            "image_path": "path"
        }
        mock_paginator.return_value = []

        await _render_category_page(call, state, parent_id=1, page=0)

        # When sending photo + text, we delete the previous message
        call.bot.delete_message.assert_called_once()
        call.message.answer_photo.assert_called_once()
        call.message.answer.assert_called_once()

        # We can't verify relative order of call.bot and call.message easily like mock_calls
        # on a single object because they are separate objects. But calling them both
        # is the important part of the lifecycle verification here.


@pytest.mark.asyncio
async def test_subcategory_image_rendering_and_navigation():
    from bot.handlers.user.shop_and_goods import category_selected_handler
    from aiogram.fsm.context import FSMContext
    from unittest.mock import AsyncMock, MagicMock, patch

    state = MagicMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={})
    state.get_state = AsyncMock(return_value="state")
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    call = MagicMock()
    call.data = "cat:6"
    call.answer = AsyncMock()
    call.message = MagicMock()
    call.message.chat.id = 1
    call.message.message_id = 999
    call.message.photo = None
    call.message.video = None
    call.message.document = None
    call.message.answer_photo = AsyncMock()
    call.message.edit_text = AsyncMock()
    call.message.answer = AsyncMock()
    call.bot = MagicMock()
    call.from_user = MagicMock()
    call.from_user.id = 123

    with patch('bot.handlers.user.shop_and_goods.check_category_has_subcategories', new_callable=AsyncMock) as mock_has_subcats, \
         patch('bot.handlers.user.shop_and_goods.get_category_by_id', new_callable=AsyncMock) as mock_get_cat, \
         patch('bot.handlers.user.shop_and_goods.get_category_parent_id', new_callable=AsyncMock) as mock_get_parent, \
         patch('bot.handlers.user.shop_and_goods.LazyPaginator.get_page', new_callable=AsyncMock) as mock_paginator, \
         patch('bot.handlers.user.shop_and_goods.LazyPaginator.get_total_count', new_callable=AsyncMock, return_value=1), \
         patch('bot.handlers.user.shop_and_goods.get_store_settings', new_callable=AsyncMock), \
         patch('bot.database.methods.read.get_stock_for_items', new_callable=AsyncMock) as mock_get_stock, \
         patch('bot.handlers.user.shop_and_goods.delete_product_image_safe', new_callable=AsyncMock), \
         patch('bot.misc.utils.resolve_category_image_path') as mock_resolve:

        # Subcategory 6 has NO subcategories of its own
        mock_has_subcats.return_value = False

        # We simulate category ID 6 info
        mock_get_cat.return_value = {
            "name": "Test Subcategory",
            "description": "Sub desc",
            "image_path": "category_images/child.jpg"
        }

        # Its parent is 1
        mock_get_parent.return_value = 1

        mock_paginator.return_value = []
        mock_get_stock.return_value = {}
        mock_resolve.return_value = "/full/path/to/child.jpg"

        await category_selected_handler(call, state)

        # Assert child image is sent
        call.message.answer_photo.assert_called_once()
        # Assert parent image is not sent, because we only fetched image for ID 6
        mock_get_cat.assert_called_with(6)

        # Assert Back callback targets parent ID 1 (by checking the markup args)
        args, kwargs = call.message.answer.call_args
        assert "cpage:1:0" in str(kwargs.get("reply_markup"))

@pytest.mark.asyncio
async def test_category_telegram_bad_request_fallback():
    from bot.handlers.user.shop_and_goods import category_selected_handler
    from aiogram.fsm.context import FSMContext
    from aiogram.exceptions import TelegramBadRequest
    from unittest.mock import AsyncMock, MagicMock, patch

    state = MagicMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={})
    state.get_state = AsyncMock(return_value="state")
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    call = MagicMock()
    call.data = "cat:6"
    call.answer = AsyncMock()
    call.message = MagicMock()
    call.message.chat.id = 1
    call.message.message_id = 999
    call.message.photo = None
    call.message.video = None
    call.message.document = None

    # Mock answer_photo to raise TelegramBadRequest
    call.message.answer_photo = AsyncMock(side_effect=TelegramBadRequest(method="sendPhoto", message="Bad Request: IMAGE_PROCESS_FAILED"))

    call.message.edit_text = AsyncMock()
    call.message.answer = AsyncMock()
    call.bot = MagicMock()
    call.bot.send_message = AsyncMock()
    call.bot.delete_message = AsyncMock()
    call.from_user = MagicMock()
    call.from_user.id = 123

    with patch('bot.handlers.user.shop_and_goods.check_category_has_subcategories', new_callable=AsyncMock) as mock_has_subcats, \
         patch('bot.handlers.user.shop_and_goods.get_category_by_id', new_callable=AsyncMock) as mock_get_cat, \
         patch('bot.handlers.user.shop_and_goods.get_category_parent_id', new_callable=AsyncMock) as mock_get_parent, \
         patch('bot.handlers.user.shop_and_goods.LazyPaginator.get_page', new_callable=AsyncMock) as mock_paginator, \
         patch('bot.handlers.user.shop_and_goods.LazyPaginator.get_total_count', new_callable=AsyncMock, return_value=1), \
         patch('bot.handlers.user.shop_and_goods.get_store_settings', new_callable=AsyncMock), \
         patch('bot.database.methods.read.get_stock_for_items', new_callable=AsyncMock) as mock_get_stock, \
         patch('bot.handlers.user.shop_and_goods.delete_product_image_safe', new_callable=AsyncMock), \
         patch('bot.handlers.user.shop_and_goods._edit_message_safe', new_callable=AsyncMock) as mock_edit_safe, \
         patch('bot.misc.utils.resolve_category_image_path') as mock_resolve:

        # Subcategory 6 has NO subcategories of its own
        mock_has_subcats.return_value = False

        mock_get_cat.return_value = {
            "name": "Test Subcategory",
            "description": "Sub desc",
            "image_path": "category_images/child.jpg"
        }

        mock_get_parent.return_value = 1
        mock_paginator.return_value = []
        mock_get_stock.return_value = {}
        mock_resolve.return_value = "/full/path/to/child.jpg"

        await category_selected_handler(call, state)

        # Should catch TelegramBadRequest and fallback to _edit_message_safe
        mock_edit_safe.assert_called_once()
        assert not call.message.answer.called # because it fell back to edit
