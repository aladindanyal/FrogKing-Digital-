import pytest
import json
from decimal import Decimal
from starlette.testclient import TestClient
from bot.web.admin import create_admin_app

@pytest.fixture
def admin_client():
    app = create_admin_app()
    with TestClient(app) as client:
        yield client

@pytest.mark.asyncio
async def test_conversation_opening_and_multi_message(admin_client):
    from bot.database.models import User, Goods, Categories, Order, OrderItem, ManualFulfillmentJob, ManualOrderInteraction, ManualOrderConversationSession
    from bot.database.main import Database
    import asyncio

    async with Database().session() as s:
        user = User(telegram_id=99998, balance=Decimal('0'))
        s.add(user)
        cat = Categories(name='Test Cat', description='test')
        s.add(cat)
        await s.flush()
        goods = Goods(name='Test Product', price=Decimal('10'), description='desc', category_id=cat.id, fulfillment_mode='manual')
        s.add(goods)
        await s.flush()
        order = Order(user_id=99998, currency='USD', total=10, subtotal=10, public_id='TEST-CONV-1')
        s.add(order)
        await s.flush()
        order_item = OrderItem(order_id=order.id, item_id=goods.id, quantity=1, unit_price=10, subtotal=10, total=10, product_name_snapshot='Test Product')
        s.add(order_item)
        await s.flush()
        job = ManualFulfillmentJob(order_item_id=order_item.id, status='in_progress')
        s.add(job)
        await s.commit()
        job_id = job.id

        # Open conversation session manually
        import datetime
        expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=30)
        session = ManualOrderConversationSession(telegram_id=99998, order_id=order.id, fulfillment_job_id=job.id, status='active', expires_at=expires)
        s.add(session)
        await s.commit()

    # Route isolation test: test that the state endpoint separates them
    async with Database().session() as s:
        ia1 = ManualOrderInteraction(
            order_id=order.id,
            fulfillment_job_id=job_id,
            direction='admin_to_customer',
            kind='message',
            encrypted_content='{"ciphertext": "abc", "version": 1}',
            safe_preview='Admin says hi'
        )
        ia2 = ManualOrderInteraction(
            order_id=order.id,
            fulfillment_job_id=job_id,
            direction='customer_to_admin',
            kind='customer_reply',
            encrypted_content='{"ciphertext": "def", "version": 1}',
            safe_preview='Customer reply',
            is_sensitive=True
        )
        ia3 = ManualOrderInteraction(
            order_id=order.id,
            fulfillment_job_id=job_id,
            direction='system',
            kind='status_change',
            safe_preview='Status changed'
        )
        s.add_all([ia1, ia2, ia3])
        await s.commit()

    # Mock auth for admin
    import unittest.mock as mock
    with mock.patch("bot.web.fulfillment.is_authenticated", return_value=True), \
         mock.patch("sqladmin.authentication.AuthenticationBackend.authenticate", return_value=True):

        state_response = admin_client.get(f"/admin/fulfillment/api/order/{job_id}/state")
        assert state_response.status_code == 200
        state_data = state_response.json()

        assert "conversation_messages" in state_data
        assert "fulfillment_events" in state_data

        assert len(state_data["conversation_messages"]) == 2
        assert len(state_data["fulfillment_events"]) == 1

        # Verify ui segregation
        assert state_data["fulfillment_events"][0]["kind"] == "status_change"
        assert state_data["conversation_messages"][0]["kind"] == "message"
        assert state_data["conversation_messages"][1]["kind"] == "customer_reply"

        # Regression check: interactions is missing from state_data now, or if it's there it's just backward compat

        # Verify UI rendering
        ui_response = admin_client.get(f"/admin/fulfillment/order/{job_id}")
        assert ui_response.status_code == 200
        html = ui_response.text

        # Ensure new timeline separated blocks exist
        assert "Customer Conversation" in html
        assert "Operational Timeline" in html
        assert "id=\"inlineMessageText\"" in html
        assert "id=\"inlineMessageSubmit\"" in html
        assert "id=\"btn-msg\"" not in html # modal button was removed

@pytest.mark.asyncio
async def test_on_reply_to_order():
    from bot.database.models import User, Goods, Categories, Order, OrderItem, ManualFulfillmentJob, ManualOrderConversationSession
    from bot.database.main import Database
    from bot.handlers.user.order_reply import on_reply_to_order
    from sqlalchemy import select
    import asyncio
    import json

    async with Database().session() as s:
        user = User(telegram_id=99999, balance=Decimal('0'))
        s.add(user)
        cat = Categories(name='Test Cat', description='test')
        s.add(cat)
        await s.flush()
        goods = Goods(name='Test Product', price=Decimal('10'), description='desc', category_id=cat.id, fulfillment_mode='manual')
        s.add(goods)
        await s.flush()
        order = Order(user_id=99999, currency='USD', total=10, subtotal=10, public_id='TEST-CONV-2')
        s.add(order)
        await s.flush()
        order_item = OrderItem(order_id=order.id, item_id=goods.id, quantity=1, unit_price=10, subtotal=10, total=10, product_name_snapshot='Test Product')
        s.add(order_item)
        await s.flush()
        job = ManualFulfillmentJob(order_item_id=order_item.id, status='waiting_customer')
        s.add(job)
        await s.commit()
        job_id = job.id
        order_id = order.id

    class MockUser:
        id = 99999

    class MockMessage:
        def __init__(self):
            from aiogram.types import InlineKeyboardMarkup
            self.reply_markup = InlineKeyboardMarkup(inline_keyboard=[])
            self.last_text = None
            self.last_reply_markup = None

        async def edit_reply_markup(self, reply_markup=None):
            self.reply_markup = reply_markup
            self.last_reply_markup = reply_markup
        async def answer(self, text, reply_markup=None):
            self.last_text = text
            self.last_reply_markup = reply_markup

    class MockCallback:
        def __init__(self, data, from_user):
            self.data = data
            self.from_user = from_user
            self.message = MockMessage()
            self.answered = False
            self.answered_kwargs = {}

        async def answer(self, text=None, **kwargs):
            self.answered = True
            self.answered_kwargs = kwargs

    call = MockCallback(f"reply_order_{order_id}_{job_id}", MockUser())
    await on_reply_to_order(call)

    assert call.answered is True
    assert "Order Conversation Opened" in getattr(call.message, 'last_text', '')

    # Verify idempotent
    call2 = MockCallback(f"reply_order_{order_id}_{job_id}", MockUser())
    await on_reply_to_order(call2)

    async with Database().session() as s:
        res = await s.execute(select(ManualOrderConversationSession).filter_by(telegram_id=99999, status='active'))
        sessions = res.scalars().all()
        assert len(sessions) == 1
        assert sessions[0].fulfillment_job_id == job_id

    # The keyboard is now removed entirely from opened_msg
    kb = call.message.last_reply_markup
    assert kb is None or len(kb.inline_keyboard) == 0

    # Test process_order_reply keyboard
    from bot.handlers.user.order_reply import process_order_reply
    class MockMessageText:
        def __init__(self, from_user):
            self.from_user = from_user
            self.text = "Hello Admin!"
            self.message_id = 1234
            self.last_text = None
            self.last_reply_markup = None
        async def answer(self, text, reply_markup=None):
            self.last_text = text
            self.last_reply_markup = reply_markup

    msg = MockMessageText(MockUser())
    await process_order_reply(msg, sessions[0].id)
    assert "Message Received" in getattr(msg, 'last_text', '')
    kb_msg = msg.last_reply_markup
    assert kb_msg is None

    # Test outbox dispatch formatting
    from bot.misc.services.outbox_dispatcher import outbox_dispatcher
    class DummyBot:
        async def send_message(self, chat_id, text, reply_markup):
            self.last_text = text
            self.last_markup = reply_markup
            class Ret:
                message_id = 999
            return Ret()
    outbox_dispatcher.bot = DummyBot()

    class DummyNotif:
        def __init__(self, t_id, o_id, j_id):
            class DU:
                telegram_id = t_id
            class DO:
                public_id = "PUB123"
                user = DU()
            class DOI:
                product_name_snapshot = "Prod"
            class DJ:
                order_item = DOI()

            self.order_id = o_id
            self.fulfillment_job_id = j_id
            self.idempotency_key = "comp_1"
            self.order = DO()
            self.job = DJ()

    class DummySession:
        async def get(self, model, id):
            return None
        async def execute(self, stmt):
            class Res:
                def scalar_one_or_none(self):
                    return None
            return Res()

    await outbox_dispatcher._send_notification(DummyNotif(99999, order_id, job_id), DummySession())
    kb_out = outbox_dispatcher.bot.last_markup
    assert kb_out.inline_keyboard[0][0].callback_data == f"orders:view:{order_id}:c:0"
    assert kb_out.inline_keyboard[0][1].callback_data == "back_to_menu"
    assert len(f"orders:view:{order_id}:c:0") <= 64

    # Ensure handler logic correctly matches
    from bot.handlers.user.orders import order_view_handler
    assert order_view_handler is not None
