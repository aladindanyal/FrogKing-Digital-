import asyncio
import logging
from sqlalchemy import select, update, func
from sqlalchemy.orm import selectinload
from bot.database.main import Database
from bot.database.models.main import ManualOrderNotification, Order, ManualFulfillmentJob, OrderItem, User

logger = logging.getLogger(__name__)

class OutboxDispatcher:
    def __init__(self, polling_interval: int = 15):
        self.polling_interval = polling_interval
        self._is_running = False
        self._task = None
        self.bot = None
        self._wake_event = asyncio.Event()

    def wake_up(self):
        self._wake_event.set()

    async def start(self, bot):
        if self._is_running:
            return
        self.bot = bot
        self._is_running = True
        self._task = asyncio.create_task(self._poll_outbox())
        logger.info("OutboxDispatcher started")

    async def stop(self):
        self._is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("OutboxDispatcher stopped")

    async def _poll_outbox(self):
        while self._is_running:
            try:
                await self._process_pending_notifications()
            except Exception as e:
                logger.error(f"Error in OutboxDispatcher: {e}")

            try:
                # Wait for either the polling interval or the wake event
                await asyncio.wait_for(self._wake_event.wait(), timeout=self.polling_interval)
                self._wake_event.clear()
            except asyncio.TimeoutError:
                pass

    async def _process_pending_notifications(self):
        async with Database().session() as session:
            # Find pending notifications
            result = await session.execute(
                select(ManualOrderNotification)
                .options(
                    selectinload(ManualOrderNotification.order).selectinload(Order.user),
                    selectinload(ManualOrderNotification.job).selectinload(ManualFulfillmentJob.order_item)
                )
                .filter(
                    ManualOrderNotification.status == 'pending',
                    (ManualOrderNotification.next_attempt_at == None) | (ManualOrderNotification.next_attempt_at <= func.now())
                )
                .limit(50)
            )
            notifications = result.scalars().all()

            if not notifications:
                return

            for notif in notifications:
                try:
                    await self._send_notification(notif, session)
                    notif.status = 'sent'
                    notif.sent_at = func.now()
                except Exception as e:
                    logger.error(f"Failed to send notification {notif.id}: {e}")
                    notif.attempts += 1
                    notif.last_error = str(e)

                    if notif.attempts >= 3:
                        notif.status = 'failed'
                    else:
                        # Exponential backoff: 30s, 120s
                        backoff = 30 * (4 ** (notif.attempts - 1))
                        notif.next_attempt_at = func.now() + func.cast(f"{backoff} seconds", sqlalchemy.Interval)

            await session.commit()

    async def _send_notification(self, notif: ManualOrderNotification, session):
        telegram_id = notif.order.user.telegram_id
        public_id = notif.order.public_id
        product_name = notif.job.order_item.product_name_snapshot

        parts = notif.idempotency_key.split('_')
        notif_type = parts[0]

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        from bot.database.models.main import ManualOrderInteraction, ManualOrderConversationSession
        from sqlalchemy import select

        interaction = None
        if notif_type in ('msg', 'verify', 'comp') and len(parts) >= 2:
            interaction_id = int(parts[1])
            interaction = await session.get(ManualOrderInteraction, interaction_id)

        # Centralized keyboard policy
        active_session_res = await session.execute(
            select(ManualOrderConversationSession).filter(
                ManualOrderConversationSession.telegram_id == telegram_id,
                ManualOrderConversationSession.order_id == notif.order_id,
                ManualOrderConversationSession.fulfillment_job_id == notif.fulfillment_job_id,
                ManualOrderConversationSession.status == 'active'
            )
        )
        is_active = active_session_res.scalar_one_or_none() is not None

        # Source context logic
        source_id = interaction.id if interaction else 0

        kb = None
        message_text = ""

        from bot.i18n.main import get_locale
        locale = get_locale()
        is_ar = (locale == "ar")

        if notif_type == 'comp':
            source_kind = "c"
            view_callback = f"orders:view:{notif.order_id}:{source_kind}:{source_id}"
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="View Order", callback_data=view_callback),
                    InlineKeyboardButton(text="🏠 Home", callback_data="back_to_menu")
                ]
            ])
            message_text = f"✅ Order Completed\n\nOrder ID:\n{public_id}\n\nProduct:\n{product_name}\n\nYour Order has been completed successfully."
            if interaction and interaction.safe_preview and interaction.safe_preview != "Order completed":
                message_text += f"\n\n{interaction.safe_preview}"

        elif notif_type == 'verify':
            source_kind = "v"
            view_callback = f"orders:view:{notif.order_id}:{source_kind}:{source_id}"

            if is_active:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="View Order", callback_data=view_callback)]
                ])
                if is_ar:
                    note = "\n\n✅ المحادثة فعّالة الآن. أرسل الكود مباشرة داخل المحادثة."
                    message_text = f"🔐 مطلوب رمز التحقق\n\nبدأنا بمعالجة طلبك.\nيرجى إرسال رمز التحقق الذي وصلك حتى نتمكن من متابعة تنفيذ الطلب.\n\n⚠️ للبدء:\nاضغط زر «الرد على هذا الطلب» مرة واحدة، ثم أرسل الكود.\n\nبعد ذلك يمكنك إرسال أي رسائل إضافية مباشرة دون الضغط على زر الرد مرة أخرى.{note}"
                else:
                    note = "\n\n✅ The conversation is already active. Send the code directly in this chat."
                    message_text = f"🔐 Verification Required\n\nWe have started processing your Order.\nPlease send the verification code you received to continue processing your Order.\n\n⚠️ To start:\nTap “Reply to this Order” once, then send the code.\n\nAfter that, you can send any additional messages directly without pressing Reply again.{note}"
            else:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Reply to this Order", callback_data=f"reply_order_{notif.order_id}_{notif.fulfillment_job_id}")],
                    [InlineKeyboardButton(text="View Order", callback_data=view_callback)]
                ])
                if is_ar:
                    message_text = f"🔐 مطلوب رمز التحقق\n\nبدأنا بمعالجة طلبك.\nيرجى إرسال رمز التحقق الذي وصلك حتى نتمكن من متابعة تنفيذ الطلب.\n\n⚠️ للبدء:\nاضغط زر «الرد على هذا الطلب» مرة واحدة، ثم أرسل الكود.\n\nبعد ذلك يمكنك إرسال أي رسائل إضافية مباشرة دون الضغط على زر الرد مرة أخرى."
                else:
                    message_text = f"🔐 Verification Required\n\nWe have started processing your Order.\nPlease send the verification code you received to continue processing your Order.\n\n⚠️ To start:\nTap “Reply to this Order” once, then send the code.\n\nAfter that, you can send any additional messages directly without pressing Reply again."

        elif notif_type == 'msg':
            source_kind = "m"
            view_callback = f"orders:view:{notif.order_id}:{source_kind}:{source_id}"

            if is_active:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="View Order", callback_data=view_callback)]
                ])
                if is_ar:
                    message_text = f"💬 رسالة حول طلبك\n\nرقم الطلب:\n{public_id}\n\n{interaction.safe_preview}\n\n✅ المحادثة فعّالة الآن. يمكنك الرد مباشرة."
                else:
                    message_text = f"💬 Message About Your Order\n\nOrder ID:\n{public_id}\n\n{interaction.safe_preview}\n\n✅ The conversation is active. You can reply directly."
            else:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Reply to this Order", callback_data=f"reply_order_{notif.order_id}_{notif.fulfillment_job_id}")],
                    [InlineKeyboardButton(text="View Order", callback_data=view_callback)]
                ])
                if is_ar:
                    message_text = f"💬 رسالة حول طلبك\n\nرقم الطلب:\n{public_id}\n\n{interaction.safe_preview}\n\n⚠️ للرد:\nاضغط زر «الرد على هذا الطلب» مرة واحدة، ثم اكتب رسالتك."
                else:
                    message_text = f"💬 Message About Your Order\n\nOrder ID:\n{public_id}\n\n{interaction.safe_preview}\n\n⚠️ To reply:\nTap “Reply to this Order” once, then type your message."

        msg = await self.bot.send_message(chat_id=telegram_id, text=message_text, reply_markup=kb)
        if interaction:
            interaction.telegram_message_id = msg.message_id
            interaction.sent_at = func.now()

outbox_dispatcher = OutboxDispatcher()
