import pytest
from aiogram import Dispatcher, Bot
from aiogram.types import Message, Chat, User as AiogramUser, Update
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from datetime import datetime, timezone
from decimal import Decimal

from bot.handlers.main import register_all_handlers
from bot.database.main import Database
from bot.database.models.main import User, Categories, Goods, ProductCustomerField, CheckoutIntakeDraft
from bot.misc import encryption
import os

@pytest.fixture
def test_dp():
    dp = Dispatcher(storage=MemoryStorage())
    register_all_handlers(dp)
    return dp

@pytest.fixture
def mock_bot():
    class MockBot(Bot):
        def __init__(self):
            super().__init__(token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
        async def __call__(self, method, *args, **kwargs):
            if method.__class__.__name__ == 'DeleteMessage':
                return True
            msg = Message(message_id=999, date=datetime.now(timezone.utc), chat=Chat(id=1, type="private"))
            return msg
            
    return MockBot()


from unittest.mock import patch, AsyncMock

@pytest.mark.asyncio
@patch('aiogram.types.Message.delete', new_callable=AsyncMock)
async def test_manual_intake_routing_and_deletion(mock_delete, test_dp, mock_bot):
    os.environ["DATA_ENCRYPTION_ACTIVE_VERSION"] = "1"
    os.environ["DATA_ENCRYPTION_KEY_V1"] = "yHq-6nF1A73n_z01N9t6tGq7x_J_1gqWwQ3Z_f5H9Y0="
    
    async with Database().session() as s:
        # Create user
        user = User(telegram_id=9999993, balance=Decimal("100.00"))
        s.add(user)
        
        # Create category
        cat = Categories(name="Test Manual Cat Routing", description="Manual category")
        s.add(cat)
        await s.flush()
        
        # Create goods
        goods = Goods(
            name="Test Manual Goods Routing",
            price=Decimal("15.00"),
            description="desc",
            category_id=cat.id,
            fulfillment_mode="manual"
        )
        s.add(goods)
        await s.flush()
        
        # Create fields
        f1 = ProductCustomerField(goods_id=goods.id, field_key="email", field_type="email", required=True, is_sensitive=False, sort_order=1, scope="per_order", label_i18n={"en":"Email"})
        f2 = ProductCustomerField(goods_id=goods.id, field_key="password", field_type="secret", required=True, is_sensitive=True, sort_order=2, scope="per_order", label_i18n={"en":"Password"})
        s.add_all([f1, f2])
        await s.flush()

        from bot.database.methods.intake_drafts import get_or_create_draft
        draft, _ = await get_or_create_draft(s, 9999993, goods.id, 1, [f1, f2])
        await s.commit()

        draft_id = draft.id

    from bot.handlers.user.manual_intake import ManualIntakeStates

    # Mock an FSM Context
    state_ctx = test_dp.fsm.resolve_context(bot=mock_bot, chat_id=1, user_id=9999993)
    await state_ctx.set_state(ManualIntakeStates.WAITING_FOR_ANSWER)
    await state_ctx.update_data(
        intake_draft_id=draft_id,
        intake_current_field_id=f1.id,
        intake_item_name="Test Manual Goods Routing",
        intake_quantity=1
    )

    msg = Message(
        message_id=1001,
        date=datetime.now(timezone.utc),
        chat=Chat(id=1, type="private"),
        from_user=AiogramUser(id=9999993, is_bot=False, first_name="Test"),
        text="test@example.com"
    )
    
    # 1. Send the email (should not be intercepted by promo handler)
    await test_dp.feed_update(mock_bot, Update(update_id=1, message=msg))
    
    # Verify DB state
    async with Database().session() as s:
        draft = await s.get(CheckoutIntakeDraft, draft_id)
        assert draft.current_step == 1
        
        # 2. Prepare the next state Context for password
        await state_ctx.set_state(ManualIntakeStates.WAITING_FOR_ANSWER)
        await state_ctx.update_data(intake_current_field_id=f2.id)
        
    msg_pw = Message(
        message_id=1002,
        date=datetime.now(timezone.utc),
        chat=Chat(id=1, type="private"),
        from_user=AiogramUser(id=9999993, is_bot=False, first_name="Test"),
        text="MySecretPassword!"
    )

    # 3. Send password
    await test_dp.feed_update(mock_bot, Update(update_id=2, message=msg_pw))

    async with Database().session() as s:
        draft = await s.get(CheckoutIntakeDraft, draft_id)
        assert draft.current_step == 2
        payload = encryption.decrypt_json(draft.encrypted_payload, draft.encryption_version)
        answers = payload.get("answers", [])
        assert len(answers) == 2
        assert answers[0]['value'] == "test@example.com"
        assert answers[1]['value'] == "MySecretPassword!"

    assert mock_delete.call_count == 2
