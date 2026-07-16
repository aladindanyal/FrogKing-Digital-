import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram.types import CallbackQuery, Message, User, Chat
from aiogram.fsm.context import FSMContext

from bot.handlers.user.orders import order_buy_again_handler, order_view_handler
from bot.database.models.main import Order, OrderItem, Goods
from bot.database.main import Database
from bot.misc import EnvKeys

class TestOrderHandlers:

    @pytest.fixture
    def mock_call(self):
        call = AsyncMock(spec=CallbackQuery)
        call.from_user = User(id=900005, is_bot=False, first_name="Test")
        call.message = AsyncMock(spec=Message)
        call.message.edit_text = AsyncMock()
        call.answer = AsyncMock()
        return call

    @pytest.fixture
    def mock_state(self):
        return AsyncMock(spec=FSMContext)

    async def test_order_buy_again_invalid_callback(self, mock_call, mock_state):
        mock_call.data = "order_buy_again:abc"
        await order_buy_again_handler(mock_call, mock_state)
        mock_call.answer.assert_called_once_with("Invalid callback data.", show_alert=True)

    async def test_order_buy_again_forbidden(self, mock_call, mock_state, user_factory, item_factory):
        # Create user and item
        await user_factory(telegram_id=900006, balance=100) # different user
        await item_factory(name="TestItem", price=10)
        
        async with Database().session() as s:
            # Create a manual order/order_item belonging to 900006
            from bot.database.methods.orders import create_order_with_item
            order, order_item = await create_order_with_item(
                s, 900006, 1, "TestItem", 1, 10, 10, 0, 10, "RUB", None, "desc"
            )
            await s.commit()
            item_id = order_item.id
            
        mock_call.data = f"order_buy_again:{item_id}"
        await order_buy_again_handler(mock_call, mock_state)
        # mocked call from_user is 900005, which is not 900006
        mock_call.answer.assert_called_once_with("Order not found.", show_alert=True)

    @patch("bot.handlers.user.shop_and_goods._render_item_page_by_id")
    async def test_order_buy_again_success(self, mock_render, mock_call, mock_state, user_factory, item_factory):
        await user_factory(telegram_id=900005, balance=100)
        await item_factory(name="TestItem2", price=10)
        
        async with Database().session() as s:
            from sqlalchemy import select
            goods = (await s.execute(select(Goods).where(Goods.name == "TestItem2"))).scalars().first()
            from bot.database.methods.orders import create_order_with_item
            order, order_item = await create_order_with_item(
                s, 900005, goods.id, "TestItem2", 1, 10, 10, 0, 10, "RUB", None, "desc"
            )
            await s.commit()
            item_id = order_item.id
            goods_id = goods.id
            
        mock_call.data = f"order_buy_again:{item_id}"
        await order_buy_again_handler(mock_call, mock_state)
        
        mock_render.assert_called_once_with(mock_call, mock_state, goods_id, back_data='menu', user_id=900005, send_new=True)
        mock_call.answer.assert_called_once_with()

    async def test_support_button_when_configured(self, mock_call, user_factory, item_factory):
        await user_factory(telegram_id=900005, balance=100)
        async with Database().session() as s:
            from bot.database.methods.orders import create_order_with_item
            order, _ = await create_order_with_item(
                s, 900005, 1, "Test", 1, 10, 10, 0, 10, "RUB", None, "desc"
            )
            await s.commit()
            public_id = order.public_id
            order_id = order.id
            
        mock_call.data = f"orders:view:{order_id}"
        
        with patch.object(EnvKeys, 'HELPER_ID', 123456):
            await order_view_handler(mock_call)
            
        args, kwargs = mock_call.message.edit_text.call_args
        reply_markup = kwargs.get('reply_markup')
        
        # Look for support button
        support_found = False
        for row in reply_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data == f"support_order:{public_id}":
                    support_found = True
        assert support_found is True

    async def test_support_button_when_not_configured(self, mock_call, user_factory):
        await user_factory(telegram_id=900005, balance=100)
        async with Database().session() as s:
            from bot.database.methods.orders import create_order_with_item
            order, _ = await create_order_with_item(
                s, 900005, 1, "Test", 1, 10, 10, 0, 10, "RUB", None, "desc"
            )
            await s.commit()
            public_id = order.public_id
            order_id = order.id
            
        mock_call.data = f"orders:view:{order_id}"
        
        with patch.object(EnvKeys, 'HELPER_ID', None):
            await order_view_handler(mock_call)
            
        args, kwargs = mock_call.message.edit_text.call_args
        reply_markup = kwargs.get('reply_markup')
        
        # Look for support button
        support_found = False
        for row in reply_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data and btn.callback_data.startswith("support_order:"):
                    support_found = True
        assert support_found is False
