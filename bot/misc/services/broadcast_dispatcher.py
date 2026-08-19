import asyncio
import logging
from sqlalchemy import select, update, func, text
from sqlalchemy.orm import selectinload
from bot.database.main import Database
from bot.database.models.main import BroadcastCampaign, BroadcastRecipient, User
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramNotFound, TelegramBadRequest
import datetime

logger = logging.getLogger(__name__)

class BroadcastDispatcher:
    def __init__(self, polling_interval: int = 5):
        self.polling_interval = polling_interval
        self._is_running = False
        self._task = None
        self.bot = None
        self._wake_event = asyncio.Event()
        self.batch_size = 30

    def wake_up(self):
        self._wake_event.set()

    async def start(self, bot):
        if self._is_running:
            return
        self.bot = bot
        self._is_running = True
        self._task = asyncio.create_task(self._poll_broadcasts())
        logger.info("BroadcastDispatcher started")

    async def stop(self):
        self._is_running = False
        self.wake_up()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("BroadcastDispatcher stopped")

    async def _poll_broadcasts(self):
        # On restart, cleanup stale states
        try:
            await self._cleanup_stale_states_on_start()
        except Exception as e:
            logger.error(f"Error cleaning up broadcast states on start: {e}")

        while self._is_running:
            try:
                has_more = await self._process_active_campaign()
                if not has_more:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=self.polling_interval)
                    self._wake_event.clear()
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in BroadcastDispatcher: {e}")
                await asyncio.sleep(self.polling_interval)

    async def _cleanup_stale_states_on_start(self):
        async with Database().session() as session:
            # Convert stale sending recipients to uncertain
            await session.execute(
                update(BroadcastRecipient)
                .where(BroadcastRecipient.status == 'sending')
                .values(status='uncertain', updated_at=func.now())
            )
            # Ensure confirmed campaigns get picked up by just letting them be.
            # (In _process_active_campaign, we will look for 'confirmed' or 'running')
            await session.commit()

    async def _process_active_campaign(self) -> bool:
        """Processes one batch for the currently active campaign. Returns True if there's more work."""
        async with Database().session() as session:
            # Find the active campaign (confirmed or running)
            result = await session.execute(
                select(BroadcastCampaign)
                .where(BroadcastCampaign.status.in_(['confirmed', 'running']))
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            campaign = result.scalars().first()

            if not campaign:
                return False

            if campaign.status == 'confirmed':
                campaign.status = 'running'
                campaign.run_started_at = func.now()
                await session.commit()
                return True # Loop again to start sending

            # Find a batch of pending recipients to process
            # Claim them transactionally with skip_locked
            recipients_result = await session.execute(
                select(BroadcastRecipient)
                .where(
                    BroadcastRecipient.campaign_id == campaign.id,
                    BroadcastRecipient.status == 'pending',
                    (BroadcastRecipient.next_attempt_at.is_(None)) | (BroadcastRecipient.next_attempt_at <= func.now())
                )
                .limit(self.batch_size)
                .with_for_update(skip_locked=True)
            )
            recipients = recipients_result.scalars().all()

            if not recipients:
                # Check if there are any recipients that are 'sending' or 'pending' (delayed)
                # If none, campaign is completed.
                pending_count = (await session.execute(
                    select(func.count(BroadcastRecipient.id))
                    .where(
                        BroadcastRecipient.campaign_id == campaign.id,
                        BroadcastRecipient.status.in_(['pending', 'sending'])
                    )
                )).scalar()

                if pending_count == 0:
                    campaign.status = 'completed'
                    campaign.run_ended_at = func.now()
                    await session.commit()
                return False

            # Mark claimed recipients as 'sending'
            for r in recipients:
                r.status = 'sending'
                r.claimed_at = func.now()
                r.attempts += 1

            await session.commit()

        # Process the batch concurrently
        tasks = []
        for r in recipients:
            tasks.append(self._send_to_recipient(campaign, r))

        await asyncio.gather(*tasks, return_exceptions=True)

        return True # Process next batch

    async def _send_to_recipient(self, campaign: BroadcastCampaign, recipient: BroadcastRecipient):
        async with Database().session() as session:
            # Re-fetch recipient to update
            r = await session.get(BroadcastRecipient, recipient.id)
            if not r or r.status != 'sending':
                return

            user = await session.get(User, r.user_id)
            if not user or user.is_blocked:
                # User blocked in the meantime
                r.status = 'blocked'
                r.updated_at = func.now()
                await session.commit()
                return

            try:
                if campaign.photo_file_id:
                    await self.bot.send_photo(
                        chat_id=user.telegram_id,
                        photo=campaign.photo_file_id,
                        caption=campaign.message_text or "",
                        parse_mode=campaign.parse_mode,
                        disable_notification=True
                    )
                else:
                    await self.bot.send_message(
                        chat_id=user.telegram_id,
                        text=campaign.message_text,
                        parse_mode=campaign.parse_mode,
                        disable_notification=True
                    )
                r.status = 'sent'
                r.sent_at = func.now()

            except TelegramRetryAfter as e:
                r.status = 'pending'
                r.next_attempt_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=e.retry_after)
            except (TelegramForbiddenError, TelegramNotFound):
                r.status = 'blocked'
                user.is_blocked = True
            except TelegramBadRequest as e:
                r.status = 'failed'
                r.error_message = str(e)[:255] # Sanitized short error
            except Exception as e:
                logger.error(f"Ambiguous error sending broadcast to {user.telegram_id}: {e}")
                r.status = 'uncertain'
                r.error_message = "Ambiguous network/server failure"

            r.updated_at = func.now()
            await session.commit()

broadcast_dispatcher = BroadcastDispatcher()
