import asyncio
import datetime
import logging
from unittest.mock import MagicMock, patch, AsyncMock
import pytest
from sqlalchemy import select, delete, update
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramRetryAfter,
    TelegramNetworkError,
    TelegramBadRequest,
)

from bot.database import Database
from bot.database.models.main import Goods, ItemValues, User, ProductRestockSubscription, Categories, Role
from bot.database.methods.create import add_values_to_item
from bot.database.methods.restock_subscriptions import (
    subscribe_to_restock,
    claim_ready_restock_subscriptions,
    get_dispatchable_restock_count,
    recover_stale_processing_subscriptions,
    mark_restock_notified,
    mark_restock_failed,
    return_restock_to_active,
)
from bot.misc.services.restock_dispatcher import (
    RestockDispatcher,
    RestockRateLimiter,
    wake_restock_dispatcher,
    _get_restock_view_keyboard,
    restock_dispatcher,
)
from bot.i18n.main import localize
from bot.i18n.strings import TRANSLATIONS
from bot.misc.env import EnvKeys


class MockBot:
    def __init__(self):
        self.sent = []
        self.exception = None

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        if self.exception:
            raise self.exception
        self.sent.append({
            "chat_id": chat_id,
            "text": text,
            "reply_markup": reply_markup,
            "parse_mode": parse_mode
        })


@pytest.fixture(autouse=True)
async def ensure_categories():
    """Ensure default category exists for tests."""
    async with Database().session() as s:
        cat = (await s.execute(select(Categories).where(Categories.id == 1))).scalars().first()
        if not cat:
            s.add(Categories(id=1, name="Electronics"))
            await s.commit()


@pytest.mark.asyncio
async def test_dispatcher_wake_up_instant_dispatch():
    """1. Successful inventory commit wakes the dispatcher immediately without waiting for polling interval."""
    now = datetime.datetime.now(datetime.timezone.utc)
    user_id = 111001
    item_id = 222001

    async with Database().session() as s:
        # Targeted pre-cleanup
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == item_id))
        await s.execute(delete(ItemValues).where(ItemValues.item_id == item_id))
        await s.execute(delete(Goods).where(Goods.id == item_id))
        await s.execute(delete(User).where(User.telegram_id == user_id))

        s.add(User(telegram_id=user_id, language_code="en", registration_date=now))
        s.add(Goods(id=item_id, name="Smart Watch", description="Smart Watch desc", price=150, category_id=1, is_enabled=True))
        s.add(ProductRestockSubscription(user_id=user_id, item_id=item_id, status='active', attempts=0, created_at=now, updated_at=now))
        await s.commit()

    bot = MockBot()
    dispatcher = RestockDispatcher(bot, polling_interval=100)
    await dispatcher.start()

    try:
        with patch.object(dispatcher, "wake_up", wraps=dispatcher.wake_up) as mock_wake:
            with patch("bot.misc.services.restock_dispatcher.restock_dispatcher", dispatcher):
                ok = await add_values_to_item("Smart Watch", "val-watch-1", False)
                assert ok is True
                assert mock_wake.called

        for _ in range(20):
            if len(bot.sent) >= 1:
                break
            await asyncio.sleep(0.05)

        assert len(bot.sent) == 1
        assert bot.sent[0]["chat_id"] == user_id
        assert "Smart Watch" in bot.sent[0]["text"]

        async with Database().session() as s:
            sub = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.user_id == user_id))).scalars().first()
            assert sub.status == 'notified'
            assert sub.notified_at is not None

            # Targeted post-cleanup
            await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == item_id))
            await s.execute(delete(ItemValues).where(ItemValues.item_id == item_id))
            await s.execute(delete(Goods).where(Goods.id == item_id))
            await s.execute(delete(User).where(User.telegram_id == user_id))
            await s.commit()
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_real_transaction_commit_ordering():
    """Verify transaction commit & session exit complete strictly before wake_up() is invoked."""
    call_order = []

    item_id = 222002
    async with Database().session() as s:
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == item_id))
        await s.execute(delete(ItemValues).where(ItemValues.item_id == item_id))
        await s.execute(delete(Goods).where(Goods.id == item_id))
        s.add(Goods(id=item_id, name="Ordered Item", description="Desc", price=100, category_id=1, is_enabled=True))
        await s.commit()

    orig_session_factory = Database()._Database__SessionLocal

    class InstrumentedSession:
        def __init__(self, real_session):
            self.real_session = real_session

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            res = await self.real_session.__aexit__(exc_type, exc_val, exc_tb)
            call_order.append("session_exit_completed")
            return res

        async def commit(self):
            await self.real_session.commit()
            call_order.append("commit_completed")

        def __getattr__(self, name):
            return getattr(self.real_session, name)

    def instrumented_session_factory():
        real = orig_session_factory()
        return InstrumentedSession(real)

    def logged_wake():
        call_order.append("wake_up")

    with patch.object(Database(), "_Database__SessionLocal", side_effect=instrumented_session_factory):
        with patch("bot.misc.services.restock_dispatcher.restock_dispatcher.wake_up", side_effect=logged_wake):
            res = await add_values_to_item("Ordered Item", "val-ordered-1", False)
            assert res is True

    # Assert exact truthful order: commit first, session exit second, wake_up third
    assert call_order == ["commit_completed", "session_exit_completed", "wake_up"]

    # Verify data is committed in DB
    async with Database().session() as s:
        vals = (await s.execute(select(ItemValues).where(ItemValues.item_id == item_id))).scalars().all()
        assert len(vals) == 1
        await s.execute(delete(ItemValues).where(ItemValues.item_id == item_id))
        await s.execute(delete(Goods).where(Goods.id == item_id))
        await s.commit()


@pytest.mark.asyncio
async def test_transaction_rollback_ordering_and_no_wake():
    """Verify that on injected commit/session-exit failure, rollback occurs, 0 rows survive, and wake_up is never called."""
    wake_mock = MagicMock()

    item_id = 222003
    async with Database().session() as s:
        await s.execute(delete(ItemValues).where(ItemValues.item_id == item_id))
        await s.execute(delete(Goods).where(Goods.id == item_id))
        s.add(Goods(id=item_id, name="Rollback Item", description="Desc", price=100, category_id=1, is_enabled=True))
        await s.commit()

    orig_session_factory = Database()._Database__SessionLocal

    class FailingSession:
        def __init__(self, real_session):
            self.real_session = real_session

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return await self.real_session.__aexit__(exc_type, exc_val, exc_tb)

        async def commit(self):
            # Allow insert/flush to occur in transaction, but inject a failure at the commit boundary
            raise RuntimeError("Injected database commit failure")

        def __getattr__(self, name):
            return getattr(self.real_session, name)

    def failing_session_factory():
        real = orig_session_factory()
        return FailingSession(real)

    with patch.object(Database(), "_Database__SessionLocal", side_effect=failing_session_factory):
        with patch("bot.misc.services.restock_dispatcher.restock_dispatcher.wake_up", wake_mock):
            res = await add_values_to_item("Rollback Item", "valid-stock-val", False)
            assert res is False
            assert wake_mock.call_count == 0

    # Verify zero stock rows survived the failed transaction in DB
    async with Database().session() as s:
        vals = (await s.execute(select(ItemValues).where(ItemValues.item_id == item_id))).scalars().all()
        assert len(vals) == 0
        await s.execute(delete(Goods).where(Goods.id == item_id))
        await s.commit()


@pytest.mark.asyncio
async def test_starvation_prevention_out_of_stock_vs_in_stock():
    """Verify out-of-stock subscriptions do not starve newer subscriptions for available products."""
    now = datetime.datetime.now(datetime.timezone.utc)
    earlier = now - datetime.timedelta(hours=2)

    item_oos = 222004
    item_stocked = 222005

    async with Database().session() as s:
        # Pre-cleanup
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id.in_([item_oos, item_stocked])))
        await s.execute(delete(ItemValues).where(ItemValues.item_id.in_([item_oos, item_stocked])))
        await s.execute(delete(Goods).where(Goods.id.in_([item_oos, item_stocked])))

        s.add(Goods(id=item_oos, name="OOS Product", description="Desc", price=100, category_id=1, is_enabled=True))
        s.add(Goods(id=item_stocked, name="Stocked Product", description="Desc", price=100, category_id=1, is_enabled=True))
        s.add(ItemValues(item_id=item_stocked, value="stock-val-1", is_infinity=False))

        # 10 older subscriptions for OOS Product
        for i in range(10):
            uid = 800010 + i
            s.add(User(telegram_id=uid, language_code="en", registration_date=earlier))
            s.add(ProductRestockSubscription(user_id=uid, item_id=item_oos, status='active', attempts=0, created_at=earlier, updated_at=earlier))

        # 1 newer subscription for Stocked Product
        uid_stocked = 800099
        s.add(User(telegram_id=uid_stocked, language_code="en", registration_date=now))
        s.add(ProductRestockSubscription(user_id=uid_stocked, item_id=item_stocked, status='active', attempts=0, created_at=now, updated_at=now))
        await s.commit()

    # Claim with limit=5 (smaller than OOS subscription count)
    claimed = await claim_ready_restock_subscriptions(limit=5)

    # Must claim the Stocked Product subscription, skipping OOS subscriptions completely
    assert len(claimed) == 1
    assert claimed[0].item_id == item_stocked
    assert claimed[0].user_id == uid_stocked

    async with Database().session() as s:
        # Cleanup
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id.in_([item_oos, item_stocked])))
        await s.execute(delete(ItemValues).where(ItemValues.item_id.in_([item_oos, item_stocked])))
        await s.execute(delete(Goods).where(Goods.id.in_([item_oos, item_stocked])))
        await s.commit()


@pytest.mark.asyncio
async def test_disabled_stocked_subscriptions_batch_no_hot_loop():
    """Verify a full batch of disabled products with stock remain active, are not sent, and do not cause hot loop."""
    now = datetime.datetime.now(datetime.timezone.utc)
    item_disabled = 222006
    batch_size = 15

    user_ids = [810000 + i for i in range(batch_size)]

    async with Database().session() as s:
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == item_disabled))
        await s.execute(delete(ItemValues).where(ItemValues.item_id == item_disabled))
        await s.execute(delete(Goods).where(Goods.id == item_disabled))
        await s.execute(delete(User).where(User.telegram_id.in_(user_ids)))

        s.add(Goods(id=item_disabled, name="Disabled Product", description="Desc", price=100, category_id=1, is_enabled=False))
        s.add(ItemValues(item_id=item_disabled, value="stock-disabled-1", is_infinity=True))

        for uid in user_ids:
            s.add(User(telegram_id=uid, language_code="en", registration_date=now))
            s.add(ProductRestockSubscription(user_id=uid, item_id=item_disabled, status='active', attempts=0, created_at=now, updated_at=now))
        await s.commit()

    # 1. Claim must return empty because goods are disabled
    claimed = await claim_ready_restock_subscriptions(limit=batch_size)
    assert len(claimed) == 0

    # 2. Dispatchable count must be 0
    dispatchable = await get_dispatchable_restock_count()
    assert dispatchable == 0

    # 3. Process batch in dispatcher must return 0 claims immediately without loop
    bot = MockBot()
    dispatcher = RestockDispatcher(bot)
    claimed_count = await dispatcher.process_batch()
    assert claimed_count == 0
    assert len(bot.sent) == 0

    # 4. Verify all subscriptions remain in 'active' status
    async with Database().session() as s:
        subs = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.item_id == item_disabled))).scalars().all()
        assert len(subs) == batch_size
        for sub in subs:
            assert sub.status == 'active'

        # Cleanup
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == item_disabled))
        await s.execute(delete(ItemValues).where(ItemValues.item_id == item_disabled))
        await s.execute(delete(Goods).where(Goods.id == item_disabled))
        await s.execute(delete(User).where(User.telegram_id.in_(user_ids)))
        await s.commit()


@pytest.mark.asyncio
async def test_product_disabled_after_claim_returns_to_active():
    """Verify that if a product is disabled after claim, dispatcher returns subscription to active without consuming attempts."""
    now = datetime.datetime.now(datetime.timezone.utc)
    user_id = 820001
    item_id = 222007

    async with Database().session() as s:
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == item_id))
        await s.execute(delete(ItemValues).where(ItemValues.item_id == item_id))
        await s.execute(delete(Goods).where(Goods.id == item_id))
        await s.execute(delete(User).where(User.telegram_id == user_id))

        s.add(User(telegram_id=user_id, language_code="en", registration_date=now))
        s.add(Goods(id=item_id, name="Soon Disabled", description="Desc", price=100, category_id=1, is_enabled=True))
        s.add(ItemValues(item_id=item_id, value="val-1", is_infinity=False))
        s.add(ProductRestockSubscription(user_id=user_id, item_id=item_id, status='active', attempts=0, created_at=now, updated_at=now))
        await s.commit()

    claimed = await claim_ready_restock_subscriptions(limit=5)
    assert len(claimed) == 1
    sub_obj = claimed[0]

    # Disable product after claim
    async with Database().session() as s:
        await s.execute(update(Goods).where(Goods.id == item_id).values(is_enabled=False))
        await s.commit()

    bot = MockBot()
    dispatcher = RestockDispatcher(bot)
    await dispatcher.process_subscription(sub_obj)

    # No message sent
    assert len(bot.sent) == 0

    # Subscription returned to 'active' without attempt increment
    async with Database().session() as s:
        sub = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.id == sub_obj.id))).scalars().first()
        assert sub.status == 'active'
        assert sub.attempts == 0

        # Cleanup
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == item_id))
        await s.execute(delete(ItemValues).where(ItemValues.item_id == item_id))
        await s.execute(delete(Goods).where(Goods.id == item_id))
        await s.execute(delete(User).where(User.telegram_id == user_id))
        await s.commit()


@pytest.mark.asyncio
async def test_rate_limiter_spaces_actual_send_starts_across_concurrent_tasks():
    """Verify that dispatcher-wide rate limiter spaces actual bot.send_message starts across concurrent tasks."""
    now = datetime.datetime.now(datetime.timezone.utc)
    item_id = 222015
    num_subs = 4
    user_ids = [870000 + i for i in range(num_subs)]

    async with Database().session() as s:
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == item_id))
        await s.execute(delete(ItemValues).where(ItemValues.item_id == item_id))
        await s.execute(delete(Goods).where(Goods.id == item_id))
        await s.execute(delete(User).where(User.telegram_id.in_(user_ids)))

        s.add(Goods(id=item_id, name="RateLimited Device", description="Desc", price=200, category_id=1, is_enabled=True))
        s.add(ItemValues(item_id=item_id, value="val-infinity", is_infinity=True))

        for uid in user_ids:
            s.add(User(telegram_id=uid, language_code="en", registration_date=now))
            s.add(ProductRestockSubscription(user_id=uid, item_id=item_id, status='active', attempts=0, created_at=now, updated_at=now))
        await s.commit()

    claimed = await claim_ready_restock_subscriptions(limit=num_subs)
    assert len(claimed) == num_subs

    # Deterministic virtual clock simulation
    current_virtual_time = 1000.0
    send_start_times = []

    async def deterministic_sleep(duration):
        nonlocal current_virtual_time
        current_virtual_time += duration

    class DeterministicBot:
        async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
            # Record virtual time when send_message begins
            send_start_times.append(current_virtual_time)

    # 10 msg/sec -> 0.1s interval
    rate_limiter = RestockRateLimiter(rate_per_sec=10.0)
    bot = DeterministicBot()
    dispatcher = RestockDispatcher(bot, rate_limiter=rate_limiter)

    with patch("asyncio.get_running_loop") as mock_loop:
        fake_loop = MagicMock()
        fake_loop.time.side_effect = lambda: current_virtual_time
        mock_loop.return_value = fake_loop

        with patch("asyncio.sleep", side_effect=deterministic_sleep):
            tasks = [dispatcher.process_subscription(sub) for sub in claimed]
            await asyncio.gather(*tasks)

    assert len(send_start_times) == num_subs
    # Verify send starts are strictly spaced by >= 0.1s
    for i in range(1, len(send_start_times)):
        spacing = round(send_start_times[i] - send_start_times[i-1], 4)
        assert spacing >= 0.1, f"Send starts at index {i-1} and {i} not spaced by 0.1s (got {spacing})"

    # Cleanup
    async with Database().session() as s:
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == item_id))
        await s.execute(delete(ItemValues).where(ItemValues.item_id == item_id))
        await s.execute(delete(Goods).where(Goods.id == item_id))
        await s.execute(delete(User).where(User.telegram_id.in_(user_ids)))
        await s.commit()


@pytest.mark.asyncio
async def test_telegram_retry_after_observability_and_max_attempts_boundary(caplog):
    """Verify TelegramRetryAfter schedules retry & logs restock_retry_scheduled below max, and marks failed & logs restock_failed at max."""
    now = datetime.datetime.now(datetime.timezone.utc)
    user_id_below = 830001
    user_id_at_max = 830002
    item_id = 222008

    async with Database().session() as s:
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == item_id))
        await s.execute(delete(ItemValues).where(ItemValues.item_id == item_id))
        await s.execute(delete(Goods).where(Goods.id == item_id))
        await s.execute(delete(User).where(User.telegram_id.in_([user_id_below, user_id_at_max])))

        s.add(User(telegram_id=user_id_below, language_code="en", registration_date=now))
        s.add(User(telegram_id=user_id_at_max, language_code="en", registration_date=now))
        s.add(Goods(id=item_id, name="RateLimited Product", description="Desc", price=100, category_id=1, is_enabled=True))
        s.add(ItemValues(item_id=item_id, value="val-1", is_infinity=False))

        # Sub 1: below max attempts (attempts = 0)
        s.add(ProductRestockSubscription(
            user_id=user_id_below, item_id=item_id, status='active', attempts=0, created_at=now, updated_at=now
        ))
        # Sub 2: at max attempts (attempts = RESTOCK_MAX_ATTEMPTS)
        s.add(ProductRestockSubscription(
            user_id=user_id_at_max, item_id=item_id, status='active', attempts=EnvKeys.RESTOCK_MAX_ATTEMPTS, created_at=now, updated_at=now
        ))
        await s.commit()

    claimed = await claim_ready_restock_subscriptions(limit=5)
    assert len(claimed) == 2
    sub_below = next(c for c in claimed if c.user_id == user_id_below)
    sub_max = next(c for c in claimed if c.user_id == user_id_at_max)

    bot = MockBot()
    bot.exception = TelegramRetryAfter(method=MagicMock(), message="Too many requests", retry_after=10)
    dispatcher = RestockDispatcher(bot)

    # 1. Test below max attempts: retry scheduled, no restock_failed logged
    with patch("bot.misc.services.restock_dispatcher.logger.warning") as mock_log_warn, \
         patch("bot.misc.services.restock_dispatcher.logger.info") as mock_log_info:
        await dispatcher.process_subscription(sub_below)

        assert mock_log_warn.called
        assert mock_log_warn.call_args[0][0] == "restock_retry_scheduled"
        assert not any(call[0][0] == "restock_failed" for call in mock_log_info.call_args_list)

    async with Database().session() as s:
        sub = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.id == sub_below.id))).scalars().first()
        assert sub.status == 'active'
        assert sub.next_attempt_at is not None
        assert sub.attempts == 1

    # 2. Test at max attempts boundary: restock_failed logged with retry_limit_exceeded, no restock_retry_scheduled logged
    with patch("bot.misc.services.restock_dispatcher.logger.warning") as mock_log_warn, \
         patch("bot.misc.services.restock_dispatcher.logger.info") as mock_log_info:
        await dispatcher.process_subscription(sub_max)

        assert any(
            call[0][0] == "restock_failed" and call[1].get("extra", {}).get("error") == "retry_limit_exceeded"
            for call in mock_log_info.call_args_list
        )
        assert not mock_log_warn.called

    async with Database().session() as s:
        sub = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.id == sub_max.id))).scalars().first()
        assert sub.status == 'failed'
        assert sub.last_error == 'retry_limit_exceeded'

        # Cleanup
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == item_id))
        await s.execute(delete(ItemValues).where(ItemValues.item_id == item_id))
        await s.execute(delete(Goods).where(Goods.id == item_id))
        await s.execute(delete(User).where(User.telegram_id.in_([user_id_below, user_id_at_max])))
        await s.commit()


@pytest.mark.asyncio
async def test_admin_portal_replenishment_hooks():
    """Verify SQLAdmin ItemValuesAdmin and GoodsAdmin hooks trigger wake_up."""
    from bot.web.admin import ItemValuesAdmin, GoodsAdmin

    mock_admin_iv = MagicMock(spec=ItemValuesAdmin)
    mock_admin_goods = MagicMock(spec=GoodsAdmin)

    with patch("bot.misc.services.restock_dispatcher.wake_restock_dispatcher") as mock_wake:
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/admin/item-values/create",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
        from starlette.requests import Request
        request = Request(scope)

        # 1. ItemValuesAdmin after_model_change
        item_val_model = MagicMock(spec=ItemValues)
        item_val_model.id = 501
        await ItemValuesAdmin.after_model_change(mock_admin_iv, {}, item_val_model, True, request)
        assert mock_wake.call_count == 1

        # 2. GoodsAdmin after_model_change when is_enabled=True
        goods_model = MagicMock(spec=Goods)
        goods_model.id = 601
        goods_model.is_enabled = True
        await GoodsAdmin.after_model_change(mock_admin_goods, {}, goods_model, False, request)
        assert mock_wake.call_count == 2


@pytest.mark.asyncio
async def test_unlimited_stock_transition_notifies_subscribers():
    """Verify switching a product to unlimited stock triggers notifications."""
    now = datetime.datetime.now(datetime.timezone.utc)
    user_id = 840001
    item_id = 222009

    async with Database().session() as s:
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == item_id))
        await s.execute(delete(ItemValues).where(ItemValues.item_id == item_id))
        await s.execute(delete(Goods).where(Goods.id == item_id))
        await s.execute(delete(User).where(User.telegram_id == user_id))

        s.add(User(telegram_id=user_id, language_code="en", registration_date=now))
        s.add(Goods(id=item_id, name="Cloud VPS", description="VPS", price=50, category_id=1, is_enabled=True))
        s.add(ProductRestockSubscription(user_id=user_id, item_id=item_id, status='active', attempts=0, created_at=now, updated_at=now))
        await s.commit()

    bot = MockBot()
    dispatcher = RestockDispatcher(bot, polling_interval=100)
    await dispatcher.start()

    try:
        with patch("bot.misc.services.restock_dispatcher.restock_dispatcher", dispatcher):
            ok = await add_values_to_item("Cloud VPS", "infinity-token", True)
            assert ok is True

        for _ in range(20):
            if len(bot.sent) >= 1:
                break
            await asyncio.sleep(0.05)

        assert len(bot.sent) == 1
        assert bot.sent[0]["chat_id"] == user_id

        async with Database().session() as s:
            await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == item_id))
            await s.execute(delete(ItemValues).where(ItemValues.item_id == item_id))
            await s.execute(delete(Goods).where(Goods.id == item_id))
            await s.execute(delete(User).where(User.telegram_id == user_id))
            await s.commit()
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_multilingual_locale_and_button_resolution():
    """Verify 7-locale resolution for restock notification and view button."""
    locales = ["en", "ru", "ar", "zh", "vi", "tr", "es"]
    now = datetime.datetime.now(datetime.timezone.utc)
    item_id = 222010

    async with Database().session() as s:
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == item_id))
        await s.execute(delete(ItemValues).where(ItemValues.item_id == item_id))
        await s.execute(delete(Goods).where(Goods.id == item_id))

        s.add(Goods(
            id=item_id,
            name="Keyboard",
            name_en="Keyboard",
            name_ru="Клавиатура",
            name_ar="لوحة مفاتيح",
            name_zh="键盘",
            name_vi="Bàn phím",
            name_tr="Klavye",
            name_es="Teclado",
            description="Keyboard desc",
            price=100,
            category_id=1,
            is_enabled=True
        ))
        s.add(ItemValues(item_id=item_id, value="val-kbd-1", is_infinity=True))

        for idx, loc in enumerate(locales):
            uid = 850000 + idx
            await s.execute(delete(User).where(User.telegram_id == uid))
            s.add(User(telegram_id=uid, language_code=loc, registration_date=now))
            s.add(ProductRestockSubscription(user_id=uid, item_id=item_id, status='active', attempts=0, created_at=now, updated_at=now))
        await s.commit()

    bot = MockBot()
    dispatcher = RestockDispatcher(bot)

    claimed = await claim_ready_restock_subscriptions(limit=10)
    assert len(claimed) == 7

    for c in claimed:
        await dispatcher.process_subscription(c)

    assert len(bot.sent) == 7

    btn_expected = {
        "en": "🛒 View Product",
        "ru": "🛒 Смотреть товар",
        "ar": "🛒 عرض المنتج",
        "zh": "🛒 查看产品",
        "vi": "🛒 Xem Sản phẩm",
        "tr": "🛒 Ürünü Görüntüle",
        "es": "🛒 Ver Producto",
    }

    for msg in bot.sent:
        uid = msg["chat_id"]
        loc = locales[uid - 850000]
        btn = msg["reply_markup"].inline_keyboard[0][0]
        assert btn.text == btn_expected[loc]
        assert btn.callback_data == f"direct_item:{item_id}"

    async with Database().session() as s:
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == item_id))
        await s.execute(delete(ItemValues).where(ItemValues.item_id == item_id))
        await s.execute(delete(Goods).where(Goods.id == item_id))
        await s.execute(delete(User).where(User.telegram_id.in_([850000 + i for i in range(len(locales))])))
        await s.commit()


@pytest.mark.asyncio
async def test_stale_processing_recovery_integration():
    """Verify subscriptions stuck in processing state are automatically recovered."""
    now = datetime.datetime.now(datetime.timezone.utc)
    stale_time = now - datetime.timedelta(seconds=400)
    user_id = 860001
    item_id = 222011

    async with Database().session() as s:
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == item_id))
        await s.execute(delete(ItemValues).where(ItemValues.item_id == item_id))
        await s.execute(delete(Goods).where(Goods.id == item_id))
        await s.execute(delete(User).where(User.telegram_id == user_id))

        s.add(User(telegram_id=user_id, language_code="en", registration_date=now))
        s.add(Goods(id=item_id, name="Router", description="Router desc", price=75, category_id=1, is_enabled=True))
        s.add(ProductRestockSubscription(
            user_id=user_id, item_id=item_id, status='processing', attempts=1,
            processing_started_at=stale_time,
            created_at=stale_time, updated_at=stale_time
        ))
        await s.commit()

    recovered = await recover_stale_processing_subscriptions(stale_timeout_seconds=300)
    assert recovered == 1

    async with Database().session() as s:
        sub = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.user_id == user_id))).scalars().first()
        assert sub.status == 'active'

        # Cleanup
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == item_id))
        await s.execute(delete(Goods).where(Goods.id == item_id))
        await s.execute(delete(User).where(User.telegram_id == user_id))
        await s.commit()
