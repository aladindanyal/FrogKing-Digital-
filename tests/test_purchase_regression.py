import pytest
from decimal import Decimal
from bot.database.main import Database
from bot.database.models import User, Categories, Goods, Order, OrderItem
from bot.handlers.user.renderers import render_purchase_success_from_order

@pytest.mark.asyncio
async def test_render_purchase_success_regression():
    # 1. Guard against re-adding product_name to OrderItem
    assert not hasattr(OrderItem, 'product_name'), "OrderItem should not have product_name attribute"

    # Setup database with a real automatic order
    async with Database().session() as s:
        user = User(telegram_id=99998, balance=Decimal('0'))
        s.add(user)
        cat = Categories(name='Test Cat', description='test')
        s.add(cat)
        await s.flush()
        
        goods = Goods(name='Automatic Test Product', price=Decimal('10'), description='desc', category_id=cat.id, fulfillment_mode='instant')
        s.add(goods)
        await s.flush()
        
        order = Order(user_id=99998, currency='USD', total=10, subtotal=10, public_id='TEST-AUTO-1', status='completed')
        s.add(order)
        await s.flush()
        
        order_item = OrderItem(
            order_id=order.id, 
            item_id=goods.id, 
            quantity=1, 
            unit_price=10, 
            subtotal=10, 
            total=10, 
            product_name_snapshot='Automatic Test Product',
            fulfillment_status='delivered'
        )
        s.add(order_item)
        await s.commit()
        
        order_id = order.id
        order_item_id = order_item.id

    class MockMessage:
        def __init__(self):
            self.answers = []
            self.last_reply_markup = None
            
        async def answer(self, text, reply_markup=None, parse_mode=None, show_alert=False):
            self.answers.append((text, reply_markup))
            if reply_markup:
                self.last_reply_markup = reply_markup

    msg = MockMessage()
    
    # Render the success receipt
    text, kb = await render_purchase_success_from_order(msg, order_id)
    
    assert text is not None
    assert kb is not None
    all_text = text
    
    # 4. Product name renders correctly from product_name_snapshot
    assert "Automatic Test Product" in all_text
    # 5. Leave a Review button appears for delivered items
    # 6. View Order callback contains purchase-success origin (orders:view:ID:p)
    found_review = False
    found_view_order = False
    
    if kb:
        for row in kb.inline_keyboard:
            for btn in row:
                if "review:start" in btn.callback_data:
                    found_review = True
                    assert btn.callback_data == f"review:start:{order_item_id}:p:{order_id}"
                if "orders:view" in btn.callback_data:
                    found_view_order = True
                    assert btn.callback_data == f"orders:view:{order_id}:p"
                    
    assert found_review, "Leave a Review button should appear"
    assert found_view_order, "View Order button with 'p' origin should appear"
