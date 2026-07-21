import re

with open("bot/misc/services/restock_dispatcher.py", "r", encoding="utf-8") as f:
    content = f.read()

# Replace process_subscription
new_process_sub = """    async def process_subscription(self, sub):
        \"\"\"Process a single subscription.\"\"\"
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
                item = await get_goods_info(sub.item_id)
                if not item:
                    # Item deleted or not active
                    await return_restock_to_active(sub.id)
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
                safe_name = html.escape(item['name'])
                
                from bot.database.models import User
                
                async with Database().session() as session:
                    user = (await session.execute(select(User).where(User.telegram_id == sub.user_id))).scalars().first()
                
                user_lang = user.language if user and user.language else EnvKeys.BOT_LOCALE
                
                from bot.i18n.main import current_locale
                token = current_locale.set(user_lang)
                
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
"""

content = re.sub(r'    async def process_subscription\(self, sub\):.*?await mark_restock_notified\(sub.id\)', new_process_sub, content, flags=re.DOTALL)

# Add poll_completed logic
new_batch = """    async def process_batch(self):
        \"\"\"Process one batch of restock subscriptions.\"\"\"
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
"""

content = re.sub(r'    async def process_batch\(self\):.*?await asyncio.gather\(\*tasks\)', new_batch, content, flags=re.DOTALL)

with open("bot/misc/services/restock_dispatcher.py", "w", encoding="utf-8") as f:
    f.write(content)

