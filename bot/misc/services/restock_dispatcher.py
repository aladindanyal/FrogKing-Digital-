import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import (
    TelegramRetryAfter,
    TelegramForbiddenError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramServerError
)

from bot.database.methods.read import get_stock_for_items, get_goods_info
from bot.database.methods.restock_subscriptions import (
    recover_stale_processing_subscriptions,
    claim_ready_restock_subscriptions,
    release_restock_for_retry,
    mark_restock_notified,
    mark_restock_failed,
    return_restock_to_active,
)
from bot.i18n.main import localize
from bot.misc.env import EnvKeys

from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup

def _get_restock_view_keyboard(item_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text=localize("btn.view_product", default="🛒 View Product"),
        callback_data=f"direct_item:{item_id}"
    )
    return kb.as_markup()

logger = logging.getLogger(__name__)

class RestockDispatcher:
    """Background service to dispatch restock notifications"""

    def __init__(self, bot: Bot):
        self.bot = bot
        self.running = False
        self._task = None
        self._semaphore = asyncio.Semaphore(EnvKeys.RESTOCK_MAX_CONCURRENT_SENDS)

    async def start(self):
        """Start the dispatcher."""
        if self._task and not self._task.done():
            logger.warning("Restock dispatcher already running")
            return
            
        self.running = True
        self._task = asyncio.create_task(self.run())
        logger.info("dispatcher_started", extra={"event": "dispatcher_started"})

    async def stop(self):
        """Stop the dispatcher."""
        self.running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("dispatcher_stopped", extra={"event": "dispatcher_stopped"})

    async def run(self):
        """Main dispatcher loop."""
        while self.running:
            try:
                # 1. Recover stale
                recovered = await recover_stale_processing_subscriptions(EnvKeys.RESTOCK_STALE_TIMEOUT)
                if recovered > 0:
                    logger.info("stale_processing_recovered", extra={"event": "stale_processing_recovered", "count": recovered})

                # 2. Process batch
                await self.process_batch()
                
            except Exception as e:
                if isinstance(e, asyncio.CancelledError):
                    raise
                error_str = str(e)
                if hasattr(self, "_last_error") and self._last_error == error_str:
                    pass
                else:
                    self._last_error = error_str
                    logger.error(f"Restock dispatcher loop error: {e}", exc_info=True)
            
            # 3. Sleep with jitter
            jitter = random.uniform(0, EnvKeys.RESTOCK_JITTER_MAX)
            await asyncio.sleep(EnvKeys.RESTOCK_POLL_INTERVAL + jitter)

    async def process_batch(self):
        """Process one batch of restock subscriptions."""
        import time
        start_t = time.time()
        claimed_subs = await claim_ready_restock_subscriptions(EnvKeys.RESTOCK_BATCH_SIZE)
        if not claimed_subs:
            return
            
        tasks = []
        for sub in claimed_subs:
            logger.info("restock_claimed", extra={
                "event": "restock_claimed",
                "subscription_id": sub.id,
                "item_id": sub.item_id
            })
            tasks.append(asyncio.create_task(self.process_subscription(sub)))
            
        # Global pacing below 20 msgs/second = 50ms delay is done per task
        await asyncio.gather(*tasks)
        
        logger.debug("restock_poll_completed", extra={
            "event": "restock_poll_completed",
            "claimed_count": len(claimed_subs),
            "duration_ms": int((time.time() - start_t) * 1000)
        })


    async def process_subscription(self, sub):
        """Process a single subscription."""
        async with self._semaphore:
            # Respect max messages per second globally
            await asyncio.sleep(1.0 / EnvKeys.RESTOCK_MAX_MESSAGES_PER_SECOND)
            
            try:
                from bot.database import Database
                from sqlalchemy import select
                from bot.database.models import ProductRestockSubscription
                
                async with Database().session() as session:
                    db_sub = (await session.execute(select(ProductRestockSubscription).where(ProductRestockSubscription.id == sub.id))).scalars().first()
                    if not db_sub or db_sub.status != 'processing':
                        return
                
                # 1. Re-validate item is still enabled
                from bot.database.models import Goods
                async with Database().session() as session:
                    item = (await session.execute(select(Goods).where(Goods.id == sub.item_id))).scalars().first()
                if not item:
                    # Item deleted or not active
                    await return_restock_to_active(sub.id)
                    return_restock_to_active(sub.id)
                    return
                
                # 2. Re-validate stock
                stock_dict = await get_stock_for_items([sub.item_id])
                current_stock = stock_dict.get(sub.item_id, 0)
                
                if current_stock == 0:
                    # Stock sold out before sending
                    await return_restock_to_active(sub.id)
                    return
                
                # 3. Build message
                import html
                safe_name = html.escape(item.name)
                
                from bot.i18n.main import current_locale
                token = current_locale.set(EnvKeys.BOT_LOCALE)
                
                text = localize('restock_notification_text', item_name=safe_name)
                
                keyboard = _get_restock_view_keyboard(sub.item_id)
                
                current_locale.reset(token)

                # 4. Send Message
                await self.bot.send_message(
                    chat_id=sub.user_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
                
                logger.info("restock_sent", extra={
                    "event": "restock_sent",
                    "subscription_id": sub.id
                })
                
                # 5. Mark notified
                await mark_restock_notified(sub.id)

                logger.info("restock_sent", extra={
                    "event": "restock_sent",
                    "subscription_id": sub.id,
                    "item_id": sub.item_id
                })

            except TelegramForbiddenError:
                await mark_restock_failed(sub.id, "telegram_forbidden")
                logger.info("restock_failed", extra={
                    "event": "restock_failed",
                    "subscription_id": sub.id,
                    "item_id": sub.item_id,
                    "error": "telegram_forbidden"
                })
            except TelegramRetryAfter as e:
                retry_at = datetime.now(timezone.utc) + timedelta(seconds=e.retry_after + random.uniform(0, 3))
                await release_restock_for_retry(sub.id, retry_at, "telegram_retry_after")
                logger.warning("restock_retry_scheduled", extra={
                    "event": "restock_retry_scheduled",
                    "subscription_id": sub.id,
                    "item_id": sub.item_id,
                    "retry_after": e.retry_after
                })
            except (TelegramNetworkError, TelegramServerError) as e:
                if sub.attempts >= EnvKeys.RESTOCK_MAX_ATTEMPTS:
                    await mark_restock_failed(sub.id, "retry_limit_exceeded")
                else:
                    backoff = min(300, 2 ** sub.attempts * 10)
                    retry_at = datetime.now(timezone.utc) + timedelta(seconds=backoff)
                    await release_restock_for_retry(sub.id, retry_at, "telegram_network_error")
            except TelegramBadRequest as e:
                if "chat not found" in str(e).lower() or "user is deactivated" in str(e).lower():
                    await mark_restock_failed(sub.id, "user_unavailable")
                else:
                    logger.error(f"Malformed message for sub {sub.id}: {e}")
                    await mark_restock_failed(sub.id, "bad_request")
            except Exception as e:
                logger.error(f"Unexpected error sending restock sub {sub.id}: {e}")
                if sub.attempts >= EnvKeys.RESTOCK_MAX_ATTEMPTS:
                    await mark_restock_failed(sub.id, "retry_limit_exceeded")
                else:
                    retry_at = datetime.now(timezone.utc) + timedelta(seconds=60)
                    await release_restock_for_retry(sub.id, retry_at, "unexpected_error")
