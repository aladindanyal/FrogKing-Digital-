import re

with open("bot/misc/services/restock_dispatcher.py", "r", encoding="utf-8") as f:
    content = f.read()

# Make sure stale recovered log only prints if > 0
# Wait, my previous code already has if recovered > 0: logger.info(...)

# We also need to survive unexpected loop errors, but logging full tracebacks for deterministic errors is bad.
# "Unexpected loop errors should still be logged, but the loop must survive."
# "Do not repeatedly dump a full traceback every polling cycle for the same deterministic schema error."

loop_body = """            except asyncio.CancelledError:
                break
            except Exception as e:
                error_str = str(e)
                if hasattr(self, "_last_error") and self._last_error == error_str:
                    pass
                else:
                    self._last_error = error_str
                    logger.error(f"Restock dispatcher loop error: {e}", exc_info=True)"""

content = re.sub(r'            except asyncio.CancelledError:\n                break\n            except Exception as e:\n                logger.error\(f"Restock dispatcher loop error: \{e\}", exc_info=True\)', loop_body, content, flags=re.DOTALL)

with open("bot/misc/services/restock_dispatcher.py", "w", encoding="utf-8") as f:
    f.write(content)

