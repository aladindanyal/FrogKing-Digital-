from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from bot.misc.utils import answer_callback_safe
from aiogram.enums.chat_type import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import datetime

from bot.database.methods import (
    select_max_role_id, create_user, check_role, check_user,
    select_user_operations, select_user_items, check_user_cached
)
from bot.database.methods.read import get_cart_count, get_store_settings
from bot.database.methods.lazy_queries import query_user_operations_history
from bot.handlers.other import check_sub_channel, _parse_channel_username
from bot.keyboards import main_menu, back, profile_keyboard, check_sub
from bot.keyboards.inline import simple_buttons, lazy_paginated_keyboard
from bot.misc import EnvKeys
from bot.misc.utils import safe_edit_or_send
from bot.misc.metrics import get_metrics
from bot.i18n import localize
from bot.logger_mesh import logger

router = Router()

async def _send_or_edit_main_menu(message_or_call, role_data: int, channel_username, user_id: int):
    settings = await get_store_settings()
    
    title = settings.main_menu_title if settings and settings.main_menu_title else localize("menu.title")
    desc = settings.main_menu_description if settings and settings.main_menu_description else ""
    footer = settings.main_menu_footer if settings and settings.main_menu_footer else ""
    
    text = f"<b>{title}</b>\n\n"
    if desc:
        text += f"{desc}\n\n"
    if footer:
        text += f"<i>{footer}</i>"
        
    from bot.database.methods.read import get_main_menu_buttons
    from bot.i18n.main import current_locale
    
    buttons = await get_main_menu_buttons()
    markup = main_menu(role=role_data, buttons_config=buttons, locale=current_locale.get(), helper=EnvKeys.HELPER_ID)
    image_path = settings.main_menu_image_path if settings else None
    
    msg = message_or_call if isinstance(message_or_call, Message) else message_or_call.message
    
    if image_path:
        import os
        if not os.path.exists(image_path):
            logger.warning(f"Main menu image path exists but file is missing: {image_path}")
            image_path = None

    if image_path:
        if isinstance(message_or_call, CallbackQuery) and msg.photo:
            try:
                await msg.edit_caption(caption=text, reply_markup=markup, parse_mode='HTML')
            except Exception as e:
                if "message is not modified" not in str(e):
                    try:
                        await msg.delete()
                    except:
                        pass
                    await msg.answer_photo(photo=FSInputFile(image_path), caption=text, reply_markup=markup, parse_mode='HTML')
        else:
            try:
                await msg.delete()
            except:
                pass
            await msg.answer_photo(photo=FSInputFile(image_path), caption=text, reply_markup=markup, parse_mode='HTML')
    else:
        if isinstance(message_or_call, CallbackQuery):
            try:
                await msg.edit_text(text, reply_markup=markup, parse_mode='HTML')
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    try:
                        await msg.delete()
                    except:
                        pass
                    await msg.answer(text, reply_markup=markup, parse_mode='HTML')
        else:
            try:
                await msg.delete()
            except:
                pass
            await msg.answer(text, reply_markup=markup, parse_mode='HTML')



@router.message(F.text.startswith('/start'))
async def start(message: Message, state: FSMContext):
    """
    Handle /start:
    - Ensure user exists (register if new)
    - (Optional) Check channel subscription
    - Show the main menu
    """
    if message.chat.type != ChatType.PRIVATE:
        return

    user_id = message.from_user.id
    await state.clear()

    owner_max_role = await select_max_role_id()
    referral_id = message.text[7:] if message.text[7:] != str(user_id) else None
    user_role = owner_max_role if user_id == EnvKeys.OWNER_ID else 1

    is_new_user = (await check_user(user_id)) is None

    # registration_date is DateTime
    await create_user(
        telegram_id=int(user_id),
        registration_date=datetime.datetime.now(datetime.timezone.utc),
        referral_id=int(referral_id) if referral_id else None,
        role=user_role
    )

    if is_new_user:
        metrics = get_metrics()
        if metrics:
            metrics.track_event("registration", user_id)

    channel_username = _parse_channel_username()
    role_data = await check_role(user_id)

    # Optional subscription check
    try:
        if channel_username:
            chat_id = int(EnvKeys.CHANNEL_ID) if EnvKeys.CHANNEL_ID else f"@{channel_username}"
            chat_member = await message.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if not await check_sub_channel(chat_member):
                markup = check_sub(channel_username)
                await message.answer(localize("subscribe.prompt"), reply_markup=markup)
                await message.delete()
                return
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        # Ignore channel errors (private channel, wrong link, etc.)
        logger.warning(f"Channel subscription check failed for user {user_id}: {e}")

    await _send_or_edit_main_menu(message, role_data, channel_username, user_id)
    await state.clear()


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu_callback_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """
    Return user to the main menu.
    """
    user_id = call.from_user.id
    user = await check_user_cached(user_id)
    if not user:
        await create_user(
            telegram_id=user_id,
            registration_date=datetime.datetime.now(datetime.timezone.utc),
            referral_id=None,
            role=1
        )
        user = await check_user_cached(user_id)

    role_id = user.get('role_id')

    channel_username = _parse_channel_username()

    await _send_or_edit_main_menu(call, role_id, channel_username, user_id)
    await state.clear()


@router.callback_query(F.data == "rules")
async def rules_callback_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """
    Show rules text if provided in ENV.
    """
    rules_data = EnvKeys.RULES
    if rules_data:
        await safe_edit_or_send(call, rules_data, reply_markup=back("back_to_menu"))
    else:
        await answer_callback_safe(call, localize("rules.not_set"))
    await state.clear()


@router.callback_query(F.data == "profile")
async def profile_callback_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """
    Send profile info (balance, purchases count, id, etc.).
    """
    user_id = call.from_user.id
    tg_user = call.from_user
    user_info = await check_user_cached(user_id)

    balance = user_info.get('balance')
    operations = await select_user_operations(user_id)
    overall_balance = sum(operations) if operations else 0
    items = await select_user_items(user_id)
    referral = EnvKeys.REFERRAL_PERCENT
    markup = profile_keyboard(user_items=items)
    text = (
        f"{localize('profile.caption', name=tg_user.first_name, id=user_id)}\n"
        f"{localize('profile.id', id=user_id)}\n"
        f"{localize('profile.balance', amount=balance, currency=EnvKeys.PAY_CURRENCY)}\n"
        f"{localize('profile.total_topup', amount=overall_balance, currency=EnvKeys.PAY_CURRENCY)}\n"
        f"{localize('profile.purchased_count', count=items)}"
    )
    await safe_edit_or_send(call, text, reply_markup=markup, parse_mode='HTML')
    await state.clear()


@router.callback_query(F.data == "sub_channel_done")
async def check_sub_to_channel(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """
    Re-check channel subscription after user clicks "Check".
    """
    user_id = call.from_user.id
    channel_username = _parse_channel_username()
    helper = EnvKeys.HELPER_ID

    if channel_username:
        chat_id = int(EnvKeys.CHANNEL_ID) if EnvKeys.CHANNEL_ID else f"@{channel_username}"
        chat_member = await call.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        if await check_sub_channel(chat_member):
            user = await check_user_cached(user_id)
            role_id = user.get('role_id')
            markup = main_menu(role_id, channel_username, helper)
            await call.message.edit_text(localize("menu.title"), reply_markup=markup)
            await state.clear()
            return

    await answer_callback_safe(call, localize("errors.not_subscribed"))


# --- Operation History ---

@router.callback_query(F.data == "operation_history")
async def operation_history_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    user_id = call.from_user.id
    await _show_operations_page(call, state, user_id, 0)


@router.callback_query(F.data.startswith("ops-page_"))
async def navigate_operations(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    page = int(call.data.split("_")[1])
    await _show_operations_page(call, state, call.from_user.id, page)


async def _show_operations_page(call: CallbackQuery, state: FSMContext, user_id: int, page: int):
    await answer_callback_safe(call)
    from functools import partial
    from bot.misc import LazyPaginator

    paginator = LazyPaginator(partial(query_user_operations_history, user_id), per_page=10)
    items = await paginator.get_page(page)
    total_pages = await paginator.get_total_pages()

    if not items:
        await call.message.edit_text(
            localize("history.title") + "\n\n" + localize("history.empty"),
            reply_markup=back("profile"),
        )
        return

    lines = [localize("history.title"), ""]
    for op in items:
        op_type = op['type']
        amount = op['amount']
        date = op['date']
        date_str = str(date)[:19] if date else ""

        if op_type == 'topup':
            lines.append(localize("history.topup", amount=amount, currency=EnvKeys.PAY_CURRENCY))
        elif op_type == 'purchase':
            lines.append(localize("history.purchase", amount=amount, currency=EnvKeys.PAY_CURRENCY))
        elif op_type == 'referral':
            lines.append(localize("history.referral", amount=amount, currency=EnvKeys.PAY_CURRENCY))
        lines.append(localize("history.date", date=date_str))
        lines.append("")

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    kb = InlineKeyboardBuilder()
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"ops-page_{page - 1}"))
    if total_pages > 1:
        nav_buttons.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"ops-page_{page + 1}"))
    if nav_buttons:
        kb.row(*nav_buttons)
    kb.row(InlineKeyboardButton(text=localize("btn.back"), callback_data="profile"))

    await safe_edit_or_send(call, "\n".join(lines), reply_markup=kb.as_markup())

from aiogram.filters import Command

@router.message(Command("wallet"))
async def wallet_command_handler(message: Message, state: FSMContext):
    """Command /wallet"""
    user_id = message.from_user.id
    user_info = await check_user_cached(user_id)
    
    balance = user_info.get('balance') if user_info else 0
    operations = await select_user_operations(user_id)
    overall_balance = sum(operations) if operations else 0
    referral = EnvKeys.REFERRAL_PERCENT
    
    from bot.keyboards.inline import wallet_keyboard
    markup = wallet_keyboard(referral)
    text = (
        f"💳 <b>{localize('btn.wallet', default='Wallet')}</b>\n\n"
        f"{localize('profile.balance', amount=balance, currency=EnvKeys.PAY_CURRENCY)}\n"
        f"{localize('profile.total_topup', amount=overall_balance, currency=EnvKeys.PAY_CURRENCY)}"
    )
    await message.answer(text, reply_markup=markup, parse_mode='HTML')
    await state.clear()



from bot.keyboards.inline import wallet_keyboard

@router.callback_query(F.data == "wallet")
async def wallet_callback_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    user_id = call.from_user.id
    user_info = await check_user_cached(user_id)
    
    balance = user_info.get('balance') if user_info else 0
    operations = await select_user_operations(user_id)
    overall_balance = sum(operations) if operations else 0
    referral = EnvKeys.REFERRAL_PERCENT
    
    markup = wallet_keyboard(referral)
    text = (
        f"💳 <b>{localize('btn.wallet', default='Wallet')}</b>\n\n"
        f"ID: <code>{user_id}</code>\n"
        f"{localize('profile.balance', amount=balance, currency=EnvKeys.PAY_CURRENCY)}\n"
        f"{localize('profile.total_topup', amount=overall_balance, currency=EnvKeys.PAY_CURRENCY)}"
    )
    
    await safe_edit_or_send(call, text, reply_markup=markup, parse_mode='HTML')
    await state.clear()


@router.callback_query(F.data == "support_none")
async def support_none_callback(call: CallbackQuery):
    await answer_callback_safe(call)
    await answer_callback_safe(call, localize("support.not_set", default="Support not configured"), show_alert=True)


@router.callback_query(F.data == "language")
async def language_callback(call: CallbackQuery):
    await answer_callback_safe(call)
    from bot.keyboards.inline import simple_buttons
    markup = simple_buttons([
        ("🇺🇸 English", "set_lang_en"),
        ("🇸🇦 العربية", "set_lang_ar"),
        ("🇨🇳 中文", "set_lang_zh"),
        ("🇪🇸 Español", "set_lang_es"),
        ("🇫🇷 Français", "set_lang_fr"),
        ("🇩🇪 Deutsch", "set_lang_de"),
        ("🇵🇹 Português", "set_lang_pt"),
        ("🇷🇺 Русский", "set_lang_ru"),
        ("🇹🇷 Türkçe", "set_lang_tr"),
        ("🇮🇳 हिन्दी", "set_lang_hi"),
        ("🇮🇩 Bahasa Indonesia", "set_lang_id"),
        ("🇻🇳 Tiếng Việt", "set_lang_vi"),
        ("🏠 Home", "back_to_menu")
    ], per_row=2)
    
    text = localize("language.choose", default="🌐 Choose your language")
    await safe_edit_or_send(call, text, reply_markup=markup, parse_mode='HTML')


@router.callback_query(F.data.startswith("set_lang_"))
async def set_lang_callback(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    lang = call.data.split("_")[2]
    await state.update_data(lang=lang)
    
    from bot.i18n.main import current_locale
    current_locale.set(lang)
    
    lang_names = {
        "en": "English",
        "ar": "العربية",
        "zh": "中文",
        "es": "Español",
        "fr": "Français",
        "de": "Deutsch",
        "pt": "Português",
        "ru": "Русский",
        "tr": "Türkçe",
        "hi": "हिन्दी",
        "id": "Bahasa Indonesia",
        "vi": "Tiếng Việt"
    }
    lang_name = lang_names.get(lang, lang.upper())
    
    await answer_callback_safe(call, f"Language updated: {lang_name}")
    
    user_id = call.from_user.id
    user = await check_user_cached(user_id)
    role_id = user.get('role_id') if user else 1
    channel_username = _parse_channel_username()
    
    await _send_or_edit_main_menu(call, role_id, channel_username, user_id)
    await state.set_state(None)