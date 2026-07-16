import pytest
from decimal import Decimal
from sqlalchemy import select, delete
from unittest.mock import patch

from bot.database.main import Database
from bot.database.models import User, Categories, Goods, ItemValues
from bot.database.models.main import ProductCustomerField, CheckoutIntakeDraft, OrderCustomerInput, ManualFulfillmentJob, Order, OrderItem
from bot.database.methods.intake_drafts import get_or_create_draft, save_draft_answer, get_expected_steps
from bot.database.methods.transactions import buy_item_transaction
import os

@pytest.mark.asyncio
async def test_atomic_transaction_rollback():
    os.environ['DATA_ENCRYPTION_ACTIVE_VERSION'] = '1'
    os.environ['DATA_ENCRYPTION_KEY_V1'] = 'yHq-6nF1A73n_z01N9t6tGq7x_J_1gqWwQ3Z_f5H9Y0='
    
    async with Database().session() as s:
        user = User(telegram_id=9999993, balance=Decimal('100.00'))
        s.add(user)
        cat = Categories(name='Test Manual Cat 2', description='Manual category')
        s.add(cat)
        await s.flush()
        
        goods = Goods(
            name='Test Manual Goods Rollback',
            price=Decimal('10.00'),
            description='desc',
            category_id=cat.id,
            fulfillment_mode='manual'
        )
        s.add(goods)
        await s.flush()
        
        iv1 = ItemValues(item_id=goods.id, value='val1', is_infinity=False)
        s.add(iv1)
        await s.flush()
        
        field = ProductCustomerField(
            goods_id=goods.id,
            field_key='test_field',
            field_type='text',
            label_i18n={'en': 'Test'}
        )
        s.add(field)
        await s.commit()

    async with Database().session() as s:
        draft, _ = await get_or_create_draft(s, 9999993, goods.id, 1, [field])
        draft_token = draft.public_token
        steps = get_expected_steps([field], 1)
        await save_draft_answer(s, draft, steps[0], 'dummy')
        await s.commit()
    
    # Force an Exception by mocking ManualFulfillmentJob insertion
    with patch('bot.database.methods.transactions.ManualFulfillmentJob') as mock_job:
        mock_job.side_effect = Exception('Injected failure during job creation')
        
        success, msg, data = await buy_item_transaction(
            9999993,
            'Test Manual Goods Rollback',
            quantity=1,
            draft_public_token=draft_token
        )
        
    assert not success
    assert msg == 'transaction_error'
    
    # Assert database state
    async with Database().session() as s:
        # Balance unchanged
        user = (await s.execute(select(User).where(User.telegram_id == 9999993))).scalars().first()
        assert user.balance == Decimal('100.00')
        
        # Stock unchanged
        item_values = (await s.execute(select(ItemValues).where(ItemValues.item_id == goods.id))).scalars().all()
        assert len(item_values) == 1
        
        # No Order created
        orders = (await s.execute(select(Order).where(Order.user_id == 9999993))).scalars().all()
        assert len(orders) == 0
        
        # No OrderCustomerInput created
        inputs = (await s.execute(select(OrderCustomerInput).where(OrderCustomerInput.field_key_snapshot == 'test_field'))).scalars().all()
        assert len(inputs) == 0
        
        # Draft remains pending
        draft = (await s.execute(select(CheckoutIntakeDraft).where(CheckoutIntakeDraft.public_token == draft_token))).scalars().first()
        assert draft.status == 'pending'

@pytest.mark.asyncio
async def test_ensure_utc_normalizes():
    from bot.misc.utils import ensure_utc
    from datetime import datetime, timezone, timedelta
    
    # aware UTC expires_at works
    now_aware = datetime.now(timezone.utc)
    res_aware = ensure_utc(now_aware)
    assert res_aware.tzinfo == timezone.utc
    assert res_aware == now_aware
    
    # naive expires_at normalizes correctly
    now_naive = datetime.utcnow()
    res_naive = ensure_utc(now_naive)
    assert res_naive.tzinfo == timezone.utc
    
    # aware non-UTC offset normalizes correctly
    tz_plus_3 = timezone(timedelta(hours=3))
    now_plus_3 = datetime.now(tz_plus_3)
    res_plus_3 = ensure_utc(now_plus_3)
    assert res_plus_3.tzinfo == timezone.utc
    assert res_plus_3 == now_plus_3
    
    # no TypeError when comparing
    assert abs((res_naive - res_aware).total_seconds()) < 1.0
    assert res_plus_3 >= res_aware or res_plus_3 <= res_aware

@pytest.mark.asyncio
async def test_naive_expires_at_integration():
    from bot.database.models.main import CheckoutIntakeDraft, ProductCustomerField, Goods, Categories, User
    from bot.database.methods.intake_drafts import get_or_create_draft
    from bot.database.main import Database
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal
    from sqlalchemy import select
    
    async with Database().session() as s:
        user = User(telegram_id=9999994, balance=Decimal('100.00'))
        s.add(user)
        cat = Categories(name='Test Manual Cat 3', description='Manual category')
        s.add(cat)
        await s.flush()
        goods = Goods(name='Test Manual Goods 3', price=Decimal('10.00'), description='desc', category_id=cat.id, fulfillment_mode='manual')
        s.add(goods)
        await s.flush()
        field = ProductCustomerField(goods_id=goods.id, field_key='test_field', field_type='text', label_i18n={'en': 'Test'})
        s.add(field)
        await s.commit()

    # We will test 4 scenarios: naive expired, naive future, aware UTC, aware non-UTC
    scenarios = [
        # (expires_at, expects_expired)
        (datetime.utcnow() - timedelta(minutes=10), True), # Naive expired
        (datetime.utcnow() + timedelta(minutes=10), False), # Naive future
        (datetime.now(timezone.utc) - timedelta(minutes=10), True), # Aware UTC expired
        (datetime.now(timezone(timedelta(hours=3))) + timedelta(minutes=10), False), # Aware non-UTC future
    ]
    
    for expires_at, expects_expired in scenarios:
        async with Database().session() as s:
                  from bot.database.methods.intake_drafts import compute_schema_fingerprint
                  from bot.misc import encryption
                  
                  fingerprint = compute_schema_fingerprint([field])
                  initial_payload = {"schema_fingerprint": fingerprint, "answers": []}
                  enc = encryption.encrypt_json(initial_payload)
                  
                  draft = CheckoutIntakeDraft(
                      user_id=9999994,
                      goods_id=goods.id,
                      quantity=1,
                      status='pending',
                      created_at=datetime.now(timezone.utc) - timedelta(minutes=20),
                      expires_at=expires_at,
                      schema_fingerprint=fingerprint,
                      public_token="mock_token_tz",
                      encrypted_payload=enc['ciphertext'],
                      encryption_version=enc['version']
                  )
                  s.add(draft)
                  await s.commit()
                  draft_id = draft.id

        # Now test production get_or_create_draft
        async with Database().session() as s:
            returned_draft, created = await get_or_create_draft(s, 9999994, goods.id, 1, [field])
            
            # The function should not raise TypeError
            if expects_expired:
                # the old draft should have been marked expired, and a NEW one created
                assert returned_draft.id != draft_id
                assert created in (True, 'expired')
                old_draft = await s.get(CheckoutIntakeDraft, draft_id)
                assert old_draft.status == 'expired'
            else:
                # The old draft should be returned, still pending
                print(f"DEBUG: draft_id={draft_id}, returned_draft.id={returned_draft.id}")
                print(f"DEBUG: draft.schema_fingerprint={draft.schema_fingerprint}, newly computed={compute_schema_fingerprint([field])}")
                assert returned_draft.id == draft_id, f"Expected {draft_id}, got {returned_draft.id}. Created reason: {created}"
                assert created is None
                assert returned_draft.status == 'pending'

        # Cleanup for next scenario
        async with Database().session() as s:
            await s.execute(CheckoutIntakeDraft.__table__.delete().where(CheckoutIntakeDraft.user_id == 9999994))
            await s.commit()

