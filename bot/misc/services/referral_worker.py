"""Background availability worker for referral earnings."""

import asyncio
import logging

from bot.database import Database
from bot.database.methods.referrals import release_mature_referral_earnings
from bot.misc import EnvKeys


logger = logging.getLogger(__name__)


class ReferralAvailabilityWorker:
    def __init__(
        self,
        *,
        interval: int | None = None,
        batch_size: int | None = None,
    ):
        self.interval = interval or EnvKeys.REFERRAL_WORKER_INTERVAL
        self.batch_size = batch_size or EnvKeys.REFERRAL_WORKER_BATCH_SIZE
        self._running = False
        self._task: asyncio.Task | None = None
        self._wake_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="referral-availability-worker")
        logger.info("ReferralAvailabilityWorker started")

    async def stop(self) -> None:
        self._running = False
        self._wake_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("ReferralAvailabilityWorker stopped")

    async def process_batch(self) -> int:
        async with Database().session() as session:
            count = await release_mature_referral_earnings(
                session,
                batch_size=self.batch_size,
            )
            await session.commit()
            return count

    async def _run(self) -> None:
        backoff = 1
        while self._running:
            try:
                while self._running and await self.process_batch() == self.batch_size:
                    await asyncio.sleep(0)
                backoff = 1
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=self.interval)
                    self._wake_event.clear()
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Referral availability batch failed")
                await asyncio.sleep(min(backoff, self.interval))
                backoff = min(backoff * 2, max(self.interval, 1))


referral_availability_worker = ReferralAvailabilityWorker()
