import pytest
from bot.database.models.main import User, Order
from bot.web.admin import UserAdmin
import pytest_asyncio
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from bot.database.models.main import User, Order, OrderItem
from bot.web.admin import UserAdmin

@pytest.mark.asyncio
async def test_useradmin_column_details_list():
    # 1. Verify that column_details_list is explicitly set to scalar fields
    assert hasattr(UserAdmin, "column_details_list")
    
    details_list = UserAdmin.column_details_list
    assert details_list is not None
    
    # 2. Verify relationships are excluded (no orders, no interactions)
    field_names = [getattr(col, "key", str(col)) for col in details_list]
    
    # Extract just the string names like 'telegram_id'
    clean_names = []
    for f in field_names:
        if isinstance(f, str) and '.' in f:
            clean_names.append(f.split('.')[-1])
        else:
            clean_names.append(str(f))
            
    assert "telegram_id" in clean_names
    assert "telegram_username" in clean_names
    assert "first_name" in clean_names
    
    # Heavy relationships should NOT be in the details list
    assert "orders" not in clean_names
    assert "user_goods" not in clean_names
    assert "user_operations" not in clean_names
    assert "referral_earnings_received" not in clean_names
    assert "checkout_drafts" not in clean_names

@pytest.mark.asyncio
async def test_heavy_user_loads_scalar_only():
    from bot.database.main import Database
    async with Database().session() as session:
        # Create a heavy user in the test database
        heavy_user_id = 152196394600
        user = User(
            telegram_id=heavy_user_id,
            telegram_username="heavyuser",
            first_name="Heavy",
            balance=100
        )
        session.add(user)
        await session.commit()
        
        # Add multiple orders
        for i in range(10):
            order = Order(
                public_id=f"HEAVY-{i}",
                user_id=heavy_user_id,
                status="completed",
                subtotal=10,
                total=10
            )
            session.add(order)
        
        await session.commit()
        
        fetched = await session.get(User, heavy_user_id)
        assert fetched.telegram_username == "heavyuser"
        
        rendered_fields = []
        for col in UserAdmin.column_details_list:
            attr_name = col.key
            val = getattr(fetched, attr_name)
            rendered_fields.append((attr_name, val))
            
        rendered_dict = dict(rendered_fields)
        assert rendered_dict["telegram_username"] == "heavyuser"
        assert rendered_dict["first_name"] == "Heavy"
        assert "orders" not in rendered_dict
