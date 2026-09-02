from dataclasses import dataclass, field
from typing import Any
import datetime
from sqlalchemy import select, insert, func, update, exists
from bot.database.main import Database
from bot.database.models import Goods, ItemValues, ProductRestockSubscription, User


@dataclass
class ClaimedRestockSubscription:
    id: int
    user_id: int
    item_id: int
    attempts: int = 0
    status: str = 'processing'
    user_language_code: str | None = None
    item_data: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


async def is_restock_subscription_active(user_id: int, item_id: int) -> bool:
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
    """Return count of active subscriptions that are due for a retry with live stock and enabled goods."""
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as session:
        stock_exists = exists(
            select(1).where(ItemValues.item_id == Goods.id)
        )
        stmt = (
            select(func.count(ProductRestockSubscription.id))
            .join(Goods, Goods.id == ProductRestockSubscription.item_id)
            .where(
                ProductRestockSubscription.status == 'active',
                (ProductRestockSubscription.next_attempt_at == None) | (ProductRestockSubscription.next_attempt_at <= now),
                Goods.is_enabled == True,
                stock_exists,
            )
        )
        result = await session.execute(stmt)
        return result.scalar() or 0

async def claim_ready_restock_subscriptions(limit: int) -> list[ClaimedRestockSubscription]:
    """Atomically claim a batch of restock subscriptions that are ready."""
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as session:
        # 1. Lock only ProductRestockSubscription rows using FOR UPDATE OF product_restock_subscriptions SKIP LOCKED.
        # Exclude disabled goods and out-of-stock items at query level to prevent starvation.
        stock_exists = exists(
            select(1).where(ItemValues.item_id == Goods.id)
        )
        query = (
            select(ProductRestockSubscription)
            .join(Goods, Goods.id == ProductRestockSubscription.item_id)
            .where(
                ProductRestockSubscription.status == 'active',
                (ProductRestockSubscription.next_attempt_at == None) | (ProductRestockSubscription.next_attempt_at <= now),
                Goods.is_enabled == True,
                stock_exists,
            )
            .order_by(ProductRestockSubscription.created_at.asc())
            .limit(limit)
            .with_for_update(of=ProductRestockSubscription, skip_locked=True)
        )

        result = await session.execute(query)
        candidates = list(result.scalars().all())

        if not candidates:
            return []

        to_process_ids = [c.id for c in candidates]

        # 2. Transition claimed candidates to processing
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

        # 3. Eagerly load user locales and goods data to avoid detached ORM access
        claimed_user_ids = list(set(c.user_id for c in candidates))
        claimed_item_ids = list(set(c.item_id for c in candidates))

        users_result = await session.execute(
            select(User.telegram_id, User.language_code).where(User.telegram_id.in_(claimed_user_ids))
        )
        user_lang_map = {row.telegram_id: row.language_code for row in users_result}

        goods_result = await session.execute(
            select(
                Goods.id,
                Goods.name,
                Goods.name_en,
                Goods.name_ar,
                Goods.name_ru,
                Goods.name_zh,
                Goods.name_vi,
                Goods.name_tr,
                Goods.name_es,
                Goods.is_enabled,
            ).where(Goods.id.in_(claimed_item_ids))
        )
        goods_map = {
            row.id: {
                "name": row.name,
                "name_en": row.name_en,
                "name_ar": row.name_ar,
                "name_ru": row.name_ru,
                "name_zh": row.name_zh,
                "name_vi": row.name_vi,
                "name_tr": row.name_tr,
                "name_es": row.name_es,
                "is_enabled": row.is_enabled,
            }
            for row in goods_result
        }

        # 4. Build detached structures
        claimed = [
            ClaimedRestockSubscription(
                id=c.id,
                user_id=c.user_id,
                item_id=c.item_id,
                attempts=c.attempts,
                status='processing',
                user_language_code=user_lang_map.get(c.user_id),
                item_data=goods_map.get(c.item_id, {}),
            )
            for c in candidates
        ]

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
