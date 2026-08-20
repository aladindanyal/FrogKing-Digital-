from datetime import datetime
from typing import Optional

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext

from bot.database.models.main import Permission
from bot.i18n import localize
from bot.i18n.registry import get_enabled_locales, LOCALE_METADATA
from bot.keyboards import back
from bot.filters import HasPermissionFilter
from bot.states import BroadcastFSM
from bot.misc.services.broadcast_dispatcher import broadcast_dispatcher
from bot.misc.services.broadcast_service import validate_payload, create_draft, confirm_campaign, cancel_campaign

router = Router()

def build_target_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=localize("broadcast.target_all"), callback_data="bc_target_all")]
    ]
    for loc in get_enabled_locales():
        name = LOCALE_METADATA[loc]["name"]
        buttons.append([InlineKeyboardButton(text=localize("broadcast.target_locale", name=name), callback_data=f"bc_target_{loc}")])
    buttons.append([InlineKeyboardButton(text=localize("buttons.back"), callback_data="console")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def build_confirm_keyboard(campaign_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=localize("broadcast.btn_confirm"), callback_data=f"bc_confirm_{campaign_id}")],
        [InlineKeyboardButton(text=localize("broadcast.btn_cancel"), callback_data=f"bc_cancel_{campaign_id}")]
    ])

@router.callback_query(F.data == "send_message", HasPermissionFilter(permission=Permission.BROADCAST))
async def send_message_callback_handler(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        localize("broadcast.select_audience"),
        reply_markup=build_target_keyboard(),
    )
    await state.set_state(BroadcastFSM.waiting_target)

@router.callback_query(BroadcastFSM.waiting_target, F.data.startswith("bc_target_"), HasPermissionFilter(permission=Permission.BROADCAST))
async def target_callback_handler(call: CallbackQuery, state: FSMContext):
    target = call.data.replace("bc_target_", "")
    await state.update_data(target_locale=target if target != "all" else None)

    await call.message.edit_text(
        localize("broadcast.prompt"),
        reply_markup=back("send_message"),
    )
    await state.set_state(BroadcastFSM.waiting_message)

@router.message(BroadcastFSM.waiting_message, F.text | F.photo, HasPermissionFilter(permission=Permission.BROADCAST))
async def broadcast_message_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    target_locale = data.get("target_locale")

    text_content = message.html_text or ""
    photo_file_id = None
    if message.photo:
        photo_file_id = message.photo[-1].file_id

    is_valid, err_msg, safe_text = await validate_payload(text_content, photo_file_id, target_locale)

    if not is_valid:
        if err_msg == "Payload cannot be completely empty.":
            await message.answer(localize("broadcast.error_empty"), reply_markup=back("send_message"))
        elif err_msg == "Text payload exceeds maximum length of 4000 characters.":
            await message.answer(localize("broadcast.error_length"), reply_markup=back("send_message"))
        else:
            await message.answer(err_msg, reply_markup=back("send_message"))
        return

    campaign = await create_draft(message.from_user.id, target_locale, safe_text, photo_file_id)

    await message.answer(localize("broadcast.preview_title"))
    if photo_file_id:
        await message.answer_photo(photo=photo_file_id, caption=safe_text, parse_mode="HTML")
    else:
        await message.answer(safe_text, parse_mode="HTML", disable_web_page_preview=True)

    await message.answer(
        localize("broadcast.confirm_prompt"),
        reply_markup=build_confirm_keyboard(campaign.id)
    )
    await state.set_state(BroadcastFSM.waiting_confirmation)

@router.callback_query(BroadcastFSM.waiting_confirmation, F.data.startswith("bc_confirm_"), HasPermissionFilter(permission=Permission.BROADCAST))
async def confirm_broadcast_handler(call: CallbackQuery, state: FSMContext):
    campaign_id = int(call.data.replace("bc_confirm_", ""))

    success, msg, campaign, recipient_count = await confirm_campaign(campaign_id)

    if not success:
        if msg == "Another campaign is currently active.":
            await call.answer(localize("broadcast.active_conflict"), show_alert=True)
        elif msg == "Campaign is not in draft state.":
            await call.answer(localize("broadcast.already_confirmed"), show_alert=True)
        elif msg == "Campaign not found.":
            await call.answer(localize("errors.invalid_data"), show_alert=True)
        else:
            await call.answer(msg, show_alert=True)
        return

    await call.message.edit_text(localize("broadcast.queued", count=recipient_count))
    broadcast_dispatcher.wake_up()
    await state.clear()

@router.callback_query(F.data.startswith("bc_cancel_"), HasPermissionFilter(permission=Permission.BROADCAST))
async def cancel_broadcast_handler(call: CallbackQuery, state: FSMContext):
    campaign_id = int(call.data.replace("bc_cancel_", ""))

    success, msg = await cancel_campaign(campaign_id, call.from_user.id)

    if not success:
        if msg == "Campaign not found.":
            await call.answer(localize("errors.invalid_data"), show_alert=True)
        elif msg == "Campaign is already finished.":
            await call.answer(localize("broadcast.already_finished"), show_alert=True)
        else:
            await call.answer(msg, show_alert=True)
        return

    await call.message.edit_text(localize("broadcast.cancel"))
    await state.clear()
