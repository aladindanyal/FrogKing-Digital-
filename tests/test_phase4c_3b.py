import pytest
from decimal import Decimal
from sqlalchemy import select, delete

from bot.database.main import Database
from bot.database.models import User, Categories, Goods, ItemValues
from bot.database.models.main import ProductCustomerField, CheckoutIntakeDraft, OrderCustomerInput, ManualFulfillmentJob, Order, OrderItem
from bot.database.methods.intake_drafts import get_or_create_draft, save_draft_answer, get_expected_steps, get_draft_by_token
from bot.database.methods.transactions import buy_item_transaction
from bot.misc.intake_validator import validate_field_input
from bot.misc import encryption
import os


@pytest.mark.asyncio
async def test_manual_checkout_flow():
    # Configure dummy encryption for tests
    os.environ["DATA_ENCRYPTION_ACTIVE_VERSION"] = "1"
    os.environ["DATA_ENCRYPTION_KEY_V1"] = "yHq-6nF1A73n_z01N9t6tGq7x_J_1gqWwQ3Z_f5H9Y0="
    
    # Insert test data
    async with Database().session() as s:
        # Create user
        user = User(telegram_id=9999991, balance=Decimal("100.00"))
        s.add(user)
        
        # Create category
        cat = Categories(name="Test Manual Cat", description="Manual category")
        s.add(cat)
        await s.flush()
        
        # Create goods
        goods = Goods(
            name="Test Manual Goods 1",
            price=Decimal("15.00"),
            description="desc",
            category_id=cat.id,
            fulfillment_mode="manual"
        )
        s.add(goods)
        await s.flush()
        
        # Stock
        iv1 = ItemValues(item_id=goods.id, value="val1", is_infinity=False)
        iv2 = ItemValues(item_id=goods.id, value="val2", is_infinity=False)
        s.add_all([iv1, iv2])
        
        # Create field
        field = ProductCustomerField(
            goods_id=goods.id,
            field_key="test_email",
            field_type="email",
            scope="per_order",
            sort_order=1,
            label_i18n={"en": "Test Email", "ar": "بريد إلكتروني اختباري"}
        )
        s.add(field)
        await s.commit()

        # Step 1: Create draft
        active_fields = [field]
        draft, _ = await get_or_create_draft(s, 9999991, goods.id, 2, active_fields)
        await s.commit()
        
        # Verify draft creation
        assert draft.public_token is not None
        assert draft.status == 'pending'
        assert draft.current_step == 0
        
        draft_token = draft.public_token
        
        # Step 2: Answer question
        draft = await get_draft_by_token(s, draft_token, 9999991)
        steps = get_expected_steps(active_fields, 2)
        assert len(steps) == 1
        
        val = validate_field_input(field, "test@example.com")
        await save_draft_answer(s, draft, steps[0], val)
        await s.commit()
        
        # Verify draft update
        draft = await get_draft_by_token(s, draft_token, 9999991)
        assert draft.current_step == 1
        payload = encryption.decrypt_json(draft.encrypted_payload, draft.encryption_version)
        assert payload["answers"][0]["value"] == "test@example.com"
        
    # Step 3: Transaction
    success, error_key, details = await buy_item_transaction(
        telegram_id=9999991,
        item_name="Test Manual Goods 1",
        quantity=2,
        draft_public_token=draft_token
    )
    
    assert success is True
    assert details["quantity"] == 2
    assert details["subtotal"] == 30.00
    
    # Verify post-transaction state
    async with Database().session() as s:
        # Check order
        order = await s.execute(select(Order).where(Order.id == details["order_id"]))
        order = order.scalar_one()
        assert order.status == "processing"
        
        # Check draft consumed
        draft = await s.execute(select(CheckoutIntakeDraft).where(CheckoutIntakeDraft.public_token == draft_token))
        draft = draft.scalar_one()
        assert draft.status == "consumed"
        
        # Check order customer input
        inputs = await s.execute(select(OrderCustomerInput).where(OrderCustomerInput.order_id == order.id))
        inputs = inputs.scalars().all()
        assert len(inputs) == 1
        assert inputs[0].field_key_snapshot == "test_email"
        assert inputs[0].masked_preview == "t***t@example.com"
        
        # Check fulfillment job
        order_item = await s.execute(select(OrderItem).where(OrderItem.order_id == order.id))
        order_item = order_item.scalars().first()
        job = await s.execute(select(ManualFulfillmentJob).where(ManualFulfillmentJob.order_item_id == order_item.id))
        job = job.scalar_one_or_none()
        assert job is not None
        assert job.status == "queued"
        
        # Check stock consumed
        stock = await s.execute(select(ItemValues).where(ItemValues.item_id == goods.id))
        assert len(stock.scalars().all()) == 0

    # Step 4: Idempotency
    success, error_key, details = await buy_item_transaction(
        telegram_id=9999991,
        item_name="Test Manual Goods 1",
        quantity=2,
        draft_public_token=draft_token
    )
    assert success is False
    assert error_key == "intake.already_purchased"


@pytest.mark.asyncio
async def test_instant_checkout_unaffected():
    # Insert test data
    async with Database().session() as s:
        # Create user
        user = User(telegram_id=9999992, balance=Decimal("100.00"))
        s.add(user)
        
        # Create category
        cat = Categories(name="Test Instant Cat", description="Instant category")
        s.add(cat)
        await s.flush()
        
        # Create goods
        goods = Goods(
            name="Test Instant Goods 1",
            price=Decimal("10.00"),
            description="desc",
            category_id=cat.id,
            fulfillment_mode="instant"
        )
        s.add(goods)
        await s.flush()
        
        # Stock
        iv1 = ItemValues(item_id=goods.id, value="instant_val_1", is_infinity=False)
        s.add(iv1)
        await s.commit()
        
    # Transaction
    success, error_key, details = await buy_item_transaction(
        telegram_id=9999992,
        item_name="Test Instant Goods 1",
        quantity=1,
    )
    
    assert success is True
    assert details["quantity"] == 1
    assert details["subtotal"] == 10.00
    assert details["delivered_values"][0] == "instant_val_1"
    
    # Verify post-transaction state
    async with Database().session() as s:
        order = await s.execute(select(Order).where(Order.id == details["order_id"]))
        order = order.scalar_one()
        assert order.status == "completed"

        # Check stock consumed
        stock = await s.execute(select(ItemValues).where(ItemValues.item_id == goods.id))
        assert len(stock.scalars().all()) == 0

