import pytest
from unittest.mock import patch, AsyncMock
from decimal import Decimal
import os
import random
from sqlalchemy import select

from bot.database.main import Database
from bot.database.models.main import User, Categories, Goods, ItemValues, CheckoutIntakeDraft, ProductCustomerField, Order, OrderItem, BoughtGoods, OrderCustomerInput, ManualFulfillmentJob
from bot.database.methods.transactions import buy_item_transaction
from bot.database.methods.intake_drafts import get_or_create_draft

@pytest.fixture(autouse=True)
def setup_env():
    os.environ["DATA_ENCRYPTION_ACTIVE_VERSION"] = "1"
    os.environ["DATA_ENCRYPTION_KEY_V1"] = "yHq-6nF1A73n_z01N9t6tGq7x_J_1gqWwQ3Z_f5H9Y0="
    yield

@pytest.mark.asyncio
async def test_manual_success_unlimited_stock():
    uid = random.randint(1000000, 9000000)
    async with Database().session() as s:
        user = User(telegram_id=uid, balance=Decimal("100"))
        s.add(user)
        cat = Categories(name="Cat")
        s.add(cat)
        await s.flush()
        
        goods = Goods(name=f"Manual_Inf_{uid}", description="desc", price=Decimal("10"), category_id=cat.id, fulfillment_mode="manual")
        s.add(goods)
        await s.flush()
        
        # Unlimited stock
        iv = ItemValues(item_id=goods.id, value="infinity_stock_placeholder", is_infinity=True)
        s.add(iv)
        
        # Fields
        f1 = ProductCustomerField(goods_id=goods.id, field_key="email", field_type="text", scope="per_order", label_i18n={"en": "test"})
        f2 = ProductCustomerField(goods_id=goods.id, field_key="password", field_type="secret", is_sensitive=True, scope="per_order", label_i18n={"en": "test"})
        s.add_all([f1, f2])
        await s.flush()
        
        # Step 1: Create a Draft
        draft, _ = await get_or_create_draft(s, uid, goods.id, 1, [f1, f2])
        await s.commit()
        draft_token = draft.public_token
        
        # Simulate saving answers
        from bot.database.methods.intake_drafts import save_draft_answer
        await save_draft_answer(s, draft, {"field": f1, "unit_index": 0}, "test@test.com")
        await save_draft_answer(s, draft, {"field": f2, "unit_index": 0}, "secretpass")
        await s.commit()

    # Purchase
    success, msg, data = await buy_item_transaction(uid, f"Manual_Inf_{uid}", draft_public_token=draft_token)
    assert success is True
    assert data["delivered_values"] == []
    
    async with Database().session() as s:
        user_db = await s.get(User, uid)
        assert float(user_db.balance) == 90.0
        
        orders = (await s.execute(select(Order).where(Order.user_id == uid))).scalars().all()
        assert len(orders) == 1
        assert orders[0].status == "processing"
        
        order_items = (await s.execute(select(OrderItem).where(OrderItem.order_id == orders[0].id))).scalars().all()
        assert len(order_items) == 1
        assert order_items[0].fulfillment_status == "pending"
        
        jobs = (await s.execute(select(ManualFulfillmentJob).where(ManualFulfillmentJob.order_item_id == order_items[0].id))).scalars().all()
        assert len(jobs) == 1
        assert jobs[0].status == "queued"
        
        inputs = (await s.execute(select(OrderCustomerInput).where(OrderCustomerInput.order_id == orders[0].id))).scalars().all()
        assert len(inputs) == 2
        
        bought = (await s.execute(select(BoughtGoods).where(BoughtGoods.order_id == orders[0].id))).scalars().all()
        assert len(bought) == 0
        
        draft_db = await s.get(CheckoutIntakeDraft, draft.id)
        assert draft_db.status == "consumed"
        assert draft_db.order_id == orders[0].id
        assert draft_db.consumed_at is not None

@pytest.mark.asyncio
async def test_manual_success_finite_stock():
    uid = random.randint(1000000, 9000000)
    async with Database().session() as s:
        user = User(telegram_id=uid, balance=Decimal("100"))
        s.add(user)
        cat = Categories(name="Cat")
        s.add(cat)
        await s.flush()
        
        goods = Goods(name=f"Manual_Fin_{uid}", description="desc", price=Decimal("10"), category_id=cat.id, fulfillment_mode="manual")
        s.add(goods)
        await s.flush()
        
        # Finite stock
        iv1 = ItemValues(item_id=goods.id, value="finite_1", is_infinity=False)
        iv2 = ItemValues(item_id=goods.id, value="finite_2", is_infinity=False)
        s.add_all([iv1, iv2])
        
        # Fields
        f1 = ProductCustomerField(goods_id=goods.id, field_key="email", field_type="text", scope="per_order", label_i18n={"en": "test"})
        s.add(f1)
        await s.flush()
        
        draft, _ = await get_or_create_draft(s, uid, goods.id, 1, [f1])
        await s.commit()
        draft_token = draft.public_token
        
        # Simulate saving answers
        from bot.database.methods.intake_drafts import save_draft_answer
        await save_draft_answer(s, draft, {"field": f1, "unit_index": 0}, "test@test.com")
        await s.commit()

    # Purchase
    success, msg, data = await buy_item_transaction(uid, f"Manual_Fin_{uid}", draft_public_token=draft_token)
    assert success is True
    assert data["delivered_values"] == []
    
    async with Database().session() as s:
        user_db = await s.get(User, uid)
        assert float(user_db.balance) == 90.0
        
        # Stock capacity should be consumed
        ivs = (await s.execute(select(ItemValues).where(ItemValues.item_id == goods.id))).scalars().all()
        assert len(ivs) == 1
        
        orders = (await s.execute(select(Order).where(Order.user_id == uid))).scalars().all()
        bought = (await s.execute(select(BoughtGoods).where(BoughtGoods.order_id == orders[0].id))).scalars().all()
        assert len(bought) == 0

@pytest.mark.asyncio
async def test_failure_rollback():
    uid = random.randint(1000000, 9000000)
    async with Database().session() as s:
        user = User(telegram_id=uid, balance=Decimal("100"))
        s.add(user)
        cat = Categories(name="Cat")
        s.add(cat)
        await s.flush()
        
        goods = Goods(name=f"Manual_Fail_{uid}", description="desc", price=Decimal("10"), category_id=cat.id, fulfillment_mode="manual")
        s.add(goods)
        await s.flush()
        
        iv = ItemValues(item_id=goods.id, value="fin", is_infinity=False)
        s.add(iv)
        
        f1 = ProductCustomerField(goods_id=goods.id, field_key="email", field_type="text", scope="per_order", label_i18n={"en": "test"})
        s.add(f1)
        await s.flush()
        
        draft, _ = await get_or_create_draft(s, uid, goods.id, 1, [f1])
        await s.commit()
        
        from bot.database.methods.intake_drafts import save_draft_answer
        await save_draft_answer(s, draft, {"field": f1, "unit_index": 0}, "test@test.com")
        await s.commit()
        draft_token = draft.public_token

    # Force failure by patching uuid4
    with patch('bot.database.methods.transactions.uuid4') as mock_uuid:
        mock_uuid.side_effect = Exception("Simulated DB Crash")
        try:
            await buy_item_transaction(uid, f"Manual_Fail_{uid}", draft_public_token=draft_token)
        except Exception:
            pass

    async with Database().session() as s:
        user_db = await s.get(User, uid)
        # Balance unchanged
        assert float(user_db.balance) == 100.0
        
        # Stock unchanged
        ivs = (await s.execute(select(ItemValues).where(ItemValues.item_id == goods.id))).scalars().all()
        assert len(ivs) == 1
        
        # No partial data
        orders = (await s.execute(select(Order).where(Order.user_id == uid))).scalars().all()
        assert len(orders) == 0
        
        draft_db = await s.get(CheckoutIntakeDraft, draft.id)
        # Draft pending and retryable
        assert draft_db.status == "pending"
        assert draft_db.current_step >= 1
        assert draft_db.order_id is None
        assert draft_db.consumed_at is None

@pytest.mark.asyncio
async def test_instant_regression():
    uid = random.randint(1000000, 9000000)
    async with Database().session() as s:
        user = User(telegram_id=uid, balance=Decimal("100"))
        s.add(user)
        cat = Categories(name="Cat")
        s.add(cat)
        await s.flush()
        
        goods = Goods(name=f"Instant_{uid}", description="desc", price=Decimal("10"), category_id=cat.id, fulfillment_mode="instant")
        s.add(goods)
        await s.flush()
        
        iv = ItemValues(item_id=goods.id, value="SECRET_INSTANT_KEY", is_infinity=False)
        s.add(iv)
        await s.commit()

    # Purchase
    success, msg, data = await buy_item_transaction(uid, f"Instant_{uid}")
    assert success is True
    assert data["delivered_values"] == ["SECRET_INSTANT_KEY"]
    
    async with Database().session() as s:
        orders = (await s.execute(select(Order).where(Order.user_id == uid))).scalars().all()
        assert len(orders) == 1
        assert orders[0].status == "completed"
        
        bought = (await s.execute(select(BoughtGoods).where(BoughtGoods.order_id == orders[0].id))).scalars().all()
        assert len(bought) == 1
        assert bought[0].value == "SECRET_INSTANT_KEY"
