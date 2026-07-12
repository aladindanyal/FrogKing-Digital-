import re

with open("bot/main.py", "r", encoding="utf-8") as f:
    content = f.read()

# Change the logging configuration for uvicorn to ERROR so it hides warnings and infos
old_log = \"\"\"    logging.getLogger("uvicorn").setLevel(logging.WARNING)\"\"\"
new_log = \"\"\"    logging.getLogger("uvicorn").setLevel(logging.CRITICAL)
    logging.getLogger("uvicorn.error").setLevel(logging.CRITICAL)\"\"\"

content = content.replace(old_log, new_log)

with open("bot/main.py", "w", encoding="utf-8") as f:
    f.write(content)
