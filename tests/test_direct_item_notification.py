import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_notification_callback_contains_goods_id():
    from bot.misc.services.restock_dispatcher import _get_restock_view_keyboard
    kb = _get_restock_view_keyboard(42)
    assert kb.inline_keyboard[0][0].callback_data == 'direct_item:42'

@pytest.mark.asyncio
async def test_direct_item_handler_missing_id():
    from bot.handlers.user.shop_and_goods import direct_item_handler
    
    call = AsyncMock()
    call.data = "direct_item:999"
    state = AsyncMock()
    
    with patch('bot.database.methods.read.get_item_info_by_id', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with patch('bot.handlers.user.shop_and_goods.safe_edit_or_send', new_callable=AsyncMock) as mock_edit:
            await direct_item_handler(call, state)
            mock_get.assert_called_once_with(999)
            mock_edit.assert_called_once()

@pytest.mark.asyncio
async def test_direct_item_handler_valid_id_and_special_names():
    from bot.handlers.user.shop_and_goods import direct_item_handler
    
    call = AsyncMock()
    call.data = "direct_item:42"
    call.from_user.id = 123
    state = AsyncMock()
    
    special_name = "Arabic emoji duplicate space"
    
    with patch('bot.database.methods.read.get_item_info_by_id', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {'id': 42, 'name': special_name}
        with patch('bot.handlers.user.shop_and_goods._render_item_page', new_callable=AsyncMock) as mock_render:
            await direct_item_handler(call, state)
            mock_get.assert_called_once_with(42)
            state.update_data.assert_called_with(item_quantity=1, keypad_value='0', item_id=42, csrf_item=special_name, item_back_data='menu')
            mock_render.assert_called_once_with(call, state, special_name, back_data='menu', user_id=123)
