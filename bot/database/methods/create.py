from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, exists

from bot.database.models import User, ItemValues, Goods, Categories, Operations, Payments, ReferralEarnings, Role
from bot.database.models.main import PromoCodes, CartItems, Reviews
from bot.database import Database
from bot.database.methods.cache_utils import safe_create_task
from bot.database.methods.read import invalidate_stats_cache


async def create_user(
    telegram_id: int,
    registration_date: datetime,
    referral_id: int | None,
    role: int = 1,
    telegram_username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None
) -> None:
    """Create user if missing; commit."""
    async with Database().session() as s:
        result = await s.execute(select(exists().where(User.telegram_id == telegram_id)))
        if result.scalar():
            return

        profile_updated_at = None
        if telegram_username or first_name or last_name:
            profile_updated_at = registration_date

        s.add(
            User(
                telegram_id=telegram_id,
                role_id=role,
                registration_date=registration_date,
                referral_id=referral_id,
                telegram_username=telegram_username,
                first_name=first_name,
                last_name=last_name,
                profile_updated_at=profile_updated_at
            )
        )
        await s.commit()


async def create_item(item_name: str, item_description: str, item_price: int, category_name: str) -> None:
    """Insert item (goods); commit. Resolves category_name to category_id."""
    async with Database().session() as s:
        result = await s.execute(select(exists().where(Goods.name == item_name)))
        if result.scalar():
            return
        cat = (await s.execute(select(Categories.id).where(Categories.name == category_name))).scalar()
        if not cat:
            return
        s.add(
            Goods(
                name=item_name,
                description=item_description,
                price=item_price,
                category_id=cat,
            )
        )

    safe_create_task(invalidate_stats_cache())


async def add_values_to_item(item_name: str, value: str, is_infinity: bool) -> bool:
    """Add item value if not duplicate; True if inserted. Resolves item_name to item_id."""
    value_norm = (value or "").strip()
    if not value_norm:
        return False

    async with Database().session() as s:
        item_id = (await s.execute(select(Goods.id).where(Goods.name == item_name))).scalar()
        if not item_id:
            return False

        dup = (await s.execute(
            select(exists().where(
                ItemValues.item_id == item_id,
                ItemValues.value == value_norm,
            ))
        )).scalar()
        if dup:
            return False

        try:
            s.add(ItemValues(item_id=item_id, value=value_norm, is_infinity=bool(is_infinity)))
            await s.flush()
            from bot.database.methods.read import invalidate_item_cache
            from bot.database.methods.cache_utils import safe_create_task
            safe_create_task(invalidate_item_cache(item_name))
            return True
        except Exception:
            return False


async def get_next_display_order(parent_id: int | None, s) -> int:
    """Calculate the next display order for a category within its parent group."""
    from bot.database.models.main import Categories
    from sqlalchemy import func, select

    query = select(func.max(Categories.display_order))
    if parent_id is None:
        query = query.where(Categories.parent_id.is_(None))
    else:
        query = query.where(Categories.parent_id == parent_id)

    result = await s.execute(query)
    max_order = result.scalar()

    if max_order is None:
        return 10
    return max_order + 10


async def create_category(category_name: str, parent_id: int | None = None) -> None:
    """Insert category; commit."""
    async with Database().session() as s:
        result = await s.execute(select(exists().where(Categories.name == category_name)))
        if result.scalar():
            return

        display_order = await get_next_display_order(parent_id, s)
        s.add(Categories(name=category_name, parent_id=parent_id, display_order=display_order))
        await s.commit()

    safe_create_task(invalidate_stats_cache())


async def create_operation(user_id: int, value: int, operation_time: datetime) -> None:
    """Record completed balance operation; commit."""
    async with Database().session() as s:
        s.add(Operations(user_id, value, operation_time))


async def create_pending_payment(provider: str, external_id: str, user_id: int, amount: int, currency: str) -> None:
    """Create pending payment."""
    async with Database().session() as s:
        s.add(Payments(
            provider=provider,
            external_id=external_id,
            user_id=user_id,
            amount=Decimal(amount),
            currency=currency,
            status="pending"
        ))


async def create_referral_earning(referrer_id: int, referral_id: int, amount: int, original_amount: int) -> None:
    """Create a settled legacy row for compatibility with historical tooling.

    New Phase 6B earnings must be created from purchase transactions instead.
    """
    async with Database().session() as s:
        s.add(
            ReferralEarnings(
                referrer_id=referrer_id,
                referral_id=referral_id,
                amount=Decimal(amount),
                original_amount=Decimal(original_amount),
                status="settled",
                earning_type="legacy_topup",
                reason="Compatibility import for pre-Phase 6B earning",
            )
        )


async def create_role(name: str, permissions: int) -> int | None:
    """Create a new role. Returns the new role ID, or None if name conflict."""
    async with Database().session() as s:
        result = await s.execute(select(exists().where(Role.name == name)))
        if result.scalar():
            return None
        role = Role(name=name, permissions=permissions)
        s.add(role)
        await s.flush()
        return role.id


async def create_promo_code(
    code: str,
    discount_type: str,
    discount_value,
    max_uses: int = 0,
    expires_at=None,
    category_id: int = None,
    item_id: int = None,
) -> int | None:
    """Create a promo code. Returns ID or None if code already exists."""
    from decimal import Decimal
    async with Database().session() as s:
        result = await s.execute(select(exists().where(PromoCodes.code == code.upper())))
        if result.scalar():
            return None
        promo = PromoCodes(
            code=code.upper(),
            discount_type=discount_type,
            discount_value=Decimal(str(discount_value)),
            max_uses=max_uses,
            expires_at=expires_at,
            category_id=category_id,
            item_id=item_id,
        )
        s.add(promo)
        await s.flush()
        return promo.id


async def add_to_cart(user_id: int, item_name: str, promo_code: str = None) -> tuple[bool, str]:
    """Add item to user's cart. Returns (success, message)."""
    from sqlalchemy import func as sa_func
    CART_MAX_ITEMS = 10
    async with Database().session() as s:
        count = (await s.execute(
            select(sa_func.count(CartItems.id)).where(CartItems.user_id == user_id)
        )).scalar() or 0
        if count >= CART_MAX_ITEMS:
            return False, "cart_full"

        # Check item exists
        item_exists = (await s.execute(
            select(exists().where(Goods.name == item_name))
        )).scalar()
        if not item_exists:
            return False, "item_not_found"

        s.add(CartItems(user_id=user_id, item_name=item_name, promo_code=promo_code))
        return True, "success"



async def create_review(user_id: int, product_id: int, order_id: int, order_item_id: int, item_name: str, rating: int, comment: str = None) -> int | None:
    """Create a pending review. Returns ID or None if already reviewed."""
    async with Database().session() as s:
        existing = (await s.execute(
            select(exists().where(
                Reviews.order_item_id == order_item_id
            ))
        )).scalar()
        if existing:
            return None
        review = Reviews(
            user_id=user_id,
            product_id=product_id,
            order_id=order_id,
            order_item_id=order_item_id,
            item_name=item_name,
            rating=rating,
            comment=comment,
            status='pending'
        )
        s.add(review)
        await s.flush()
        return review.id
