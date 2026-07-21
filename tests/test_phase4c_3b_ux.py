import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bot.handlers.user.manual_intake import process_draft_answer, handle_intake_confirm, handle_intake_cancel, start_manual_intake
from aiogram.types import Message, CallbackQuery, User, Chat
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey

class DummyDraft:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
    def __bool__(self): return True

class DummyOrder:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
    def __bool__(self): return True

@pytest.mark.asyncio
async def test_email_does_not_show_password_received():
    msg = AsyncMock()
    state = AsyncMock()
    state.get_data.return_value = {'intake_item_name': 'test'}
    
    with patch('bot.handlers.user.manual_intake.save_draft_answer', new_callable=AsyncMock), \
         patch('bot.handlers.user.manual_intake._render_step', new_callable=AsyncMock), \
         patch('bot.handlers.user.manual_intake.get_item_info_cached', new_callable=AsyncMock) as mock_info, \
         patch('bot.handlers.user.manual_intake.get_active_product_fields', new_callable=AsyncMock) as mock_fields, \
         patch('bot.handlers.user.manual_intake.Database') as mock_db:
         
        mock_info.return_value = {'id': 1}
        session = AsyncMock()
        mock_db.return_value.session.return_value.__aenter__.return_value = session
        draft = DummyDraft(status='pending', current_step=0, id=1)
        session.scalar.return_value = draft
        
        with patch('bot.handlers.user.manual_intake.get_expected_steps') as mock_steps:
            field = MagicMock()
            field.field_type = 'email'
            field.is_sensitive = False
            mock_steps.return_value = [{"field": field}]
            
            with patch('bot.handlers.user.manual_intake.validate_field_input') as validate:
                validate.return_value = 'test@example.com'
                await process_draft_answer(msg, state, 1, 'test@example.com')
                
                msg.answer.assert_not_called() # No "Password received"

@pytest.mark.asyncio
async def test_secret_does_show_password_received():
    msg = AsyncMock(spec=Message)
    msg.answer = AsyncMock()
    msg.delete = AsyncMock()
    state = AsyncMock()
    state.get_data.return_value = {'intake_item_name': 'test'}
    
    with patch('bot.handlers.user.manual_intake.save_draft_answer', new_callable=AsyncMock), \
         patch('bot.handlers.user.manual_intake._render_step', new_callable=AsyncMock), \
         patch('bot.handlers.user.manual_intake.get_item_info_cached', new_callable=AsyncMock) as mock_info, \
         patch('bot.handlers.user.manual_intake.get_active_product_fields', new_callable=AsyncMock) as mock_fields, \
         patch('bot.handlers.user.manual_intake.Database') as mock_db, \
         patch('asyncio.sleep', new_callable=AsyncMock):
         
        mock_info.return_value = {'id': 1}
        session = AsyncMock()
        mock_db.return_value.session.return_value.__aenter__.return_value = session
        draft = DummyDraft(status='pending', current_step=0, id=1)
        session.scalar.return_value = draft
        
        with patch('bot.handlers.user.manual_intake.get_expected_steps') as mock_steps:
            field = MagicMock()
            field.field_type = 'secret'
            field.is_sensitive = True
            mock_steps.return_value = [{"field": field}]
            
            with patch('bot.handlers.user.manual_intake.validate_field_input') as validate:
                validate.return_value = 'mysecret'
                await process_draft_answer(msg, state, 1, 'mysecret')
                
                msg.answer.assert_called_with('Password received ✅')

@pytest.mark.asyncio
async def test_permanent_processing_receipt():
    call = AsyncMock()
    call.from_user = User(id=1, is_bot=False, first_name='Test')
    state = AsyncMock()
    state.get_data.return_value = {'intake_item_name': 'test_item', 'intake_draft_id': 1, 'intake_quantity': 1}
    
    with patch('bot.handlers.user.manual_intake.Database') as mock_db, \
         patch('bot.handlers.user.manual_intake.get_item_info_cached') as mock_info, \
         patch('bot.handlers.user.manual_intake.get_active_product_fields') as mock_fields, \
         patch('bot.handlers.user.manual_intake.get_expected_steps') as mock_steps, \
         patch('bot.handlers.user.manual_intake.buy_item_transaction', new_callable=AsyncMock) as mock_buy, \
         patch('bot.handlers.user.manual_intake.safe_edit_or_send', new_callable=AsyncMock) as mock_send, \
         patch('bot.handlers.user.orders.render_order_receipt', new_callable=AsyncMock) as mock_render:
         
        session = AsyncMock()
        mock_db.return_value.session.return_value.__aenter__.return_value = session
        
        draft = DummyDraft(status='pending', current_step=1, schema_fingerprint='fp1', quantity=1, public_token='token')
        from datetime import datetime, timezone, timedelta
        draft.expires_at = datetime.now(timezone.utc) + timedelta(days=1)
        session.scalar.return_value = draft
        
        mock_info.return_value = {'id': 1}
        
        with patch('bot.misc.intake_validator.compute_schema_fingerprint') as mock_fp:
            mock_fp.return_value = 'fp1'
            mock_steps.return_value = [{"field": "test"}]
            
            details = {
                'public_order_id': 'ORDER-123',
                'quantity': 1,
                'total': 10,
                'currency': 'USD'
            }
            mock_buy.return_value = (True, None, details)
            mock_render.return_value = ("Receipt: ORDER-123\nTotal: 10.00 USD\nYour order has been received and is now being prepared.", MagicMock())
            
            await handle_intake_confirm(call, state)
            
            # Verify the permanent processing receipt was sent and state cleared
            state.set_state.assert_called_with(None)
            mock_send.assert_called_once()
            args, kwargs = mock_send.call_args
            assert 'ORDER-123' in args[1]
            assert '10.00 USD' in args[1]
            assert 'Your order has been received and is now being prepared.' in args[1]

@pytest.mark.asyncio
async def test_active_order_warning():
    call = AsyncMock()
    call.from_user = User(id=1, is_bot=False, first_name='Test')
    state = AsyncMock()
    state.get_data.return_value = {'item_back_data': 'shop', 'item_quantity': 1}
    
    with patch('bot.handlers.user.manual_intake.Database') as mock_db, \
         patch('bot.handlers.user.manual_intake.get_item_info_cached', new_callable=AsyncMock) as mock_info, \
         patch('bot.handlers.user.manual_intake.safe_edit_or_send', new_callable=AsyncMock) as mock_send, \
         patch('bot.handlers.user.manual_intake.get_active_product_fields', new_callable=AsyncMock) as mock_fields:
         
        mock_info.return_value = {'id': 1}
        session = AsyncMock()
        mock_db.return_value.session.return_value.__aenter__.return_value = session
        
        # Simulate active order found
        session.scalar.return_value = DummyOrder(id=999, public_id='ORDER-123') # existing active jobs
        
        await start_manual_intake(call, state, 'test item', 'item_1', 1)
        
        # Verify warning sent
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        assert 'You already have an order being processed' in args[1]
        
@pytest.mark.asyncio
async def test_buy_another_bypass():
    call = AsyncMock()
    call.from_user = User(id=1, is_bot=False, first_name='Test')
    state = AsyncMock()
    state.get_data.return_value = {'item_back_data': 'shop', 'item_quantity': 1}
    
    with patch('bot.handlers.user.manual_intake.Database') as mock_db, \
         patch('bot.handlers.user.manual_intake.get_item_info_cached', new_callable=AsyncMock) as mock_info, \
         patch('bot.handlers.user.manual_intake.get_or_create_draft', new_callable=AsyncMock) as mock_draft, \
         patch('bot.handlers.user.manual_intake.get_active_product_fields', new_callable=AsyncMock) as mock_fields, \
         patch('bot.handlers.user.manual_intake._render_step', new_callable=AsyncMock):
         
        mock_info.return_value = {'id': 1}
        session = AsyncMock()
        mock_db.return_value.session.return_value.__aenter__.return_value = session
        
        mock_draft.return_value = (DummyDraft(status='pending', current_step=0, id=1), True)
        
        await start_manual_intake(call, state, 'test item', 'item_1', 1, bypass_active_check=True)
        
        # Bypass active check means we don't query for jobs and go straight to draft creation
        mock_draft.assert_called_once()
        
@pytest.mark.asyncio
async def test_cancel_returns_cleanly():
    call = AsyncMock()
    call.from_user = User(id=1, is_bot=False, first_name='Test')
    state = AsyncMock()
    state.get_data.return_value = {'intake_item_name': 'test_item', 'intake_draft_id': 1}
    
    with patch('bot.handlers.user.manual_intake.Database') as mock_db, \
         patch('bot.handlers.user.shop_and_goods._render_item_page', new_callable=AsyncMock) as mock_render:
         
        session = AsyncMock()
        mock_db.return_value.session.return_value.__aenter__.return_value = session
        
        draft = MagicMock()
        draft.status = 'pending'
        session.scalar.return_value = draft
        
        await handle_intake_cancel(call, state)
        
        # Verify cleanup and return
        assert draft.status == 'cancelled'
        state.set_state.assert_called_with(None)
        mock_render.assert_called_once_with(call, state, 'test_item', user_id=1)
