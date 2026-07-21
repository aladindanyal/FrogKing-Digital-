import re

with open("tests/test_restock_dispatcher.py", "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace('User(telegram_id=999999, username="test", language="en")', 'User(telegram_id=999999)')

with open("tests/test_restock_dispatcher.py", "w", encoding="utf-8") as f:
    f.write(content)

with open("bot/misc/services/restock_dispatcher.py", "r", encoding="utf-8") as f:
    content = f.read()

process_sub_fix = """                # 3. Build message
                import html
                safe_name = html.escape(item['name'])
                
                from bot.i18n.main import current_locale
                token = current_locale.set(EnvKeys.BOT_LOCALE)
                
                text = localize('restock_notification_text', item_name=safe_name)
                
                keyboard = _get_restock_view_keyboard(sub.item_id)
                
                current_locale.reset(token)

                # 4. Send Message"""

content = re.sub(r'                # 3\. Build message.*?# 4\. Send Message', process_sub_fix, content, flags=re.DOTALL)

with open("bot/misc/services/restock_dispatcher.py", "w", encoding="utf-8") as f:
    f.write(content)

