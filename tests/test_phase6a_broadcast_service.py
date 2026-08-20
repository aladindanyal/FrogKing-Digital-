import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy import select
from bot.database.main import Database
from bot.database.models.main import User, Role, Permission, BroadcastCampaign, BroadcastRecipient
from bot.misc.services.broadcast_service import (
    validate_payload, estimate_audience, create_draft, confirm_campaign, cancel_campaign, get_campaign_stats
)

@pytest.fixture
async def broadcast_setup(user_factory, role_factory):
    role = await role_factory(name="AdminBC", permissions=Permission.BROADCAST)
    user = await user_factory(telegram_id=999, role_id=role)
    # Create some target users
    user_role = await role_factory(name="User", permissions=Permission.USE)
    await user_factory(telegram_id=111, role_id=user_role)
    await user_factory(telegram_id=222, role_id=user_role)
    from bot.database.methods.update import update_user_language
    await update_user_language(111, 'en')
    await update_user_language(222, 'en')
    return user

@pytest.mark.asyncio
async def test_invalid_locale():
    is_valid, msg, safe = await validate_payload("Test", None, "xx")
    assert not is_valid
    assert "Invalid target locale" in msg

@pytest.mark.asyncio
async def test_malformed_payload():
    is_valid, msg, safe = await validate_payload("", None, "en")
    assert not is_valid
    assert "Payload cannot be completely empty." in msg

    long_text = "A" * 5000
    is_valid, msg, safe = await validate_payload(long_text, None, "en")
    assert not is_valid
    assert "Text payload exceeds maximum length" in msg

@pytest.mark.asyncio
async def test_text_caption_limits():
    is_valid, msg, safe = await validate_payload("A" * 1500, "file_id", "en")
    assert not is_valid
    assert "Caption exceeds maximum length of 1024 characters." in msg

    is_valid, msg, safe = await validate_payload("A" * 1000, "file_id", "en")
    assert is_valid

@pytest.mark.asyncio
async def test_audience_estimation(broadcast_setup):
    count = await estimate_audience("en")
    assert count == 2

@pytest.mark.asyncio
async def test_preview_creates_zero_recipients(broadcast_setup):
    admin = broadcast_setup
    campaign = await create_draft(admin["telegram_id"], "en", "Test", None)
    assert campaign.status == "draft"

    async with Database().session() as session:
        result = await session.execute(select(BroadcastRecipient).where(BroadcastRecipient.campaign_id == campaign.id))
        recipients = result.scalars().all()
        assert len(recipients) == 0

@pytest.mark.asyncio
async def test_preview_performs_zero_deliveries(broadcast_setup):
    admin = broadcast_setup
    with patch("bot.misc.services.broadcast_dispatcher.BroadcastDispatcher.wake_up") as mock_wakeup:
        campaign = await create_draft(admin["telegram_id"], "en", "Test", None)
        mock_wakeup.assert_not_called()

@pytest.mark.asyncio
async def test_idempotent_confirmation(broadcast_setup):
    admin = broadcast_setup
    campaign = await create_draft(admin["telegram_id"], "en", "Test", None)

    success, msg, camp, count = await confirm_campaign(campaign.id)
    assert success

    success2, msg2, camp2, count2 = await confirm_campaign(campaign.id)
    assert not success2
    assert "Campaign is not in draft state" in msg2

    async with Database().session() as session:
        res = await session.execute(select(BroadcastRecipient).where(BroadcastRecipient.campaign_id == campaign.id))
        assert len(res.scalars().all()) == 2

@pytest.mark.asyncio
async def test_concurrent_confirmation(broadcast_setup):
    admin = broadcast_setup
    campaign = await create_draft(admin["telegram_id"], "en", "Test", None)

    results = await asyncio.gather(
        confirm_campaign(campaign.id),
        confirm_campaign(campaign.id),
        return_exceptions=True
    )

    successes = [r for r in results if isinstance(r, tuple) and r[0]]
    assert len(successes) == 1

@pytest.mark.asyncio
async def test_one_active_campaign_conflict(broadcast_setup):
    admin = broadcast_setup
    camp1 = await create_draft(admin["telegram_id"], "en", "Test1", None)
    camp2 = await create_draft(admin["telegram_id"], "en", "Test2", None)

    success, _, _, _ = await confirm_campaign(camp1.id)
    assert success

    success2, msg2, _, _ = await confirm_campaign(camp2.id)
    assert not success2
    assert "Another campaign is currently active" in msg2

@pytest.mark.asyncio
async def test_payload_immutability_after_confirmation(broadcast_setup):
    admin = broadcast_setup
    campaign = await create_draft(admin["telegram_id"], "en", "Test", None)
    await confirm_campaign(campaign.id)

    async with Database().session() as session:
        camp = await session.get(BroadcastCampaign, campaign.id)
        assert camp.message_text == "Test"

@pytest.mark.asyncio
async def test_selected_campaign_cancellation(broadcast_setup):
    admin = broadcast_setup
    camp1 = await create_draft(admin["telegram_id"], "en", "Test1", None)
    await confirm_campaign(camp1.id)

    success, _ = await cancel_campaign(camp1.id, admin)
    assert success

    async with Database().session() as session:
        camp = await session.get(BroadcastCampaign, camp1.id)
        assert camp.status == "cancelled"

@pytest.mark.asyncio
async def test_statistics_correctness(broadcast_setup):
    admin = broadcast_setup
    camp = await create_draft(admin["telegram_id"], "en", "Test1", None)
    await confirm_campaign(camp.id)

    stats = await get_campaign_stats(camp.id)
    assert stats["total"] == 2
    assert stats["sent"] == 0

@pytest.mark.asyncio
async def test_telegram_handler_uses_shared_service(broadcast_setup):
    admin = broadcast_setup
    camp = await create_draft(admin["telegram_id"], "en", "Test", None)
    from bot.handlers.admin.broadcast import confirm_broadcast_handler

    msg = MagicMock()
    msg.data = f"bc_confirm_{camp.id}"
    msg.message = AsyncMock()
    msg.from_user.id = 999

    state = MagicMock()
    state.clear = AsyncMock()
    state.get_data = AsyncMock(return_value={"campaign_id": camp.id})

    with patch("bot.handlers.admin.broadcast.confirm_campaign", new_callable=AsyncMock) as mock_confirm:
        mock_confirm.return_value = (True, "", camp, 2)
        await confirm_broadcast_handler(msg, state)
        mock_confirm.assert_called_once_with(camp.id)

@pytest.mark.asyncio
async def test_no_real_telegram_recipient_contacted(broadcast_setup):
    admin = broadcast_setup
    camp = await create_draft(admin["telegram_id"], "en", "Test", None)
    with patch("aiogram.Bot.send_message") as mock_send:
        await confirm_campaign(camp.id)
        mock_send.assert_not_called()
