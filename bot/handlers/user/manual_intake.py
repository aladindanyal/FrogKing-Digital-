import asyncio
from typing import Optional

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.database import Database
from bot.database.methods import get_item_info_cached, get_store_settings
from bot.database.methods.read import get_active_product_fields
from bot.database.methods.intake_drafts import (
    get_or_create_draft, get_expected_steps, save_draft_answer, get_draft_by_token
)
from bot.database.methods.transactions import buy_item_transaction
from bot.misc.intake_validator import validate_field_input, IntakeValidationError
from bot.misc import encryption
from bot.misc.customer_fields import get_localized_label
from bot.misc.utils import safe_edit_or_send, answer_callback_safe, ensure_utc
from bot.i18n import localize
from bot.misc.env import EnvKeys
from bot.keyboards.inline import simple_buttons

router = Router()

class ManualIntakeStates(StatesGroup):
    WAITING_FOR_ANSWER = State()

def get_intake_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=localize("intake.btn.cancel_draft", default="❌ Cancel"), callback_data="intake_cancel")]
    ])

def get_intake_skip_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=localize("intake.btn.skip", default="⏭ Skip"), callback_data="intake_skip")],
        [InlineKeyboardButton(text=localize("intake.btn.cancel_draft", default="❌ Cancel"), callback_data="intake_cancel")]
    ])

def get_intake_select_keyboard(options: list, language: str) -> InlineKeyboardMarkup:
    buttons = []
    for opt in options:
        label = get_localized_label(opt.get("label_i18n", {}), language) or opt.get("key", "Unknown")
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"intake_sel:{opt['key']}")])
    buttons.append([InlineKeyboardButton(text=localize("intake.btn.cancel_draft", default="❌ Cancel"), callback_data="intake_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def _render_step(message, state: FSMContext, draft, active_fields, quantity: int, language: str, is_new: bool = False, item_name: str = "", intro_text: str = ""):
    steps = get_expected_steps(active_fields, quantity)
    
    if draft.current_step >= len(steps):
        # All answered, show review
        await _render_review(message, state, draft, steps, language)
        return

    step = steps[draft.current_step]
    field = step["field"]
    unit_index = step["unit_index"]
    
    label = get_localized_label(field.label_i18n, language) or field.field_key
    help_text = get_localized_label(field.help_text_i18n, language) if field.help_text_i18n else ""
    
    step_msg = ""
    if is_new:
        if intro_text:
            step_msg += f"{intro_text}\n\n"
        step_msg += f"📝 <b>Order Information</b>\n\n"
        step_msg += f"Product:\n{item_name}\n\n"
        step_msg += f"Quantity:\n{quantity}\n\n"
        step_msg += f"Please enter the {label.lower()}.\n"
        if field.is_sensitive:
            step_msg += f"\nSecurity:\nNever send OTP or recovery codes.\n\n"
    else:
        step_msg += f"<b>{localize('intake.step_progress', current=draft.current_step+1, total=len(steps))}</b>\n\n"
        if field.scope == 'per_unit':
            step_msg += f"📦 <b>Item #{unit_index}</b>\n\n"
            
        step_msg += f"<b>{label}</b>\n"
        if help_text:
            step_msg += f"<i>{help_text}</i>\n"
            
        if field.is_sensitive:
            step_msg += f"\n{localize('intake.secret_warning')}\n"
        
    if field.field_type == 'select':
        kb = get_intake_select_keyboard(field.select_options_i18n, language)
        sent_msg = await message.answer(step_msg, reply_markup=kb, parse_mode="HTML")
    else:
        kb = get_intake_skip_cancel_keyboard() if not field.required else get_intake_cancel_keyboard()
        step_msg += f"\n{localize('intake.send_answer')}"
        sent_msg = await message.answer(step_msg, reply_markup=kb, parse_mode="HTML")
        
    await state.update_data(
        intake_draft_id=draft.id,
        intake_current_field_id=field.id,
        intake_question_msg_id=sent_msg.message_id
    )
    await state.set_state(ManualIntakeStates.WAITING_FOR_ANSWER)


async def _render_review(message, state: FSMContext, draft, steps, language: str):
    payload = encryption.decrypt_json(draft.encrypted_payload, draft.encryption_version)
    answers = payload.get("answers", [])
    
    text = f"<b>{localize('intake.review.title')}</b>\n\n"
    text += f"{localize('intake.review.instructions')}\n\n"
    
    for ans in answers:
        if ans.get("field_type") == 'secret':
            val = "********"
        else:
            val = ans.get("value", "")
            if not val:
                val = "—"
                
        # Find label
        field_match = next((s["field"] for s in steps if s["field"].id == ans.get("field_id")), None)
        label = get_localized_label(field_match.label_i18n, language) if field_match else ans.get("field_key")
        
        if ans.get("scope") == 'per_unit':
            label = f"[#{ans.get('unit_index')}] {label}"
            
        text += localize('intake.review.item', label=label, value=val) + "\n"
        
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=localize("intake.btn.confirm_pay"), callback_data="intake_confirm")],
        [InlineKeyboardButton(text=localize("intake.btn.cancel_draft"), callback_data="intake_cancel")]
    ])
    
    await message.answer(text, reply_markup=kb, parse_mode="HTML")
    await state.set_state(None)


async def start_manual_intake(event: CallbackQuery | Message, state: FSMContext, item_name: str, item_id_str: str, user_id: int, bypass_active_check: bool = False):
    """Entry point for manual product checkout."""
    data = await state.get_data()
    quantity = data.get('item_quantity', 1)
    
    # Store minimal intent
    await state.update_data(
        intake_item_name=item_name,
        intake_item_id_str=item_id_str,
        intake_quantity=quantity,
        intake_applied_promo=data.get('applied_promo')
    )
    
    language = data.get("language", "en")
    

    async with Database().session() as session:
        item_info = await get_item_info_cached(item_name)
        
        if not bypass_active_check:
            from bot.database.models import Order, OrderItem
            from sqlalchemy import select
            
            active_order = await session.scalar(
                select(Order)
                .join(OrderItem, Order.id == OrderItem.order_id)
                .where(
                    Order.user_id == user_id,
                    Order.paid_at.isnot(None),
                    Order.status == 'processing',
                    OrderItem.item_id == item_info['id']
                )
                .limit(1)
            )
            
            if active_order:
                default_msg = f"You already have an order being processed for this product.\n\nOrder: {active_order.public_id}\nStatus: Processing"
                msg = (
                    f"<b>{localize('intake.active_order.title', default='Active Order Found')}</b>\n\n"
                    f"{localize('intake.active_order.body', order_id=active_order.public_id, default=default_msg)}"
                )
                from bot.keyboards.inline import back
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=localize("intake.btn.view_order", default="View Existing Order"), callback_data=f"orders:view:{active_order.id}:a")],
                    [InlineKeyboardButton(text=localize("intake.btn.buy_another", default="Buy Another"), callback_data="intake_buy_another")],
                    [InlineKeyboardButton(text=localize("btn.back", default="Back"), callback_data="shop")]
                ])
                await safe_edit_or_send(event, msg, reply_markup=kb)
                return

        active_fields = await get_active_product_fields(session, item_info['id'])
        
        draft, draft_reason = await get_or_create_draft(session, user_id, item_info['id'], quantity, active_fields)
        await session.commit()
        
        message_obj = event.message if isinstance(event, CallbackQuery) else event

    if draft_reason == 'expired':
        await message_obj.answer(localize("intake.error.expired"))
    elif draft_reason == 'mismatched':
        await message_obj.answer(localize("intake.error.mismatched"))

    if not active_fields:
        # Zero fields edge case: skip intake, go straight to review/payment
        await _render_review(message_obj, state, draft, [], language)
        return

    if draft.current_step == 0 or draft_reason is not None:
        # New draft (or previously started but no questions answered): Jump straight to question 1
        intro_text = get_localized_label(item_info.get("customer_input_intro_i18n"), language)
        await _render_step(message_obj, state, draft, active_fields, quantity, language, is_new=True, item_name=item_name, intro_text=intro_text)
    else:
        # Resume or Start Over
        msg = f"<b>{localize('intake.resume_title')}</b>\n\n{localize('intake.resume_body', product_name=item_name)}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=localize("intake.btn.resume"), callback_data="intake_start")],
            [InlineKeyboardButton(text=localize("intake.btn.start_over"), callback_data="intake_start_over")]
        ])
        await safe_edit_or_send(event, msg, reply_markup=kb)


@router.callback_query(F.data == "intake_buy_another")
async def handle_intake_buy_another(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    data = await state.get_data()
    item_name = data.get('intake_item_name')
    item_id_str = data.get('intake_item_id_str')
    
    if not item_name or not item_id_str:
        from bot.keyboards.inline import back
        await safe_edit_or_send(call, "Session expired.", reply_markup=back("shop"))
        return
        
    try:
        await call.message.delete()
    except Exception:
        pass
        
        
    await start_manual_intake(call, state, item_name, item_id_str, call.from_user.id, bypass_active_check=True)

@router.callback_query(F.data == "intake_start")
async def handle_intake_start(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    data = await state.get_data()
    item_name = data.get('intake_item_name')
    quantity = data.get('intake_quantity', 1)
    
    language = data.get("language", "en")
    

    async with Database().session() as session:
        item_info = await get_item_info_cached(item_name)
        active_fields = await get_active_product_fields(session, item_info['id'])
        draft, _ = await get_or_create_draft(session, call.from_user.id, item_info['id'], quantity, active_fields)
        steps = get_expected_steps(active_fields, quantity)
        
    if draft.current_step >= len(steps):
        await _render_review(call.message, state, draft, steps, language)
    else:
        await _render_step(call.message, state, draft, active_fields, quantity, language)


@router.callback_query(F.data == "intake_start_over")
async def handle_intake_start_over(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    data = await state.get_data()
    item_name = data.get('intake_item_name')
    quantity = data.get('intake_quantity', 1)
    
    language = data.get("language", "en")
    
    async with Database().session() as session:
        item_info = await get_item_info_cached(item_name)
        active_fields = await get_active_product_fields(session, item_info['id'])
        from bot.database.models.main import CheckoutIntakeDraft
        from sqlalchemy import update
        from sqlalchemy.sql import func
        from datetime import datetime, timezone
        
        await session.execute(
            update(CheckoutIntakeDraft)
            .where(CheckoutIntakeDraft.user_id == call.from_user.id, CheckoutIntakeDraft.goods_id == item_info['id'], CheckoutIntakeDraft.status == 'pending')
            .values(status='invalidated', invalidated_at=datetime.now(timezone.utc))
        )
        await state.set_state(None) # Clear old FSM context
        draft, _ = await get_or_create_draft(session, call.from_user.id, item_info['id'], quantity, active_fields)
        await session.commit()
        
        intro_text = get_localized_label(item_info.get("customer_input_intro_i18n"), language)
        
    await _render_step(call.message, state, draft, active_fields, quantity, language, is_new=True, item_name=item_name, intro_text=intro_text)


@router.callback_query(F.data == "intake_cancel")
async def handle_intake_cancel(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    
    data = await state.get_data()
    draft_id = data.get('intake_draft_id')
    item_id_str = data.get('intake_item_id_str', '0')
    item_name = data.get('intake_item_name')
    if draft_id:
        async with Database().session() as session:
            from bot.database.models.main import CheckoutIntakeDraft
            from sqlalchemy import select
            from datetime import datetime, timezone
            
            draft = await session.scalar(select(CheckoutIntakeDraft).where(CheckoutIntakeDraft.id == draft_id, CheckoutIntakeDraft.user_id == call.from_user.id))
            if draft and draft.status == 'pending':
                draft.status = 'cancelled'
                draft.cancelled_at = datetime.now(timezone.utc)
                session.add(draft)
                await session.commit()
    
    await state.set_state(None)
    
    if item_name:
        from bot.handlers.user.shop_and_goods import _render_item_page
        await _render_item_page(call, state, item_name, user_id=call.from_user.id)
    else:
        from bot.keyboards.inline import back
        from bot.misc.utils import safe_edit_or_send
        await safe_edit_or_send(call, localize("shop.goods.choose"), reply_markup=back("shop"))


@router.callback_query(F.data == "intake_skip")
async def handle_intake_skip(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    await process_draft_answer(call.message, state, call.from_user.id, "")


@router.callback_query(F.data.startswith("intake_sel:"))
async def handle_intake_select(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    selected_key = call.data.split(":", 1)[1]
    await process_draft_answer(call.message, state, call.from_user.id, selected_key)


@router.message(ManualIntakeStates.WAITING_FOR_ANSWER)
async def handle_intake_text(message: Message, state: FSMContext):
    await process_draft_answer(message, state, message.from_user.id, message.text or "")


async def process_draft_answer(message_or_call_msg, state: FSMContext, user_id: int, answer_text: str):
    data = await state.get_data()
    item_name = data.get('intake_item_name')
    quantity = data.get('intake_quantity', 1)
    draft_id = data.get('intake_draft_id')
    
    language = data.get("language", "en")
    

    async with Database().session() as session:
        from bot.database.models.main import CheckoutIntakeDraft
        from sqlalchemy import select
        draft = await session.scalar(select(CheckoutIntakeDraft).where(CheckoutIntakeDraft.id == draft_id, CheckoutIntakeDraft.user_id == user_id))
        
        if not draft or draft.status != 'pending':
            await state.set_state(None)
            await message_or_call_msg.answer(localize("intake.draft_invalidated"))
            return
            
        item_info = await get_item_info_cached(item_name)
        active_fields = await get_active_product_fields(session, item_info['id'])
        steps = get_expected_steps(active_fields, quantity)
        
        if draft.current_step >= len(steps):
            await _render_review(message_or_call_msg, state, draft, steps, language)
            return
            
        step = steps[draft.current_step]
        field = step["field"]
        
        try:
            validated_val = validate_field_input(field, answer_text)
        except IntakeValidationError as e:
            await message_or_call_msg.answer(localize("intake.invalid_answer", error=str(e)))
            return
            
        question_msg_id = data.get('intake_question_msg_id')
        if question_msg_id:
            try:
                await message_or_call_msg.bot.edit_message_reply_markup(
                    chat_id=message_or_call_msg.chat.id,
                    message_id=question_msg_id,
                    reply_markup=None
                )
            except Exception:
                pass
                
        # Secret message deletion best-effort
        if field.is_sensitive and isinstance(message_or_call_msg, Message):
            try:
                await message_or_call_msg.delete()
            except Exception:
                pass
            
            if field.field_type == 'secret':
                temp_msg = await message_or_call_msg.answer("Password received ✅")
                await asyncio.sleep(1)
                try:
                    await temp_msg.delete()
                except Exception:
                    pass
                
        await save_draft_answer(session, draft, step, validated_val)
        await session.commit()
        
    await _render_step(message_or_call_msg, state, draft, active_fields, quantity, language, item_name=item_name)


@router.callback_query(F.data == "intake_confirm")
async def handle_intake_confirm(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    item_name = data.get('intake_item_name')
    quantity = data.get('intake_quantity', 1)
    promo_code = data.get('intake_applied_promo')
    draft_id = data.get('intake_draft_id')
    
    if not draft_id:
        await answer_callback_safe(call, localize("intake.draft_invalidated", default="Draft expired or invalid."), show_alert=True)
        return
        
    await answer_callback_safe(call, localize("shop.purchase.processing", default="⏳ Processing..."))
    
    # 1. Guard check before buy_item_transaction
    async with Database().session() as session:
        from bot.database.models.main import CheckoutIntakeDraft
        from bot.misc.intake_validator import compute_schema_fingerprint
        from sqlalchemy import select
        from datetime import datetime, timezone
        
        draft = await session.scalar(select(CheckoutIntakeDraft).where(CheckoutIntakeDraft.id == draft_id, CheckoutIntakeDraft.user_id == call.from_user.id))
        
        def _get_start_over_kb():
            return InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=localize("intake.btn.start_over", default="Start Over"), callback_data="intake_start_over")]
            ])

        if not draft or draft.status != 'pending':
            await state.set_state(None)
            await safe_edit_or_send(call, localize("intake.draft_invalidated", default="Draft expired or invalid."), reply_markup=_get_start_over_kb())
            return
            
        now = datetime.now(timezone.utc)
        if ensure_utc(draft.expires_at) <= now:
            draft.status = 'expired'
            draft.invalidated_at = now
            session.add(draft)
            await session.commit()
            await state.set_state(None)
            await safe_edit_or_send(call, localize("intake.error.expired", default="Your saved order information has expired. A new form will be started."), reply_markup=_get_start_over_kb())
            return

        item_info = await get_item_info_cached(item_name)
        active_fields = await get_active_product_fields(session, item_info['id'])
        fingerprint = compute_schema_fingerprint(active_fields)

        if draft.schema_fingerprint != fingerprint or draft.quantity != quantity:
            draft.status = 'invalidated'
            draft.invalidated_at = now
            session.add(draft)
            await session.commit()
            await state.set_state(None)
            await safe_edit_or_send(call, localize("intake.error.mismatched", default="The required product information has changed. A new form will be started."), reply_markup=_get_start_over_kb())
            return
            
        draft_token = draft.public_token
        
        steps = get_expected_steps(active_fields, quantity)
        
        if draft.current_step < len(steps):
            await safe_edit_or_send(call, localize("intake.incomplete", default="Please complete all required fields."))
            return

    success, error_key, details = await buy_item_transaction(
        telegram_id=call.from_user.id,
        item_name=item_name,
        promo_code=promo_code,
        quantity=quantity,
        draft_public_token=draft_token
    )
    
    if success:
        await state.set_state(None)
        
        from bot.handlers.user.orders import render_order_receipt
        msg, kb = await render_order_receipt(session, details.get('order_id'))
        await safe_edit_or_send(call, msg, reply_markup=kb)
    else:
        # Re-allow resume if it failed due to balance/stock but draft is still fine. Or show start over?
        # Actually buy_item_transaction rollbacks, so draft remains pending.
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=localize("intake.btn.resume", default="Resume Order"), callback_data="intake_start")]
        ])
        await safe_edit_or_send(call, localize(error_key, default=f"Error: {error_key}"), reply_markup=kb)
