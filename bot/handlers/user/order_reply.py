import json
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.filters import StateFilter
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload
from typing import Any, Dict, Union
from aiogram.filters import Filter
import datetime

from bot.database.main import Database
from bot.database.models.main import Order, ManualFulfillmentJob, ManualOrderInteraction, ManualOrderConversationSession
from bot.misc.encryption import encrypt_text

router = Router()

class NonCommandConversationMessageFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return True
        if message.text.startswith('/'):
            return False
        if message.entities:
            for entity in message.entities:
                if entity.type == 'bot_command' and entity.offset == 0:
                    return False
        return True

class ActiveConversationFilter(Filter):
    async def __call__(self, message: Message) -> Union[bool, Dict[str, Any]]:
        async with Database().session() as session:
            result = await session.execute(
                select(ManualOrderConversationSession)
                .filter(
                    ManualOrderConversationSession.telegram_id == message.from_user.id,
                    ManualOrderConversationSession.status == 'active'
                )
            )
            active_session = result.scalar_one_or_none()
            if active_session:
                expires_at = active_session.expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                if expires_at > now_utc:
                    return {"active_session_id": active_session.id}
                if expires_at <= now_utc:
                    active_session.status = 'expired'
                    await session.commit()

            return False

@router.callback_query(F.data.startswith("reply_order_"))
async def on_reply_to_order(call: CallbackQuery):
    parts = call.data.split("_")
    order_id = int(parts[2])
    # For backward compatibility with existing "reply_order_1" buttons
    job_id = int(parts[3]) if len(parts) > 3 else None

    async with Database().session() as session:
        # Verify order exists and belongs to user
        result = await session.execute(
            select(Order).options(selectinload(Order.items)).filter(Order.id == order_id)
        )
        order = result.scalar_one_or_none()

        if not order or order.user_id != call.from_user.id:
            await call.answer("Order not found or access denied.", show_alert=True)
            return

        if order.status in ('completed', 'cancelled'):
            await call.answer("This Order is already completed or cancelled.", show_alert=True)
            return

        item_ids = [item.id for item in order.items]
        if not item_ids:
            await call.answer("Order has no items.", show_alert=True)
            return

        if not job_id:
            job_result = await session.execute(
                select(ManualFulfillmentJob)
                .filter(ManualFulfillmentJob.order_item_id.in_(item_ids))
                .limit(1)
            )
            job = job_result.scalar_one_or_none()
        else:
            job = await session.get(ManualFulfillmentJob, job_id)

        if not job or job.order_item_id not in item_ids:
            await call.answer("No fulfillment job found for this Order.", show_alert=True)
            return

        if job.status not in ('waiting_customer', 'in_progress', 'queued'):
            await call.answer("This Order cannot receive replies right now.", show_alert=True)
            return

        # Check existing active sessions for this user transactionally
        active_sessions_result = await session.execute(
            select(ManualOrderConversationSession)
            .filter(ManualOrderConversationSession.telegram_id == call.from_user.id, ManualOrderConversationSession.status == 'active')
            .with_for_update()
        )
        active_sessions = active_sessions_result.scalars().all()

        now = datetime.datetime.now(datetime.timezone.utc)
        expires = now + datetime.timedelta(minutes=30)

        existing_session = None
        for s in active_sessions:
            if s.order_id == order.id and s.fulfillment_job_id == job.id:
                existing_session = s
                s.last_activity_at = now
                s.expires_at = expires
            else:
                s.status = 'closed'
                s.closed_at = func.now()

        if not existing_session:
            new_conv = ManualOrderConversationSession(
                telegram_id=call.from_user.id,
                order_id=order.id,
                fulfillment_job_id=job.id,
                status='active',
                expires_at=expires
            )
            session.add(new_conv)

        await session.commit()

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        from bot.i18n.main import localize
        from bot.logger_mesh import logger
        import logging

        try:
            if call.message and call.message.reply_markup:
                # Keep only View Order button if present
                new_inline_keyboard = []
                for row in call.message.reply_markup.inline_keyboard:
                    new_row = [btn for btn in row if "orders:view" in (btn.callback_data or "")]
                    if new_row:
                        new_inline_keyboard.append(new_row)
                kb_clean = InlineKeyboardMarkup(inline_keyboard=new_inline_keyboard)
                await call.message.edit_reply_markup(reply_markup=kb_clean)
        except Exception as e:
            logger.warning(f"Could not remove old Reply to this Order button: {e}")

        # Try to find interaction id from the message if possible, else 0
        source_id = 0
        if call.message and call.message.reply_markup:
            for row in call.message.reply_markup.inline_keyboard:
                for btn in row:
                    if btn.callback_data and btn.callback_data.startswith("orders:view:"):
                        parts = btn.callback_data.split(":")
                        if len(parts) >= 5:
                            source_id = parts[4]

        # No buttons attached to the confirmation message
        from bot.i18n.main import get_locale
        locale = get_locale()
        is_ar = (locale == "ar")

        if is_ar:
            opened_msg = (
                "💬 تم فتح محادثة الطلب\n\n"
                f"رقم الطلب:\n{order.public_id}\n\n"
                "✅ المحادثة أصبحت فعّالة الآن.\n\n"
                "أرسل رمز التحقق أو أي رسائل إضافية مباشرة داخل هذه المحادثة.\n\n"
                "لا تحتاج إلى الضغط على زر الرد مرة أخرى."
            )
        else:
            opened_msg = (
                "💬 Order Conversation Opened\n\n"
                f"Order ID:\n{order.public_id}\n\n"
                "✅ The conversation is now active.\n\n"
                "Send the verification code or any additional messages directly in this chat.\n\n"
                "You do not need to press Reply again."
            )

        await call.message.answer(opened_msg)
        await call.answer()




@router.message(StateFilter(None), F.text, NonCommandConversationMessageFilter(), ActiveConversationFilter())
async def process_order_reply(message: Message, active_session_id: int):
    if len(message.text) > 1000:
        await message.answer("Your message is too long. Please keep it under 1000 characters.")
        return

    async with Database().session() as session:
        # Load session
        conv_res = await session.execute(
            select(ManualOrderConversationSession)
            .filter(ManualOrderConversationSession.id == active_session_id)
        )
        conv = conv_res.scalar_one_or_none()

        if not conv or conv.status != 'active':
            await message.answer("Conversation is no longer active.")
            return

        order_res = await session.execute(select(Order).filter(Order.id == conv.order_id))
        order = order_res.scalar_one_or_none()

        if not order or order.user_id != message.from_user.id or order.status in ('completed', 'cancelled'):
            conv.status = 'closed'
            conv.closed_at = func.now()
            await session.commit()
            await message.answer("This Order is no longer active or you don't have access.")
            return

        job_result = await session.execute(
            select(ManualFulfillmentJob).filter(ManualFulfillmentJob.id == conv.fulfillment_job_id)
        )
        job = job_result.scalar_one_or_none()

        if not job or job.status in ('completed', 'cancelled'):
            conv.status = 'closed'
            conv.closed_at = func.now()
            await session.commit()
            await message.answer("Fulfillment job not active.")
            return

        encrypted = encrypt_text(message.text)

        interaction = ManualOrderInteraction(
            order_id=order.id,
            fulfillment_job_id=job.id,
            direction='customer_to_admin',
            kind='customer_reply',
            encrypted_content=json.dumps(encrypted),
            safe_preview="Customer reply received",
            is_sensitive=True,
            telegram_message_id=message.message_id
        )

        conv.last_activity_at = func.now()
        conv.expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=30)

        session.add(interaction)
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            await message.answer("Failed to save your reply. Please try again later.")
            return

        from bot.i18n.main import get_locale
        locale = get_locale()
        is_ar = (locale == "ar")

        if is_ar:
            confirm_text = (
                f"✅ تم استلام الرسالة\n\n"
                f"تم ربط رسالتك بالطلب:\n{order.public_id}\n\n"
                f"يمكنك إرسال رسالة أخرى مباشرة."
            )
        else:
            confirm_text = (
                f"✅ Message Received\n\n"
                f"Your message was linked to Order:\n{order.public_id}\n\n"
                f"You may send another message directly."
            )
        await message.answer(confirm_text)

@router.callback_query(F.data.startswith("orders:source_back:"))
async def back_to_source(call: CallbackQuery):
    parts = call.data.split(":")
    if len(parts) < 5:
        await call.answer("Invalid callback data", show_alert=True)
        return

    order_id = int(parts[2])
    source_kind = parts[3]
    source_id = int(parts[4])

    async with Database().session() as session:
        # Load order, job
        order_res = await session.execute(
            select(Order)
            .options(selectinload(Order.items))
            .filter(Order.id == order_id)
        )
        order = order_res.scalar_one_or_none()

        if not order or order.user_id != call.from_user.id:
            await call.answer("Order not found or access denied.", show_alert=True)
            return

        item_ids = [item.id for item in order.items]
        if not item_ids:
            await call.answer("Order has no items.", show_alert=True)
            return

        job_result = await session.execute(
            select(ManualFulfillmentJob)
            .options(selectinload(ManualFulfillmentJob.order_item))
            .filter(ManualFulfillmentJob.order_item_id.in_(item_ids))
            .limit(1)
        )
        job = job_result.scalar_one_or_none()

        if not job:
            await call.answer("Fulfillment job not found.", show_alert=True)
            return

        product_name = job.order_item.product_name_snapshot

        active_session_res = await session.execute(
            select(ManualOrderConversationSession).filter(
                ManualOrderConversationSession.telegram_id == call.from_user.id,
                ManualOrderConversationSession.order_id == order_id,
                ManualOrderConversationSession.fulfillment_job_id == job.id,
                ManualOrderConversationSession.status == 'active'
            )
        )
        active_session = active_session_res.scalar_one_or_none()

        interaction = None
        if source_id:
            interaction_res = await session.execute(
                select(ManualOrderInteraction).filter(
                    ManualOrderInteraction.id == source_id,
                    ManualOrderInteraction.order_id == order_id,
                    ManualOrderInteraction.direction == 'admin_to_customer'
                )
            )
            interaction = interaction_res.scalar_one_or_none()

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        from bot.i18n.main import get_locale
        locale = get_locale()
        is_ar = (locale == "ar")

        text = ""
        kb = None

        # Fallback values
        fb_text = (
            f"📦 Order Details\n\n"
            f"Order ID:\n{order.public_id}"
        )
        fb_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="View Order", callback_data=f"orders:view:{order.id}:{source_kind}:{source_id}"),
                InlineKeyboardButton(text="🏠 Home", callback_data="back_to_menu")
            ]
        ])

        if source_kind == "v" and interaction:
            view_callback = f"orders:view:{order.id}:{source_kind}:{source_id}"
            if active_session:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="View Order", callback_data=view_callback)]
                ])
                if is_ar:
                    note = "\n\n✅ المحادثة فعّالة الآن. أرسل الكود مباشرة داخل المحادثة."
                    text = f"🔐 مطلوب رمز التحقق\n\nبدأنا بمعالجة طلبك.\nيرجى إرسال رمز التحقق الذي وصلك حتى نتمكن من متابعة تنفيذ الطلب.\n\n⚠️ للبدء:\nاضغط زر «الرد على هذا الطلب» مرة واحدة، ثم أرسل الكود.\n\nبعد ذلك يمكنك إرسال أي رسائل إضافية مباشرة دون الضغط على زر الرد مرة أخرى.{note}"
                else:
                    note = "\n\n✅ The conversation is already active. Send the code directly in this chat."
                    text = f"🔐 Verification Required\n\nWe have started processing your Order.\nPlease send the verification code you received to continue processing your Order.\n\n⚠️ To start:\nTap “Reply to this Order” once, then send the code.\n\nAfter that, you can send any additional messages directly without pressing Reply again.{note}"
            else:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Reply to this Order", callback_data=f"reply_order_{order.id}_{job.id}")],
                    [InlineKeyboardButton(text="View Order", callback_data=view_callback)]
                ])
                if is_ar:
                    text = f"🔐 مطلوب رمز التحقق\n\nبدأنا بمعالجة طلبك.\nيرجى إرسال رمز التحقق الذي وصلك حتى نتمكن من متابعة تنفيذ الطلب.\n\n⚠️ للبدء:\nاضغط زر «الرد على هذا الطلب» مرة واحدة، ثم أرسل الكود.\n\nبعد ذلك يمكنك إرسال أي رسائل إضافية مباشرة دون الضغط على زر الرد مرة أخرى."
                else:
                    text = f"🔐 Verification Required\n\nWe have started processing your Order.\nPlease send the verification code you received to continue processing your Order.\n\n⚠️ To start:\nTap “Reply to this Order” once, then send the code.\n\nAfter that, you can send any additional messages directly without pressing Reply again."

        elif source_kind == "m" and interaction:
            view_callback = f"orders:view:{order.id}:{source_kind}:{source_id}"
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="View Order", callback_data=view_callback)]
            ])
            text = f"💬 Message About Your Order\n\nOrder ID:\n{order.public_id}\n\n{interaction.safe_preview}"

        elif source_kind == "c":
            view_callback = f"orders:view:{order.id}:{source_kind}:{source_id}"
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="View Order", callback_data=view_callback),
                    InlineKeyboardButton(text="🏠 Home", callback_data="back_to_menu")
                ]
            ])
            text = f"✅ Order Completed\n\nOrder ID:\n{order.public_id}\n\nProduct:\n{product_name}\n\nYour Order has been completed successfully."

        else:
            text = fb_text
            kb = fb_kb

        try:
            await call.message.edit_text(text, reply_markup=kb)
        except Exception:
            # Fallback if text is the same or something went wrong
            try:
                await call.message.answer(text, reply_markup=kb)
                await call.message.delete()
            except Exception:
                pass

        await call.answer()

@router.message(StateFilter(None), ~F.text, ActiveConversationFilter())
async def reject_non_text_reply(message: Message, active_session_id: int):
    await message.answer("Please send text only.")
