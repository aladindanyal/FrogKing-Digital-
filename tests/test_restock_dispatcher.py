import pytest
import asyncio
import datetime
from sqlalchemy import select, update, insert
from bot.database import Database
from bot.database.models import Goods, ItemValues, User, ProductRestockSubscription
from bot.misc.services.restock_dispatcher import RestockDispatcher
from bot.database.methods.restock_subscriptions import (
    claim_ready_restock_subscriptions,
    recover_stale_processing_subscriptions
)
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramNetworkError, TelegramBadRequest

@pytest.fixture
async def clear_subs():
    async with Database().session() as s:
        await s.execute(ProductRestockSubscription.__table__.delete())
        await s.commit()

@pytest.fixture
async def sample_user():
    async with Database().session() as s:
        u = User(telegram_id=999999)
        s.add(u)
        await s.commit()
        yield u
        await s.execute(User.__table__.delete().where(User.telegram_id == 999999))
        await s.commit()

@pytest.fixture
async def out_of_stock_item():
    async with Database().session() as s:
        g = Goods(name="OOS Item", price=10, description="OOS", category_id=1)
        s.add(g)
        await s.flush()
        yield g
        await s.execute(Goods.__table__.delete().where(Goods.id == g.id))
        await s.commit()

@pytest.fixture
async def finite_stock_item():
    async with Database().session() as s:
        g = Goods(name="Finite Item", price=10, description="Fin", category_id=1)
        s.add(g)
        await s.flush()
        v = ItemValues(item_id=g.id, value="val1", is_infinity=False)
        s.add(v)
        await s.commit()
        yield g
        await s.execute(Goods.__table__.delete().where(Goods.id == g.id))
        await s.commit()

@pytest.fixture
async def unlimited_stock_item():
    async with Database().session() as s:
        g = Goods(name="Unlim Item", price=10, description="Unlim", category_id=1)
        s.add(g)
        await s.flush()
        v = ItemValues(item_id=g.id, value="val1", is_infinity=True)
        s.add(v)
        await s.commit()
        yield g
        await s.execute(Goods.__table__.delete().where(Goods.id == g.id))
        await s.commit()

class MockBot:
    def __init__(self):
        self.sent = []
        self.exception = None
    
    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        if self.exception:
            raise self.exception
        self.sent.append((chat_id, text, reply_markup))

@pytest.mark.asyncio
async def test_dispatcher_lifecycle(clear_subs):
    bot = MockBot()
    dispatcher = RestockDispatcher(bot)
    
    # dispatcher start is idempotent
    dispatcher.start()
    task = dispatcher._task
    dispatcher.start()
    assert dispatcher._task is task
    
    # dispatcher shutdown is clean
    await dispatcher.stop()
    assert not dispatcher.running

@pytest.mark.asyncio
async def test_out_of_stock_not_claimed(clear_subs, sample_user, out_of_stock_item):
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as s:
        s.add(ProductRestockSubscription(user_id=sample_user.telegram_id, item_id=out_of_stock_item.id, status='active', attempts=0, created_at=now, updated_at=now))
        await s.commit()
        
    claimed = await claim_ready_restock_subscriptions(50)
    assert len(claimed) == 0

@pytest.mark.asyncio
async def test_available_finite_stock_claimed(clear_subs, sample_user, finite_stock_item):
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as s:
        s.add(ProductRestockSubscription(user_id=sample_user.telegram_id, item_id=finite_stock_item.id, status='active', attempts=0, created_at=now, updated_at=now))
        await s.commit()
        
    claimed = await claim_ready_restock_subscriptions(50)
    assert len(claimed) == 1
    
    # active transitions to processing
    async with Database().session() as s:
        sub = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.id == claimed[0].id))).scalars().first()
        assert sub.status == 'processing'

@pytest.mark.asyncio
async def test_unlimited_stock_claimed(clear_subs, sample_user, unlimited_stock_item):
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as s:
        s.add(ProductRestockSubscription(user_id=sample_user.telegram_id, item_id=unlimited_stock_item.id, status='active', attempts=0, created_at=now, updated_at=now))
        await s.commit()
        
    claimed = await claim_ready_restock_subscriptions(50)
    assert len(claimed) == 1

@pytest.mark.asyncio
async def test_process_subscription_success(clear_subs, sample_user, finite_stock_item):
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as s:
        sub = ProductRestockSubscription(user_id=sample_user.telegram_id, item_id=finite_stock_item.id, status='processing', attempts=0, created_at=now, updated_at=now)
        s.add(sub)
        await s.commit()
        sub_id = sub.id
        
    bot = MockBot()
    dispatcher = RestockDispatcher(bot)
    
    # Re-fetch it as an object
    async with Database().session() as s:
        sub = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.id == sub_id))).scalars().first()
        await dispatcher.process_subscription(sub)
        
    # successful send transitions processing to notified
    assert len(bot.sent) == 1
    async with Database().session() as s:
        db_sub = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.id == sub_id))).scalars().first()
        assert db_sub.status == 'notified'

@pytest.mark.asyncio
async def test_cancelled_or_notified_never_sent(clear_subs, sample_user, finite_stock_item, unlimited_stock_item):
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as s:
        s.add(ProductRestockSubscription(user_id=sample_user.telegram_id, item_id=finite_stock_item.id, status='cancelled', attempts=0, created_at=now, updated_at=now))
        s.add(ProductRestockSubscription(user_id=sample_user.telegram_id, item_id=unlimited_stock_item.id, status='notified', attempts=0, created_at=now, updated_at=now))
        await s.commit()
        
    claimed = await claim_ready_restock_subscriptions(50)
    assert len(claimed) == 0

@pytest.mark.asyncio
async def test_stock_returns_to_zero_after_claim(clear_subs, sample_user, finite_stock_item):
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as s:
        sub = ProductRestockSubscription(user_id=sample_user.telegram_id, item_id=finite_stock_item.id, status='processing', attempts=0, created_at=now, updated_at=now)
        s.add(sub)
        await s.commit()
        sub_id = sub.id
        
        # Remove stock
        await s.execute(ItemValues.__table__.delete().where(ItemValues.item_id == finite_stock_item.id))
        await s.commit()
        
    bot = MockBot()
    dispatcher = RestockDispatcher(bot)
    
    async with Database().session() as s:
        sub = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.id == sub_id))).scalars().first()
        await dispatcher.process_subscription(sub)
        
    # No message sent, returns active
    assert len(bot.sent) == 0
    async with Database().session() as s:
        db_sub = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.id == sub_id))).scalars().first()
        assert db_sub.status == 'active'

@pytest.mark.asyncio
async def test_telegram_forbidden(clear_subs, sample_user, finite_stock_item):
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as s:
        sub = ProductRestockSubscription(user_id=sample_user.telegram_id, item_id=finite_stock_item.id, status='processing', attempts=0, created_at=now, updated_at=now)
        s.add(sub)
        await s.commit()
        sub_id = sub.id
        
    bot = MockBot()
    bot.exception = TelegramForbiddenError(method=None, message="Forbidden")
    dispatcher = RestockDispatcher(bot)
    
    async with Database().session() as s:
        sub = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.id == sub_id))).scalars().first()
        await dispatcher.process_subscription(sub)
        
    async with Database().session() as s:
        db_sub = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.id == sub_id))).scalars().first()
        assert db_sub.status == 'failed'

@pytest.mark.asyncio
async def test_telegram_retry_after(clear_subs, sample_user, finite_stock_item):
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as s:
        sub = ProductRestockSubscription(user_id=sample_user.telegram_id, item_id=finite_stock_item.id, status='processing', attempts=0, created_at=now, updated_at=now)
        s.add(sub)
        await s.commit()
        sub_id = sub.id
        
    bot = MockBot()
    bot.exception = TelegramRetryAfter(method=None, message="Retry", retry_after=10)
    dispatcher = RestockDispatcher(bot)
    
    async with Database().session() as s:
        sub = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.id == sub_id))).scalars().first()
        await dispatcher.process_subscription(sub)
        
    async with Database().session() as s:
        db_sub = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.id == sub_id))).scalars().first()
        assert db_sub.status == 'active'
        assert db_sub.next_attempt_at is not None

@pytest.mark.asyncio
async def test_telegram_network_error(clear_subs, sample_user, finite_stock_item):
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as s:
        sub = ProductRestockSubscription(user_id=sample_user.telegram_id, item_id=finite_stock_item.id, status='processing', attempts=0, created_at=now, updated_at=now)
        s.add(sub)
        await s.commit()
        sub_id = sub.id
        
    bot = MockBot()
    bot.exception = TelegramNetworkError(method=None, message="Network error")
    dispatcher = RestockDispatcher(bot)
    
    async with Database().session() as s:
        sub = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.id == sub_id))).scalars().first()
        await dispatcher.process_subscription(sub)
        
    async with Database().session() as s:
        db_sub = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.id == sub_id))).scalars().first()
        assert db_sub.status == 'active'
        assert db_sub.attempts == 1

@pytest.mark.asyncio
async def test_stale_recovery(clear_subs, sample_user, finite_stock_item):
    now = datetime.datetime.now(datetime.timezone.utc)
    stale_time = now - datetime.timedelta(seconds=4000)
    async with Database().session() as s:
        sub = ProductRestockSubscription(user_id=sample_user.telegram_id, item_id=finite_stock_item.id, status='processing', attempts=0, created_at=stale_time, updated_at=stale_time, processing_started_at=stale_time)
        s.add(sub)
        await s.commit()
        sub_id = sub.id
        
    recovered = await recover_stale_processing_subscriptions(3600)
    assert recovered == 1
    
    async with Database().session() as s:
        db_sub = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.id == sub_id))).scalars().first()
        assert db_sub.status == 'active'

@pytest.mark.asyncio
async def test_view_product_callback(clear_subs, sample_user, finite_stock_item):
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as s:
        sub = ProductRestockSubscription(user_id=sample_user.telegram_id, item_id=finite_stock_item.id, status='processing', attempts=0, created_at=now, updated_at=now)
        s.add(sub)
        await s.commit()
        sub_id = sub.id
        
    bot = MockBot()
    dispatcher = RestockDispatcher(bot)
    
    async with Database().session() as s:
        sub = (await s.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.id == sub_id))).scalars().first()
        await dispatcher.process_subscription(sub)
        
    # Check callback data in markup
    assert len(bot.sent) == 1
    markup = bot.sent[0][2]
    assert markup.inline_keyboard[0][0].callback_data == f"direct_item:{finite_stock_item.id}"



