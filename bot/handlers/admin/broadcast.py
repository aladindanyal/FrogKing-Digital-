from datetime import datetime
from typing import Optional

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, text, func, update
from bot.database.main import Database
from bot.database.models.main import BroadcastCampaign, BroadcastRecipient, Permission, User
from bot.i18n import localize
from bot.i18n.registry import get_enabled_locales, LOCALE_METADATA
from bot.keyboards import back
from bot.filters import HasPermissionFilter
from bot.misc import sanitize_html
from bot.states import BroadcastFSM
from bot.misc.services.broadcast_dispatcher import broadcast_dispatcher

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

    if not text_content and not photo_file_id:
        await message.answer(localize("broadcast.error_empty"), reply_markup=back("send_message"))
        return

    if len(text_content) > 4000:
        await message.answer(localize("broadcast.error_length"), reply_markup=back("send_message"))
        return

    safe_text = sanitize_html(text_content)

    async with Database().session() as session:
        campaign = BroadcastCampaign(
            admin_id=message.from_user.id,
            target_locale=target_locale,
            message_text=safe_text,
            photo_file_id=photo_file_id,
            parse_mode="HTML",
            status="draft"
        )
        session.add(campaign)
        await session.commit()
        await session.refresh(campaign)

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

    async with Database().session() as session:
        # Check active campaigns
        active_count = (await session.execute(
            select(func.count()).select_from(BroadcastCampaign).where(BroadcastCampaign.status.in_(['confirmed', 'running']))
        )).scalar()

        if active_count > 0:
            await call.answer(localize("broadcast.active_conflict"), show_alert=True)
            return

        campaign = await session.get(BroadcastCampaign, campaign_id)
        if not campaign:
            await call.answer(localize("errors.invalid_data"), show_alert=True)
            return

        if campaign.status != "draft":
            await call.answer(localize("broadcast.already_confirmed"), show_alert=True)
            return

        # Build snapshot
        query = select(User.telegram_id).where(User.is_blocked.is_(False))
        if campaign.target_locale:
            query = query.where(User.language_code == campaign.target_locale)

        users = (await session.execute(query)).scalars().all()

        # Insert recipients (ignore conflicts handled by SQLAlchemy merge or manual insert)
        recipients = [BroadcastRecipient(campaign_id=campaign.id, user_id=uid) for uid in users]
        session.add_all(recipients)

        campaign.status = "confirmed"
        await session.commit()

    await call.message.edit_text(localize("broadcast.queued", count=len(users)))
    broadcast_dispatcher.wake_up()
    await state.clear()

@router.callback_query(F.data.startswith("bc_cancel_"), HasPermissionFilter(permission=Permission.BROADCAST))
async def cancel_broadcast_handler(call: CallbackQuery, state: FSMContext):
    campaign_id = int(call.data.replace("bc_cancel_", ""))

    async with Database().session() as session:
        campaign = await session.get(BroadcastCampaign, campaign_id)
        if not campaign:
            await call.answer(localize("errors.invalid_data"), show_alert=True)
            return

        if campaign.status in ["completed", "cancelled", "failed"]:
            await call.answer(localize("broadcast.already_finished"), show_alert=True)
            return

        campaign.status = "cancelled"

        # Cancel pending recipients
        await session.execute(
            update(BroadcastRecipient)
            .where(BroadcastRecipient.campaign_id == campaign_id, BroadcastRecipient.status == 'pending')
            .values(status='cancelled', updated_at=func.now())
        )
        await session.commit()

    await call.message.edit_text(localize("broadcast.cancel"))
    await state.clear()
