import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from aiogram.types import CallbackQuery, Message
from bot.states import ShopStates

@pytest.fixture
def make_callback_query():
    def _make(data, user_id=123):
        call = AsyncMock(spec=CallbackQuery)
        call.data = data
        call.from_user = MagicMock(id=user_id)
        call.message = AsyncMock(spec=Message)
        call.message.edit_text = AsyncMock()
        call.answer = AsyncMock()
        return call
    return _make

@pytest.fixture
def mock_fsm():
    class MockFSM:
        def __init__(self):
            self.data = {}
            self.state = None
        async def update_data(self, **kwargs):
            self.data.update(kwargs)
        async def get_data(self):
            return self.data
        async def set_state(self, state):
            self.state = state
        async def get_state(self):
            return self.state
    return MockFSM()

@pytest.mark.asyncio
async def test_goods_page_stock_labels_and_batching(make_callback_query, mock_fsm):
    from bot.handlers.user.shop_and_goods import _render_goods_page
    
    call = make_callback_query("dummy_cpage")
    
    # Mock dependencies
    page_items = [
        (101, "Normal Item", "desc", 100, "Cat"),
        (102, "Single Item", "desc", 100, "Cat"),
        (103, "Out of Stock", "desc", 100, "Cat"),
        (104, "Unlimited Item", "desc", 100, "Cat"),
        (105, "Very Long Name That Exceeds The Maximum Allowed Limit So It Must Truncate", "desc", 100, "Cat")
    ]
    
    mock_paginator = AsyncMock()
    mock_paginator.get_page.return_value = page_items
    mock_paginator.has_next = False
    mock_paginator.has_previous = False
    mock_paginator.total_pages = 1
    mock_paginator.get_total_pages.return_value = 1
    
    stock_dict = {
        101: 5,
        102: 1,
        103: 0,
        104: -1,
        105: 10
    }
    
    with patch('bot.handlers.user.shop_and_goods.LazyPaginator', return_value=mock_paginator), \
         patch('bot.database.methods.read.get_stock_for_items', new_callable=AsyncMock) as mock_get_stock, \
         patch('bot.handlers.user.shop_and_goods.get_category_parent_id', new_callable=AsyncMock, return_value=None), \
         patch('bot.handlers.user.shop_and_goods.get_category_by_id', new_callable=AsyncMock, return_value={"name": "Test Cat", "description": ""}), \
         patch('bot.handlers.user.shop_and_goods.get_store_settings', new_callable=AsyncMock) as mock_settings, \
         patch('bot.handlers.user.shop_and_goods.localize') as mock_localize:
         
        mock_get_stock.return_value = stock_dict
        mock_settings.return_value.product_columns = 2  # Even if 2, should be forced to 1
        
        def fake_localize(key):
            if key == "shop.goods.sold_out": return "Sold Out"
            if key == "shop.goods.available": return "Available"
            return key
        mock_localize.side_effect = fake_localize
        
        await _render_goods_page(call, mock_fsm, category_id=1, page=0)
        
        # 1. Batch query assertion
        mock_get_stock.assert_called_once_with([101, 102, 103, 104, 105])
        
        # 2. Check UI rendering
        edit_text_args = call.message.edit_text.call_args
        assert edit_text_args is not None
        
        markup = edit_text_args.kwargs['reply_markup']
        buttons = markup.inline_keyboard
        
        # one product remains one keyboard row
        assert len(buttons) >= 5
        for i in range(5):
            assert len(buttons[i]) == 1  # Forced row_width=1
            
        # 3. Formats & stock text check
        # (101, "Normal Item") -> stock 5
        assert buttons[0][0].text == "Normal Item · 📦 5"
        assert buttons[0][0].callback_data == "itm:0:0"
        
        # (102, "Single Item") -> stock 1
        assert buttons[1][0].text == "Single Item · 📦 1"
        
        # (103, "Out of Stock") -> stock 0
        assert buttons[2][0].text == "Out of Stock · ⛔ Sold Out"
        
        # (104, "Unlimited Item") -> stock -1
        assert buttons[3][0].text == "Unlimited Item · ♾️ Available"
        assert "-1" not in buttons[3][0].text
        
        # (105, Very long) -> Truncated
        long_btn_text = buttons[4][0].text
        assert "Very Long Name That Exceeds The M" in long_btn_text
        assert "· 📦 10" in long_btn_text

@pytest.mark.asyncio
async def test_goods_page_arabic_localization(make_callback_query, mock_fsm):
    from bot.handlers.user.shop_and_goods import _render_goods_page
    
    call = make_callback_query("dummy_cpage")
    page_items = [
        (101, "Test 1", "desc", 100, "Cat"),
        (102, "Test 2", "desc", 100, "Cat"),
    ]
    
    mock_paginator = AsyncMock()
    mock_paginator.get_page.return_value = page_items
    mock_paginator.get_total_pages.return_value = 1
    stock_dict = {101: 0, 102: -1}
    
    with patch('bot.handlers.user.shop_and_goods.LazyPaginator', return_value=mock_paginator), \
         patch('bot.database.methods.read.get_stock_for_items', new_callable=AsyncMock, return_value=stock_dict), \
         patch('bot.handlers.user.shop_and_goods.get_category_parent_id', new_callable=AsyncMock, return_value=None), \
         patch('bot.handlers.user.shop_and_goods.get_category_by_id', new_callable=AsyncMock, return_value={"name": "Test", "description": ""}), \
         patch('bot.handlers.user.shop_and_goods.get_store_settings', new_callable=AsyncMock), \
         patch('bot.handlers.user.shop_and_goods.localize') as mock_localize:
         
        def fake_localize_ar(key):
            if key == "shop.goods.sold_out": return "نفد المخزون"
            if key == "shop.goods.available": return "متوفر"
            return key
        mock_localize.side_effect = fake_localize_ar
        
        await _render_goods_page(call, mock_fsm, category_id=1, page=0)
        
        markup = call.message.edit_text.call_args.kwargs['reply_markup']
        buttons = markup.inline_keyboard
        
        assert buttons[0][0].text == "Test 1 · ⛔ نفد المخزون"
        assert buttons[1][0].text == "Test 2 · ♾️ متوفر"
