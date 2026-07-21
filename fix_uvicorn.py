import re

with open("bot/main.py", "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace("admin_server = None", "admin_server = None\nadmin_server_task = None")
content = content.replace("global recovery_manager, admin_server", "global recovery_manager, admin_server, admin_server_task")

old_start = \"\"\"    admin_server = uvicorn.Server(config)
    asyncio.create_task(admin_server.serve())\"\"\"

new_start = \"\"\"    admin_server = uvicorn.Server(config)
    admin_server_task = asyncio.create_task(admin_server.serve())\"\"\"

content = content.replace(old_start, new_start)

old_stop = \"\"\"    # Admin server stop
    if admin_server:
        admin_server.should_exit = True\"\"\"

new_stop = \"\"\"    # Admin server stop
    if admin_server:
        admin_server.should_exit = True
        if 'admin_server_task' in globals() and admin_server_task:
            try:
                import asyncio
                await asyncio.wait_for(admin_server_task, timeout=5.0)
            except Exception:
                pass\"\"\"

content = content.replace(old_stop, new_stop)

with open("bot/main.py", "w", encoding="utf-8") as f:
    f.write(content)

