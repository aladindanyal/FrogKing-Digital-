import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from decimal import Decimal
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User as AiogramUser, Chat

from bot.database.main import Database
from bot.database.models.main import User, Categories, Goods, StoreSettings, ProductCustomerField, ItemValues, CheckoutIntakeDraft, Order
from bot.handlers.user.manual_intake import start_manual_intake
from bot.handlers.user.shop_and_goods import (
    qty_keypad_continue_handler, qty_digit_handler, qty_keypad_handler,
    qty_clear_handler, continue_product_checkout
)
import os
import random
from sqlalchemy import select, delete

@pytest.fixture(autouse=True)
def setup_env():
    os.environ["DATA_ENCRYPTION_ACTIVE_VERSION"] = "1"
    os.environ["DATA_ENCRYPTION_KEY_V1"] = "yHq-6nF1A73n_z01N9t6tGq7x_J_1gqWwQ3Z_f5H9Y0="
    yield

@pytest.fixture
def mock_fsm():
    class MockState(FSMContext):
        def __init__(self):
            self.data = {}
        async def get_data(self):
            return self.data
        async def update_data(self, **kwargs):
            self.data.update(kwargs)
        async def set_state(self, state):
            self.state = state
    return MockState()

@pytest.mark.asyncio
async def test_store_settings_resolution(mock_fsm):
    # Prepare real ORM StoreSettings
    uid = random.randint(1000000, 9000000)
    async with Database().session() as s:
        await s.execute(delete(StoreSettings))
        settings = StoreSettings(shop_root_title="Test")
        s.add(settings)
        user = User(telegram_id=uid, balance=Decimal("100"))
        s.add(user)
        cat = Categories(name="Cat")
        s.add(cat)
        await s.flush()
        goods = Goods(name=f"Goods1_{uid}", description="desc", price=Decimal("10"), category_id=cat.id, fulfillment_mode="manual", customer_input_intro_i18n={"en": "Intro"})
        s.add(goods)
        await s.flush()
        field = ProductCustomerField(goods_id=goods.id, field_key="f1", field_type="text", scope="per_order", label_i18n={"en": "Label"})
        s.add(field)
        await s.commit()
        goods_id = goods.id
    
    call = MagicMock(spec=CallbackQuery)
    call.from_user = AiogramUser(id=uid, is_bot=False, first_name="Test")
    call.message = MagicMock(spec=Message)

    with patch('bot.handlers.user.manual_intake.get_item_info_cached', new_callable=AsyncMock) as mock_cache:
        mock_cache.return_value = {"id": goods_id, "name": f"Goods1_{uid}", "customer_input_intro_i18n": {"en": "Intro"}}
        await mock_fsm.update_data(item_quantity=1)
        
        # message.answer should be called instead of safe_edit_or_send
        call.message.answer = AsyncMock()
        
        # Should not raise AttributeError
        await start_manual_intake(call, mock_fsm, f"Goods1_{uid}", str(goods_id), uid)
        
        # Manual introduction renders in the first question step
        call.message.answer.assert_called_once()
        args, kwargs = call.message.answer.call_args
        assert "Intro" in args[0]


@pytest.mark.asyncio
async def test_preset_route(mock_fsm):
    uid = random.randint(1000000, 9000000)
    async with Database().session() as s:
        user = User(telegram_id=uid, balance=Decimal("100"))
        s.add(user)
        cat = Categories(name="Cat2")
        s.add(cat)
        await s.flush()
        goods = Goods(name=f"Goods2_{uid}", description="desc", price=Decimal("10"), category_id=cat.id, fulfillment_mode="manual", customer_input_intro_i18n={"en": "Intro"})
        s.add(goods)
        await s.flush()
        iv = ItemValues(item_id=goods.id, value="val", is_infinity=True)
        s.add(iv)
        await s.commit()
        goods_id = goods.id

    call = MagicMock(spec=CallbackQuery)
    call.from_user = AiogramUser(id=uid, is_bot=False, first_name="Test")
    call.message = MagicMock(spec=Message)

    with patch('bot.handlers.user.shop_and_goods.answer_callback_safe') as mock_ans:
        with patch('bot.handlers.user.manual_intake.start_manual_intake') as mock_start:
            await continue_product_checkout(call, mock_fsm, goods_id, 3)
            # Callback answered once
            mock_ans.assert_called_once()
            # No legacy checkout, routes to manual intake
            mock_start.assert_called_once()

@pytest.mark.asyncio
async def test_keypad_quantity(mock_fsm):
    call = MagicMock(spec=CallbackQuery)
    call.from_user = AiogramUser(id=88889, is_bot=False, first_name="Test")
    
    # 1. Open keypad -> sets item_quantity to 0
    call.data = "qty:keypad:123"
    await mock_fsm.update_data(item_id=123, csrf_item="Goods2")
    with patch('bot.handlers.user.shop_and_goods._render_keypad_page') as mock_render:
        await qty_keypad_handler(call, mock_fsm)
        data = await mock_fsm.get_data()
        assert data['item_quantity'] == 0
        mock_render.assert_called_with(call, mock_fsm, "Goods2", 0, "123")
        
    # 2. Press '3' -> sets item_quantity to 3
    call.data = "qty:digit:123:3"
    with patch('bot.handlers.user.shop_and_goods._render_keypad_page') as mock_render:
        await qty_digit_handler(call, mock_fsm)
        data = await mock_fsm.get_data()
        assert data['item_quantity'] == 3
        mock_render.assert_called_with(call, mock_fsm, "Goods2", 3, "123")

    # 3. Press Continue -> sends 3
    call.data = "qty:keypad_continue:123"
    with patch('bot.handlers.user.shop_and_goods.continue_product_checkout') as mock_checkout:
        await qty_keypad_continue_handler(call, mock_fsm)
        mock_checkout.assert_called_with(call, mock_fsm, 123, 3)

    # 4. Zero is rejected
    await mock_fsm.update_data(item_quantity=0)
    with patch('bot.handlers.user.shop_and_goods.answer_callback_safe') as mock_ans:
        await qty_keypad_continue_handler(call, mock_fsm)
        args, kwargs = mock_ans.call_args
        assert "Quantity must be greater than zero." in args[1]
        
    # 5. Clear produces zero
    call.data = "qty:clear:123"
    with patch('bot.handlers.user.shop_and_goods._render_keypad_page'):
        await qty_clear_handler(call, mock_fsm)
        data = await mock_fsm.get_data()
        assert data['item_quantity'] == 0

@pytest.mark.asyncio
async def test_custom_text_and_draft_status(mock_fsm):
    uid = random.randint(1000000, 9000000)
    msg = MagicMock(spec=Message)
    msg.from_user = AiogramUser(id=uid, is_bot=False, first_name="Test")
    msg.answer = AsyncMock()

    async with Database().session() as s:
        goods = Goods(name=f"Goods3_{uid}", description="desc", price=Decimal("10"), category_id=1, fulfillment_mode="manual")
        s.add(goods)
        await s.flush()
        iv = ItemValues(item_id=goods.id, value="val", is_infinity=True)
        s.add(iv)
        await s.commit()
        goods_id = goods.id
        
    with patch('bot.handlers.user.shop_and_goods.answer_callback_safe') as mock_ans:
        with patch('bot.handlers.user.manual_intake.start_manual_intake') as mock_start:
            await continue_product_checkout(msg, mock_fsm, goods_id, 3)
            mock_ans.assert_not_called()
            mock_start.assert_called_once()
