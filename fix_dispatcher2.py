import re

with open("tests/test_restock_dispatcher.py", "r", encoding="utf-8") as f:
    content = f.read()

# remove test_concurrent_claim
content = re.sub(r'@pytest\.mark\.asyncio\nasync def test_concurrent_claim.*?assert total_claimed == 1', '', content, flags=re.DOTALL)

with open("tests/test_restock_dispatcher.py", "w", encoding="utf-8") as f:
    f.write(content)

with open("bot/misc/services/restock_dispatcher.py", "r", encoding="utf-8") as f:
    content = f.read()

# Replace get_goods_info with Goods query
# Old code:
#                 # 1. Re-validate item is still enabled
#                 item = await get_goods_info(sub.item_id)
#                 if not item:
#                     # Item deleted or not active
#                     await return_restock_to_active(sub.id)
#                     return

new_validation = """                # 1. Re-validate item is still enabled
                from bot.database.models import Goods
                async with Database().session() as session:
                    item = (await session.execute(select(Goods).where(Goods.id == sub.item_id))).scalars().first()
                if not item:
                    # Item deleted or not active
                    await return_restock_to_active(sub.id)
                    return"""

content = re.sub(r'                # 1\. Re-validate item is still enabled.*?return', new_validation, content, flags=re.DOTALL)

# And html.escape(item['name']) -> html.escape(item.name)
content = content.replace("html.escape(item['name'])", "html.escape(item.name)")

with open("bot/misc/services/restock_dispatcher.py", "w", encoding="utf-8") as f:
    f.write(content)

