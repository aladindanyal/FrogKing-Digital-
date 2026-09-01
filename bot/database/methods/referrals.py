"""Referral earning ledger operations for Phase 6B.

Every financial transition in this module is atomic. The background worker only
changes availability; it never credits store balance.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bot.database import Database
from bot.database.methods.audit import log_audit
from bot.database.methods.cache_utils import safe_create_task
from bot.database.methods.read import invalidate_stats_cache, invalidate_user_cache
from bot.database.models.main import (
    Operations,
    ReferralConversions,
    ReferralEarnings,
    StoreSettings,
    User,
)


MONEY_QUANT = Decimal("0.01")
RATE_QUANT = Decimal("0.01")
DEFAULT_REFERRAL_PERCENT = Decimal("5.00")


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


async def get_referral_rate(session=None) -> Decimal:
    """Return the dynamic rate from the canonical StoreSettings row (id=1)."""

    async def _read(active_session) -> Decimal:
        rate = (await active_session.execute(
            select(StoreSettings.referral_percent).where(StoreSettings.id == 1)
        )).scalar_one_or_none()
        if rate is None:
            return DEFAULT_REFERRAL_PERCENT
        return min(max(Decimal(str(rate)), Decimal("0")), Decimal("100")).quantize(RATE_QUANT)

    if session is not None:
        return await _read(session)
    async with Database().session() as own_session:
        return await _read(own_session)


async def create_purchase_referral_earning(
    session,
    *,
    buyer: User,
    commission_base_amount,
    ready_at: datetime | None,
    bought_goods_id: int | None = None,
    order_item_id: int | None = None,
) -> ReferralEarnings | None:
    """Create one pending earning for one canonical purchase source."""

    if (bought_goods_id is None) == (order_item_id is None):
        raise ValueError("Exactly one referral earning purchase source is required.")

    if not buyer.referral_id or buyer.referral_id == buyer.telegram_id:
        return None

    source_filter = (
        ReferralEarnings.bought_goods_id == bought_goods_id
        if bought_goods_id is not None
        else ReferralEarnings.order_item_id == order_item_id
    )
    existing = (await session.execute(
        select(ReferralEarnings).where(
            ReferralEarnings.earning_type == "order_purchase",
            source_filter,
        )
    )).scalar_one_or_none()
    if existing:
        return existing

    referrer_exists = (await session.execute(
        select(User.telegram_id).where(User.telegram_id == buyer.referral_id)
    )).scalar_one_or_none()
    if referrer_exists is None:
        return None

    base_amount = _money(commission_base_amount)
    if base_amount <= 0:
        return None

    rate = await get_referral_rate(session)
    commission = _money(base_amount * rate / Decimal("100"))
    if rate <= 0 or commission <= 0:
        return None

    earning = ReferralEarnings(
        referrer_id=buyer.referral_id,
        referral_id=buyer.telegram_id,
        amount=commission,
        original_amount=base_amount,
        status="pending",
        earning_type="order_purchase",
        bought_goods_id=bought_goods_id,
        order_item_id=order_item_id,
        ready_at=ready_at,
        commission_base_amount=base_amount,
        commission_rate=rate,
    )
    session.add(earning)
    await session.flush()
    return earning


async def release_mature_referral_earnings(
    session,
    *,
    batch_size: int = 100,
    now: datetime | None = None,
) -> int:
    """Release a locked batch without creating any financial operation."""

    now = now or datetime.now(timezone.utc)
    earnings = (await session.execute(
        select(ReferralEarnings)
        .where(
            ReferralEarnings.status == "pending",
            ReferralEarnings.earning_type == "order_purchase",
            ReferralEarnings.ready_at.is_not(None),
            ReferralEarnings.ready_at <= now,
        )
        .order_by(ReferralEarnings.ready_at.asc(), ReferralEarnings.id.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )).scalars().all()

    for earning in earnings:
        earning.status = "available"
        await log_audit(
            "referral_earning_available",
            user_id=earning.referrer_id,
            resource_type="ReferralEarnings",
            resource_id=earning.id,
            details=f"amount={earning.amount}",
            session=session,
        )
    return len(earnings)


async def start_manual_referral_hold(
    session,
    *,
    order_item_id: int,
    ready_at: datetime,
) -> bool:
    """Start a manual order hold once; repeated fulfillment cannot reset it."""

    earning = (await session.execute(
        select(ReferralEarnings)
        .where(
            ReferralEarnings.order_item_id == order_item_id,
            ReferralEarnings.earning_type == "order_purchase",
            ReferralEarnings.status == "pending",
            ReferralEarnings.ready_at.is_(None),
        )
        .with_for_update()
    )).scalar_one_or_none()
    if earning is None:
        return False
    earning.ready_at = ready_at
    await session.flush()
    return True


async def convert_referral_earnings(user_id: int) -> tuple[bool, str, Decimal]:
    """Convert all available positive earnings, offsetting debt first."""

    async with Database().session() as session:
        try:
            user = (await session.execute(
                select(User).where(User.telegram_id == user_id).with_for_update()
            )).scalar_one_or_none()
            if not user:
                return False, "user_not_found", Decimal("0.00")

            earnings = (await session.execute(
                select(ReferralEarnings)
                .where(
                    ReferralEarnings.referrer_id == user_id,
                    ReferralEarnings.status == "available",
                    ReferralEarnings.amount > 0,
                )
                .order_by(ReferralEarnings.id.asc())
                .with_for_update()
            )).scalars().all()
            if not earnings:
                return False, "no_earnings", Decimal("0.00")

            gross_amount = _money(sum((Decimal(str(row.amount)) for row in earnings), Decimal("0")))
            current_debt = _money(user.referral_debt or 0)
            debt_offset = min(gross_amount, current_debt)
            balance_credit = _money(gross_amount - debt_offset)
            converted_at = datetime.now(timezone.utc)

            operation = None
            if balance_credit > 0:
                operation = Operations(
                    user_id=user_id,
                    operation_value=balance_credit,
                    operation_time=converted_at,
                )
                session.add(operation)
                await session.flush()

            conversion = ReferralConversions(
                user_id=user_id,
                gross_amount=gross_amount,
                debt_offset=debt_offset,
                balance_credit=balance_credit,
                balance_operation_id=operation.id if operation else None,
                created_at=converted_at,
            )
            session.add(conversion)
            await session.flush()

            for earning in earnings:
                earning.status = "converted"
                earning.converted_at = converted_at
                earning.conversion_id = conversion.id

            user.referral_debt = _money(current_debt - debt_offset)
            user.balance = _money(Decimal(str(user.balance or 0)) + balance_credit)

            await log_audit(
                "referral_earnings_converted",
                user_id=user_id,
                resource_type="ReferralConversions",
                resource_id=conversion.id,
                details=(
                    f"gross={gross_amount}, debt_offset={debt_offset}, "
                    f"balance_credit={balance_credit}"
                ),
                session=session,
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await log_audit(
                "referral_conversion_failed",
                level="WARNING",
                user_id=user_id,
                details=type(exc).__name__,
            )
            return False, "conversion_failed", Decimal("0.00")

    safe_create_task(invalidate_user_cache(user_id))
    safe_create_task(invalidate_stats_cache())
    return True, "success", gross_amount


def _apply_debit(user: User, debit_amount: Decimal) -> tuple[Decimal, Decimal]:
    balance = _money(user.balance or 0)
    recovered = min(balance, debit_amount)
    debt_added = _money(debit_amount - recovered)
    user.balance = _money(balance - recovered)
    user.referral_debt = _money(Decimal(str(user.referral_debt or 0)) + debt_added)
    return recovered, debt_added


async def admin_adjust_referral(
    admin_identity: str,
    target_user_id: int,
    amount: Decimal,
    reason: str,
    idempotency_key: str,
) -> tuple[bool, str]:
    """Append an audited credit/debit; never edit ledger history."""

    identity = str(admin_identity).strip()
    reason = str(reason).strip()
    key = str(idempotency_key).strip()
    amount = _money(amount)
    if not identity or not reason or not key or amount == 0:
        return False, "invalid_adjustment"

    async with Database().session() as session:
        try:
            user = (await session.execute(
                select(User).where(User.telegram_id == target_user_id).with_for_update()
            )).scalar_one_or_none()
            if not user:
                return False, "user_not_found"

            duplicate = (await session.execute(
                select(ReferralEarnings.id).where(
                    ReferralEarnings.idempotency_key == key
                )
            )).scalar_one_or_none()
            if duplicate is not None:
                return False, "duplicate_adjustment"

            now = datetime.now(timezone.utc)
            recovered = Decimal("0.00")
            debt_added = Decimal("0.00")
            status = "available" if amount > 0 else "applied"
            if amount < 0:
                recovered, debt_added = _apply_debit(user, -amount)
                if recovered > 0:
                    session.add(Operations(
                        user_id=target_user_id,
                        operation_value=-recovered,
                        operation_time=now,
                    ))

            adjustment = ReferralEarnings(
                referrer_id=target_user_id,
                referral_id=None,
                amount=amount,
                original_amount=abs(amount),
                status=status,
                earning_type="manual_adjustment",
                admin_identity=identity,
                reason=reason,
                ready_at=now if amount > 0 else None,
                balance_recovered=recovered if amount < 0 else None,
                debt_added=debt_added if amount < 0 else None,
                idempotency_key=key,
            )
            session.add(adjustment)
            await session.flush()
            await log_audit(
                "admin_referral_adjustment",
                user_id=target_user_id,
                resource_type="ReferralEarnings",
                resource_id=adjustment.id,
                details=f"admin={identity}, amount={amount}, reason={reason}",
                session=session,
            )
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return False, "duplicate_adjustment"
        except Exception as exc:
            await session.rollback()
            await log_audit(
                "admin_referral_adjustment_failed",
                level="WARNING",
                user_id=target_user_id,
                details=type(exc).__name__,
            )
            return False, "adjustment_failed"

    safe_create_task(invalidate_user_cache(target_user_id))
    safe_create_task(invalidate_stats_cache())
    return True, "success"


async def reverse_referral_earning(
    earning_id: int,
    *,
    reason: str = "Order refunded",
) -> tuple[bool, str]:
    """Reverse an order earning idempotently using the global lock order."""

    async with Database().session() as session:
        preview = (await session.execute(
            select(ReferralEarnings.referrer_id).where(ReferralEarnings.id == earning_id)
        )).scalar_one_or_none()
        if preview is None:
            return False, "earning_not_found"

        try:
            user = (await session.execute(
                select(User).where(User.telegram_id == preview).with_for_update()
            )).scalar_one_or_none()
            earning = (await session.execute(
                select(ReferralEarnings)
                .where(ReferralEarnings.id == earning_id)
                .with_for_update()
            )).scalar_one_or_none()
            if not user or not earning:
                return False, "earning_not_found"
            if earning.earning_type != "order_purchase":
                return False, "invalid_earning_type"
            if earning.status == "reversed":
                return False, "already_reversed"

            now = datetime.now(timezone.utc)
            if earning.status in ("pending", "available"):
                earning.status = "reversed"
                earning.reversed_at = now
            elif earning.status == "converted":
                existing_reversal = (await session.execute(
                    select(ReferralEarnings.id).where(
                        ReferralEarnings.earning_type == "compensating_reversal",
                        ReferralEarnings.reversal_of_id == earning.id,
                    )
                )).scalar_one_or_none()
                if existing_reversal is not None:
                    return False, "already_reversed"

                recovered, debt_added = _apply_debit(user, _money(earning.amount))
                if recovered > 0:
                    session.add(Operations(
                        user_id=user.telegram_id,
                        operation_value=-recovered,
                        operation_time=now,
                    ))
                session.add(ReferralEarnings(
                    referrer_id=earning.referrer_id,
                    referral_id=earning.referral_id,
                    amount=-_money(earning.amount),
                    original_amount=earning.original_amount,
                    status="applied",
                    earning_type="compensating_reversal",
                    reason=str(reason).strip() or "Order refunded",
                    reversal_of_id=earning.id,
                    balance_recovered=recovered,
                    debt_added=debt_added,
                    reversed_at=now,
                ))
            else:
                return False, "earning_not_reversible"

            await log_audit(
                "referral_earning_reversed",
                user_id=earning.referrer_id,
                resource_type="ReferralEarnings",
                resource_id=earning.id,
                details=f"status={earning.status}, reason={reason}",
                session=session,
            )
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return False, "already_reversed"
        except Exception as exc:
            await session.rollback()
            await log_audit(
                "referral_reversal_failed",
                level="WARNING",
                resource_type="ReferralEarnings",
                resource_id=earning_id,
                details=type(exc).__name__,
            )
            return False, "reversal_failed"

    safe_create_task(invalidate_user_cache(preview))
    safe_create_task(invalidate_stats_cache())
    return True, "success"
