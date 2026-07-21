import os

top = '''import datetime
from sqlalchemy import select, insert, func, update
from bot.database.main import Database
from bot.database.models import Goods, ItemValues, ProductRestockSubscription

async def is_restock_active(user_id: int, item_id: int) -> bool:
    """Check if the user has an active or processing restock subscription."""
    sub = await get_restock_subscription(user_id, item_id)
    if sub and sub.status in ('active', 'processing'):
        return True
    return False

async def get_restock_subscription(user_id: int, item_id: int) -> ProductRestockSubscription | None:
    async with Database().session() as session:
        stmt = select(ProductRestockSubscription).where(
            ProductRestockSubscription.user_id == user_id,
            ProductRestockSubscription.item_id == item_id
        )
        result = await session.execute(stmt)
        return result.scalars().first()

async def subscribe_to_restock(user_id: int, item_id: int) -> str:
    """
    Idempotent subscription to restock alerts.
    
    Returns one of:
    - subscribed
    - already_active
    - available_now
    - unlimited
    - item_missing
    - item_disabled
    """
    async with Database().session() as session:
        # 1. Re-fetch item to validate existence and live stock
        item_stmt = select(Goods).where(Goods.id == item_id)
        item_result = await session.execute(item_stmt)
        item = item_result.scalars().first()
        
        if not item:
            return "item_missing"
            
        values_stmt = select(ItemValues).where(ItemValues.item_id == item_id)
        values_result = await session.execute(values_stmt)
        item_values = values_result.scalars().all()
        
        # calculate stock
        stock = 0
        is_infinity = False
        for val in item_values:
            if val.is_infinity:
                is_infinity = True
                break
            stock += 1
            
        if is_infinity:
            return "unlimited"
            
        if stock > 0:
            return "available_now"
            
        # 2. Upsert
        now = datetime.datetime.now(datetime.timezone.utc)
        
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        insert_stmt = pg_insert(ProductRestockSubscription).values(
            user_id=user_id,
            item_id=item_id,
            status='active',
            created_at=now,
            updated_at=now,
            attempts=0
        )
        
        on_conflict_stmt = insert_stmt.on_conflict_do_update(
            index_elements=['user_id', 'item_id'],
            set_={
                'status': 'active',
                'notified_at': None,
                'cancelled_at': None,
                'processing_started_at': None,
                'next_attempt_at': None,
                'attempts': 0,
                'last_error': None,
                'updated_at': now
            },
            where=(ProductRestockSubscription.status.not_in(['active', 'processing']))
        )
        
        sub_stmt = select(ProductRestockSubscription).where(
            ProductRestockSubscription.user_id == user_id,
            ProductRestockSubscription.item_id == item_id
        )
        sub = (await session.execute(sub_stmt)).scalars().first()
        
        if sub and sub.status in ('active', 'processing'):
            return "already_active"
            
        await session.execute(on_conflict_stmt)
        return "subscribed"

async def cancel_restock_subscription(user_id: int, item_id: int) -> None:
    """Idempotent cancellation of restock subscription."""
    async with Database().session() as session:
        stmt = select(ProductRestockSubscription).where(
            ProductRestockSubscription.user_id == user_id,
            ProductRestockSubscription.item_id == item_id,
            ProductRestockSubscription.status.in_(['active', 'processing'])
        )
        result = await session.execute(stmt)
        sub = result.scalars().first()
        
        if sub:
            sub.status = 'cancelled'
            sub.cancelled_at = datetime.datetime.now(datetime.timezone.utc)
            sub.processing_started_at = None
            sub.next_attempt_at = None
            sub.updated_at = datetime.datetime.now(datetime.timezone.utc)

async def count_active_restock_subscriptions(item_id: int) -> int:
    """Count how many active/processing subscriptions an item has."""
    async with Database().session() as session:
        stmt = select(func.count(ProductRestockSubscription.id)).where(
            ProductRestockSubscription.item_id == item_id,
            ProductRestockSubscription.status.in_(['active', 'processing'])
        )
        result = await session.execute(stmt)
        return result.scalar() or 0

async def recover_stale_processing_subscriptions(stale_timeout_seconds: int) -> int:
    """Recover processing subscriptions that have been stuck for too long."""
    now = datetime.datetime.now(datetime.timezone.utc)
    stale_time = now - datetime.timedelta(seconds=stale_timeout_seconds)
    async with Database().session() as session:
        stmt = (
            update(ProductRestockSubscription)
            .where(
                ProductRestockSubscription.status == 'processing',
                ProductRestockSubscription.processing_started_at < stale_time
            )
            .values(
                status='active',
                processing_started_at=None,
                next_attempt_at=now,
                updated_at=now
            )
        )
        result = await session.execute(stmt)
        return result.rowcount

async def get_dispatchable_restock_count() -> int:
    """Return count of active subscriptions that are due for a retry."""
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as session:
        stmt = select(func.count(ProductRestockSubscription.id)).where(
            ProductRestockSubscription.status == 'active',
            (ProductRestockSubscription.next_attempt_at == None) | (ProductRestockSubscription.next_attempt_at <= now)
        )
        result = await session.execute(stmt)
        return result.scalar() or 0

async def claim_ready_restock_subscriptions(limit: int) -> list[ProductRestockSubscription]:
    """Atomically claim a batch of restock subscriptions that are ready."""
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as session:
        from bot.database.methods.read import get_stock_for_items
        
        # 1. lock candidate active subscriptions using FOR UPDATE SKIP LOCKED
        query = (
            select(ProductRestockSubscription)
            .join(Goods, Goods.id == ProductRestockSubscription.item_id)
            .where(
                ProductRestockSubscription.status == 'active',
                (ProductRestockSubscription.next_attempt_at == None) | (ProductRestockSubscription.next_attempt_at <= now)
            )
            .order_by(ProductRestockSubscription.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        
        result = await session.execute(query)
        candidates = list(result.scalars().all())
        
        if not candidates:
            return []
            
        # 2. evaluate the candidate products using the canonical live-stock logic
        item_ids = list(set(c.item_id for c in candidates))
        stock_dict = await get_stock_for_items(item_ids)
        
        to_process_ids = []
        claimed = []
        
        for sub in candidates:
            stock = stock_dict.get(sub.item_id, 0)
            if stock == -1 or stock > 0:
                to_process_ids.append(sub.id)
                claimed.append(sub)
                
        # 3. transition only currently available candidates to processing
        if to_process_ids:
            stmt = (
                update(ProductRestockSubscription)
                .where(ProductRestockSubscription.id.in_(to_process_ids))
                .values(
                    status='processing',
                    processing_started_at=now,
                    updated_at=now
                )
            )
            await session.execute(stmt)
            
        # 4. leave out-of-stock candidates active without modifying timestamps
        # 5. commit and return only truly claimed subscriptions
        return claimed

async def release_restock_for_retry(subscription_id: int, next_attempt_at: datetime.datetime, error_code: str) -> None:
    """Release a claimed subscription back to active for retry."""
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as session:
        stmt = (
            update(ProductRestockSubscription)
            .where(ProductRestockSubscription.id == subscription_id, ProductRestockSubscription.status == 'processing')
            .values(
                status='active',
                processing_started_at=None,
                next_attempt_at=next_attempt_at,
                attempts=ProductRestockSubscription.attempts + 1,
                last_error=error_code,
                updated_at=now
            )
        )
        await session.execute(stmt)

async def mark_restock_notified(subscription_id: int) -> None:
    """Mark a subscription as successfully notified."""
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as session:
        stmt = (
            update(ProductRestockSubscription)
            .where(ProductRestockSubscription.id == subscription_id, ProductRestockSubscription.status == 'processing')
            .values(
                status='notified',
                notified_at=now,
                processing_started_at=None,
                next_attempt_at=None,
                last_error=None,
                updated_at=now
            )
        )
        await session.execute(stmt)

async def mark_restock_failed(subscription_id: int, error_code: str) -> None:
    """Mark a subscription as permanently failed."""
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as session:
        stmt = (
            update(ProductRestockSubscription)
            .where(ProductRestockSubscription.id == subscription_id, ProductRestockSubscription.status == 'processing')
            .values(
                status='failed',
                processing_started_at=None,
                next_attempt_at=None,
                last_error=error_code,
                updated_at=now
            )
        )
        await session.execute(stmt)

async def return_restock_to_active(subscription_id: int) -> None:
    """Return to active without incrementing attempts (e.g. stock went to 0)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as session:
        stmt = (
            update(ProductRestockSubscription)
            .where(ProductRestockSubscription.id == subscription_id, ProductRestockSubscription.status == 'processing')
            .values(
                status='active',
                processing_started_at=None,
                updated_at=now
            )
        )
        await session.execute(stmt)
'''

with open('bot/database/methods/restock_subscriptions.py', 'w', encoding='utf-8') as f:
    f.write(top)

