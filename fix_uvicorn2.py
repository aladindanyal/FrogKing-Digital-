import re

with open("bot/main.py", "r", encoding="utf-8") as f:
    content = f.read()

old_stop = \"\"\"    # Admin server stop
    if admin_server:
        admin_server.should_exit = True
        if admin_server_task:
            try:
                await asyncio.wait_for(admin_server_task, timeout=5.0)
            except Exception:
                pass\"\"\"

new_stop = \"\"\"    # Admin server stop
    if admin_server:
        admin_server.should_exit = True
        admin_server.force_exit = True
        if admin_server_task:
            try:
                import asyncio
                await asyncio.wait_for(admin_server_task, timeout=2.0)
            except Exception:
                pass\"\"\"

content = content.replace(old_stop, new_stop)

with open("bot/main.py", "w", encoding="utf-8") as f:
    f.write(content)

