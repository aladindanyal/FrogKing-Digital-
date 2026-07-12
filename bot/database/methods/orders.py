from datetime import datetime, timezone
import random
import string
from typing import Tuple, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from bot.database.models.main import Order, OrderItem

# Non-ambiguous alphabet
PUBLIC_ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

async def generate_public_order_id(session: AsyncSession) -> str:
    """
    Generate a unique public Order ID (e.g., FGK-20260712-A7K9Q2X8).
    Uses a simple select-check retry mechanism to avoid nested transaction / savepoint
    complications in SQLAlchemy with asyncpg, while safely maintaining the outer transaction.
    """
    max_retries = 10
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y%m%d")
    
    for _ in range(max_retries):
        suffix = ''.join(random.choices(PUBLIC_ID_ALPHABET, k=8))
        candidate = f"FGK-{date_str}-{suffix}"
        
        # Check if candidate exists. This does not lock, but a collision is extremely unlikely.
        # If it does collide upon insert/flush, the DB unique constraint will raise an error,
        # which will be caught outer transaction (which is an acceptable terminal failure for extreme edges).
        # But this pre-check prevents 99.999% of predictable conflicts cleanly.
        result = await session.execute(select(Order.id).where(Order.public_id == candidate))
        if result.scalar_one_or_none() is None:
            return candidate

    raise RuntimeError("Exhausted retries generating a unique public_id")

async def create_order_with_item(
    session: AsyncSession,
    user_id: int,
    item_id: int,
    product_name: str,
    quantity: int,
    unit_price: float,
    subtotal: float,
    discount_total: float,
    total: float,
    currency: str = "USD",
    promo_code: Optional[str] = None,
    product_description: Optional[str] = None
) -> Tuple[Order, OrderItem]:
    """
    Creates an Order and an OrderItem in the provided session.
    This does NOT commit the session. It only adds and flushes, ensuring
    atomic participation in the caller's outer transaction.
    
    All monetary fields must be positive.
    """
    
    public_id = await generate_public_order_id(session)
    
    # Create the parent order
    order = Order(
        public_id=public_id,
        user_id=user_id,
        status="pending",
        currency=currency,
        subtotal=subtotal,
        discount_total=discount_total,
        total=total,
        promo_code_snapshot=promo_code
    )
    
    session.add(order)
    await session.flush()  # Get order.id
    
    # Create the single order item containing the full quantity
    order_item = OrderItem(
        order_id=order.id,
        item_id=item_id,
        product_name_snapshot=product_name,
        product_description_snapshot=product_description,
        quantity=quantity,
        unit_price=unit_price,
        subtotal=subtotal,
        discount_total=discount_total,
        total=total,
        fulfillment_status="pending"
    )
    
    session.add(order_item)
    await session.flush()  # Get order_item.id
    
    return order, order_item

async def get_order_by_id(session: AsyncSession, order_id: int) -> Optional[Order]:
    result = await session.execute(select(Order).where(Order.id == order_id))
    return result.scalar_one_or_none()

async def get_order_by_public_id(session: AsyncSession, public_id: str) -> Optional[Order]:
    result = await session.execute(select(Order).where(Order.public_id == public_id))
    return result.scalar_one_or_none()

async def list_user_orders(session: AsyncSession, user_id: int, limit: int = 10, offset: int = 0) -> List[Order]:
    result = await session.execute(
        select(Order)
        .where(Order.user_id == user_id)
        .order_by(Order.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())

async def count_user_orders(session: AsyncSession, user_id: int) -> int:
    from sqlalchemy import func
    result = await session.execute(
        select(func.count()).where(Order.user_id == user_id)
    )
    return result.scalar_one()

async def get_order_items(session: AsyncSession, order_id: int) -> List[OrderItem]:
    result = await session.execute(
        select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id.asc())
    )
    return list(result.scalars().all())

async def get_order_item_by_id(session: AsyncSession, order_item_id: int) -> Optional[OrderItem]:
    result = await session.execute(select(OrderItem).where(OrderItem.id == order_item_id))
    return result.scalar_one_or_none()
