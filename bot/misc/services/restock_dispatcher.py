import asyncio
import html
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramRetryAfter,
    TelegramForbiddenError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramServerError
)
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.methods.read import get_stock_for_items
from bot.database.methods.restock_subscriptions import (
    recover_stale_processing_subscriptions,
    claim_ready_restock_subscriptions,
    release_restock_for_retry,
    mark_restock_notified,
    mark_restock_failed,
    return_restock_to_active,
)
from bot.i18n.dynamic import get_localized_field
from bot.i18n.main import localize
from bot.misc.env import EnvKeys

logger = logging.getLogger(__name__)


def _get_restock_view_keyboard(item_id: int, locale: str | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    button_text = localize("btn.view_product", default="🛒 View Product", _locale=locale)
    kb.button(
        text=button_text,
        callback_data=f"direct_item:{item_id}"
    )
    return kb.as_markup()


class RestockRateLimiter:
    """Dispatcher-wide rate limiter ensuring send-starts across all tasks respect max rate."""

    def __init__(self, rate_per_sec: float | None = None):
        self.rate = rate_per_sec or float(EnvKeys.RESTOCK_MAX_MESSAGES_PER_SECOND)
        self.interval = 1.0 / max(self.rate, 0.001)
        self._lock = asyncio.Lock()
        self._last_send_time = 0.0

    async def acquire(self):
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            elapsed = now - self._last_send_time
            if elapsed < self.interval:
                wait_time = self.interval - elapsed
                await asyncio.sleep(wait_time)
                self._last_send_time = loop.time()
            else:
                self._last_send_time = now


class RestockDispatcher:
    """Background service to dispatch restock notifications"""

    def __init__(
        self,
        bot: Bot | None = None,
        polling_interval: int | None = None,
        rate_limiter: RestockRateLimiter | None = None
    ):
        self.bot = bot
        self.polling_interval = polling_interval or EnvKeys.RESTOCK_POLL_INTERVAL
        self.running = False
        self._task: asyncio.Task | None = None
        self._wake_event = asyncio.Event()
        self._semaphore = asyncio.Semaphore(EnvKeys.RESTOCK_MAX_CONCURRENT_SENDS)
        self.rate_limiter = rate_limiter or RestockRateLimiter()

    def wake_up(self) -> None:
        """Immediately wake up the dispatcher upon inventory replenishment."""
        self._wake_event.set()

    async def start(self, bot: Bot | None = None):
        """Start the dispatcher."""
        if bot is not None:
            self.bot = bot
        if self._task and not self._task.done():
            logger.warning("Restock dispatcher already running")
            return

        self.running = True
        self._task = asyncio.create_task(self.run(), name="restock-dispatcher")
        logger.info("dispatcher_started", extra={"event": "dispatcher_started"})

    async def stop(self):
        """Stop the dispatcher."""
        self.running = False
        self.wake_up()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("dispatcher_stopped", extra={"event": "dispatcher_stopped"})

    async def run(self):
        """Main dispatcher loop."""
        while self.running:
            try:
                # 1. Recover stale processing subscriptions
                recovered = await recover_stale_processing_subscriptions(EnvKeys.RESTOCK_STALE_TIMEOUT)
                if recovered > 0:
                    logger.info("stale_processing_recovered", extra={"event": "stale_processing_recovered", "count": recovered})

                # 2. Process batches until fewer than batch size claimed
                while self.running:
                    claimed_count = await self.process_batch()
                    if claimed_count < EnvKeys.RESTOCK_BATCH_SIZE:
                        break
                    await asyncio.sleep(0)

            except Exception as e:
                if isinstance(e, asyncio.CancelledError):
                    break
                error_str = str(e)
                if hasattr(self, "_last_error") and self._last_error == error_str:
                    pass
                else:
                    self._last_error = error_str
                    logger.error(f"Restock dispatcher loop error: {e}", exc_info=True)

            # 3. Wait for either wake signal or periodic polling interval + jitter
            jitter = random.uniform(0, EnvKeys.RESTOCK_JITTER_MAX)
            timeout = self.polling_interval + jitter
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
                self._wake_event.clear()
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break

    async def process_batch(self) -> int:
        """Process one batch of restock subscriptions. Returns claimed count."""
        import time
        start_t = time.time()
        claimed_subs = await claim_ready_restock_subscriptions(EnvKeys.RESTOCK_BATCH_SIZE)
        if not claimed_subs:
            return 0

        tasks = []
        for sub in claimed_subs:
            sub_id = getattr(sub, 'id', sub)
            item_id = getattr(sub, 'item_id', None)
            logger.info("restock_claimed", extra={
                "event": "restock_claimed",
                "subscription_id": sub_id,
                "item_id": item_id
            })
            tasks.append(asyncio.create_task(self.process_subscription(sub)))

        await asyncio.gather(*tasks)

        logger.debug("restock_poll_completed", extra={
            "event": "restock_poll_completed",
            "claimed_count": len(claimed_subs),
            "duration_ms": int((time.time() - start_t) * 1000)
        })
        return len(claimed_subs)

    async def process_subscription(self, sub):
        """Process a single subscription."""
        sub_id = getattr(sub, 'id', sub)
        user_id = getattr(sub, 'user_id', None)
        item_id = getattr(sub, 'item_id', None)
        attempts = getattr(sub, 'attempts', 0)
        user_lang = getattr(sub, 'user_language_code', None)

        async with self._semaphore:
            try:
                from bot.database import Database
                from sqlalchemy import select
                from bot.database.models import ProductRestockSubscription, User, Goods

                # Re-query live Goods status and user language immediately before sending
                async with Database().session() as session:
                    db_sub = (await session.execute(
                        select(ProductRestockSubscription).where(ProductRestockSubscription.id == sub_id)
                    )).scalars().first()
                    if not db_sub or db_sub.status != 'processing':
                        return

                    if user_id is None:
                        user_id = db_sub.user_id
                    if item_id is None:
                        item_id = db_sub.item_id
                    attempts = db_sub.attempts

                    # Check live Goods status (e.g. if disabled after claim)
                    item_obj = (await session.execute(
                        select(Goods).where(Goods.id == item_id)
                    )).scalars().first()
                    if not item_obj or not item_obj.is_enabled:
                        # Product disabled; return safely to active without consuming attempt
                        await return_restock_to_active(sub_id)
                        return

                    if user_lang is None:
                        user_row = (await session.execute(
                            select(User.language_code).where(User.telegram_id == user_id)
                        )).scalar_one_or_none()
                        user_lang = user_row

                    item_data = {
                        "name": item_obj.name,
                        "name_en": item_obj.name_en,
                        "name_ar": item_obj.name_ar,
                        "name_ru": item_obj.name_ru,
                        "name_zh": item_obj.name_zh,
                        "name_vi": item_obj.name_vi,
                        "name_tr": item_obj.name_tr,
                        "name_es": item_obj.name_es,
                        "is_enabled": item_obj.is_enabled,
                    }

                # 1. Re-validate stock immediately before sending
                stock_dict = await get_stock_for_items([item_id])
                current_stock = stock_dict.get(item_id, 0)

                if current_stock == 0:
                    # Stock sold out before sending; return safely to active
                    await return_restock_to_active(sub_id)
                    return

                # 2. Build localized message
                target_locale = user_lang or EnvKeys.BOT_LOCALE
                raw_name = get_localized_field(item_data, "name", target_locale) or item_data.get("name", "")
                safe_name = html.escape(str(raw_name))

                text = localize('restock_notification_text', item_name=safe_name, _locale=target_locale)
                keyboard = _get_restock_view_keyboard(item_id, locale=target_locale)

                # 3. Space actual Telegram send starts with dispatcher-wide rate limiter
                await self.rate_limiter.acquire()

                # 4. Send Telegram Message
                await self.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )

                # 5. Mark notified
                await mark_restock_notified(sub_id)

                logger.info("restock_sent", extra={
                    "event": "restock_sent",
                    "subscription_id": sub_id,
                    "item_id": item_id,
                    "user_id": user_id
                })

            except TelegramForbiddenError:
                await mark_restock_failed(sub_id, "telegram_forbidden")
                logger.info("restock_failed", extra={
                    "event": "restock_failed",
                    "subscription_id": sub_id,
                    "item_id": item_id,
                    "error": "telegram_forbidden"
                })
            except TelegramRetryAfter as e:
                if attempts >= EnvKeys.RESTOCK_MAX_ATTEMPTS:
                    await mark_restock_failed(sub_id, "retry_limit_exceeded")
                    logger.info("restock_failed", extra={
                        "event": "restock_failed",
                        "subscription_id": sub_id,
                        "item_id": item_id,
                        "error": "retry_limit_exceeded"
                    })
                else:
                    retry_at = datetime.now(timezone.utc) + timedelta(seconds=e.retry_after + random.uniform(0, 3))
                    await release_restock_for_retry(sub_id, retry_at, "telegram_retry_after")
                    logger.warning("restock_retry_scheduled", extra={
                        "event": "restock_retry_scheduled",
                        "subscription_id": sub_id,
                        "item_id": item_id,
                        "retry_after": e.retry_after
                    })
            except (TelegramNetworkError, TelegramServerError) as e:
                if attempts >= EnvKeys.RESTOCK_MAX_ATTEMPTS:
                    await mark_restock_failed(sub_id, "retry_limit_exceeded")
                    logger.info("restock_failed", extra={
                        "event": "restock_failed",
                        "subscription_id": sub_id,
                        "item_id": item_id,
                        "error": "retry_limit_exceeded"
                    })
                else:
                    backoff = min(300, 2 ** attempts * 10)
                    retry_at = datetime.now(timezone.utc) + timedelta(seconds=backoff)
                    await release_restock_for_retry(sub_id, retry_at, "telegram_network_error")
            except TelegramBadRequest as e:
                if "chat not found" in str(e).lower() or "user is deactivated" in str(e).lower():
                    await mark_restock_failed(sub_id, "user_unavailable")
                else:
                    logger.error(f"Malformed message for sub {sub_id}: {e}")
                    await mark_restock_failed(sub_id, "bad_request")
            except Exception as e:
                logger.error(f"Unexpected error sending restock sub {sub_id}: {e}")
                if attempts >= EnvKeys.RESTOCK_MAX_ATTEMPTS:
                    await mark_restock_failed(sub_id, "retry_limit_exceeded")
                else:
                    retry_at = datetime.now(timezone.utc) + timedelta(seconds=60)
                    await release_restock_for_retry(sub_id, retry_at, "unexpected_error")


restock_dispatcher = RestockDispatcher()


def wake_restock_dispatcher() -> None:
    """Helper to wake up the global restock dispatcher singleton."""
    restock_dispatcher.wake_up()
