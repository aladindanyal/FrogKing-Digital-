from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.database.main import Database
import pytest
from bot.database.models.main import Order, OrderItem, Goods, Categories, BoughtGoods
from bot.database.methods.orders import (
    create_order_with_item, generate_public_order_id, get_order_by_public_id
)
from bot.database.methods.transactions import buy_item_transaction
from bot.database.methods.create import create_item
import uuid


class TestOrders:
    
    async def test_generate_public_order_id(self):
        async with Database().session() as s:
            public_id = await generate_public_order_id(s)
            assert public_id.startswith("FGK-")
            assert len(public_id.split("-")) == 3
            assert len(public_id.split("-")[2]) == 8
            
    async def test_create_order_with_item(self, user_factory, item_factory):
        await user_factory(telegram_id=900001, balance=500)
        await item_factory(name="Widget", price=100, values=[("val1", False)])
        
        async with Database().session() as s:
            goods_result = await s.execute(select(Goods).where(Goods.name == "Widget"))
            goods = goods_result.scalars().first()
            if goods is None:
                raise ValueError("Goods not found!")
                
            order, order_item = await create_order_with_item(
                session=s,
                user_id=900001,
                item_id=goods.id,
                product_name=goods.name,
                quantity=2,
                unit_price=100.0,
                subtotal=200.0,
                discount_total=0.0,
                total=200.0,
                currency="RUB",
                promo_code=None,
                product_description=goods.description
            )
            
            await s.commit()
            
            assert order.id is not None
            assert order.public_id.startswith("FGK-")
            assert order.user_id == 900001
            assert order.status == "pending"
            assert order.total == 200.0
            
            assert order_item.id is not None
            assert order_item.order_id == order.id
            assert order_item.quantity == 2
            assert order_item.unit_price == 100.0
            
            # Fetch by public id
            fetched_order = await get_order_by_public_id(s, order.public_id)
            assert fetched_order.id == order.id

    async def test_buy_item_transaction_quantity_3(self, user_factory, item_factory):
        # Create user with enough balance
        await user_factory(telegram_id=900002, balance=1000)
        # Create item with 3 values
        await item_factory(name="MultiWidget", price=100, values=[("val1", False), ("val2", False), ("val3", False)])
        
        # We need to get the item instance inside the session to pass into buy_item_transaction
        async with Database().session() as s:
            goods = (await s.execute(select(Goods).where(Goods.name == "MultiWidget"))).scalars().first()
            assert goods is not None
        
        # Execute purchase
        success, code, result = await buy_item_transaction(
            telegram_id=900002,
            item_name=goods.name,
            quantity=3,
            promo_code=None
        )
        assert success is True
        
        async with Database().session() as s:
            order_id = result['order_id']
            order_item_id = result['order_item_id']
            
            # Check Order
            order = await s.get(Order, order_id)
            assert order is not None
            assert order.total == Decimal("300.00")
            
            # Check OrderItem
            order_item = await s.get(OrderItem, order_item_id)
            assert order_item is not None
            assert order_item.quantity == 3
            
            # Check BoughtGoods
            bought_goods = (await s.execute(select(BoughtGoods).where(BoughtGoods.order_id == order_id))).scalars().all()
            assert len(bought_goods) == 3
            for bg in bought_goods:
                assert bg.order_item_id == order_item_id
                assert bg.order_id == order_id

    async def test_historical_bought_goods_valid(self):
        # historical BoughtGoods with NULL links remain valid
        async with Database().session() as s:
            legacy_bg = BoughtGoods(
                name="LegacyItem",
                value="legacyval",
                price=50.0,
                buyer_id=None,
                unique_id=uuid.uuid4().int >> 65,
                order_id=None,
                order_item_id=None
            )
            s.add(legacy_bg)
            await s.commit()
            
            retrieved_bg = await s.get(BoughtGoods, legacy_bg.id)
            assert retrieved_bg is not None
            assert retrieved_bg.order_id is None
            assert retrieved_bg.order_item_id is None

    async def test_cascade_set_null_on_delete(self, user_factory, item_factory):
        # Setup purchase
        await user_factory(telegram_id=900003, balance=500)
        await item_factory(name="CascadeWidget", price=100, values=[("val1", False)])
        
        async with Database().session() as s:
            goods = (await s.execute(select(Goods).where(Goods.name == "CascadeWidget"))).scalars().first()
            goods_id = goods.id
            goods_name = goods.name
            
        success, code, result = await buy_item_transaction(telegram_id=900003, item_name=goods_name, quantity=1, promo_code=None)
        assert success is True
        
        async with Database().session() as s:
            order_id = result['order_id']
            order_item_id = result['order_item_id']
            
            # Deleting Goods sets OrderItem.item_id to NULL without deleting BoughtGoods
            goods = await s.get(Goods, goods_id)
            await s.delete(goods)
            await s.commit()
            
            order_item = await s.get(OrderItem, order_item_id)
            assert order_item is not None
            assert order_item.item_id is None
            
            bg = (await s.execute(select(BoughtGoods).where(BoughtGoods.order_id == order_id))).scalars().first()
            assert bg is not None
            assert bg.order_item_id == order_item_id
            
            # Deleting an OrderItem sets BoughtGoods.order_item_id to NULL
            await s.delete(order_item)
            await s.commit()
            
            bg_after = await s.get(BoughtGoods, bg.id)
            assert bg_after.order_item_id is None
            assert bg_after.order_id == order_id
            
            # Deleting an Order sets BoughtGoods.order_id to NULL
            order = await s.get(Order, order_id)
            await s.delete(order)
            await s.commit()
            
            bg_final = await s.get(BoughtGoods, bg.id)
            assert bg_final.order_id is None
            assert bg_final.unique_id is not None # unique_id preserved

    async def test_failed_transaction_no_orphan_records(self, user_factory, item_factory):
        # Create user with insufficient funds
        await user_factory(telegram_id=900004, balance=50)
        await item_factory(name="ExpensiveWidget", price=100, values=[("val1", False)])
        
        async with Database().session() as s:
            goods = (await s.execute(select(Goods).where(Goods.name == "ExpensiveWidget"))).scalars().first()
            goods_id = goods.id
            goods_name = goods.name
        
        # Execute purchase which will fail
        success, code, result = await buy_item_transaction(telegram_id=900004, item_name=goods_name, quantity=1, promo_code=None)
        assert success is False
        assert code == "insufficient_funds"
        
        async with Database().session() as s:
            # Check that no orders, order items or bought goods were created for this user
            orders = (await s.execute(select(Order).where(Order.user_id == 900004))).scalars().all()
            assert len(orders) == 0
            
            bg = (await s.execute(select(BoughtGoods).where(BoughtGoods.buyer_id == 900004))).scalars().all()
            assert len(bg) == 0

from unittest.mock import AsyncMock, MagicMock, patch
from bot.web.admin import BoughtGoodsAdmin
from bot.handlers.user.orders import orders_list_handler
from aiogram.types import CallbackQuery, Message

class TestOrderUI:
    
    @pytest.mark.asyncio
    async def test_sqladmin_label_is_delivered_items(self):
        assert BoughtGoodsAdmin.name == "Delivered Item"
        assert BoughtGoodsAdmin.name_plural == "Delivered Items"
        assert BoughtGoodsAdmin.model == BoughtGoods

    @pytest.mark.asyncio
    async def test_my_orders_pagination_logic_first_page(self):
        call = AsyncMock(spec=CallbackQuery)
        call.data = "orders:list:0"
        call.from_user = MagicMock(id=123)
        call.message = AsyncMock(spec=Message)
        call.message.edit_text = AsyncMock()
        call.answer = AsyncMock()

        with patch('bot.handlers.user.orders.count_user_orders', return_value=12), \
             patch('bot.handlers.user.orders.list_user_orders') as mock_list, \
             patch('bot.handlers.user.orders.Database') as mock_db:
             
            mock_session = AsyncMock()
            mock_db.return_value.session.return_value.__aenter__.return_value = mock_session
            
            mock_orders = []
            for i in range(5):
                o = MagicMock()
                o.id = i
                o.public_id = f"FGK-20260712-TEST{i}"
                o.status = "completed"
                o.created_at = None
                mock_orders.append(o)
                
            mock_list.return_value = mock_orders
            
            await orders_list_handler(call)
            
            mock_list.assert_called_once()
            args, kwargs = mock_list.call_args
            assert kwargs['limit'] == 5
            assert kwargs['offset'] == 0
            
            edit_text_args = call.message.edit_text.call_args
            kb = edit_text_args.kwargs['reply_markup']
            buttons = kb.inline_keyboard
            
            for i in range(5):
                assert len(buttons[i]) == 1
                assert buttons[i][0].callback_data == f"orders:view:{i}"
                assert "FGK-" in buttons[i][0].text
                
            assert len(buttons[5]) == 2
            assert buttons[5][0].text == "1/3"
            assert buttons[5][0].callback_data == "dummy_button"
            assert "Next" in buttons[5][1].text
            assert buttons[5][1].callback_data == "orders:list:1"
            
            assert len(buttons[6]) == 1
            assert buttons[6][0].callback_data == "profile"

    @pytest.mark.asyncio
    async def test_my_orders_last_page(self):
        call = AsyncMock(spec=CallbackQuery)
        call.data = "orders:list:2"
        call.from_user = MagicMock(id=123)
        call.message = AsyncMock(spec=Message)
        call.message.edit_text = AsyncMock()
        call.answer = AsyncMock()

        with patch('bot.handlers.user.orders.count_user_orders', return_value=12), \
             patch('bot.handlers.user.orders.list_user_orders') as mock_list, \
             patch('bot.handlers.user.orders.Database') as mock_db:
             
            mock_session = AsyncMock()
            mock_db.return_value.session.return_value.__aenter__.return_value = mock_session
            
            mock_orders = []
            for i in range(2):
                o = MagicMock()
                o.id = 10 + i
                o.public_id = f"FGK-20260712-TEST{i}"
                o.status = "completed"
                o.created_at = None
                mock_orders.append(o)
                
            mock_list.return_value = mock_orders
            
            await orders_list_handler(call)
            
            mock_list.assert_called_once()
            args, kwargs = mock_list.call_args
            assert kwargs['limit'] == 5
            assert kwargs['offset'] == 10
            
            edit_text_args = call.message.edit_text.call_args
            kb = edit_text_args.kwargs['reply_markup']
            buttons = kb.inline_keyboard
            
            assert len(buttons[0]) == 1
            assert len(buttons[1]) == 1
            
            assert len(buttons[2]) == 2
            assert "Previous" in buttons[2][0].text
            assert buttons[2][0].callback_data == "orders:list:1"
            assert buttons[2][1].text == "3/3"
            assert buttons[2][1].callback_data == "dummy_button"
            
            assert len(buttons[3]) == 1
            assert buttons[3][0].callback_data == "profile"

    @pytest.mark.asyncio
    async def test_my_orders_empty_state_no_legacy_button(self):
        call = AsyncMock(spec=CallbackQuery)
        call.data = "orders:list:0"
        call.from_user = MagicMock(id=123)
        call.message = AsyncMock(spec=Message)
        call.message.edit_text = AsyncMock()
        call.answer = AsyncMock()

        with patch('bot.handlers.user.orders.count_user_orders', return_value=0), \
             patch('bot.handlers.user.orders.list_user_orders') as mock_list, \
             patch('bot.handlers.user.orders.Database') as mock_db:
             
            mock_session = AsyncMock()
            mock_db.return_value.session.return_value.__aenter__.return_value = mock_session
            
            mock_list.return_value = []
            
            await orders_list_handler(call)
            
            edit_text_args = call.message.edit_text.call_args
            kb = edit_text_args.kwargs['reply_markup']
            buttons = kb.inline_keyboard
            
            assert len(buttons) == 1
            assert len(buttons[0]) == 1
            assert buttons[0][0].callback_data == "profile"

    @pytest.mark.asyncio
    async def test_stale_legacy_callbacks_redirect(self):
        from bot.handlers.user.shop_and_goods import legacy_bought_items_redirect
        call = AsyncMock(spec=CallbackQuery)
        call.data = "bought_items"
        call.from_user = MagicMock(id=123)
        call.message = AsyncMock(spec=Message)
        call.message.edit_text = AsyncMock()
        call.answer = AsyncMock()
        
        with patch('bot.handlers.user.orders.count_user_orders', return_value=0), \
             patch('bot.handlers.user.orders.list_user_orders') as mock_list, \
             patch('bot.handlers.user.orders.Database') as mock_db:
             
            mock_session = AsyncMock()
            mock_db.return_value.session.return_value.__aenter__.return_value = mock_session
            mock_list.return_value = []

            await legacy_bought_items_redirect(call)
            
            call.answer.assert_any_call("orders.legacy_redirect:{'default': 'This legacy purchase history is no longer available. Please use My Orders.'}", show_alert=True)
            assert call.data == "orders:list:0"
            call.message.edit_text.assert_called()
