import datetime

async def recover_stale_processing_subscriptions(stale_timeout_seconds: int) -> int:
    """Recover processing subscriptions that have been stuck for too long."""
    now = datetime.datetime.now(datetime.timezone.utc)
    stale_time = now - datetime.timedelta(seconds=stale_timeout_seconds)
    async with Database().session() as session:
        from sqlalchemy import update
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
        from sqlalchemy import text
        # Postgres atomic claim:
        query = text("UPDATE product_restock_subscriptions SET status = 'processing', processing_started_at = :now, updated_at = :now WHERE id IN (SELECT sub.id FROM product_restock_subscriptions sub JOIN goods g ON g.id = sub.item_id WHERE sub.status = 'active' AND (sub.next_attempt_at IS NULL OR sub.next_attempt_at <= :now) AND g.is_active = true ORDER BY sub.created_at ASC FOR UPDATE SKIP LOCKED LIMIT :limit) RETURNING id, user_id, item_id, attempts")
        
        result = await session.execute(query, {"now": now, "limit": limit})
        
        claimed = []
        for row in result.all():
            sub = ProductRestockSubscription(
                id=row.id,
                user_id=row.user_id,
                item_id=row.item_id,
                attempts=row.attempts
            )
            claimed.append(sub)
        return claimed

async def release_restock_for_retry(subscription_id: int, next_attempt_at: datetime.datetime, error_code: str) -> None:
    """Release a claimed subscription back to active for retry."""
    now = datetime.datetime.now(datetime.timezone.utc)
    async with Database().session() as session:
        from sqlalchemy import update
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
        from sqlalchemy import update
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
        from sqlalchemy import update
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
        from sqlalchemy import update
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

