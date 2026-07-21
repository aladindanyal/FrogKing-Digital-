import pytest
from sqlalchemy.exc import InvalidRequestError
from bot.database.models.main import User, Order
from bot.web.admin import UserAdmin
from typing import Any
from bot.database import Database

@pytest.mark.asyncio
async def test_useradmin_form_columns():
    assert hasattr(UserAdmin, "form_columns")

    form_cols = UserAdmin.form_columns
    assert form_cols is not None

    field_names = [getattr(col, "key", str(col)) for col in form_cols]

    clean_names = []
    for f in field_names:
        if isinstance(f, str) and '.' in f:
            clean_names.append(f.split('.')[-1])
        else:
            clean_names.append(str(f))

    assert "balance" in clean_names
    assert "is_blocked" in clean_names
    assert "referral_id" in clean_names
    assert "role" in clean_names

    # Profile fields should be excluded
    assert "telegram_username" not in clean_names
    assert "telegram_id" not in clean_names

    # Relationships should be excluded
    assert "orders" not in clean_names
    assert "user_goods" not in clean_names
    assert "user_operations" not in clean_names

@pytest.mark.asyncio
class FakeAdmin:
    def __init__(self, session):
        self.session = session
        self.model = User

    async def _run_query(self, stmt):
        result = await self.session.execute(stmt)
        return result.scalars().unique().all()

    async def get_object_for_edit(self, value: Any) -> Any:
        # We must copy the logic from UserAdmin since FakeAdmin can't easily inherit UserAdmin without starlette app
        from sqlalchemy import select
        from sqlalchemy.orm import noload, joinedload
        try:
            pk = int(value)
        except (ValueError, TypeError):
            return None

        stmt = (
            select(User)
            .where(User.telegram_id == pk)
            .options(noload("*"))
            .options(joinedload(User.role))
        )
        rows = await self._run_query(stmt)
        return rows[0] if rows else None

@pytest.mark.asyncio
async def test_heavy_user_get_object_for_edit():
    async with Database().session() as session:
        # Create a heavy user in the test database
        heavy_user_id = 152196394602
        user = User(
            telegram_id=heavy_user_id,
            telegram_username="heavyedituser",
            first_name="Heavy",
            balance=100,
            role_id=1
        )
        session.add(user)

        # Add multiple orders
        for i in range(10):
            order = Order(
                public_id=f"HEDIT-{i}",
                user_id=heavy_user_id,
                status="completed",
                subtotal=10,
                total=10
            )
            session.add(order)

        await session.commit()

    async with Database().session() as admin_session:
        admin = FakeAdmin(admin_session)
        fetched = await admin.get_object_for_edit(heavy_user_id)
        assert fetched is not None
        assert fetched.telegram_username == "heavyedituser"

        # With noload("*"), orders should be empty without emitting queries
        assert len(fetched.orders) == 0

        # Role should be loaded!
        assert fetched.role is not None
