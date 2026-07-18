from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime

from bot.database import Database
from bot.database.methods.orders import (
    get_order_by_id, list_user_orders, count_user_orders, get_order_items, get_order_item_by_id
)
from bot.database.models.main import BoughtGoods, Goods, OrderCustomerInput
from sqlalchemy import select
from bot.i18n.main import localize, current_locale
from bot.misc.customer_fields import get_localized_label
from bot.misc.validators import sanitize_html
from bot.misc import EnvKeys

router = Router()

def get_status_emoji(status: str) -> str:
    mapping = {
        "pending": "⏳",
        "paid": "💳",
        "processing": "⚙️",
        "completed": "✅",
        "failed": "❌",
        "cancelled": "🚫",
        "refunded": "💵"
    }
    return mapping.get(status, "❓")

@router.callback_query(F.data.startswith("orders:list:"))
async def orders_list_handler(call: CallbackQuery, answer_callback: bool = True):
    page = int(call.data.split(":")[2])
    limit = 5
    offset = page * limit
    user_id = call.from_user.id

    async with Database().session() as s:
        total_orders = await count_user_orders(s, user_id)
        orders = await list_user_orders(s, user_id, limit=limit, offset=offset)

    if total_orders == 0:
        kb = InlineKeyboardBuilder()
        kb.button(text=localize("btn.back", default="🔙 Back"), callback_data="profile")
        kb.adjust(1)
        await call.message.edit_text(
            localize("orders.empty", default="📦 You don't have any orders yet."),
            reply_markup=kb.as_markup()
        )
        if answer_callback:
            await call.answer()
        return

    text = f"📦 <b>{localize('orders.my_orders', default='My Orders')}</b>\n\n"

    kb = InlineKeyboardBuilder()
    for order in orders:
        emoji = get_status_emoji(order.status)
        kb.row(InlineKeyboardBuilder().button(
            text=f"{emoji} {order.public_id}",
            callback_data=f"orders:view:{order.id}"
        ).as_markup().inline_keyboard[0][0])

    # Pagination controls
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardBuilder().button(text="⬅️ Previous", callback_data=f"orders:list:{page-1}").as_markup().inline_keyboard[0][0])

    total_pages = max(1, (total_orders + limit - 1) // limit)
    if total_pages > 0:
        nav_buttons.append(InlineKeyboardBuilder().button(text=f"{page+1}/{total_pages}", callback_data="dummy_button").as_markup().inline_keyboard[0][0])

    if offset + limit < total_orders:
        nav_buttons.append(InlineKeyboardBuilder().button(text="Next ➡️", callback_data=f"orders:list:{page+1}").as_markup().inline_keyboard[0][0])

    if nav_buttons:
        kb.row(*nav_buttons)

    kb.row(InlineKeyboardBuilder().button(text=localize("btn.back", default="🔙 Back"), callback_data="profile").as_markup().inline_keyboard[0][0])

    await call.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
    if answer_callback:
        await call.answer()

@router.callback_query(F.data.startswith("orders:view:"))
async def order_view_handler(call: CallbackQuery):
    parts = call.data.split(":")
    order_id = int(parts[2])
    origin = parts[3] if len(parts) > 3 else "m"
    user_id = call.from_user.id

    async with Database().session() as s:
        order = await get_order_by_id(s, order_id)
        if not order or order.user_id != user_id:
            await call.answer(localize("orders.not_found", default="Order not found."), show_alert=True)
            return

        items = await get_order_items(s, order.id)
        item = items[0] if items else None

        date_str = order.created_at.strftime("%Y-%m-%d %H:%M:%S")

        text = (
            f"📦 <b>Order Details</b>\n\n"
            f"🧾 <b>Order ID:</b> <code>{order.public_id}</code>\n"
            f"🔄 <b>Status:</b> {order.status.capitalize()} {get_status_emoji(order.status)}\n"
            f"🕒 <b>Date:</b> {date_str}\n\n"
        )

        if item:
            text += (
                f"🛍 <b>Product:</b> {sanitize_html(item.product_name_snapshot)}\n"
                f"🔢 <b>Quantity:</b> {item.quantity}\n"
                f"💵 <b>Unit Price:</b> {item.unit_price} {order.currency}\n"
            )

            if order.status == "processing":
                goods = (await s.execute(select(Goods).where(Goods.id == item.item_id))).scalar_one_or_none()
                if goods and goods.fulfillment_eta_minutes:
                    text += f"⏳ <b>ETA:</b> ~{goods.fulfillment_eta_minutes} mins\n"

            # Check for customer inputs
            inputs = (await s.execute(
                select(OrderCustomerInput).where(OrderCustomerInput.order_item_id == item.id).order_by(OrderCustomerInput.id.asc())
            )).scalars().all()

            if inputs:
                text += f"\n📝 <b>Submitted Details:</b>\n"
                language = current_locale.get()
                if item.quantity == 1:
                    for inp in inputs:
                        label = sanitize_html(get_localized_label(inp.label_i18n_snapshot, language) or inp.field_key_snapshot)
                        if inp.field_type_snapshot == 'secret':
                            val = "Submitted ✅"
                        elif '@' in inp.masked_preview:
                            val = "*@" + sanitize_html(inp.masked_preview.split('@')[-1])
                        else:
                            val = sanitize_html(inp.masked_preview)
                        text += f"{label}:\n{val}\n\n"
                else:
                    by_unit = {}
                    for inp in inputs:
                        idx = inp.unit_index if inp.scope_snapshot == 'per_unit' else 0
                        by_unit.setdefault(idx, []).append(inp)
                    if 0 in by_unit:
                        for inp in by_unit[0]:
                            label = sanitize_html(get_localized_label(inp.label_i18n_snapshot, language) or inp.field_key_snapshot)
                            if inp.field_type_snapshot == 'secret':
                                val = "Submitted ✅"
                            elif '@' in inp.masked_preview:
                                val = "*@" + sanitize_html(inp.masked_preview.split('@')[-1])
                            else:
                                val = sanitize_html(inp.masked_preview)
                            text += f"{label}:\n{val}\n\n"
                    for idx in sorted([k for k in by_unit.keys() if k != 0]):
                        text += f"<b>Item {idx}</b>\n"
                        for inp in by_unit[idx]:
                            label = sanitize_html(get_localized_label(inp.label_i18n_snapshot, language) or inp.field_key_snapshot)
                            if inp.field_type_snapshot == 'secret':
                                val = "Submitted ✅"
                            elif '@' in inp.masked_preview:
                                val = "*@" + sanitize_html(inp.masked_preview.split('@')[-1])
                            else:
                                val = sanitize_html(inp.masked_preview)
                            text += f"{label}: {val}\n"
                        text += "\n"

        if order.discount_total > 0:
            text += f"\n📉 <b>Discount:</b> {order.discount_total} {order.currency}"

        text += f"\n💰 <b>Total:</b> {order.total} {order.currency}"

        kb = InlineKeyboardBuilder()

        # View Delivered values if completed
        if item and item.fulfillment_status == "delivered":
            kb.button(text=localize("orders.view_delivered", default="🔑 View Delivered Items"), callback_data=f"orders:delivered:{order.id}")

        # Copy ID (fallback for non-mobile or older clients)
        kb.button(text=localize("orders.copy_id", default="📋 Copy Order ID"), callback_data=f"orders:copy:{order.id}")

        # Support
        if EnvKeys.HELPER_ID:
            kb.button(text=localize("orders.support", default="🆘 Support for This Order"), callback_data=f"support_order:{order.public_id}")

        # Buy Again
        if item:
            kb.button(text=localize("orders.buy_again", default="🔁 Buy Again"), callback_data=f"order_buy_again:{item.id}")

        if origin == "r":
            back_data = f"orders:receipt:{order.id}"
        elif origin == "a":
            back_data = f"orders:active:{order.id}"
        elif origin in ("v", "m", "c") and len(parts) > 4:
            source_id = parts[4]
            back_data = f"orders:source_back:{order.id}:{origin}:{source_id}"
        else:
            back_data = "orders:list:0"

        kb.button(text=localize("btn.back", default="🔙 Back"), callback_data=back_data)
        kb.adjust(1)

        await call.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
        await call.answer()

@router.callback_query(F.data.startswith("orders:copy:"))
async def order_copy_handler(call: CallbackQuery):
    order_id = int(call.data.split(":")[2])
    user_id = call.from_user.id

    async with Database().session() as s:
        order = await get_order_by_id(s, order_id)
        if not order or order.user_id != user_id:
            await call.answer("Order not found.", show_alert=True)
            return

    await call.answer(localize("orders.copied", default="Order ID copied!"), show_alert=False)
    # Some clients support sending the ID directly in a simple message that is easy to copy
    await call.message.answer(f"<code>{order.public_id}</code>", parse_mode="HTML")

@router.callback_query(F.data.startswith("orders:delivered:"))
async def order_delivered_handler(call: CallbackQuery):
    order_id = int(call.data.split(":")[2])
    user_id = call.from_user.id

    async with Database().session() as s:
        order = await get_order_by_id(s, order_id)
        if not order or order.user_id != user_id:
            await call.answer("Order not found.", show_alert=True)
            return

        bought_goods = (await s.execute(
            select(BoughtGoods).where(BoughtGoods.order_id == order.id).order_by(BoughtGoods.id.asc())
        )).scalars().all()

        if not bought_goods:
            await call.answer("No delivered items found for this order.", show_alert=True)
            return

        text = f"🔑 <b>Delivered Items for <code>{order.public_id}</code></b>\n\n<code>\n"
        for i, bg in enumerate(bought_goods, 1):
            text += f"{i}) {sanitize_html(bg.value)}\n"
        text += "</code>\n\n⚠️ Keep this message secure."

        kb = InlineKeyboardBuilder()
        kb.button(text=localize("btn.back", default="🔙 Back"), callback_data=f"orders:view:{order.id}")

        await call.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
        await call.answer()

from aiogram.fsm.context import FSMContext
from bot.database.methods.audit import log_audit

@router.callback_query(F.data.startswith("order_buy_again:"))
async def order_buy_again_handler(call: CallbackQuery, state: FSMContext):
    try:
        order_item_id = int(call.data.split(":")[1])
    except (IndexError, ValueError):
        await call.answer("Invalid callback data.", show_alert=True)
        return

    user_id = call.from_user.id

    async with Database().session() as s:
        order_item = await get_order_item_by_id(s, order_item_id)
        if not order_item:
            await call.answer("Order item not found.", show_alert=True)
            return

        order = await get_order_by_id(s, order_item.order_id)
        if not order or order.user_id != user_id:
            await log_audit("order_buy_again_forbidden", user_id=user_id, resource_id=order_item_id, level="WARNING")
            await call.answer("Order not found.", show_alert=True)
            return

        if not order_item.item_id:
            await log_audit("order_buy_again_missing_item", user_id=user_id, resource_id=order_item_id, level="INFO")
            await call.answer(localize("shop.item.not_found", default="Product is no longer available."), show_alert=True)
            return

        goods = (await s.execute(select(Goods).where(Goods.id == order_item.item_id))).scalar_one_or_none()
        if not goods:
            await log_audit("order_buy_again_missing_item", user_id=user_id, resource_id=order_item_id, level="INFO")
            await call.answer(localize("shop.item.not_found", default="Product is no longer available."), show_alert=True)
            return

    await log_audit("order_buy_again_opened", user_id=user_id, resource_id=order_item_id, level="INFO")

    # Open the canonical product page using the item's internal ID.
    from bot.handlers.user.shop_and_goods import _render_item_page_by_id
    await _render_item_page_by_id(call, state, goods.id, back_data='menu', user_id=user_id, send_new=True)
    await call.answer()

@router.callback_query(F.data.startswith("orders:submitted:"))
async def order_submitted_handler(call: CallbackQuery):
    try:
        order_id = int(call.data.split(":")[2])
    except (IndexError, ValueError):
        await call.answer("Invalid callback data.", show_alert=True)
        return

    user_id = call.from_user.id

    async with Database().session() as s:
        order = await get_order_by_id(s, order_id)
        if not order or order.user_id != user_id:
            await call.answer("Order not found.", show_alert=True)
            return

        items = await get_order_items(s, order.id)
        if not items:
            await call.answer("No items found.", show_alert=True)
            return

        item = items[0]
        inputs = (await s.execute(
            select(OrderCustomerInput).where(OrderCustomerInput.order_item_id == item.id).order_by(OrderCustomerInput.id.asc())
        )).scalars().all()

        if not inputs:
            await call.answer("No submitted information found.", show_alert=True)
            return

        language = current_locale.get()
        msg_parts = ["Submitted Information\n"]
        if item.quantity == 1:
            for inp in inputs:
                label = get_localized_label(inp.label_i18n_snapshot, language) or inp.field_key_snapshot
                if inp.field_type_snapshot == 'secret':
                    val = "Submitted ✅"
                elif '@' in inp.masked_preview:
                    val = "*@" + inp.masked_preview.split('@')[-1]
                else:
                    val = inp.masked_preview
                msg_parts.append(f"{label}:\n{val}\n")
        else:
            by_unit = {}
            for inp in inputs:
                idx = inp.unit_index if inp.scope_snapshot == 'per_unit' else 0
                by_unit.setdefault(idx, []).append(inp)
            if 0 in by_unit:
                for inp in by_unit[0]:
                    label = get_localized_label(inp.label_i18n_snapshot, language) or inp.field_key_snapshot
                    if inp.field_type_snapshot == 'secret':
                        val = "Submitted ✅"
                    elif '@' in inp.masked_preview:
                        val = "*@" + inp.masked_preview.split('@')[-1]
                    else:
                        val = inp.masked_preview
                    msg_parts.append(f"{label}: {val}")
                msg_parts.append("")
            for idx in sorted([k for k in by_unit.keys() if k != 0]):
                msg_parts.append(f"Item {idx}")
                for inp in by_unit[idx]:
                    label = get_localized_label(inp.label_i18n_snapshot, language) or inp.field_key_snapshot
                    if inp.field_type_snapshot == 'secret':
                        val = "Submitted ✅"
                    elif '@' in inp.masked_preview:
                        val = "*@" + inp.masked_preview.split('@')[-1]
                    else:
                        val = inp.masked_preview
                    msg_parts.append(f"{label}: {val}")
                msg_parts.append("")

        alert_text = "\n".join(msg_parts).strip()
        if len(alert_text) > 195:
            alert_text = alert_text[:192] + "..."
        await call.answer(alert_text, show_alert=True)

async def render_order_receipt(session: AsyncSession, order_id: int) -> tuple[str, InlineKeyboardMarkup]:
    from sqlalchemy.future import select
    from sqlalchemy.orm import selectinload
    from bot.database.models.main import Order, Goods

    from bot.misc.validators import sanitize_html
    from bot.misc.env import EnvKeys
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    order = (await session.execute(
        select(Order).options(selectinload(Order.items)).where(Order.id == order_id)
    )).scalar_one_or_none()

    if not order or not order.items:
        return "Order not found.", InlineKeyboardMarkup(inline_keyboard=[])

    item = order.items[0]
    eta = "—"
    if order.status == "processing":
        goods = (await session.execute(select(Goods).where(Goods.id == item.item_id))).scalar_one_or_none()
        if goods and goods.fulfillment_eta_minutes:
            eta = f"~{goods.fulfillment_eta_minutes} mins"

    default_processing_msg = "Your order has been received and is now being prepared.\nYou will be notified when it is ready."
    text = (
        f"📦 <b>{localize('intake.success.title', default='Order Received')}</b>\n\n"
        f"<b>{localize('intake.review.order_id', default='Order ID')}:</b>\n<code>{order.public_id}</code>\n\n"
        f"<b>{localize('intake.review.payment', default='Payment')}:</b>\n{localize('intake.payment.confirmed', default='Confirmed')}\n\n"
        f"<b>{localize('intake.review.status', default='Status')}:</b>\n{localize(f'status.{order.status}', default=order.status.capitalize())}\n\n"
        f"<b>{localize('intake.review.product', default='Product')}:</b>\n{sanitize_html(item.product_name_snapshot)}\n\n"
        f"<b>{localize('intake.review.quantity', default='Quantity')}:</b>\n{item.quantity}\n\n"
        f"<b>{localize('intake.review.total', default='Total')}:</b>\n{float(order.total):.2f} {order.currency}\n\n"
        f"<b>{localize('intake.review.eta', default='Estimated Delivery')}:</b>\n{eta}\n\n"
        f"{localize('intake.processing_details', default=default_processing_msg)}"
    )

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=localize("intake.btn.view_order", default="View Order"), callback_data=f"orders:view:{order.id}:r")],
        [InlineKeyboardButton(text=localize("btn.to_menu", default="🏠 Home"), callback_data="back_to_menu")]
    ])
    if EnvKeys.HELPER_ID:
        kb.inline_keyboard.insert(1, [InlineKeyboardButton(text=localize("btn.support", default="🆘 Support"), url=f"t.me/{EnvKeys.HELPER_ID}")])

    return text, kb

@router.callback_query(F.data.startswith("orders:receipt:"))
async def order_receipt_handler(call: CallbackQuery):
    order_id = int(call.data.split(":")[2])
    async with Database().session() as s:
        text, kb = await render_order_receipt(s, order_id)
        await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        await call.answer()

@router.callback_query(F.data.startswith("orders:active:"))
async def order_active_warning_handler(call: CallbackQuery):
    order_id = int(call.data.split(":")[2])
    user_id = call.from_user.id
    async with Database().session() as s:
        order = await get_order_by_id(s, order_id)
        if not order or order.user_id != user_id:
            await call.answer("Order not found.", show_alert=True)
            return

        default_msg = f"You already have an order being processed for this product.\n\nOrder:\n{order.public_id}\n\nStatus:\nProcessing"
        msg = (
            f"<b>{localize('intake.active_order.title', default='⚠️ Existing Order')}</b>\n\n"
            f"{localize('intake.active_order.body', order_id=order.public_id, default=default_msg)}"
        )
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=localize("intake.btn.view_order", default="View Existing Order"), callback_data=f"orders:view:{order.id}:a")],
            [InlineKeyboardButton(text=localize("intake.btn.buy_another", default="Buy Another"), callback_data="intake_buy_another")],
            [InlineKeyboardButton(text=localize("btn.back", default="🔙 Back"), callback_data="shop")]
        ])
        await call.message.edit_text(msg, reply_markup=kb, parse_mode="HTML")
        await call.answer()
