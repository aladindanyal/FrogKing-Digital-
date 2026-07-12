import re

with open('tests/test_restock_dispatcher.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('TelegramForbiddenError("Forbidden", method="sendMessage")', 'TelegramForbiddenError(method=None, message="Forbidden")')
content = content.replace('TelegramRetryAfter("Retry", method="sendMessage", retry_after=10)', 'TelegramRetryAfter(method=None, message="Retry", retry_after=10)')
content = content.replace('TelegramNetworkError("Network error", method="sendMessage")', 'TelegramNetworkError(method=None, message="Network error")')

content = content.replace(
    'async def test_cancelled_or_notified_never_sent(clear_subs, sample_user, finite_stock_item):',
    'async def test_cancelled_or_notified_never_sent(clear_subs, sample_user, finite_stock_item, unlimited_stock_item):'
)
content = content.replace(
    "s.add(ProductRestockSubscription(user_id=sample_user.telegram_id, item_id=finite_stock_item.id, status='notified', attempts=0, created_at=now, updated_at=now))",
    "s.add(ProductRestockSubscription(user_id=sample_user.telegram_id, item_id=unlimited_stock_item.id, status='notified', attempts=0, created_at=now, updated_at=now))"
)

with open('tests/test_restock_dispatcher.py', 'w', encoding='utf-8') as f:
    f.write(content)
