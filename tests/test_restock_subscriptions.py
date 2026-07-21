import pytest
from sqlalchemy.exc import IntegrityError
import datetime

from bot.database.models.main import ProductRestockSubscription, Goods, ItemValues, User
from bot.database.methods.restock_subscriptions import (
    subscribe_to_restock,
    cancel_restock_subscription,
    is_restock_subscription_active,
    count_active_restock_subscriptions
)
from bot.i18n.main import localize, current_locale
from bot.database.main import Database
from sqlalchemy import select

class TestRestockSubscriptions:

    @pytest.mark.asyncio
    async def test_restock_subscription_lifecycle(self, setup_test_database):
        user_id = 999999999
        item_id = 999

        async with Database().session() as db_session:
            # 1. Create user and out-of-stock item
            db_session.add(User(telegram_id=user_id, registration_date=datetime.datetime.now(datetime.timezone.utc)))
            db_session.add(Goods(id=item_id, name="OOS Item", description="Test", price=100, category_id=1))
            await db_session.commit()

        # Empty item values means out of stock

        # 2. First subscribe creates one active row
        result = await subscribe_to_restock(user_id, item_id)
        assert result == "subscribed"

        active = await is_restock_subscription_active(user_id, item_id)
        assert active is True

        count = await count_active_restock_subscriptions(item_id)
        assert count == 1

        # 3. Repeated subscribe leaves one row (returns already_active)
        result2 = await subscribe_to_restock(user_id, item_id)
        assert result2 == "already_active"

        async with Database().session() as db_session:
            stmt = select(ProductRestockSubscription).where(ProductRestockSubscription.user_id == user_id)
            rows = (await db_session.execute(stmt)).scalars().all()
            assert len(rows) == 1

        # 4. Cancel changes active to cancelled
        await cancel_restock_subscription(user_id, item_id)
        active_after_cancel = await is_restock_subscription_active(user_id, item_id)
        assert active_after_cancel is False

        async with Database().session() as db_session:
            row = (await db_session.execute(stmt)).scalars().first()
            assert row.status == "cancelled"
            assert row.cancelled_at is not None

        # 5. Repeated Cancel is safe
        await cancel_restock_subscription(user_id, item_id)
        
        async with Database().session() as db_session:
            row = (await db_session.execute(stmt)).scalars().first()
            assert row.status == "cancelled"

        # 6. Resubscribe reactivates the same row
        result3 = await subscribe_to_restock(user_id, item_id)
        assert result3 == "subscribed"

        async with Database().session() as db_session:
            rows = (await db_session.execute(stmt)).scalars().all()
            assert len(rows) == 1
            assert rows[0].status == "active"
            assert rows[0].cancelled_at is None

        # 7. Notified row reactivates
        async with Database().session() as db_session:
            rows = (await db_session.execute(stmt)).scalars().all()
            rows[0].status = "notified"
            rows[0].notified_at = datetime.datetime.now(datetime.timezone.utc)
            await db_session.commit()

        result4 = await subscribe_to_restock(user_id, item_id)
        assert result4 == "subscribed"

        async with Database().session() as db_session:
            rows = (await db_session.execute(stmt)).scalars().all()
            assert len(rows) == 1
            assert rows[0].status == "active"
            assert rows[0].notified_at is None

        # 8. Failed row reactivates
        async with Database().session() as db_session:
            rows = (await db_session.execute(stmt)).scalars().all()
            rows[0].status = "failed"
            rows[0].attempts = 3
            rows[0].last_error = "some error"
            await db_session.commit()

        result5 = await subscribe_to_restock(user_id, item_id)
        assert result5 == "subscribed"

        async with Database().session() as db_session:
            rows = (await db_session.execute(stmt)).scalars().all()
            assert len(rows) == 1
            assert rows[0].status == "active"
            assert rows[0].attempts == 0
            assert rows[0].last_error is None


    @pytest.mark.asyncio
    async def test_restock_subscription_conditions(self, setup_test_database):
        user_id = 888888888
        
        async with Database().session() as db_session:
            db_session.add(User(telegram_id=user_id, registration_date=datetime.datetime.now(datetime.timezone.utc)))
            await db_session.commit()

        # 1. Missing product is rejected
        result = await subscribe_to_restock(user_id, 99999)
        assert result == "item_missing"

        # 2. Available finite product cannot be subscribed to
        item_id_finite = 888
        async with Database().session() as db_session:
            db_session.add(Goods(id=item_id_finite, name="Finite Item", description="Test", price=100, category_id=1))
            db_session.add(ItemValues(item_id=item_id_finite, value="val1", is_infinity=False))
            await db_session.commit()

        result_finite = await subscribe_to_restock(user_id, item_id_finite)
        assert result_finite == "available_now"

        # 3. Unlimited product cannot be subscribed to
        item_id_inf = 777
        async with Database().session() as db_session:
            db_session.add(Goods(id=item_id_inf, name="Infinity Item", description="Test", price=100, category_id=1))
            db_session.add(ItemValues(item_id=item_id_inf, value="val1", is_infinity=True))
            await db_session.commit()

        result_inf = await subscribe_to_restock(user_id, item_id_inf)
        assert result_inf == "unlimited"

    @pytest.mark.asyncio
    async def test_restock_db_constraints(self, setup_test_database):
        user_id = 777777777
        item_id = 666

        async with Database().session() as db_session:
            db_session.add(User(telegram_id=user_id, registration_date=datetime.datetime.now(datetime.timezone.utc)))
            db_session.add(Goods(id=item_id, name="Constraint Item", description="Test", price=100, category_id=1))
            await db_session.commit()

            # Invalid status should raise IntegrityError. Oh wait, SQLAlchemy sqlite doesn't enforce String length, but maybe it's not a constraint.
            # Postgres will just accept string length up to 20. But wait, I added index, not check constraint on status. Let's just test unique constraint.
            
            sub1 = ProductRestockSubscription(user_id=user_id, item_id=item_id, status="active")
            db_session.add(sub1)
            await db_session.commit()

            # Duplicate user/item pair violates UNIQUE CONSTRAINT
            sub2 = ProductRestockSubscription(user_id=user_id, item_id=item_id, status="cancelled")
            db_session.add(sub2)
            with pytest.raises(IntegrityError):
                await db_session.commit()
            await db_session.rollback()

    def test_restock_localizations(self):
        # Verify Arabic and English labels do not expose localization keys
        for lang in ['en', 'ar']:
            t = current_locale.set(lang)
            try:
                notify = localize("btn.notify_restock")
                assert notify != "btn.notify_restock"
                assert "notify" not in notify.lower() or lang == 'en'

                alert_sub = localize("shop.restock.subscribed")
                assert alert_sub != "shop.restock.subscribed"

                alert_cancel = localize("shop.restock.cancelled")
                assert alert_cancel != "shop.restock.cancelled"
            finally:
                current_locale.reset(t)
