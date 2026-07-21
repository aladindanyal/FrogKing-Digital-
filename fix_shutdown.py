import re

with open("bot/misc/services/restock_dispatcher.py", "r", encoding="utf-8") as f:
    content = f.read()

# Fix the run() loop to safely catch CancelledError globally
old_run = \"\"\"    async def run(self):
        \"\"\"Main dispatcher loop.\"\"\"
        while self.running:
            try:
                # 1. Recover stale
                recovered = await recover_stale_processing_subscriptions(EnvKeys.RESTOCK_STALE_TIMEOUT)
                if recovered > 0:
                    logger.info("stale_processing_recovered", extra={"event": "stale_processing_recovered", "count": recovered})

                # 2. Process batch
                await self.process_batch()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                error_str = str(e)
                if hasattr(self, "_last_error") and self._last_error == error_str:
                    pass
                else:
                    self._last_error = error_str
                    logger.error(f"Restock dispatcher loop error: {e}", exc_info=True)
            
            # 3. Sleep with jitter
            jitter = random.uniform(0, EnvKeys.RESTOCK_JITTER_MAX)
            await asyncio.sleep(EnvKeys.RESTOCK_POLL_INTERVAL + jitter)\"\"\"

new_run = \"\"\"    async def run(self):
        \"\"\"Main dispatcher loop.\"\"\"
        try:
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
        except asyncio.CancelledError:
            pass\"\"\"

content = content.replace(old_run, new_run)

with open("bot/misc/services/restock_dispatcher.py", "w", encoding="utf-8") as f:
    f.write(content)

