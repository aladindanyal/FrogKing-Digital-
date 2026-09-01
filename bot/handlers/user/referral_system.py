from functools import partial
from decimal import Decimal

from aiogram import Router, F
from aiogram.types import CallbackQuery
from bot.misc.utils import answer_callback_safe
from aiogram.fsm.context import FSMContext

from bot.database.methods import (
    check_user_referrals, get_referral_earnings_stats, get_one_referral_earning, query_user_referrals,
    query_referral_earnings_from_user, query_all_referral_earnings,
    convert_referral_earnings, get_referral_percent,
)
from bot.handlers.other import get_bot_info
from bot.misc.utils import safe_edit_or_send
from bot.keyboards import back, referral_system_keyboard, lazy_paginated_keyboard
from bot.misc import EnvKeys, LazyPaginator
from bot.i18n import localize

router = Router()


def _format_money(value) -> str:
    return f"{Decimal(str(value or 0)):.2f}"


@router.callback_query(F.data == "referral_system")
async def referral_callback_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """
    Show referral info, personal invite link, and additional buttons.
    """
    user_id = call.from_user.id
    referrals_count = await check_user_referrals(user_id)
    referral_percent = await get_referral_percent()
    bot_username = await get_bot_info(call)

    earnings_stats = await get_referral_earnings_stats(user_id)

    has_referrals = referrals_count > 0
    has_earnings = earnings_stats['total_earnings_count'] > 0
    has_convertible = earnings_stats['available_amount'] > 0

    text = (
        f"{localize('referral.title')}\n"
        f"{localize('referral.link', bot_username=bot_username, user_id=user_id)}\n"
        f"{localize('referral.count', count=referrals_count)}\n"
        f"{localize('referral.description', percent=referral_percent)}"
    )

    if has_earnings:
        text += "\n\n" + localize('referrals.stats.template',
                                  active_count=earnings_stats['active_referrals_count'],
                                  total_earned=_format_money(earnings_stats['total_amount']),
                                  total_original=_format_money(earnings_stats['total_original_amount']),
                                  pending=_format_money(earnings_stats['pending_amount']),
                                  available=_format_money(earnings_stats['available_amount']),
                                  converted=_format_money(earnings_stats['converted_amount']),
                                  debt=_format_money(earnings_stats['referral_debt']),
                                  earnings_count=earnings_stats['total_earnings_count'],
                                  currency=EnvKeys.PAY_CURRENCY
                                  )

    markup = referral_system_keyboard(has_referrals, has_earnings, has_convertible)
    await safe_edit_or_send(call, text, reply_markup=markup)
    await state.clear()


@router.callback_query(F.data == "view_referrals")
async def view_referrals_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """
    Show a list of all user referrals with lazy loading.
    """
    user_id = call.from_user.id

    # Create paginator
    query_func = partial(query_user_referrals, user_id)
    paginator = LazyPaginator(query_func, per_page=10)

    # Check if there are any referrals
    total = await paginator.get_total_count()
    if total == 0:
        await safe_edit_or_send(call,
            localize("referrals.list.empty"),
            reply_markup=back("referral_system")
        )
        return

    markup = await lazy_paginated_keyboard(
        paginator=paginator,
        item_text=lambda referral_data: localize("referrals.item.format",
                                                 telegram_id=referral_data['telegram_id'],
                                                 total_earned=_format_money(referral_data['total_earned']),
                                                 currency=EnvKeys.PAY_CURRENCY),
        item_callback=lambda referral_data: f"referral_earnings_{referral_data['telegram_id']}",
        page=0,
        back_cb="referral_system",
        nav_cb_prefix="referrals_page_"
    )

    await safe_edit_or_send(call,
        localize("referrals.list.title"),
        reply_markup=markup
    )

    # Save state
    await state.update_data(referrals_paginator=paginator.get_state())


@router.callback_query(F.data.startswith("referrals_page_"))
async def referrals_pagination_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """
    Pagination processing for the referral list with lazy loading.
    """
    try:
        page = int(call.data.split("_")[-1])
    except (ValueError, IndexError):
        await answer_callback_safe(call, localize("errors.pagination_invalid"))
        return

    user_id = call.from_user.id

    # Get saved state
    data = await state.get_data()
    paginator_state = data.get('referrals_paginator')

    # Create paginator with cached state
    query_func = partial(query_user_referrals, user_id)
    paginator = LazyPaginator(query_func, per_page=10, state=paginator_state)

    markup = await lazy_paginated_keyboard(
        paginator=paginator,
        item_text=lambda referral_data: localize("referrals.item.format",
                                                 telegram_id=referral_data['telegram_id'],
                                                 total_earned=_format_money(referral_data['total_earned']),
                                                 currency=EnvKeys.PAY_CURRENCY),
        item_callback=lambda referral_data: f"referral_earnings_{referral_data['telegram_id']}",
        page=page,
        back_cb="referral_system",
        nav_cb_prefix="referrals_page_"
    )

    await safe_edit_or_send(call,
        localize("referrals.list.title"),
        reply_markup=markup
    )

    # Update state
    await state.update_data(referrals_paginator=paginator.get_state())


@router.callback_query(F.data.startswith("referral_earnings_"))
async def referral_earnings_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """
    Show all earnings from a specific referral with lazy loading.
    """
    try:
        referral_id = int(call.data.split("_")[-1])
    except (ValueError, IndexError):
        await answer_callback_safe(call, localize("errors.invalid_data"))
        return

    user_id = call.from_user.id

    # Create paginator
    query_func = partial(query_referral_earnings_from_user, user_id, referral_id)
    paginator = LazyPaginator(query_func, per_page=10)

    # Check if there are any earnings
    total = await paginator.get_total_count()
    if total == 0:
        referral_info = await call.message.bot.get_chat(referral_id)
        await safe_edit_or_send(call,
            localize("referral.earnings.empty", id=referral_id, name=referral_info.first_name),
            reply_markup=back("view_referrals")
        )
        return

    markup = await lazy_paginated_keyboard(
        paginator=paginator,
        item_text=lambda earning: localize("referral.earning.format",
                                           amount=_format_money(earning.amount),
                                           currency=EnvKeys.PAY_CURRENCY,
                                           date=earning.created_at.strftime("%d.%m.%Y %H:%M"),
                                           original_amount=_format_money(earning.original_amount),
                                           status=localize(f"referral.status.{earning.status}")),
        item_callback=lambda earning: f"earning_detail:{earning.id}:referral_earnings_{referral_id}",
        page=0,
        back_cb="view_referrals",
        nav_cb_prefix=f"ref_earnings_{referral_id}_page_"
    )

    referral_info = await call.message.bot.get_chat(referral_id)
    title_text = localize("referral.earnings.title", telegram_id=referral_id, name=referral_info.first_name)
    await safe_edit_or_send(call, title_text, reply_markup=markup)

    # Save state
    await state.update_data(ref_earnings_paginator=paginator.get_state())


@router.callback_query(F.data == "view_all_earnings")
async def view_all_earnings_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """
    Show all user referral earnings with lazy loading.
    """
    user_id = call.from_user.id

    # Create paginator
    query_func = partial(query_all_referral_earnings, user_id)
    paginator = LazyPaginator(query_func, per_page=10)

    # Check if there are any earnings
    total = await paginator.get_total_count()
    if total == 0:
        await safe_edit_or_send(call,
            localize("all.earnings.empty"),
            reply_markup=back("referral_system")
        )
        return

    markup = await lazy_paginated_keyboard(
        paginator=paginator,
        item_text=lambda earning: localize("all.earning.format",
                                           amount=_format_money(earning.amount),
                                           currency=EnvKeys.PAY_CURRENCY,
                                           referral_id=earning.referral_id,
                                           date=earning.created_at.strftime("%d.%m.%Y %H:%M"),
                                           status=localize(f"referral.status.{earning.status}")),
        item_callback=lambda earning: f"earning_detail:{earning.id}:view_all_earnings",
        page=0,
        back_cb="referral_system",
        nav_cb_prefix="all_earnings_page_"
    )

    await safe_edit_or_send(call,
        localize("all.earnings.title"),
        reply_markup=markup
    )

    # Save state
    await state.update_data(all_earnings_paginator=paginator.get_state())


@router.callback_query(F.data.startswith("all_earnings_page_"))
async def all_earnings_pagination_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """
    Pagination processing for all referral earnings with lazy loading.
    """
    try:
        page = int(call.data.split("_")[-1])
    except (ValueError, IndexError):
        await answer_callback_safe(call, localize("errors.pagination_invalid"))
        return

    user_id = call.from_user.id

    # Get saved state
    data = await state.get_data()
    paginator_state = data.get('all_earnings_paginator')

    # Create paginator with cached state
    query_func = partial(query_all_referral_earnings, user_id)
    paginator = LazyPaginator(query_func, per_page=10, state=paginator_state)

    markup = await lazy_paginated_keyboard(
        paginator=paginator,
        item_text=lambda earning: localize("all.earning.format",
                                           amount=_format_money(earning.amount),
                                           currency=EnvKeys.PAY_CURRENCY,
                                           referral_id=earning.referral_id,
                                           date=earning.created_at.strftime("%d.%m.%Y %H:%M"),
                                           status=localize(f"referral.status.{earning.status}")),
        item_callback=lambda earning: f"earning_detail:{earning.id}:all_earnings_page_{page}",
        page=page,
        back_cb="referral_system",
        nav_cb_prefix="all_earnings_page_"
    )

    await safe_edit_or_send(call,
        localize("all.earnings.title"),
        reply_markup=markup
    )

    # Update state
    await state.update_data(all_earnings_paginator=paginator.get_state())


@router.callback_query(F.data.startswith("earning_detail:"))
async def referral_callback_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """
    Show referral info, personal invite link, and additional buttons.
    """
    trash, earning_id, back_data = call.data.split(':', 2)
    earning_info = await get_one_referral_earning(int(earning_id))
    if not earning_info:
        await answer_callback_safe(call, localize("errors.invalid_data"), show_alert=True)
        return
    referral_id = earning_info.get('referral_id')
    if referral_id:
        user_info = await call.message.bot.get_chat(referral_id)
        user_name = user_info.first_name
    else:
        user_name = earning_info.get('admin_identity') or "Admin"

    await safe_edit_or_send(call, localize('referral.item.info',
                                          id=earning_id,
                                          telegram_id=referral_id or "—",
                                          name=user_name,
                                          amount=_format_money(earning_info['amount']),
                                          currency=EnvKeys.PAY_CURRENCY,
                                          date=earning_info['created_at'].strftime("%d.%m.%Y %H:%M"),
                                          original_amount=_format_money(earning_info['original_amount']),
                                          status=localize(f"referral.status.{earning_info['status']}")
                                          ), reply_markup=back(back_data))
    await state.clear()


@router.callback_query(F.data == "convert_referral_earnings")
async def convert_referral_earnings_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    success, message, gross = await convert_referral_earnings(call.from_user.id)
    if not success:
        key = "referral.conversion.empty" if message == "no_earnings" else "referral.conversion.failed"
        await answer_callback_safe(call, localize(key), show_alert=True)
        return

    stats = await get_referral_earnings_stats(call.from_user.id)
    await safe_edit_or_send(
        call,
        localize(
            "referral.conversion.success",
            gross=_format_money(gross),
            debt=_format_money(stats['referral_debt']),
            currency=EnvKeys.PAY_CURRENCY,
        ),
        reply_markup=back("referral_system"),
    )
    await state.clear()
