from bot.misc.utils import answer_callback_safe
from decimal import Decimal
from functools import partial

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

from bot.database.methods import (
    get_bought_item_info, check_value, query_categories, query_user_bought_items, get_item_info_cached,
    select_item_values_amount_cached
)
from bot.database.methods.read import (
    get_item_avg_rating, has_purchased_item, validate_promo_for_item,
    get_user_review, invalidate_rating_cache, get_item_info,
    get_store_settings, get_category_by_id,
)
from bot.database.methods.create import create_review
from bot.database.methods.lazy_queries import query_item_reviews
from bot.database.methods.transactions import redeem_balance_promo
from bot.database.methods.audit import log_audit
from bot.misc.utils import safe_edit_or_send
from bot.keyboards import item_info, back, lazy_paginated_keyboard
from bot.keyboards.inline import simple_buttons, rating_keyboard
from bot.i18n import localize
from bot.misc import EnvKeys, LazyPaginator
from bot.misc.metrics import get_metrics
from bot.states import ShopStates
from bot.states.review_state import ReviewFSM
from bot.states.promo_state import PromoFSM

router = Router()


# --- Shared helper: render item page ---

async def _render_item_page(target, state: FSMContext, item_name: str, back_data: str = None, user_id: int = None):
    """
    Render the item detail page for quantity selection.
    """
    data = await state.get_data()
    if not back_data:
        back_data = data.get('item_back_data', 'gp_0')

    current_quantity = data.get('item_quantity', 1)

    item_info_data = await get_item_info_cached(item_name)
    if not item_info_data:
        if isinstance(target, CallbackQuery):
            await target.answer(localize("shop.item.not_found"), show_alert=True)
        else:
            await target.answer(localize("shop.item.not_found"))
        return

    quantity = await select_item_values_amount_cached(item_name)
    has_infinite = await check_value(item_name)
    stock = -1 if has_infinite else quantity

    if stock == 0:
        quantity_line = "📦 <b>Stock Status:</b> ❌ Out of Stock"
    else:
        quantity_line = (
            f"📦 <b>Available Stock:</b> ♾ Unlimited"
            if has_infinite
            else f"📦 <b>Available Stock:</b> {quantity}"
        )

    reviews_enabled = EnvKeys.REVIEWS_ENABLED == "1"
    avg_rating = None
    review_count_val = 0

    if reviews_enabled:
        avg_rating = await get_item_avg_rating(item_name)
        review_count_val = await query_item_reviews(item_name, count_only=True)

    unit_price = Decimal(str(item_info_data["price"]))
    total_price = unit_price * current_quantity

    price_line = (
        f"💵 <b>Unit Price:</b> {unit_price} {EnvKeys.PAY_CURRENCY}\n"
        f"{quantity_line}\n"
        f"🔢 <b>Selected Quantity:</b> {current_quantity}\n"
        f"💰 <b>Total:</b> {total_price} {EnvKeys.PAY_CURRENCY}"
    )

    item_id = data.get('item_id')
    markup = item_info(
        item_name, back_data,
        avg_rating=avg_rating, review_count=review_count_val,
        has_purchased=False, applied_promo=None,
        reviews_enabled=reviews_enabled, quantity=current_quantity,
        stock=stock, item_id=item_id,
    )

    text_lines = [
        f"📦 <b>{item_name}</b>",
        f"📝 {item_info_data['description']}",
        "",
        price_line,
    ]
    if reviews_enabled and avg_rating is not None:
        text_lines.append("")
        text_lines.append(localize("review.avg_rating", rating=avg_rating, count=review_count_val))

    text = "\n".join(text_lines)

    try:
        if hasattr(target, 'message') and hasattr(target.message, 'edit_text'):
            await safe_edit_or_send(target, text, reply_markup=markup)
        else:
            await target.answer(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


# --- Shop / categories / items ---

from bot.database.methods.lazy_queries import check_category_has_subcategories, get_category_parent_id

@router.callback_query(F.data == "shop")
async def shop_callback_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    from bot.handlers.user.main import delete_main_menu_hero_safe
    await delete_main_menu_hero_safe(call.bot, call.message.chat.id, call.from_user.id)
    """
    Show list of shop top-level categories.
    """
    metrics = get_metrics()
    if metrics:
        metrics.track_conversion("purchase_funnel", "view_shop", call.from_user.id)

    await _render_category_page(call, state, parent_id=None, page=0)


@router.callback_query(F.data.startswith('cpage:'))
async def navigate_categories(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """
    Pagination across shop categories.
    Format: cpage:{parent_id_or_None}:{page}
    """
    parts = call.data.split(':')
    parent_id_str = parts[1]
    parent_id = int(parent_id_str) if parent_id_str != 'None' else None
    page = int(parts[2]) if len(parts) > 2 else 0

    await _render_category_page(call, state, parent_id, page)
async def _edit_message_safe(call: CallbackQuery, message, text: str, reply_markup):
    await answer_callback_safe(call)
    await safe_edit_or_send(call, text, reply_markup=reply_markup)



async def _render_category_page(call: CallbackQuery, state: FSMContext, parent_id: int | None, page: int):
    await answer_callback_safe(call)
    paginator = LazyPaginator(partial(query_categories, parent_id), per_page=10)
    page_items = await paginator.get_page(page)

    settings = await get_store_settings()

    if parent_id is None:
        back_cb = "back_to_menu"

        # Root shop text
        title = settings.shop_root_title if settings and settings.shop_root_title else localize("shop.categories.title")
        description = settings.shop_root_description if settings and settings.shop_root_description else ""

        display_text = f"<b>{title}</b>\n\n{description}".strip()
    else:
        grandparent_id = await get_category_parent_id(parent_id)
        back_cb = f"cpage:{grandparent_id}:0" if grandparent_id is not None else "cpage:None:0"

        # Subcategory text
        cat_info = await get_category_by_id(parent_id)
        if cat_info:
            display_text = f"<b>{cat_info['name']}</b>"
            if cat_info.get("description"):
                display_text += f"\n\n{cat_info['description']}"
        else:
            display_text = localize("shop.categories.title")

    # determine row_width based on settings and context
    root_columns = (
        settings.root_category_columns
        if settings and settings.root_category_columns in (1, 2)
        else 1
    )
    subcategory_columns = (
        settings.subcategory_columns
        if settings and settings.subcategory_columns in (1, 2)
        else 2
    )
    row_width = root_columns if parent_id is None else subcategory_columns

    # item is (id, name)
    markup = await lazy_paginated_keyboard(
        paginator=paginator,
        item_text=lambda cat: cat[1],
        item_callback=lambda cat: f"cat:{cat[0]}",
        page=page,
        back_cb=back_cb,
        nav_cb_prefix=f"cpage:{parent_id}:",
        row_width=row_width,
        home_cb="back_to_menu"
    )

    await _edit_message_safe(call, call.message, display_text, markup)
    await state.set_state(ShopStates.viewing_categories)


@router.callback_query(F.data.startswith('cat:'))
async def category_selected_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """
    Handle category selection.
    Format: cat:{category_id}
    """
    parts = call.data.split(':')
    cat_id = int(parts[1])

    has_subcats = await check_category_has_subcategories(cat_id)
    if has_subcats:
        return await _render_category_page(call, state, parent_id=cat_id, page=0)

    return await _render_goods_page(call, state, cat_id, page=0)


async def _render_goods_page(call: CallbackQuery, state: FSMContext, category_id: int, page: int):
    await answer_callback_safe(call)
    await state.update_data(current_category_id=category_id)

    from bot.database.methods.lazy_queries import query_items_in_category
    query_func = partial(query_items_in_category, category_id)
    paginator = LazyPaginator(query_func, per_page=10)

    page_items = await paginator.get_page(page)
    item_ids = [item[0] for item in page_items]
    from bot.database.methods.read import get_stock_for_items
    stock_by_item_id = await get_stock_for_items(item_ids)

    items_index = {item[0]: i for i, item in enumerate(page_items)}
    await state.update_data(goods_page_items=list(page_items))

    parent_id = await get_category_parent_id(category_id)
    back_cb = f"cpage:{parent_id}:0" if parent_id is not None else "cpage:None:0"

    # Category text
    cat_info = await get_category_by_id(category_id)
    if cat_info:
        display_text = f"<b>{cat_info['name']}</b>"
        if cat_info.get("description"):
            display_text += f"\n\n{cat_info['description']}"
    else:
        display_text = localize("shop.goods.choose")

    settings = await get_store_settings()
    product_columns = (
        settings.product_columns
        if settings and settings.product_columns in (1, 2)
        else 1
    )

    markup = await lazy_paginated_keyboard(
        paginator=paginator,
        item_text=lambda item: f"❌ {item[1]} — OUT OF STOCK" if stock_by_item_id.get(item[0], 0) == 0 else item[1],
        item_callback=lambda item: f"itm:{items_index[item[0]]}:{page}",
        page=page,
        back_cb=back_cb,
        nav_cb_prefix="gp_",
        row_width=product_columns
    )

    await _edit_message_safe(call, call.message, display_text, markup)
    await state.set_state(ShopStates.viewing_goods)


@router.callback_query(F.data.startswith('gp_'), ShopStates.viewing_goods)
async def navigate_goods(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """
    Pagination for items inside selected category.
    Format: gp_{page}
    """
    current_index = int(call.data[3:])
    data = await state.get_data()
    category_id = data.get('current_category_id')
    if category_id is None:
        await answer_callback_safe(call, localize("shop.item.not_found"), show_alert=True)
        return
    await _render_goods_page(call, state, category_id, page=current_index)


@router.callback_query(F.data.startswith('itm:'))
async def item_info_callback_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """
    Show detailed information about the item.
    Format: itm:{index}:{page}
    """
    parts = call.data.split(':')
    idx = int(parts[1])
    goods_page = int(parts[2]) if len(parts) > 2 else 0

    data = await state.get_data()
    goods_page_items = data.get('goods_page_items', [])

    if idx < 0 or idx >= len(goods_page_items):
        await answer_callback_safe(call, localize("shop.item.not_found"), show_alert=True)
        return

    item_tuple = goods_page_items[idx]
    if isinstance(item_tuple, (list, tuple)):
        item_id = item_tuple[0]
        item_name = item_tuple[1]
    else:
        item_name = item_tuple
        item_id = None

    back_data = f"gp_{goods_page}"

    metrics = get_metrics()
    if metrics:
        metrics.track_conversion("purchase_funnel", "view_item", call.from_user.id)

    # Save item name, back_data and reset quantity in state
    await state.update_data(
        csrf_item=item_name,
        item_id=item_id,
        item_back_data=back_data,
        item_quantity=1
    )

    await _render_item_page(call, state, item_name, back_data, user_id=call.from_user.id)

# --- Quantity Selection ---

@router.callback_query(F.data.startswith("qty:quick:"))
async def qty_quick_handler(call: CallbackQuery, state: FSMContext):
    parts = call.data.split(':')
    item_id_str = parts[2]
    requested_qty = int(parts[3])

    data = await state.get_data()
    item_name = data.get('csrf_item')

    # Validation
    if not item_name or str(data.get('item_id')) != item_id_str:
        await answer_callback_safe(call, localize("shop.item.not_found"), show_alert=True)
        return

    from bot.database.methods import check_value, select_item_values_amount_cached
    is_infinity = await check_value(item_name)
    stock = await select_item_values_amount_cached(item_name)

    if not is_infinity and requested_qty > stock:
        await answer_callback_safe(call, f"Only {stock} items are currently available.", show_alert=True)
        return

    await answer_callback_safe(call)
    await state.update_data(item_quantity=requested_qty)
    await _render_item_page(call, state, item_name, user_id=call.from_user.id)

@router.callback_query(F.data.startswith("qty:keypad:"))
async def qty_keypad_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    item_id_str = call.data.split(':')[2]
    data = await state.get_data()
    item_name = data.get('csrf_item')

    if not item_name or str(data.get('item_id')) != item_id_str:
        await safe_edit_or_send(call, localize("shop.item.not_found"), reply_markup=back("back_to_menu"))
        return

    await state.update_data(keypad_value='0')
    await _render_keypad_page(call, state, item_name, '0', item_id_str)

async def _render_keypad_page(call: CallbackQuery, state: FSMContext, item_name: str, keypad_value: str, item_id_str: str):
    from bot.keyboards.inline import numeric_keypad
    from bot.database.methods import get_item_info_cached, check_value, select_item_values_amount_cached

    item_info_data = await get_item_info_cached(item_name)
    if not item_info_data:
        return

    unit_price = Decimal(str(item_info_data["price"]))
    qty = int(keypad_value) if keypad_value else 0
    total_price = unit_price * qty

    is_infinity = await check_value(item_name)
    stock = await select_item_values_amount_cached(item_name)
    stock_line = "♾ Unlimited" if is_infinity else str(stock)

    text = (
        f"📦 <b>{item_name}</b>\n\n"
        f"💵 <b>Unit Price:</b> {unit_price} {EnvKeys.PAY_CURRENCY}\n"
        f"📦 <b>Available Stock:</b> {stock_line}\n\n"
        f"🔢 <b>Entered Quantity:</b> {qty}\n"
        f"💰 <b>Total:</b> {total_price} {EnvKeys.PAY_CURRENCY}"
    )

    await safe_edit_or_send(call, text, reply_markup=numeric_keypad(int(item_id_str)))

@router.callback_query(F.data.startswith("qty:digit:"))
async def qty_digit_handler(call: CallbackQuery, state: FSMContext):
    parts = call.data.split(':')
    item_id_str = parts[2]
    digit = parts[3]

    data = await state.get_data()
    item_name = data.get('csrf_item')

    if not item_name or str(data.get('item_id')) != item_id_str:
        await answer_callback_safe(call, localize("shop.item.not_found"), show_alert=True)
        return

    keypad_value = data.get('keypad_value', '0')
    if keypad_value == '0':
        new_value = digit
    else:
        new_value = keypad_value + digit

    if len(new_value) > 4:
        await answer_callback_safe(call, "Maximum 4 digits allowed.", show_alert=True)
        return

    await answer_callback_safe(call)
    await state.update_data(keypad_value=new_value)
    await _render_keypad_page(call, state, item_name, new_value, item_id_str)

@router.callback_query(F.data.startswith("qty:backspace:"))
async def qty_backspace_handler(call: CallbackQuery, state: FSMContext):
    item_id_str = call.data.split(':')[2]
    data = await state.get_data()
    item_name = data.get('csrf_item')

    if not item_name or str(data.get('item_id')) != item_id_str:
        await answer_callback_safe(call, localize("shop.item.not_found"), show_alert=True)
        return

    keypad_value = data.get('keypad_value', '0')
    new_value = keypad_value[:-1]
    if not new_value:
        new_value = '0'

    await answer_callback_safe(call)
    await state.update_data(keypad_value=new_value)
    await _render_keypad_page(call, state, item_name, new_value, item_id_str)

@router.callback_query(F.data.startswith("qty:clear:"))
async def qty_clear_handler(call: CallbackQuery, state: FSMContext):
    item_id_str = call.data.split(':')[2]
    data = await state.get_data()
    item_name = data.get('csrf_item')

    if not item_name or str(data.get('item_id')) != item_id_str:
        await answer_callback_safe(call, localize("shop.item.not_found"), show_alert=True)
        return

    await answer_callback_safe(call)
    await state.update_data(keypad_value='0')
    await _render_keypad_page(call, state, item_name, '0', item_id_str)

@router.callback_query(F.data.startswith("qty:keypad_continue:"))
async def qty_keypad_continue_handler(call: CallbackQuery, state: FSMContext):
    item_id_str = call.data.split(':')[2]
    data = await state.get_data()
    item_name = data.get('csrf_item')

    if not item_name or str(data.get('item_id')) != item_id_str:
        await answer_callback_safe(call, localize("shop.item.not_found"), show_alert=True)
        return

    qty_str = data.get('keypad_value', '0')
    qty = int(qty_str) if qty_str else 0

    if qty <= 0:
        await answer_callback_safe(call, "Quantity must be greater than zero.", show_alert=True)
        return

    from bot.database.methods import check_value, select_item_values_amount_cached
    is_infinity = await check_value(item_name)
    stock = await select_item_values_amount_cached(item_name)

    if not is_infinity and qty > stock:
        await answer_callback_safe(call, f"Maximum available quantity is {stock}.", show_alert=True)
        return

    if is_infinity and qty > 1000:
        await answer_callback_safe(call, "Maximum 1000 allowed for unlimited products.", show_alert=True)
        return

    await answer_callback_safe(call)
    await state.update_data(item_quantity=qty, keypad_value='0')
    await _render_checkout_page(call, state, item_name, item_id_str, user_id=call.from_user.id)

@router.callback_query(F.data.startswith("qty:keypad_back:"))
async def qty_keypad_back_handler(call: CallbackQuery, state: FSMContext):
    item_id_str = call.data.split(':')[2]
    data = await state.get_data()
    item_name = data.get('csrf_item')

    if not item_name or str(data.get('item_id')) != item_id_str:
        await answer_callback_safe(call, localize("shop.item.not_found"), show_alert=True)
        return

    await answer_callback_safe(call)
    await state.update_data(keypad_value='0')
    await _render_item_page(call, state, item_name, user_id=call.from_user.id)


# --- Checkout & Promo Code Flow ---

@router.callback_query(F.data.startswith("checkout:"))
async def checkout_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    item_id_str = call.data.split(':')[1]

    data = await state.get_data()
    item_name = data.get('csrf_item')

    if not item_name or str(data.get('item_id')) != item_id_str:
        await safe_edit_or_send(call, localize("shop.item.not_found"), reply_markup=back("back_to_menu"))
        return

    await _render_checkout_page(call, state, item_name, item_id_str, user_id=call.from_user.id)

async def _render_checkout_page(call: CallbackQuery, state: FSMContext, item_name: str, item_id_str: str, user_id: int):
    from bot.keyboards.inline import checkout_confirmation_keyboard
    from bot.database.methods import get_item_info_cached, check_user_cached
    from bot.database.methods import check_value, select_item_values_amount_cached
    from bot.database.methods.read import validate_promo_for_item

    data = await state.get_data()
    current_quantity = data.get('item_quantity', 1)
    applied_promo = data.get('applied_promo')

    item_info_data = await get_item_info_cached(item_name)
    if not item_info_data:
        return

    is_infinity = await check_value(item_name)
    stock = await select_item_values_amount_cached(item_name)

    if not is_infinity and current_quantity > stock:
        current_quantity = stock
        await state.update_data(item_quantity=stock)

    unit_price = Decimal(str(item_info_data["price"]))
    subtotal = unit_price * current_quantity
    discount = Decimal("0.00")

    if applied_promo:
        valid, _, promo_data = await validate_promo_for_item(applied_promo, item_name, user_id)
        if valid:
            if promo_data.get('discount_type') == 'percent':
                discount_per_unit = unit_price * Decimal(str(promo_data.get('discount_value', 0))) / 100
            else:
                discount_per_unit = min(Decimal(str(promo_data.get('discount_value', 0))), unit_price)
            discount = (discount_per_unit * current_quantity).quantize(Decimal("0.01"))
        else:
            applied_promo = None
            await state.update_data(applied_promo=None)

    total = subtotal - discount

    user_info = await check_user_cached(user_id)
    balance_dec = Decimal(str(user_info.get('balance', 0))) if user_info else Decimal("0.00")
    balance_after = balance_dec - total

    can_purchase = balance_after >= 0

    text = (
        f"🛒 <b>Confirm Your Order</b>\n\n"
        f"📦 <b>Product:</b> {item_name}\n"
        f"💵 <b>Unit Price:</b> {unit_price} {EnvKeys.PAY_CURRENCY}\n"
        f"🔢 <b>Quantity:</b> {current_quantity}\n"
        f"💰 <b>Subtotal:</b> {subtotal} {EnvKeys.PAY_CURRENCY}\n"
        f"🏷 <b>Discount:</b> {discount} {EnvKeys.PAY_CURRENCY}\n"
        f"💳 <b>Total:</b> {total} {EnvKeys.PAY_CURRENCY}\n\n"
        f"👛 <b>Wallet Balance:</b> {balance_dec} {EnvKeys.PAY_CURRENCY}\n"
    )

    if can_purchase:
        text += f"📉 <b>Balance After Purchase:</b> {balance_after} {EnvKeys.PAY_CURRENCY}"
    else:
        text += f"❌ <b>Insufficient Balance</b>"

    await safe_edit_or_send(call, text, reply_markup=checkout_confirmation_keyboard(int(item_id_str), can_purchase, applied_promo))

@router.callback_query(F.data.startswith("checkout_change_qty:"))
async def checkout_change_qty_handler(call: CallbackQuery, state: FSMContext):
    item_id_str = call.data.split(':')[1]
    data = await state.get_data()
    item_name = data.get('csrf_item')

    if not item_name or str(data.get('item_id')) != item_id_str:
        await answer_callback_safe(call, localize("shop.item.not_found"), show_alert=True)
        return

    await answer_callback_safe(call)
    await _render_item_page(call, state, item_name, user_id=call.from_user.id)

@router.callback_query(F.data.startswith("apply_promo:"))
async def apply_promo_handler(call: CallbackQuery, state: FSMContext):
    item_id_str = call.data.split(':')[1]
    data = await state.get_data()
    item_name = data.get('csrf_item')

    if not item_name or str(data.get('item_id')) != item_id_str:
        await answer_callback_safe(call, localize("shop.item.not_found"), show_alert=True)
        return

    await answer_callback_safe(call)
    await safe_edit_or_send(call, localize("promo.enter_code"), reply_markup=back(f"checkout:{item_id_str}"))
    await state.update_data(awaiting_promo=True)

@router.callback_query(F.data.startswith("remove_promo:"))
async def remove_promo_handler(call: CallbackQuery, state: FSMContext):
    item_id_str = call.data.split(':')[1]
    data = await state.get_data()
    item_name = data.get('csrf_item')

    if not item_name or str(data.get('item_id')) != item_id_str:
        await answer_callback_safe(call, localize("shop.item.not_found"), show_alert=True)
        return

    await answer_callback_safe(call)
    await state.update_data(applied_promo=None)
    await _render_checkout_page(call, state, item_name, item_id_str, user_id=call.from_user.id)


# --- Balance Promo Redemption (from profile) ---

@router.callback_query(F.data == "redeem_promo")
async def redeem_promo_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    from bot.handlers.user.main import delete_main_menu_hero_safe
    await delete_main_menu_hero_safe(call.bot, call.message.chat.id, call.from_user.id)
    await safe_edit_or_send(call, localize("promo.enter_redeem_code"), reply_markup=back("profile"))
    await state.set_state(PromoFSM.waiting_redeem_code)


@router.message(PromoFSM.waiting_redeem_code, F.text)
async def redeem_promo_code_handler(message: Message, state: FSMContext):
    code = (message.text or "").strip().upper()
    success, error_key, amount = await redeem_balance_promo(code, message.from_user.id)

    if success:
        await message.answer(
            localize("promo.balance_redeemed", code=code, amount=amount, currency=EnvKeys.PAY_CURRENCY),
            reply_markup=back("profile"),
        )
        await log_audit(
            "promo_redeem", user_id=message.from_user.id,
            resource_type="PromoCode", resource_id=code,
        )
    else:
        await message.answer(localize(error_key), reply_markup=back("profile"))

    await state.clear()


# --- Review Handlers ---

@router.callback_query(F.data.startswith("review:"))
async def start_review_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    if EnvKeys.REVIEWS_ENABLED != "1":
        await answer_callback_safe(call, localize("review.disabled"), show_alert=True)
        return

    item_name = call.data.split(":", 1)[1]

    # Check if user purchased the item
    purchased = await has_purchased_item(call.from_user.id, item_name)
    if not purchased:
        await answer_callback_safe(call, localize("review.not_purchased"), show_alert=True)
        return

    # Check if already reviewed
    existing = await get_user_review(call.from_user.id, item_name)
    if existing:
        await answer_callback_safe(call, localize("review.already_exists"), show_alert=True)
        return

    await state.update_data(review_item_name=item_name)
    await safe_edit_or_send(call,
        localize("review.prompt_rating", name=item_name),
        reply_markup=rating_keyboard(item_name),
    )
    await state.set_state(ReviewFSM.waiting_rating)


@router.callback_query(F.data.startswith("rating:"), ReviewFSM.waiting_rating)
async def receive_rating_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    rating = int(call.data.split(":")[1])
    await state.update_data(review_rating=rating)

    buttons = [
        (localize("btn.skip_review_text"), "skip_review_text"),
        (localize("btn.back"), "back_to_menu"),
    ]
    await safe_edit_or_send(call,
        localize("review.prompt_text"),
        reply_markup=simple_buttons(buttons),
    )
    await state.set_state(ReviewFSM.waiting_text)


@router.callback_query(F.data == "skip_review_text", ReviewFSM.waiting_text)
async def skip_review_text_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    data = await state.get_data()
    item_name = data.get('review_item_name')
    rating = data.get('review_rating')

    await create_review(call.from_user.id, item_name, rating)
    await invalidate_rating_cache(item_name)
    await safe_edit_or_send(call, localize("review.created"), reply_markup=back("back_to_menu"))
    await state.clear()


@router.message(ReviewFSM.waiting_text, F.text)
async def receive_review_text_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    item_name = data.get('review_item_name')
    rating = data.get('review_rating')
    text = (message.text or "")[:500].strip()

    await create_review(message.from_user.id, item_name, rating, text)
    await invalidate_rating_cache(item_name)
    await message.answer(localize("review.created"), reply_markup=back("back_to_menu"))
    await state.clear()





# --- View Reviews ---

@router.callback_query(F.data.startswith("reviews:"))
async def view_reviews_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    if EnvKeys.REVIEWS_ENABLED != "1":
        await answer_callback_safe(call, localize("review.disabled"), show_alert=True)
        return

    parts = call.data.split(":")
    item_name = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 0

    paginator = LazyPaginator(
        partial(query_item_reviews, item_name),
        per_page=5,
    )

    reviews = await paginator.get_page(page)
    total_pages = await paginator.get_total_pages()

    if not reviews:
        await safe_edit_or_send(call,
            localize("review.list_empty"),
            reply_markup=back("back_to_item"),
        )
        return

    lines = [localize("review.list_title", name=item_name), ""]
    for r in reviews:
        if r.get('text'):
            lines.append(localize("review.item", rating=r['rating'], text=r['text'][:100]))
        else:
            lines.append(localize("review.item_no_text", rating=r['rating']))

    # Navigation
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    kb = InlineKeyboardBuilder()
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"reviews:{item_name}:{page - 1}"))
    if total_pages > 1:
        nav_buttons.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"reviews:{item_name}:{page + 1}"))
    if nav_buttons:
        kb.row(*nav_buttons)
    kb.row(InlineKeyboardButton(text=localize("btn.back"), callback_data="back_to_item"))

    await safe_edit_or_send(call, "\n".join(lines), reply_markup=kb.as_markup())


# --- Bought items ---

@router.callback_query(F.data == "bought_items")
async def bought_items_callback_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """
    Show list of user's purchased items with lazy loading.
    """
    user_id = call.from_user.id

    # Create paginator for user's bought items
    query_func = partial(query_user_bought_items, user_id)
    paginator = LazyPaginator(query_func, per_page=10)

    markup = await lazy_paginated_keyboard(
        paginator=paginator,
        item_text=lambda item: item.item_name,
        item_callback=lambda item: f"bought-item:{item.id}:bought-goods-page_user_0",
        page=0,
        back_cb="profile",
        nav_cb_prefix="bought-goods-page_user_"
    )

    await safe_edit_or_send(call, localize("purchases.title"), reply_markup=markup)

    # Save paginator state
    await state.update_data(bought_items_paginator=paginator.get_state())


@router.callback_query(F.data.startswith('bought-goods-page_'))
async def navigate_bought_items(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """
    Pagination for user's purchased items with lazy loading.
    Format: 'bought-goods-page_{data}_{page}', where data = 'user' or user_id.
    """
    parts = call.data.split('_')
    if len(parts) < 3:
        await answer_callback_safe(call, localize("purchases.pagination.invalid"))
        return

    data_type = parts[1]
    try:
        current_index = int(parts[2])
    except ValueError:
        current_index = 0

    if data_type == 'user':
        user_id = call.from_user.id
        back_cb = 'profile'
        pre_back = f'bought-goods-page_user_{current_index}'
    else:
        user_id = int(data_type)
        back_cb = f'check-user_{data_type}'
        pre_back = f'bought-goods-page_{data_type}_{current_index}'

    # Get saved state
    data = await state.get_data()
    paginator_state = data.get('bought_items_paginator')

    # Create paginator with cached state
    query_func = partial(query_user_bought_items, user_id)
    paginator = LazyPaginator(query_func, per_page=10, state=paginator_state)

    markup = await lazy_paginated_keyboard(
        paginator=paginator,
        item_text=lambda item: item.item_name,
        item_callback=lambda item: f"bought-item:{item.id}:{pre_back}",
        page=current_index,
        back_cb=back_cb,
        nav_cb_prefix=f"bought-goods-page_{data_type}_"
    )

    await safe_edit_or_send(call, localize("purchases.title"), reply_markup=markup)

    # Update state
    await state.update_data(bought_items_paginator=paginator.get_state())


@router.callback_query(F.data.startswith('bought-item:'))
async def bought_item_info_callback_handler(call: CallbackQuery):
    await answer_callback_safe(call)
    """
    Show details for a purchased item.
    """
    trash, item_id, back_data = call.data.split(':', 2)
    item = await get_bought_item_info(int(item_id))
    if not item:
        await answer_callback_safe(call, localize("purchases.item.not_found"), show_alert=True)
        return

    text = "\n".join([
        localize("purchases.item.name", name=item["item_name"]),
        localize("purchases.item.price", amount=item["price"], currency=EnvKeys.PAY_CURRENCY),
        localize("purchases.item.datetime", dt=item["bought_datetime"]),
        localize("purchases.item.unique_id", uid=item["unique_id"]),
        localize("purchases.item.value", value=item["value"]),
    ])
    await safe_edit_or_send(call, text, parse_mode='HTML', reply_markup=back(back_data))


# --- Stock Refresh Handlers ---

@router.callback_query(F.data.startswith('refresh:goods:'))
async def refresh_goods_handler(call: CallbackQuery, state: FSMContext):
    parts = call.data.split(':')
    category_id = int(parts[2])
    page = int(parts[3])
    
    await _render_goods_page(call, state, category_id, page)

@router.callback_query(F.data.startswith('refresh:item:'))
async def refresh_item_handler(call: CallbackQuery, state: FSMContext):
    item_id_str = call.data.split(':')[2]
    data = await state.get_data()
    item_name = data.get('csrf_item')
    
    if not item_name or str(data.get('item_id')) != item_id_str:
        await safe_edit_or_send(call, localize("shop.item.not_found"), reply_markup=back("back_to_menu"))
        return
        
    from bot.database.methods.read import invalidate_item_cache, select_item_values_amount, check_value
    await invalidate_item_cache(item_name)
    
    quantity = await select_item_values_amount(item_name)
    has_infinite = await check_value(item_name)
    stock = -1 if has_infinite else quantity
    
    current_quantity = data.get('item_quantity', 1)
    
    alert_text = None
    if stock == 0:
        await state.update_data(item_quantity=1, keypad_value='0')
        if current_quantity > 0:
            alert_text = "Stock changed. The item is now out of stock."
    elif stock != -1 and current_quantity > stock:
        await state.update_data(item_quantity=stock, keypad_value=str(stock))
        alert_text = f"Stock changed. Selected quantity was adjusted to {stock}."
        
    if alert_text:
        await answer_callback_safe(call, alert_text, show_alert=True)
    else:
        await answer_callback_safe(call)
        
    await _render_item_page(call, state, item_name, user_id=call.from_user.id)


# --- Post-Purchase Action Panel Handlers ---

@router.callback_query(F.data.startswith("buy_again:"))
async def buy_again_handler(call: CallbackQuery, state: FSMContext):
    item_id_str = call.data.split(':')[1]
    data = await state.get_data()
    item_name = data.get('csrf_item')
    
    if not item_name or str(data.get('item_id')) != item_id_str:
        await safe_edit_or_send(call, localize("shop.item.not_found"), reply_markup=back("back_to_menu"))
        return
        
    await answer_callback_safe(call)
    await state.update_data(item_quantity=1, keypad_value='0')
    await _render_item_page(call, state, item_name, user_id=call.from_user.id)

@router.callback_query(F.data.startswith("support_order:"))
async def support_order_handler(call: CallbackQuery):
    unique_id = call.data.split(':')[1]
    helper = EnvKeys.HELPER_ID
    if helper:
        text = (
            f"🆘 <b>Support for Order:</b> <code>{unique_id}</code>\n\n"
            f"Please tap the order reference above to copy it, then forward it to our support team using the button below."
        )
        from bot.keyboards.inline import simple_buttons
        markup = simple_buttons([
            ("Contact Support", f"tg://user?id={helper}"),
            ("⬅️ Home", "back_to_menu")
        ])
        await safe_edit_or_send(call, text, reply_markup=markup, parse_mode='HTML')
    else:
        await answer_callback_safe(call, localize("support.not_set", default="Support not configured"), show_alert=True)





# --- Promo code text input (catch-all, must be AFTER state-specific message handlers) ---

@router.message(F.text)
async def promo_code_text_handler(message: Message, state: FSMContext):
    """Handle promo code text input when awaiting_promo is set."""
    data = await state.get_data()
    if not data.get('awaiting_promo'):
        return  # Not awaiting promo input — skip

    item_name = data.get('csrf_item')
    if not item_name:
        await state.update_data(awaiting_promo=False)
        return

    code = (message.text or "").strip().upper()
    valid, error_key, promo_data = await validate_promo_for_item(code, item_name, message.from_user.id)

    item_id = data.get('item_id')
    if not valid:
        await message.answer(localize(error_key), reply_markup=back(f"checkout:{item_id}"))
        await state.update_data(awaiting_promo=False)
        return

    # Store promo data for discounted price display
    await state.update_data(
        applied_promo=code,
        applied_promo_data={
            'discount_type': promo_data.get('discount_type'),
            'discount_value': str(promo_data.get('discount_value', 0)),
        },
        awaiting_promo=False,
    )

    # Re-render checkout page with discounted price
    await _render_checkout_page(message, state, item_name, str(item_id), user_id=message.from_user.id)
