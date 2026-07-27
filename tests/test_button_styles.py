import pytest
from aiogram.enums import ButtonStyle
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards.inline import (
    main_menu,
    item_info,
    checkout_confirmation_keyboard,
    lazy_paginated_keyboard
)
from bot.misc import LazyPaginator

def test_button_style_serialization():
    # 1. primary serializes as "primary"
    btn_primary = InlineKeyboardButton(text="Primary", callback_data="cb_1", style=ButtonStyle.PRIMARY)
    dump_primary = btn_primary.model_dump(exclude_none=True)
    assert dump_primary["style"] == "primary"

    # 2. success serializes as "success"
    btn_success = InlineKeyboardButton(text="Success", callback_data="cb_2", style=ButtonStyle.SUCCESS)
    dump_success = btn_success.model_dump(exclude_none=True)
    assert dump_success["style"] == "success"

    # 3. danger serializes as "danger"
    btn_danger = InlineKeyboardButton(text="Danger", callback_data="cb_3", style=ButtonStyle.DANGER)
    dump_danger = btn_danger.model_dump(exclude_none=True)
    assert dump_danger["style"] == "danger"

    # 4. neutral omits style
    btn_neutral = InlineKeyboardButton(text="Neutral", callback_data="cb_4")
    dump_neutral = btn_neutral.model_dump(exclude_none=True)
    assert "style" not in dump_neutral

def test_main_menu_shop_style():
    class MockBtn:
        def __init__(self, action_key, row, enabled=True):
            self.action_key = action_key
            self.is_enabled = enabled
            self.owner_only = False
            self.label_en = action_key
            self.row_order = row
            self.column_order = 1
            self.id = 1

    btns = [MockBtn("shop", 1), MockBtn("wallet", 2), MockBtn("terms", 3)]
    kb = main_menu(255, btns, "en")

    shop_btn = kb.inline_keyboard[0][0]
    assert shop_btn.callback_data == "shop"
    assert shop_btn.style == ButtonStyle.SUCCESS

    wallet_btn = kb.inline_keyboard[1][0]
    assert wallet_btn.style is None

def test_purchase_flow_styles():
    # item_info
    kb = item_info("Item", "back", stock=10, item_id=1)

    # Find Continue button
    continue_btn = None
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.callback_data == "checkout:1":
                continue_btn = btn
                break

    assert continue_btn is not None
    assert continue_btn.style == ButtonStyle.SUCCESS

    # checkout confirm
    kb2 = checkout_confirmation_keyboard(1, can_purchase=True)
    confirm_btn = kb2.inline_keyboard[0][0]
    assert confirm_btn.callback_data == "confirm_purchase:1"
    assert confirm_btn.style == ButtonStyle.SUCCESS

@pytest.mark.asyncio
async def test_goods_pagination_styles():
    class DummyPaginator:
        async def get_page(self, page):
            return [
                (1, "Available Item", 10),
                (2, "Out of Stock Item", 0),
                (3, "Unlimited Item", -1)
            ]
        async def get_total_pages(self):
            return 1

    stock_map = {1: 10, 2: 0, 3: -1}

    kb = await lazy_paginated_keyboard(
        paginator=DummyPaginator(),
        item_text=lambda item: item[1],
        item_callback=lambda item: f"itm:{item[0]}",
        item_style=lambda item: ButtonStyle.DANGER if stock_map.get(item[0], 0) == 0 else ButtonStyle.PRIMARY,
        back_cb="back_to_menu"
    )

    btn_avail = kb.inline_keyboard[0][0]
    assert btn_avail.style == ButtonStyle.PRIMARY
    assert btn_avail.callback_data == "itm:1"

    btn_oos = kb.inline_keyboard[1][0]
    assert btn_oos.style == ButtonStyle.DANGER
    assert btn_oos.callback_data == "itm:2"

    btn_unlim = kb.inline_keyboard[2][0]
    assert btn_unlim.style == ButtonStyle.PRIMARY
    assert btn_unlim.callback_data == "itm:3"

    # Back button should be neutral
    btn_back = kb.inline_keyboard[3][0]
    assert btn_back.callback_data == "back_to_menu"
    assert btn_back.style is None
