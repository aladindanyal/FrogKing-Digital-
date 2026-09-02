"""
PostgreSQL 16 concurrency acceptance test for Phase 6C restock dispatcher.
Verifies SELECT ... FOR UPDATE OF product_restock_subscriptions SKIP LOCKED under concurrent workers.
"""
import asyncio
import datetime
import os
import re
import pytest
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from bot.database import Database
from bot.database.models.main import Goods, ItemValues, User, ProductRestockSubscription, Categories, Role
from bot.database.methods.restock_subscriptions import (
    claim_ready_restock_subscriptions,
)
from bot.misc.services.restock_dispatcher import RestockDispatcher


class MockBot:
    def __init__(self):
        self.sent = []
        self._lock = asyncio.Lock()

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        async with self._lock:
            self.sent.append({
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
                "parse_mode": parse_mode
            })


def _validate_safe_test_db(database_url: str) -> str:
    """Ensure DATABASE_URL strictly points to an isolated test database."""
    base_url, db_name = database_url.rsplit("/", 1)
    db_name = db_name.split("?", 1)[0]
    if not db_name or not re.fullmatch(r"[A-Za-z0-9_]+", db_name):
        raise ValueError(f"Invalid database name in DATABASE_URL: {db_name}")
    if db_name.lower() in ("telegram_shop", "postgres", "production", "prod"):
        raise RuntimeError(f"CRITICAL SAFETY VIOLATION: Refusing to run tests against database '{db_name}'")
    if "test" not in db_name.lower():
        raise RuntimeError(f"PostgreSQL concurrency test requires a database name containing 'test' (got: '{db_name}')")
    return db_name


@pytest.fixture
async def pg_database():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url or "postgres" not in database_url.lower():
        pytest.skip("DATABASE_URL pointing to isolated PostgreSQL required for this test")

    # Apply strict database guard before creating engine
    _validate_safe_test_db(database_url)

    engine = create_async_engine(database_url, echo=False, pool_size=10, max_overflow=10)
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    db = Database()
    old_engine = getattr(db, "_Database__engine", None)
    old_session = getattr(db, "_Database__SessionLocal", None)

    db.__dict__["_Database__engine"] = engine
    db.__dict__["_Database__SessionLocal"] = session_factory

    yield db

    await engine.dispose()
    if old_engine:
        db.__dict__["_Database__engine"] = old_engine
    if old_session:
        db.__dict__["_Database__SessionLocal"] = old_session


@pytest.mark.asyncio
async def test_postgres_skip_locked_concurrency(pg_database):
    """Verify that multiple concurrent dispatcher workers on PostgreSQL never double-claim or double-notify."""
    now = datetime.datetime.now(datetime.timezone.utc)
    test_item_id = 999100
    num_subscribers = 20
    test_user_ids = [900000 + i for i in range(num_subscribers)]

    await Role.insert_roles()

    async with Database().session() as s:
        # Targeted pre-cleanup
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == test_item_id))
        await s.execute(delete(ItemValues).where(ItemValues.item_id == test_item_id))
        await s.execute(delete(Goods).where(Goods.id == test_item_id))
        await s.execute(delete(User).where(User.telegram_id.in_(test_user_ids)))

        cat = (await s.execute(select(Categories).where(Categories.id == 1))).scalars().first()
        if not cat:
            s.add(Categories(id=1, name="Electronics"))

        s.add(Goods(
            id=test_item_id,
            name="Concurrent GPU",
            name_en="Concurrent GPU",
            name_ar="معالج رسومي متزامن",
            description="High performance GPU",
            price=1500,
            category_id=1,
            is_enabled=True
        ))
        s.add(ItemValues(item_id=test_item_id, value="gpu-stock-unlimited", is_infinity=True))

        for uid in test_user_ids:
            s.add(User(telegram_id=uid, language_code="en" if uid % 2 == 0 else "ar", registration_date=now))
            s.add(ProductRestockSubscription(
                user_id=uid,
                item_id=test_item_id,
                status='active',
                attempts=0,
                created_at=now,
                updated_at=now
            ))
        await s.commit()

    bot = MockBot()
    dispatcher_a = RestockDispatcher(bot)
    dispatcher_b = RestockDispatcher(bot)
    dispatcher_c = RestockDispatcher(bot)

    claims_a = []
    claims_b = []
    claims_c = []

    barrier = asyncio.Barrier(3)

    async def worker(dispatcher: RestockDispatcher, record_list: list):
        # Synchronize all workers to trigger their first claim simultaneously
        await barrier.wait()
        for _ in range(15):
            claimed = await claim_ready_restock_subscriptions(limit=5)
            if claimed:
                record_list.extend([c.id for c in claimed])
                tasks = [asyncio.create_task(dispatcher.process_subscription(c)) for c in claimed]
                await asyncio.gather(*tasks)
            else:
                await asyncio.sleep(0.01)

    # Run 3 concurrent worker loops synchronized at barrier
    await asyncio.gather(
        worker(dispatcher_a, claims_a),
        worker(dispatcher_b, claims_b),
        worker(dispatcher_c, claims_c),
    )

    all_claimed = claims_a + claims_b + claims_c
    set_a = set(claims_a)
    set_b = set(claims_b)
    set_c = set(claims_c)

    # 1. Assert that at least two workers received non-empty claim sets
    non_empty_workers = sum(1 for s_set in (set_a, set_b, set_c) if len(s_set) > 0)
    assert non_empty_workers >= 2, f"Expected at least 2 workers to claim subscriptions, got {non_empty_workers}"

    # 2. Mutually exclusive disjoint sets (FOR UPDATE OF product_restock_subscriptions SKIP LOCKED guarantees zero double-claim)
    assert set_a.isdisjoint(set_b), f"Overlap between Worker A and Worker B: {set_a & set_b}"
    assert set_a.isdisjoint(set_c), f"Overlap between Worker A and Worker C: {set_a & set_c}"
    assert set_b.isdisjoint(set_c), f"Overlap between Worker B and Worker C: {set_b & set_c}"

    # 3. Total claimed must match total subscriptions exactly
    assert len(all_claimed) == num_subscribers, f"Expected {num_subscribers} claims, got {len(all_claimed)}"

    # 4. Exactly one message sent per user
    sent_user_ids = [m["chat_id"] for m in bot.sent]
    assert len(sent_user_ids) == num_subscribers
    assert len(set(sent_user_ids)) == num_subscribers

    # 5. Database verification: all subscriptions are in 'notified' status
    async with Database().session() as s:
        subs = (await s.execute(
            select(ProductRestockSubscription).where(ProductRestockSubscription.item_id == test_item_id)
        )).scalars().all()
        assert len(subs) == num_subscribers
        for sub in subs:
            assert sub.status == "notified"
            assert sub.notified_at is not None

        # Targeted cleanup
        await s.execute(delete(ProductRestockSubscription).where(ProductRestockSubscription.item_id == test_item_id))
        await s.execute(delete(ItemValues).where(ItemValues.item_id == test_item_id))
        await s.execute(delete(Goods).where(Goods.id == test_item_id))
        await s.execute(delete(User).where(User.telegram_id.in_(test_user_ids)))
        await s.commit()
