from typing import Callable, Iterable, Tuple
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot.i18n import localize
from bot.database.models import Permission
from bot.misc import LazyPaginator # noqa: F401
from bot.misc.utils import get_quick_quantities


def main_menu(role: int, buttons_config: list, locale: str, helper: str | None = None) -> InlineKeyboardMarkup:
    """
    Main menu premium layout.
    """
    kb = InlineKeyboardBuilder()

    action_map = {
        "shop": "shop",
        "wallet": "wallet",
        "profile": "profile",
        "support": f"tg://user?id={helper}" if helper else "support_none",
        "language": "language",
        "terms": "rules",
        "promo": "redeem_promo",
        "admin": "console"
    }

    fallback_en = {
        "shop": "🛒 Shop",
        "wallet": "💳 Wallet",
        "profile": "👤 Profile",
        "support": "🆘 Support",
        "language": "🌐 Language",
        "terms": "📜 Terms",
        "promo": "🔥 Promo Code",
        "admin": "🎛 Admin Panel"
    }

    fallback_ar = {
        "shop": "🛒 المتجر",
        "wallet": "💳 المحفظة",
        "profile": "👤 حسابي",
        "support": "🆘 الدعم",
        "language": "🌐 اللغة",
        "terms": "📜 الشروط",
        "promo": "🔥 كود الخصم",
        "admin": "🎛 لوحة الإدارة"
    }

    sorted_buttons = sorted(buttons_config, key=lambda b: (b.row_order, b.column_order, b.id))

    rows = {}
    for btn in sorted_buttons:
        if not btn.is_enabled:
            continue

        if btn.owner_only and not Permission.has_any_admin_perm(role):
            continue

        if btn.action_key == "admin" and not Permission.has_any_admin_perm(role):
            continue

        label = None
        if locale == "ar":
            label = btn.label_ar or btn.label_en or fallback_ar.get(btn.action_key) or fallback_en.get(btn.action_key)
        else:
            label = btn.label_en or fallback_en.get(btn.action_key)

        if not label:
            label = "Unknown"

        cb_data = action_map.get(btn.action_key, btn.action_key)

        button = None
        if btn.action_key == "support" and helper:
            button = InlineKeyboardButton(text=label, url=cb_data)
        else:
            button = InlineKeyboardButton(text=label, callback_data=cb_data)

        rows.setdefault(btn.row_order, []).append(button)

    for row_order in sorted(rows.keys()):
        kb.row(*rows[row_order])

    return kb.as_markup()


def wallet_keyboard(referral_percent: int) -> InlineKeyboardMarkup:
    """
    Wallet keyboard (balance, top up, history).
    """
    kb = InlineKeyboardBuilder()
    kb.button(text=localize("btn.replenish"), callback_data="replenish_balance")
    kb.button(text=localize("btn.redeem_promo"), callback_data="redeem_promo")
    kb.button(text=localize("btn.operation_history"), callback_data="operation_history")
    if referral_percent != 0:
        kb.button(text=localize("btn.referral"), callback_data="referral_system")
    kb.button(text="🏠 Home", callback_data="back_to_menu")
    kb.adjust(1)
    return kb.as_markup()


def profile_keyboard(user_items: int = 0) -> InlineKeyboardMarkup:
    """
    My Account keyboard.
    """
    kb = InlineKeyboardBuilder()
    if user_items != 0:
        kb.button(text=localize("btn.purchased"), callback_data="bought_items")
    kb.button(text=localize("btn.operation_history"), callback_data="operation_history")
    kb.button(text="🏠 Home", callback_data="back_to_menu")
    kb.adjust(1)
    return kb.as_markup()


def admin_console_keyboard(maintenance_mode: bool = False, role: int = 127) -> InlineKeyboardMarkup:
    """
    Admin panel — shows only buttons the user has permissions for.
    """
    kb = InlineKeyboardBuilder()
    if role & Permission.CATALOG_MANAGE:
        kb.button(text=localize("admin.menu.shop"), callback_data="shop_management")
        kb.button(text=localize("admin.menu.goods"), callback_data="goods_management")
        kb.button(text=localize("admin.menu.categories"), callback_data="categories_management")
    if role & Permission.PROMO_MANAGE:
        kb.button(text=localize("admin.menu.promo"), callback_data="promo_mgmt")
    if role & Permission.USERS_MANAGE:
        kb.button(text=localize("admin.menu.users"), callback_data="user_management")
    if role & Permission.ADMINS_MANAGE:
        kb.button(text=localize("admin.menu.roles"), callback_data="role_mgmt")
    if role & Permission.BROADCAST:
        kb.button(text=localize("admin.menu.broadcast"), callback_data="send_message")
    if role & Permission.SETTINGS_MANAGE:
        maintenance_key = "admin.menu.maintenance_on" if maintenance_mode else "admin.menu.maintenance_off"
        kb.button(text=localize(maintenance_key), callback_data="toggle_maintenance")
    kb.button(text="🏠 Home", callback_data="back_to_menu")
    kb.adjust(1)
    return kb.as_markup()


def simple_buttons(buttons: Iterable[Tuple[str, str]], per_row: int = 1) -> InlineKeyboardMarkup:
    """
    Universal button assembly from (text, callback_data)
    """
    kb = InlineKeyboardBuilder()
    for text, cb in buttons:
        kb.button(text=text, callback_data=cb)
    kb.adjust(per_row)
    return kb.as_markup()


def back(cb: str = "menu", text: str | None = None) -> InlineKeyboardMarkup:
    if not text and cb == "back_to_menu":
        text = "🏠 Home"
        return simple_buttons([(text, cb)])

    return simple_buttons([
        (text or localize("btn.back"), cb),
        ("🏠 Home", "back_to_menu")
    ], per_row=1)


def close() -> InlineKeyboardMarkup:
    """
    One button 'Close'.
    """
    return simple_buttons([(localize("btn.close"), "close")])


async def lazy_paginated_keyboard(
        paginator: 'LazyPaginator',
        item_text: Callable[[object], str],
        item_callback: Callable[[object], str],
        page: int = 0,
        back_cb: str | None = None,
        home_cb: str | None = None,
        nav_cb_prefix: str = "",
        back_text: str | None = None,
        row_width: int = 1,
        refresh_cb: str | None = None,
) -> InlineKeyboardMarkup:
    """
    Lazy pagination keyboard with data loading on demand
    """
    kb = InlineKeyboardBuilder()

    # Get items for current page
    items = await paginator.get_page(page)

    for item in items:
        kb.button(text=item_text(item), callback_data=item_callback(item))
    kb.adjust(row_width)

    # Navigation
    total_pages = await paginator.get_total_pages()
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"{nav_cb_prefix}{page - 1}"))
        nav_buttons.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"{nav_cb_prefix}{page + 1}"))
        kb.row(*nav_buttons)

    if refresh_cb:
        kb.row(InlineKeyboardButton(text="🔄 Refresh Stock", callback_data=refresh_cb))

    if back_cb and home_cb:
        kb.row(
            InlineKeyboardButton(text=back_text or localize("btn.back"), callback_data=back_cb),
            InlineKeyboardButton(text="🏠 Home", callback_data=home_cb)
        )
    elif back_cb:
        kb.row(InlineKeyboardButton(text=back_text or localize("btn.back"), callback_data=back_cb))
    elif home_cb:
        kb.row(InlineKeyboardButton(text="🏠 Home", callback_data=home_cb))

    return kb.as_markup()


def item_info(
        item_name: str, back_data: str, avg_rating: float = None,
        review_count: int = 0, has_purchased: bool = False,
        applied_promo: str = None, reviews_enabled: bool = True,
        quantity: int = 1, stock: int = -1, item_id: int = None,
        has_active_restock_sub: bool = False,
) -> InlineKeyboardMarkup:
    """
    Product card for quantity selection.
    """
    kb = InlineKeyboardBuilder()

    if stock == 0:
        # Out of stock layout
        kb.row(InlineKeyboardButton(text="🔄 Check Availability", callback_data=f"refresh:item:{item_id}"))
        if has_active_restock_sub:
            kb.row(InlineKeyboardButton(text=localize("btn.cancel_restock", default="🔕 Cancel Restock Alert"), callback_data=f"restock:cancel:{item_id}"))
        else:
            kb.row(InlineKeyboardButton(text=localize("btn.notify_restock", default="🔔 Notify Me When Available"), callback_data=f"restock:subscribe:{item_id}"))
    else:
        # Quick Quantity buttons
        is_infinity = stock == -1
        quick_qtys = get_quick_quantities(stock if not is_infinity else -1, is_infinity)

        row = []
        for label, val in quick_qtys:
            row.append(InlineKeyboardButton(text=label, callback_data=f"qty:quick:{item_id}:{val}"))
            if len(row) == 4:
                kb.row(*row)
                row = []
        if row:
            kb.row(*row)

        # Custom Amount & Continue
        kb.row(InlineKeyboardButton(text="✏️ Custom Quantity", callback_data=f"qty:keypad:{item_id}"))
        kb.row(InlineKeyboardButton(text="🛒 Continue", callback_data=f"checkout:{item_id}"))
        kb.row(InlineKeyboardButton(text="🔄 Refresh Stock", callback_data=f"refresh:item:{item_id}"))

    kb.row(InlineKeyboardButton(text=localize("btn.back"), callback_data=back_data),
           InlineKeyboardButton(text="🏠 Home", callback_data="back_to_menu"))

    return kb.as_markup()

def numeric_keypad(item_id: int) -> InlineKeyboardMarkup:
    """
    Callback-only numeric keypad for custom quantity.
    """
    kb = InlineKeyboardBuilder()

    kb.row(
        InlineKeyboardButton(text="1", callback_data=f"qty:digit:{item_id}:1"),
        InlineKeyboardButton(text="2", callback_data=f"qty:digit:{item_id}:2"),
        InlineKeyboardButton(text="3", callback_data=f"qty:digit:{item_id}:3")
    )
    kb.row(
        InlineKeyboardButton(text="4", callback_data=f"qty:digit:{item_id}:4"),
        InlineKeyboardButton(text="5", callback_data=f"qty:digit:{item_id}:5"),
        InlineKeyboardButton(text="6", callback_data=f"qty:digit:{item_id}:6")
    )
    kb.row(
        InlineKeyboardButton(text="7", callback_data=f"qty:digit:{item_id}:7"),
        InlineKeyboardButton(text="8", callback_data=f"qty:digit:{item_id}:8"),
        InlineKeyboardButton(text="9", callback_data=f"qty:digit:{item_id}:9")
    )
    kb.row(
        InlineKeyboardButton(text="⌫", callback_data=f"qty:backspace:{item_id}"),
        InlineKeyboardButton(text="0", callback_data=f"qty:digit:{item_id}:0"),
        InlineKeyboardButton(text="↺ Clear", callback_data=f"qty:clear:{item_id}")
    )
    kb.row(InlineKeyboardButton(text="✅ Continue", callback_data=f"qty:keypad_continue:{item_id}"))
    kb.row(
        InlineKeyboardButton(text="⬅️ Back", callback_data=f"qty:keypad_back:{item_id}"),
        InlineKeyboardButton(text="🏠 Home", callback_data="back_to_menu")
    )

    return kb.as_markup()

def checkout_confirmation_keyboard(item_id: int, can_purchase: bool, applied_promo: str = None) -> InlineKeyboardMarkup:
    """
    Keyboard for the two-step checkout confirmation screen.
    """
    kb = InlineKeyboardBuilder()

    if can_purchase:
        kb.row(InlineKeyboardButton(text="✅ Confirm Purchase", callback_data=f"confirm_purchase:{item_id}"))
    else:
        kb.row(InlineKeyboardButton(text=localize("btn.replenish"), callback_data="replenish_balance"))

    kb.row(InlineKeyboardButton(text="✏️ Change Quantity", callback_data=f"checkout_change_qty:{item_id}"))

    if applied_promo:
        kb.row(InlineKeyboardButton(text=localize("btn.remove_promo"), callback_data=f"remove_promo:{item_id}"))
    else:
        kb.row(InlineKeyboardButton(text=localize("btn.apply_promo"), callback_data=f"apply_promo:{item_id}"))

    kb.row(
        InlineKeyboardButton(text="⬅️ Back", callback_data=f"checkout_change_qty:{item_id}"),
        InlineKeyboardButton(text="🏠 Home", callback_data="back_to_menu")
    )

    return kb.as_markup()


def payment_menu(pay_url: str) -> InlineKeyboardMarkup:
    """
    Buttons under the invoice (CryptoPay, etc.).
    """
    kb = InlineKeyboardBuilder()
    kb.button(text=localize("btn.pay"), url=pay_url)
    kb.button(text=localize("btn.check_payment"), callback_data="check")
    kb.button(text=localize("btn.back"), callback_data="profile")
    kb.adjust(1)
    return kb.as_markup()


def get_payment_choice() -> InlineKeyboardMarkup:
    """
    Select a payment method.
    """
    return simple_buttons(
        [
            (localize("btn.pay.crypto"), "pay_cryptopay"),
            (localize("btn.pay.stars"), "pay_stars"),
            (localize("btn.pay.tg"), "pay_fiat"),
            (localize("btn.back"), "replenish_balance"),
        ],
        per_row=1,
    )


def question_buttons(question: str, back_data: str) -> InlineKeyboardMarkup:
    """
    Universal yes/no + Back.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text=localize("btn.yes"), callback_data=f"{question}_yes")
    kb.button(text=localize("btn.no"), callback_data=f"{question}_no")
    kb.button(text=localize("btn.back"), callback_data=back_data)
    kb.adjust(2)
    return kb.as_markup()


def check_sub(channel_username: str) -> InlineKeyboardMarkup:
    """
    checks the channel subscription.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text=localize("btn.channel"), url=f"https://t.me/{channel_username}")
    kb.button(text=localize("btn.check_subscription"), callback_data="sub_channel_done")
    kb.adjust(1)
    return kb.as_markup()


def rating_keyboard(item_name: str) -> InlineKeyboardMarkup:
    """Rating selection keyboard (1-5 stars)."""
    kb = InlineKeyboardBuilder()
    for i in range(1, 6):
        kb.button(text="⭐" * i, callback_data=f"rating:{i}")
    kb.button(text="🏠 Home", callback_data="back_to_menu")
    kb.adjust(5, 1)
    return kb.as_markup()


def referral_system_keyboard(has_referrals: bool = False, has_earnings: bool = False) -> InlineKeyboardMarkup:
    """
    Referral system keyboard with additional buttons.
    """
    kb = InlineKeyboardBuilder()

    if has_referrals:
        kb.button(text=localize("btn.view_referrals"), callback_data="view_referrals")

    if has_earnings:
        kb.button(text=localize("btn.view_earnings"), callback_data="view_all_earnings")

    kb.button(text=localize("btn.back"), callback_data="profile")
    kb.adjust(1)
    return kb.as_markup()
