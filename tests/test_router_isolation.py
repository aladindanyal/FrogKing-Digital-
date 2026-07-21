import pytest
import sys
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

def test_router_isolation():
    # Helper to get a fresh dispatcher using the same trick
    def get_fresh_dp():
        keys_to_remove = [k for k in sys.modules.keys() if k.startswith("bot.handlers")]
        for k in keys_to_remove:
            sys.modules.pop(k)

        import bot.handlers.main
        dp = Dispatcher(storage=MemoryStorage())
        bot.handlers.main.register_all_handlers(dp)

        # Verify the order remains unchanged
        # admin, other, user
        routers = dp.sub_routers
        assert len(routers) == 3
        # In Aiogram, router names aren't strictly set to variable names unless specified
        # Let's just check the routers aren't the same
        return dp, bot.handlers.main.admin_router, bot.handlers.main.user_router

    dp1, admin1, user1 = get_fresh_dp()
    dp2, admin2, user2 = get_fresh_dp()

    assert dp1 is not dp2
    assert admin1 is not admin2
    assert user1 is not user2

    assert admin1.parent_router is dp1
    assert admin2.parent_router is dp2
