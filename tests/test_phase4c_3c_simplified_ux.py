import pytest
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot.misc.services.outbox_dispatcher import outbox_dispatcher
from bot.database.models.main import Order, ManualFulfillmentJob, ManualOrderInteraction, ManualOrderConversationSession, User
from sqlalchemy import select, update
from datetime import datetime, timezone, timedelta

class DummyBot:
    def __init__(self):
        self.messages = []
    async def send_message(self, chat_id, text, reply_markup):
        self.messages.append({"text": text, "reply_markup": reply_markup})
        class Ret:
            message_id = 999
        return Ret()

@pytest.mark.asyncio
async def test_verification_text_and_keyboard():
    from bot.database.main import Database
    async with Database().session() as db_session:
        # Setup test order and job
        user = User(telegram_id=12345, role_id=1)
        db_session.add(user)
        order = Order(
            user_id=12345,
            public_id="ORD-123",
            status="processing",
            total=10,
            currency="USD"
        )
        db_session.add(order)
        await db_session.commit()

        from bot.database.models.main import OrderItem
        order_item = OrderItem(
            order_id=order.id,
            product_name_snapshot="Test Product",
            quantity=1,
            unit_price=10,
            subtotal=10,
            total=10
        )
        db_session.add(order_item)
        await db_session.commit()

        job = ManualFulfillmentJob(
            order_item_id=order_item.id,
            status="waiting_customer"
        )
        db_session.add(job)
        await db_session.commit()

        interaction = ManualOrderInteraction(
            order_id=order.id,
            fulfillment_job_id=job.id,
            direction="admin_to_customer",
            kind="verification_request",
            safe_preview="Original verification text"
        )
        db_session.add(interaction)
        await db_session.commit()
        # Create notification
        from bot.database.models.main import ManualOrderNotification
        from sqlalchemy.orm import selectinload
        notif = ManualOrderNotification(
            order_id=order.id,
            fulfillment_job_id=job.id,
            idempotency_key=f"verify_{interaction.id}",
            status="pending"
        )
        db_session.add(notif)
        await db_session.commit()

        stmt = select(ManualOrderNotification).where(ManualOrderNotification.id == notif.id).options(
            selectinload(ManualOrderNotification.order).selectinload(Order.user),
            selectinload(ManualOrderNotification.job).selectinload(ManualFulfillmentJob.order_item)
        )
        notif = (await db_session.execute(stmt)).scalar_one()

        bot_mock = DummyBot()
        outbox_dispatcher.bot = bot_mock
        await outbox_dispatcher._send_notification(notif, db_session)

        # 1. Inactive verification
        assert len(bot_mock.messages) > 0
        msg = bot_mock.messages[-1]

        assert "Verification Required" in msg["text"]
        assert "Tap “Reply to this Order” once, then send the code." in msg["text"]

        kb = msg["reply_markup"]
        assert len(kb.inline_keyboard) == 2
        assert "Reply to this Order" in kb.inline_keyboard[0][0].text
        assert "View Order" in kb.inline_keyboard[1][0].text
        assert f"orders:view:{order.id}:v:{interaction.id}" in kb.inline_keyboard[1][0].callback_data

        # Finish removal check
        assert "Finish Conversation" not in kb.inline_keyboard[0][0].text
        assert "Finish Conversation" not in kb.inline_keyboard[1][0].text

        # 2. Active verification
        session = ManualOrderConversationSession(
            telegram_id=12345,
            order_id=order.id,
            fulfillment_job_id=job.id,
            status="active",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30)
        )
        db_session.add(session)
        await db_session.commit()

        await outbox_dispatcher._send_notification(notif, db_session)
        msg_active = bot_mock.messages[-1]

        kb_active = msg_active["reply_markup"]
        assert len(kb_active.inline_keyboard) == 1
        assert "View Order" in kb_active.inline_keyboard[0][0].text
        assert "✅ The conversation is already active. Send the code directly in this chat." in msg_active["text"]

@pytest.mark.asyncio
async def test_normal_admin_message_keyboard():
    from bot.database.main import Database
    async with Database().session() as db_session:
        # Setup test order and job
        user = User(telegram_id=12345, role_id=1)
        db_session.add(user)
        order = Order(
            user_id=12345,
            public_id="ORD-124",
            status="processing",
            total=10,
            currency="USD"
        )
        db_session.add(order)
        await db_session.commit()

        from bot.database.models.main import OrderItem
        order_item = OrderItem(
            order_id=order.id,
            product_name_snapshot="Test Product 2",
            quantity=1,
            unit_price=10,
            subtotal=10,
            total=10
        )
        db_session.add(order_item)
        await db_session.commit()

        job = ManualFulfillmentJob(
            order_item_id=order_item.id,
            status="in_progress"
        )
        db_session.add(job)
        await db_session.commit()

        interaction = ManualOrderInteraction(
            order_id=order.id,
            fulfillment_job_id=job.id,
            direction="admin_to_customer",
            kind="message",
            safe_preview="Hello from admin"
        )
        db_session.add(interaction)
        await db_session.commit()

        from bot.database.models.main import ManualOrderNotification
        from sqlalchemy.orm import selectinload
        notif = ManualOrderNotification(
            order_id=order.id,
            fulfillment_job_id=job.id,
            idempotency_key=f"msg_{interaction.id}",
            status="pending"
        )
        db_session.add(notif)
        await db_session.commit()

        stmt = select(ManualOrderNotification).where(ManualOrderNotification.id == notif.id).options(
            selectinload(ManualOrderNotification.order).selectinload(Order.user),
            selectinload(ManualOrderNotification.job).selectinload(ManualFulfillmentJob.order_item)
        )
        notif = (await db_session.execute(stmt)).scalar_one()

        bot_mock = DummyBot()
        outbox_dispatcher.bot = bot_mock
        await outbox_dispatcher._send_notification(notif, db_session)

        msg = bot_mock.messages[-1]
        kb = msg["reply_markup"]

        # Since there's no active session, it should have the Reply button
        assert len(kb.inline_keyboard) == 2
        assert "Reply to this Order" in kb.inline_keyboard[0][0].text
        assert "View Order" in kb.inline_keyboard[1][0].text
        assert "Finish" not in kb.inline_keyboard[0][0].text
        assert f"orders:view:{order.id}:m:{interaction.id}" in kb.inline_keyboard[1][0].callback_data
