import pytest
import datetime
import asyncio
from unittest.mock import AsyncMock, patch
from sqlalchemy import select

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest, TelegramNotFound

from bot.database.main import Database
from bot.database.models.main import BroadcastCampaign, BroadcastRecipient, User
from bot.misc.services.broadcast_dispatcher import BroadcastDispatcher
from bot.states.broadcast_state import BroadcastFSM
from bot.handlers.admin.broadcast import (
    send_message_callback_handler,
    target_callback_handler,
    broadcast_message_handler,
    confirm_broadcast_handler,
    cancel_broadcast_handler
)

pytestmark = pytest.mark.asyncio

async def create_users(user_factory, count=3):
    users = []
    for i in range(count):
        u = await user_factory(telegram_id=1000 + i)
        users.append(u)
    return users

class TestBroadcastHandlers:
    async def test_permission_rejection(self, make_callback_query, fsm_context):
        call = make_callback_query(data="send_message")
        pass

    async def test_text_draft_creation(self, make_message, fsm_context, user_factory):
        await user_factory(telegram_id=123)
        msg = make_message(text="Hello world!", user_id=123)
        msg.photo = None
        msg.html_text = "Hello world!"
        await fsm_context.update_data(target_locale=None)
        await broadcast_message_handler(msg, fsm_context)

        async with Database().session() as session:
            campaign = (await session.execute(select(BroadcastCampaign))).scalars().first()
            assert campaign is not None
            assert campaign.message_text == "Hello world!"
            assert campaign.status == "draft"

    async def test_confirmation_recipient_snapshot(self, make_callback_query, fsm_context, user_factory):
        await user_factory(telegram_id=1)
        await user_factory(telegram_id=2)
        async with Database().session() as session:
            u3 = User(telegram_id=3, registration_date=datetime.datetime.now(), is_blocked=True, role_id=1)
            session.add(u3)
            await session.commit()

            c = BroadcastCampaign(admin_id=999, message_text="Test", status="draft")
            session.add(c)
            await session.commit()
            campaign_id = c.id

        call = make_callback_query(data=f"bc_confirm_{campaign_id}")
        await confirm_broadcast_handler(call, fsm_context)

        async with Database().session() as session:
            campaign = await session.get(BroadcastCampaign, campaign_id)
            assert campaign.status == "confirmed"

            recipients = (await session.execute(select(BroadcastRecipient).where(BroadcastRecipient.campaign_id == campaign_id))).scalars().all()
            assert len(recipients) == 2
            assert {r.user_id for r in recipients} == {1, 2}

    async def test_target_locale(self, make_callback_query, fsm_context, user_factory):
        async with Database().session() as session:
            u1 = User(telegram_id=11, registration_date=datetime.datetime.now(), language_code='en', role_id=1)
            u2 = User(telegram_id=22, registration_date=datetime.datetime.now(), language_code='ru', role_id=1)
            session.add_all([u1, u2])

            c = BroadcastCampaign(admin_id=999, message_text="Test", status="draft", target_locale="en")
            session.add(c)
            await session.commit()
            campaign_id = c.id

        call = make_callback_query(data=f"bc_confirm_{campaign_id}")
        await confirm_broadcast_handler(call, fsm_context)

        async with Database().session() as session:
            recipients = (await session.execute(select(BroadcastRecipient).where(BroadcastRecipient.campaign_id == campaign_id))).scalars().all()
            assert len(recipients) == 1
            assert recipients[0].user_id == 11

    async def test_single_active_campaign_constraint(self, make_callback_query, fsm_context):
        async with Database().session() as session:
            c1 = BroadcastCampaign(admin_id=999, message_text="Running", status="running")
            c2 = BroadcastCampaign(admin_id=999, message_text="Draft", status="draft")
            session.add_all([c1, c2])
            await session.commit()
            campaign_id = c2.id

        call = make_callback_query(data=f"bc_confirm_{campaign_id}")
        await confirm_broadcast_handler(call, fsm_context)

        assert call.answer.called
        async with Database().session() as session:
            c2 = await session.get(BroadcastCampaign, campaign_id)
            assert c2.status == "draft"

    async def test_cancel_by_campaign_isolation(self, make_callback_query, fsm_context):
        async with Database().session() as session:
            c1 = BroadcastCampaign(admin_id=999, message_text="Draft", status="confirmed")
            session.add(c1)
            await session.commit()
            r1 = BroadcastRecipient(campaign_id=c1.id, user_id=1, status="pending")
            r2 = BroadcastRecipient(campaign_id=c1.id, user_id=2, status="sending")
            session.add_all([r1, r2])
            await session.commit()
            campaign_id = c1.id

        call = make_callback_query(data=f"bc_cancel_{campaign_id}")
        await cancel_broadcast_handler(call, fsm_context)

        async with Database().session() as session:
            c1 = await session.get(BroadcastCampaign, campaign_id)
            assert c1.status == "cancelled"
            r1 = await session.get(BroadcastRecipient, r1.id)
            assert r1.status == "cancelled"
            r2 = await session.get(BroadcastRecipient, r2.id)
            assert r2.status == "sending"

class TestBroadcastDispatcher:
    async def test_successful_dispatch(self, mock_bot):
        dispatcher = BroadcastDispatcher()
        dispatcher.bot = mock_bot

        async with Database().session() as session:
            u1 = User(telegram_id=101, registration_date=datetime.datetime.now(), role_id=1)
            u2 = User(telegram_id=102, registration_date=datetime.datetime.now(), role_id=1)
            c = BroadcastCampaign(admin_id=999, message_text="Hello", status="confirmed")
            session.add_all([u1, u2, c])
            await session.commit()

            session.add_all([
                BroadcastRecipient(campaign_id=c.id, user_id=101, status="pending"),
                BroadcastRecipient(campaign_id=c.id, user_id=102, status="pending"),
            ])
            await session.commit()

        has_more = await dispatcher._process_active_campaign()
        assert has_more == True
        has_more2 = await dispatcher._process_active_campaign()

        async with Database().session() as session:
            c = await session.get(BroadcastCampaign, c.id)
            assert c.status == "running"

            recs = (await session.execute(select(BroadcastRecipient))).scalars().all()
            for r in recs:
                assert r.status == "sent"

    async def test_forbidden_blocks_user(self, mock_bot):
        mock_bot.send_message.side_effect = TelegramForbiddenError(method="send_message", message="Forbidden")
        dispatcher = BroadcastDispatcher()
        dispatcher.bot = mock_bot

        async with Database().session() as session:
            u = User(telegram_id=201, registration_date=datetime.datetime.now(), role_id=1)
            c = BroadcastCampaign(admin_id=999, message_text="Hello", status="running")
            session.add_all([u, c])
            await session.commit()
            r = BroadcastRecipient(campaign_id=c.id, user_id=201, status="pending")
            session.add(r)
            await session.commit()

        await dispatcher._process_active_campaign()

        async with Database().session() as session:
            r = (await session.execute(select(BroadcastRecipient))).scalars().first()
            assert r.status == "blocked"
            u = await session.get(User, 201)
            assert u.is_blocked == True

    async def test_retry_after(self, mock_bot):
        mock_bot.send_message.side_effect = TelegramRetryAfter(method="send_message", message="Retry", retry_after=5)
        dispatcher = BroadcastDispatcher()
        dispatcher.bot = mock_bot

        async with Database().session() as session:
            u = User(telegram_id=301, registration_date=datetime.datetime.now(), role_id=1)
            c = BroadcastCampaign(admin_id=999, message_text="Hello", status="running")
            session.add_all([u, c])
            await session.commit()
            r = BroadcastRecipient(campaign_id=c.id, user_id=301, status="pending")
            session.add(r)
            await session.commit()

        await dispatcher._process_active_campaign()

        async with Database().session() as session:
            r = (await session.execute(select(BroadcastRecipient))).scalars().first()
            assert r.status == "pending"
            assert r.next_attempt_at is not None

    async def test_bad_request_fails(self, mock_bot):
        mock_bot.send_message.side_effect = TelegramBadRequest(method="send_message", message="Bad Request")
        dispatcher = BroadcastDispatcher()
        dispatcher.bot = mock_bot

        async with Database().session() as session:
            u = User(telegram_id=401, registration_date=datetime.datetime.now(), role_id=1)
            c = BroadcastCampaign(admin_id=999, message_text="Hello", status="running")
            session.add_all([u, c])
            await session.commit()
            r = BroadcastRecipient(campaign_id=c.id, user_id=401, status="pending")
            session.add(r)
            await session.commit()

        await dispatcher._process_active_campaign()

        async with Database().session() as session:
            r = (await session.execute(select(BroadcastRecipient))).scalars().first()
            assert r.status == "failed"

    async def test_cleanup_stale_sending(self):
        dispatcher = BroadcastDispatcher()

        async with Database().session() as session:
            c = BroadcastCampaign(admin_id=999, message_text="Hello", status="running")
            session.add(c)
            await session.commit()
            r = BroadcastRecipient(campaign_id=c.id, user_id=501, status="sending")
            session.add(r)
            await session.commit()

        await dispatcher._cleanup_stale_states_on_start()

        async with Database().session() as session:
            r = (await session.execute(select(BroadcastRecipient))).scalars().first()
            assert r.status == "uncertain"
