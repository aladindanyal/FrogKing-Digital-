from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime

from bot.database import Database
from bot.database.methods.orders import (
    get_order_by_id, list_user_orders, count_user_orders, get_order_items, get_order_item_by_id
)
from bot.database.models.main import BoughtGoods, Goods
from sqlalchemy import select
from bot.i18n.main import localize
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
async def orders_list_handler(call: CallbackQuery):
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
    await call.answer()

@router.callback_query(F.data.startswith("orders:view:"))
async def order_view_handler(call: CallbackQuery):
    order_id = int(call.data.split(":")[2])
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
            
        if order.discount_total > 0:
            text += f"🏷 <b>Discount:</b> {order.discount_total} {order.currency}\n"
            
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
            
        kb.button(text=localize("btn.back", default="🔙 Back"), callback_data="orders:list:0")
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
