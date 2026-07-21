import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram.types import CallbackQuery, Message, User, InaccessibleMessage
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext

# Target modules
from bot.handlers.user.shop_and_goods import _render_item_page, _render_item_page_by_id, item_info_callback_handler
from bot.handlers.user.balance_and_payment import buy_item_callback_handler
from bot.handlers.user.orders import order_buy_again_handler
from bot.database.methods.audit import log_audit
from bot.misc.utils import safe_edit_or_send

@pytest.mark.asyncio
class TestMessageLifecycle:
    pass

@pytest.mark.asyncio
class TestAudit:
    @patch('bot.database.methods.audit.audit_logger')
    @patch('bot.database.methods.audit.Database')
    async def test_audit_resource_id_cast(self, mock_db, mock_logger):
        await log_audit("test_action", resource_id=123)
        
        mock_session = mock_db.return_value.session.return_value.__aenter__.return_value
        mock_session.add.assert_called_once()
        added_audit = mock_session.add.call_args[0][0]
        assert added_audit.resource_id == "123"

    @patch('bot.database.methods.audit.audit_logger')
    @patch('bot.database.methods.audit.Database')
    async def test_audit_resource_id_none(self, mock_db, mock_logger):
        await log_audit("test_action", resource_id=None)
        
        mock_session = mock_db.return_value.session.return_value.__aenter__.return_value
        mock_session.add.assert_called_once()
        added_audit = mock_session.add.call_args[0][0]
        assert added_audit.resource_id is None
