import pytest
import datetime
from bot.database.models.main import ProductRestockSubscription, User, Goods
from bot.database.main import Database
from bot.database.methods.restock_subscriptions import is_restock_subscription_active
from sqlalchemy import select, update

@pytest.mark.asyncio
async def test_is_restock_subscription_active_statuses(setup_test_database):
    user_id = 111222333
    item_id = 444
    
    async with Database().session() as session:
        session.add(User(telegram_id=user_id, registration_date=datetime.datetime.now(datetime.timezone.utc)))
        session.add(Goods(id=item_id, name="Test Active", description="Test desc", price=10, category_id=1))
        await session.commit()
        
    # No subscription
    assert await is_restock_subscription_active(user_id, item_id) is False
    
    # Active
    async with Database().session() as session:
        sub = ProductRestockSubscription(user_id=user_id, item_id=item_id, status="active")
        session.add(sub)
        await session.commit()
    assert await is_restock_subscription_active(user_id, item_id) is True
    
    # Processing
    async with Database().session() as session:
        stmt = update(ProductRestockSubscription).where(ProductRestockSubscription.user_id==user_id).values(status="processing")
        await session.execute(stmt)
        await session.commit()
    assert await is_restock_subscription_active(user_id, item_id) is True
    
    # Cancelled
    async with Database().session() as session:
        stmt = update(ProductRestockSubscription).where(ProductRestockSubscription.user_id==user_id).values(status="cancelled")
        await session.execute(stmt)
        await session.commit()
    assert await is_restock_subscription_active(user_id, item_id) is False

    # Notified
    async with Database().session() as session:
        stmt = update(ProductRestockSubscription).where(ProductRestockSubscription.user_id==user_id).values(status="notified")
        await session.execute(stmt)
        await session.commit()
    assert await is_restock_subscription_active(user_id, item_id) is False

    # Failed
    async with Database().session() as session:
        stmt = update(ProductRestockSubscription).where(ProductRestockSubscription.user_id==user_id).values(status="failed")
        await session.execute(stmt)
        await session.commit()
    assert await is_restock_subscription_active(user_id, item_id) is False
