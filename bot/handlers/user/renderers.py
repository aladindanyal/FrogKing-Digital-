import logging
from decimal import Decimal
from aiogram.types import Message
from bot.database import Database
from bot.database.methods.orders import get_order_by_id, get_order_items
from bot.database.models.main import BoughtGoods
from sqlalchemy import select
from bot.i18n.main import localize
from html import escape
from bot.misc import EnvKeys
from bot.keyboards.inline import simple_buttons
from bot.database.methods.read import get_user_language_cached

logger = logging.getLogger(__name__)

async def render_purchase_success_from_order(message: Message, order_id: int):
    """Reconstructs and renders the exact purchase-success screen from an Order."""
    user_locale = await get_user_language_cached(message.chat.id)
    async with Database().session() as s:
        order = await get_order_by_id(s, order_id)
        if not order:
            await message.answer("Order not found.", show_alert=True)
            return None, None

        items = await get_order_items(s, order_id)
        if not items:
            await message.answer("Order items not found.", show_alert=True)
            return None, None
            
        order_item = items[0]
        
        bought_goods = (await s.execute(
            select(BoughtGoods).where(BoughtGoods.order_id == order.id).order_by(BoughtGoods.id.asc())
        )).scalars().all()

        delivered_values = [bg.value for bg in bought_goods] if bought_goods else []

    purchased_time = order.paid_at.strftime("%Y-%m-%d %H:%M:%S") if order.paid_at else order.created_at.strftime("%Y-%m-%d %H:%M:%S")
    public_order_id = order.public_id
    
    currency = order.currency or EnvKeys.PAY_CURRENCY
    unit_price = Decimal(str(order_item.unit_price)).quantize(Decimal("0.01"))
    total_paid = Decimal(str(order.total)).quantize(Decimal("0.01"))
    total_discount = Decimal(str(order.discount_total)).quantize(Decimal("0.01"))

    product_name = getattr(order_item, 'product_name_snapshot', None)
    if not product_name and 'item' in order_item.__dict__ and order_item.item:
        product_name = getattr(order_item.item, 'name', None)
    if not product_name:
        product_name = localize("shop.product_plain", _locale=user_locale)

    receipt_header = (
        localize("shop.order_completed", _locale=user_locale) + "\n\n" +
        localize("shop.order_id", id=f"<code>{public_order_id}</code>", _locale=user_locale) + "\n" +
        localize("shop.product", name=escape(product_name), _locale=user_locale) + "\n" +
        localize("shop.selected_quantity", quantity=order_item.quantity, _locale=user_locale) + "\n" +
        localize("shop.unit_price", price=unit_price, currency=currency, _locale=user_locale) + "\n"
    )
    if total_discount > 0:
        receipt_header += localize("shop.discount", discount=total_discount, currency=currency, _locale=user_locale) + "\n"
        
    receipt_header += (
        localize("shop.total_paid", total=total_paid, currency=currency, _locale=user_locale) + "\n" +
        localize("shop.purchased_at", purchased_time=purchased_time, _locale=user_locale) + "\n\n"
    )

    # We only reconstruct the receipt UI from persisted Order data.
    # The immediate purchase result must not create duplicate receipt messages or resend delivered product values.
    current_msg = receipt_header
    messages_to_send = [current_msg]
        
    for msg in messages_to_send:
        # Instead of multiple messages, we will just prepare to send or edit the single receipt message.
        # But wait, the function signature is async def render_purchase_success_from_order(message: Message, order_id: int):
        pass

    action_buttons = []
    action_buttons.append((localize("btn.view_order", _locale=user_locale), f"orders:view:{order.id}:p"))
    
    # Review eligibility
    if order.status == "completed":
        from bot.database.methods.read import get_review_by_order_item
        eligible_items = [i for i in items if i.fulfillment_status == "delivered"]
        # Only show review buttons for unreviewed items
        unreviewed_items = []
        for item in eligible_items:
            existing = await get_review_by_order_item(item.id)
            if not existing:
                unreviewed_items.append(item)
                
        if len(unreviewed_items) == 1:
            action_buttons.append((localize("orders.leave_review", default="⭐ Leave a Review"), f"review:start:{unreviewed_items[0].id}:p:{order.id}"))
        elif len(unreviewed_items) > 1:
            action_buttons.append((localize("orders.leave_review", default="⭐ Leave a Review"), f"orders:view:{order.id}:p"))
        
    action_buttons.append((localize("btn.buy_again", _locale=user_locale), f"buy_again:{items[0].item_id}"))
    
    if EnvKeys.HELPER_ID:
        action_buttons.append(("🆘 Support for This Order", f"support_order:{public_order_id}"))
        
    action_buttons.append((localize("btn.to_menu", _locale=user_locale), "back_to_menu"))
    
    return receipt_header, simple_buttons(action_buttons)
