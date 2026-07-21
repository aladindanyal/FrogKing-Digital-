import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
from aiogram import Dispatcher, Bot
from aiogram.types import Message, Chat, User as AiogramUser, MessageEntity
from aiogram.fsm.storage.memory import MemoryStorage
from decimal import Decimal
import os

from bot.database.main import Database
from bot.database.models.main import User, Order, ManualFulfillmentJob, ManualOrderConversationSession, ManualOrderInteraction
from bot.handlers.main import register_all_handlers
from bot.misc import encryption


@pytest.fixture
def mock_bot():
    class MockBot(Bot):
        def __init__(self):
            super().__init__(token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
        async def __call__(self, method, *args, **kwargs):
            if method.__class__.__name__ == 'SendMessage':
                return Message(message_id=999, date=datetime.now(timezone.utc), chat=Chat(id=1, type="private"))
            return True

    return MockBot()

@pytest.mark.asyncio
async def test_active_conversation_routing(test_dp, mock_bot):
    os.environ["DATA_ENCRYPTION_ACTIVE_VERSION"] = "1"
    os.environ["DATA_ENCRYPTION_KEY_V1"] = "yHq-6nF1A73n_z01N9t6tGq7x_J_1gqWwQ3Z_f5H9Y0="

    async with Database().session() as s:
        # Create user
        user = User(telegram_id=9999994, balance=Decimal("100.00"))
        s.add(user)

        # Create an order
        order = Order(user_id=9999994, public_id="ACR-123", status="paid", subtotal=10, total=10)
        s.add(order)
        await s.flush()

        # Create a job
        job = ManualFulfillmentJob(order_item_id=1, status="in_progress")
        s.add(job)
        await s.flush()

        # Create an active conversation
        conv = ManualOrderConversationSession(
            telegram_id=9999994,
            order_id=order.id,
            fulfillment_job_id=job.id,
            status='active',
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=30)
        )
        s.add(conv)
        await s.commit()

        from aiogram.types import Update

        # Send /start
        msg = Message(
            message_id=1001,
            date=datetime.now(timezone.utc),
            chat=Chat(id=9999994, type="private"),
            from_user=AiogramUser(id=9999994, is_bot=False, first_name="Test"),
            text="/start"
        )
        update = Update(update_id=1, message=msg)
        await test_dp.feed_update(mock_bot, update)

        # Send /start payload
        msg = Message(
            message_id=1002,
            date=datetime.now(timezone.utc),
            chat=Chat(id=9999994, type="private"),
            from_user=AiogramUser(id=9999994, is_bot=False, first_name="Test"),
            text="/start 123"
        )
        update = Update(update_id=2, message=msg)
        await test_dp.feed_update(mock_bot, update)

        # Send ordinary text message
        msg_text = Message(
            message_id=1003,
            date=datetime.now(timezone.utc),
            chat=Chat(id=9999994, type="private"),
            from_user=AiogramUser(id=9999994, is_bot=False, first_name="Test"),
            text="This is an ordinary message containing /start inside it"
        )
        update = Update(update_id=3, message=msg_text)
        await test_dp.feed_update(mock_bot, update)

        # Send unknown command
        msg_unknown = Message(
            message_id=1004,
            date=datetime.now(timezone.utc),
            chat=Chat(id=9999994, type="private"),
            from_user=AiogramUser(id=9999994, is_bot=False, first_name="Test"),
            text="/unknown_command"
        )
        update = Update(update_id=4, message=msg_unknown)
        await test_dp.feed_update(mock_bot, update)

        # Check DB
        # Only the ordinary text message should have been recorded as an interaction
        from sqlalchemy import select
        interactions = await s.execute(select(ManualOrderInteraction).filter(ManualOrderInteraction.order_id == order.id))
        interactions = interactions.scalars().all()

        assert len(interactions) == 1, f"Expected exactly 1 interaction, got {len(interactions)}"

        # Verify the session is still active
        active_session = await s.get(ManualOrderConversationSession, conv.id)
        assert active_session.status == 'active', "Session was unexpectedly closed!"
