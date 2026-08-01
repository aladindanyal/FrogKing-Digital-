import pytest
from sqlalchemy import select
from bot.database.main import Database
from bot.database.models import Categories
from bot.database.models.main import StoreSettings
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram.enums import ButtonStyle

@pytest.fixture
def make_callback_query():
    def _make(data, user_id=123):
        call = AsyncMock()
        call.data = data
        call.from_user = MagicMock(id=user_id)
        call.message = AsyncMock()
        call.message.chat = MagicMock(id=12345)
        call.message.photo = None
        call.message.video = None
        call.message.document = None
        call.message.edit_text = AsyncMock()
        call.bot = AsyncMock()
        call.bot.send_message = AsyncMock()
        call.bot.send_photo = AsyncMock()
        call.bot.delete_message = AsyncMock()
        call.answer = AsyncMock()
        return call
    return _make

@pytest.fixture
def mock_fsm():
    class MockFSM:
        def __init__(self):
            self.data = {}
            self.state = None
        async def update_data(self, **kwargs):
            self.data.update(kwargs)
        async def get_data(self):
            return self.data
        async def set_state(self, state):
            self.state = state
        async def get_state(self):
            return self.state
    return MockFSM()

@pytest.mark.asyncio
async def test_migration_and_constraints():
    """Migration and constraint validation."""
    with open('migrations/versions/phase_4c_6d_layout.py', 'r') as f:
        content = f.read()
        assert "down_revision: Union[str, None] = '7d30a688d18a'" in content
        assert "server_default='1'" in content
        assert "nullable=False" in content
        assert "op.drop_column" in content
        assert "op.drop_constraint" in content

    async with Database().session() as s:
        settings = (await s.execute(select(StoreSettings).where(StoreSettings.id == 1))).scalars().first()
        if not settings:
            settings = StoreSettings(id=1)
            s.add(settings)
            await s.commit()

        settings.root_category_buttons_per_row = 1
        await s.commit()
        settings.root_category_buttons_per_row = 2
        await s.commit()

        try:
            settings.root_category_buttons_per_row = 3
            await s.commit()
            pytest.fail("Should reject 3")
        except Exception:
            await s.rollback()

        cat = Categories(name="Test Layout", children_buttons_per_row=1)
        s.add(cat)
        await s.commit()

        cat.children_buttons_per_row = 2
        await s.commit()

        try:
            cat.children_buttons_per_row = 0
            await s.commit()
            pytest.fail("Should reject 0")
        except Exception:
            await s.rollback()

        await s.delete(cat)
        await s.commit()

@pytest.mark.asyncio
async def test_root_shop_layout(make_callback_query, mock_fsm):
    """Test Root Shop Layout with 1, 2, 3 items."""
    from bot.handlers.user.shop_and_goods import _render_category_page
    call = make_callback_query("dummy")

    with patch('bot.handlers.user.shop_and_goods.get_store_settings', new_callable=AsyncMock) as mock_settings:
        with patch('bot.handlers.user.shop_and_goods.LazyPaginator') as MockPaginator:
            mock_paginator_instance = MagicMock()
            mock_paginator_instance.get_page = AsyncMock(return_value=[(1, "Cat A"), (2, "Cat B")])
            mock_paginator_instance.has_next = AsyncMock(return_value=False)
            mock_paginator_instance.has_prev = AsyncMock(return_value=False)
            mock_paginator_instance.get_total_pages = AsyncMock(return_value=1)
            MockPaginator.return_value = mock_paginator_instance

            mock_settings.return_value = MagicMock(root_category_buttons_per_row=1, shop_root_title="Title", shop_root_description="Desc")

            await _render_category_page(call, mock_fsm, None, 1)
            call.message.edit_text.assert_called_once()
            kb = call.message.edit_text.call_args[1]['reply_markup']

            # Row width 1 -> 2 items + nav rows
            assert len(kb.inline_keyboard[0]) == 1
            assert kb.inline_keyboard[0][0].text == "Cat A"
            assert kb.inline_keyboard[0][0].style == ButtonStyle.SUCCESS
            assert len(kb.inline_keyboard[1]) == 1
            assert kb.inline_keyboard[1][0].text == "Cat B"
            assert kb.inline_keyboard[1][0].style == ButtonStyle.SUCCESS

    call.message.edit_text.reset_mock()

    with patch('bot.handlers.user.shop_and_goods.get_store_settings', new_callable=AsyncMock) as mock_settings:
        with patch('bot.handlers.user.shop_and_goods.LazyPaginator') as MockPaginator:
            mock_paginator_instance = MagicMock()
            mock_paginator_instance.get_page = AsyncMock(return_value=[(1, "Cat A"), (2, "Cat B"), (3, "Cat C")])
            mock_paginator_instance.has_next = AsyncMock(return_value=False)
            mock_paginator_instance.has_prev = AsyncMock(return_value=False)
            mock_paginator_instance.get_total_pages = AsyncMock(return_value=1)
            MockPaginator.return_value = mock_paginator_instance

            mock_settings.return_value = MagicMock(root_category_buttons_per_row=2, shop_root_title="Title", shop_root_description="Desc")

            await _render_category_page(call, mock_fsm, None, 1)
            kb = call.message.edit_text.call_args[1]['reply_markup']

            # Row width 2 -> 2 items in row 0, 1 item in row 1
            assert len(kb.inline_keyboard[0]) == 2
            assert kb.inline_keyboard[0][0].text == "Cat A"
            assert kb.inline_keyboard[0][0].style == ButtonStyle.SUCCESS
            assert kb.inline_keyboard[0][1].text == "Cat B"
            assert kb.inline_keyboard[0][1].style == ButtonStyle.SUCCESS
            assert len(kb.inline_keyboard[1]) == 1
            assert kb.inline_keyboard[1][0].text == "Cat C"
            assert kb.inline_keyboard[1][0].style == ButtonStyle.SUCCESS

@pytest.mark.asyncio
async def test_nested_subcategories_layout(make_callback_query, mock_fsm):
    """Test nested subcategories controlled by parent."""
    from bot.handlers.user.shop_and_goods import _render_category_page
    call = make_callback_query("dummy")

    with patch('bot.handlers.user.shop_and_goods.get_category_by_id', new_callable=AsyncMock) as mock_cat:
        with patch('bot.handlers.user.shop_and_goods.get_category_parent_id', new_callable=AsyncMock) as mock_parent:
            with patch('bot.handlers.user.shop_and_goods.LazyPaginator') as MockPaginator:
                with patch('bot.handlers.user.shop_and_goods.get_store_settings', new_callable=AsyncMock) as mock_settings:
                    mock_parent.return_value = None
                    mock_cat.return_value = {"id": 1, "name": "Parent", "children_buttons_per_row": 2, "image_path": None, "description": "Desc"}
                    mock_settings.return_value = MagicMock(root_category_buttons_per_row=1, shop_root_title="Title", shop_root_description="Desc")

                    mock_paginator_instance = MagicMock()
                    mock_paginator_instance.get_page = AsyncMock(return_value=[(11, "Sub A"), (12, "Sub B"), (13, "Sub C")])
                    mock_paginator_instance.has_next = AsyncMock(return_value=False)
                    mock_paginator_instance.has_prev = AsyncMock(return_value=False)
                    mock_paginator_instance.get_total_pages = AsyncMock(return_value=1)
                    MockPaginator.return_value = mock_paginator_instance

                    await _render_category_page(call, mock_fsm, 1, 1)
                    kb = call.message.edit_text.call_args[1]['reply_markup']

                    assert len(kb.inline_keyboard[0]) == 2
                    assert kb.inline_keyboard[0][0].text == "Sub A"
                    assert kb.inline_keyboard[0][0].style == ButtonStyle.PRIMARY
                    assert kb.inline_keyboard[0][1].text == "Sub B"
                    assert kb.inline_keyboard[0][1].style == ButtonStyle.PRIMARY
                    assert len(kb.inline_keyboard[1]) == 1
                    assert kb.inline_keyboard[1][0].text == "Sub C"
                    assert kb.inline_keyboard[1][0].style == ButtonStyle.PRIMARY

@pytest.mark.asyncio
async def test_products_and_mixed_layout(make_callback_query, mock_fsm):
    """Test products are always 1 per row."""
    from bot.handlers.user.shop_and_goods import _render_goods_page
    call = make_callback_query("dummy")

    with patch('bot.handlers.user.shop_and_goods.get_category_by_id', new_callable=AsyncMock) as mock_cat:
        with patch('bot.handlers.user.shop_and_goods.get_category_parent_id', new_callable=AsyncMock) as mock_parent:
            with patch('bot.handlers.user.shop_and_goods.LazyPaginator') as MockPaginator:
                with patch('bot.database.methods.read.get_stock_for_items', new_callable=AsyncMock) as mock_stock:
                    with patch('bot.handlers.user.shop_and_goods.get_store_settings', new_callable=AsyncMock) as mock_settings:
                        mock_parent.return_value = None
                        # Parent setting shouldn't matter for products
                        mock_cat.return_value = {"id": 1, "name": "Parent", "children_buttons_per_row": 2, "image_path": None, "description": "Desc"}
                        mock_stock.return_value = {101: 5, 102: -1}
                        mock_settings.return_value = MagicMock(root_category_buttons_per_row=1, shop_root_title="Title", shop_root_description="Desc")

                        mock_paginator_instance = MagicMock()
                        mock_paginator_instance.get_page = AsyncMock(return_value=[(101, "Prod A", "instant"), (102, "Prod B", "instant")])
                        mock_paginator_instance.has_next = AsyncMock(return_value=False)
                        mock_paginator_instance.has_prev = AsyncMock(return_value=False)
                        mock_paginator_instance.get_total_pages = AsyncMock(return_value=1)
                        MockPaginator.return_value = mock_paginator_instance

                        await _render_goods_page(call, mock_fsm, 1, 1)
                        kb = call.message.edit_text.call_args[1]['reply_markup']

                        assert len(kb.inline_keyboard[0]) == 1
                        assert "Prod A" in kb.inline_keyboard[0][0].text

                        assert len(kb.inline_keyboard[1]) == 1
                        assert "Prod B" in kb.inline_keyboard[1][0].text

@pytest.mark.asyncio
async def test_sqladmin_preserves_other_fields():
    """SQLAdmin image save preserves layout, layout save preserves image."""
    from bot.web.admin import CategoryAdmin

    mock_model = MagicMock()
    mock_model.children_buttons_per_row = 1
    mock_model.image_path = "old.jpg"
    mock_request = MagicMock()
    mock_request.state.temp_form_data = {}

    data = {"image_upload": b"fake", "children_buttons_per_row": "1"}
    with patch('bot.web.admin.handle_managed_image_upload', new_callable=AsyncMock) as mock_upload:
        with patch('sqladmin.models.ModelView.on_model_change', new_callable=AsyncMock) as mock_super:
            mock_upload.return_value = ("new.jpg", "/abs/new.jpg")
            await CategoryAdmin.on_model_change(CategoryAdmin, data, mock_model, False, mock_request)
            assert mock_model.image_path == "new.jpg"
            assert data["children_buttons_per_row"] == "1"

    data = {"image_upload": None, "children_buttons_per_row": "2"}
    mock_model.image_path = "old.jpg"
    with patch('bot.web.admin.handle_managed_image_upload', new_callable=AsyncMock) as mock_upload:
        with patch('sqladmin.models.ModelView.on_model_change', new_callable=AsyncMock) as mock_super:
            mock_upload.return_value = (None, None)
            await CategoryAdmin.on_model_change(CategoryAdmin, data, mock_model, False, mock_request)
            assert data["children_buttons_per_row"] == "2"
