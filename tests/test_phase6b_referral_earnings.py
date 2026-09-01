from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from bot.database import Database
from bot.database.methods.referrals import (
    admin_adjust_referral,
    convert_referral_earnings,
    release_mature_referral_earnings,
    reverse_referral_earning,
    start_manual_referral_hold,
)
from bot.database.methods.transactions import (
    buy_item_transaction,
    checkout_cart_transaction,
)
from bot.database.models.main import (
    BoughtGoods,
    CartItems,
    Operations,
    Order,
    OrderItem,
    PromoCodes,
    ReferralConversions,
    ReferralEarnings,
    StoreSettings,
    User,
)


async def _configure_rate(rate: str = "10.00"):
    async with Database().session() as session:
        settings = await session.get(StoreSettings, 1)
        if settings is None:
            settings = StoreSettings(id=1)
            session.add(settings)
        settings.referral_percent = Decimal(rate)


async def _earning_for(referrer_id: int) -> ReferralEarnings:
    async with Database().session() as session:
        return (await session.execute(
            select(ReferralEarnings)
            .where(ReferralEarnings.referrer_id == referrer_id)
            .order_by(ReferralEarnings.id.asc())
        )).scalars().first()


async def _mature_all(now=None) -> int:
    async with Database().session() as session:
        return await release_mature_referral_earnings(
            session,
            now=now or datetime.now(timezone.utc) + timedelta(days=10),
        )


@pytest.mark.asyncio
async def test_instant_purchase_creates_held_net_commission(user_factory, item_factory):
    await _configure_rate("10.00")
    await user_factory(telegram_id=610001)
    await user_factory(telegram_id=610002, balance=100, referral_id=610001)
    await item_factory(name="ReferralNet", price=50, values=[("value", False)])

    async with Database().session() as session:
        session.add(PromoCodes(
            code="NET20",
            discount_type="percent",
            discount_value=Decimal("20.00"),
            max_uses=1,
            current_uses=0,
            is_active=True,
        ))

    success, message, _ = await buy_item_transaction(610002, "ReferralNet", "NET20")
    assert (success, message) == (True, "success")

    earning = await _earning_for(610001)
    assert earning.status == "pending"
    assert earning.order_item_id is not None
    assert earning.bought_goods_id is None
    assert earning.ready_at is not None
    assert earning.commission_base_amount == Decimal("40.00")
    assert earning.amount == Decimal("4.00")


@pytest.mark.asyncio
async def test_cart_creates_one_commission_per_bought_good(user_factory, item_factory):
    await _configure_rate("5.00")
    await user_factory(telegram_id=620001)
    await user_factory(telegram_id=620002, balance=100, referral_id=620001)
    await item_factory(name="CartReferralA", price=20, values=[("a", False)])
    await item_factory(name="CartReferralB", price=30, values=[("b", False)])
    async with Database().session() as session:
        session.add_all([
            CartItems(user_id=620002, item_name="CartReferralA"),
            CartItems(user_id=620002, item_name="CartReferralB"),
        ])

    success, message, results = await checkout_cart_transaction(620002)
    assert (success, message) == (True, "success")
    assert len(results) == 2
    async with Database().session() as session:
        earnings = (await session.execute(
            select(ReferralEarnings)
            .where(ReferralEarnings.referrer_id == 620001)
            .order_by(ReferralEarnings.bought_goods_id)
        )).scalars().all()
        assert [row.amount for row in earnings] == [Decimal("1.00"), Decimal("1.50")]
        assert all(row.bought_goods_id is not None and row.order_item_id is None for row in earnings)


@pytest.mark.asyncio
async def test_worker_only_releases_and_is_repeat_safe(user_factory, item_factory):
    await _configure_rate()
    await user_factory(telegram_id=630001, balance=9)
    await user_factory(telegram_id=630002, balance=50, referral_id=630001)
    await item_factory(name="WorkerReferral", price=20, values=[("v", False)])
    assert (await buy_item_transaction(630002, "WorkerReferral"))[0]

    before = datetime.now(timezone.utc)
    assert await _mature_all(before + timedelta(days=10)) == 1
    assert await _mature_all(before + timedelta(days=10)) == 0
    async with Database().session() as session:
        user = await session.get(User, 630001)
        operations = (await session.execute(
            select(func.count()).select_from(Operations).where(Operations.user_id == 630001)
        )).scalar_one()
        earning = await _earning_for(630001)
        assert user.balance == Decimal("9.00")
        assert operations == 0
        assert earning.status == "available"


@pytest.mark.asyncio
async def test_repeated_manual_fulfillment_does_not_reset_hold(user_factory):
    await user_factory(telegram_id=635001)
    await user_factory(telegram_id=635002, referral_id=635001)
    first_ready_at = datetime.now(timezone.utc) + timedelta(hours=72)
    second_ready_at = first_ready_at + timedelta(hours=1)
    async with Database().session() as session:
        order = Order(
            public_id="FGK-MANUAL-HOLD",
            user_id=635002,
            status="processing",
            currency="USD",
            subtotal=Decimal("20.00"),
            discount_total=Decimal("0.00"),
            total=Decimal("20.00"),
        )
        session.add(order)
        await session.flush()
        item = OrderItem(
            order_id=order.id,
            item_id=None,
            product_name_snapshot="Manual",
            quantity=1,
            unit_price=Decimal("20.00"),
            subtotal=Decimal("20.00"),
            discount_total=Decimal("0.00"),
            total=Decimal("20.00"),
            fulfillment_status="pending",
        )
        session.add(item)
        await session.flush()
        session.add(ReferralEarnings(
            referrer_id=635001,
            referral_id=635002,
            amount=Decimal("2.00"),
            original_amount=Decimal("20.00"),
            status="pending",
            earning_type="order_purchase",
            order_item_id=item.id,
            commission_base_amount=Decimal("20.00"),
            commission_rate=Decimal("10.00"),
            ready_at=None,
        ))
        await session.flush()
        assert await start_manual_referral_hold(
            session, order_item_id=item.id, ready_at=first_ready_at
        ) is True
        assert await start_manual_referral_hold(
            session, order_item_id=item.id, ready_at=second_ready_at
        ) is False

    earning = await _earning_for(635001)
    # SQLite drops timezone metadata; PostgreSQL preserves TIMESTAMPTZ.
    assert earning.ready_at.replace(tzinfo=timezone.utc) == first_ready_at


@pytest.mark.asyncio
async def test_conversion_offsets_debt_before_balance(user_factory):
    await user_factory(telegram_id=640001, balance=1)
    async with Database().session() as session:
        user = await session.get(User, 640001)
        user.referral_debt = Decimal("3.00")

    assert (await admin_adjust_referral(
        "telegram:999", 640001, Decimal("10.00"), "approved credit", "credit-640001"
    )) == (True, "success")
    success, message, gross = await convert_referral_earnings(640001)
    assert (success, message, gross) == (True, "success", Decimal("10.00"))

    async with Database().session() as session:
        user = await session.get(User, 640001)
        conversion = (await session.execute(select(ReferralConversions))).scalar_one()
        earning = (await session.execute(select(ReferralEarnings))).scalar_one()
        operation = (await session.execute(
            select(Operations).where(Operations.user_id == 640001)
        )).scalar_one()
        assert user.referral_debt == Decimal("0.00")
        assert user.balance == Decimal("8.00")
        assert conversion.gross_amount == Decimal("10.00")
        assert conversion.debt_offset == Decimal("3.00")
        assert conversion.balance_credit == Decimal("7.00")
        assert operation.operation_value == Decimal("7.00")
        assert earning.status == "converted"
        assert earning.conversion_id == conversion.id


@pytest.mark.asyncio
async def test_double_conversion_credits_once(user_factory):
    await user_factory(telegram_id=650001)
    assert (await admin_adjust_referral(
        "telegram:999", 650001, Decimal("5.00"), "one credit", "credit-650001"
    ))[0]
    first = await convert_referral_earnings(650001)
    second = await convert_referral_earnings(650001)
    assert first == (True, "success", Decimal("5.00"))
    assert second == (False, "no_earnings", Decimal("0.00"))
    async with Database().session() as session:
        user = await session.get(User, 650001)
        assert user.balance == Decimal("5.00")
        assert (await session.execute(select(func.count()).select_from(ReferralConversions))).scalar_one() == 1


@pytest.mark.asyncio
async def test_refund_before_availability_has_no_financial_effect(user_factory, item_factory):
    await _configure_rate()
    await user_factory(telegram_id=660001, balance=7)
    await user_factory(telegram_id=660002, balance=50, referral_id=660001)
    await item_factory(name="EarlyRefund", price=20, values=[("v", False)])
    assert (await buy_item_transaction(660002, "EarlyRefund"))[0]
    earning = await _earning_for(660001)

    assert await reverse_referral_earning(earning.id) == (True, "success")
    async with Database().session() as session:
        user = await session.get(User, 660001)
        earning = await session.get(ReferralEarnings, earning.id)
        assert earning.status == "reversed"
        assert earning.reversed_at is not None
        assert user.balance == Decimal("7.00")
        assert user.referral_debt == Decimal("0.00")
        assert (await session.execute(
            select(func.count()).select_from(ReferralEarnings).where(
                ReferralEarnings.earning_type == "compensating_reversal"
            )
        )).scalar_one() == 0


@pytest.mark.asyncio
async def test_late_refund_recovers_balance_then_adds_debt(user_factory, item_factory):
    await _configure_rate("50.00")
    await user_factory(telegram_id=670001)
    await user_factory(telegram_id=670002, balance=100, referral_id=670001)
    await item_factory(name="LateRefund", price=20, values=[("v", False)])
    assert (await buy_item_transaction(670002, "LateRefund"))[0]
    await _mature_all()
    earning = await _earning_for(670001)
    assert (await convert_referral_earnings(670001))[0]

    async with Database().session() as session:
        user = await session.get(User, 670001)
        user.balance = Decimal("4.00")

    assert await reverse_referral_earning(earning.id, reason="refund") == (True, "success")
    assert await reverse_referral_earning(earning.id, reason="refund") == (False, "already_reversed")
    async with Database().session() as session:
        user = await session.get(User, 670001)
        original = await session.get(ReferralEarnings, earning.id)
        reversal = (await session.execute(
            select(ReferralEarnings).where(
                ReferralEarnings.earning_type == "compensating_reversal"
            )
        )).scalar_one()
        assert original.status == "converted"
        assert user.balance == Decimal("0.00")
        assert user.referral_debt == Decimal("6.00")
        assert reversal.amount == Decimal("-10.00")
        assert reversal.balance_recovered == Decimal("4.00")
        assert reversal.debt_added == Decimal("6.00")
        assert reversal.bought_goods_id is None and reversal.order_item_id is None


@pytest.mark.asyncio
async def test_manual_debit_is_audited_and_idempotent(user_factory):
    await user_factory(telegram_id=680001, balance=3)
    first = await admin_adjust_referral(
        "telegram:999", 680001, Decimal("-8.00"), "fraud correction", "debit-680001"
    )
    second = await admin_adjust_referral(
        "telegram:999", 680001, Decimal("-8.00"), "fraud correction", "debit-680001"
    )
    assert first == (True, "success")
    assert second == (False, "duplicate_adjustment")
    async with Database().session() as session:
        user = await session.get(User, 680001)
        adjustment = (await session.execute(select(ReferralEarnings))).scalar_one()
        assert user.balance == Decimal("0.00")
        assert user.referral_debt == Decimal("5.00")
        assert adjustment.status == "applied"
        assert adjustment.admin_identity == "telegram:999"
        assert adjustment.reason == "fraud correction"
        assert adjustment.balance_recovered == Decimal("3.00")
        assert adjustment.debt_added == Decimal("5.00")


@pytest.mark.asyncio
async def test_legacy_rows_are_never_convertible(user_factory):
    await user_factory(telegram_id=690001)
    await user_factory(telegram_id=690002, referral_id=690001)
    async with Database().session() as session:
        session.add(ReferralEarnings(
            referrer_id=690001,
            referral_id=690002,
            amount=Decimal("2.00"),
            original_amount=Decimal("20.00"),
            status="settled",
            earning_type="legacy_topup",
            reason="pre Phase 6B",
        ))
    assert await convert_referral_earnings(690001) == (
        False, "no_earnings", Decimal("0.00")
    )


@pytest.mark.asyncio
async def test_zero_earning_is_rejected_by_database(user_factory):
    await user_factory(telegram_id=691001)
    async with Database().session() as session:
        session.add(ReferralEarnings(
            referrer_id=691001,
            referral_id=None,
            amount=Decimal("0.00"),
            original_amount=Decimal("0.00"),
            status="available",
            earning_type="manual_adjustment",
            admin_identity="telegram:999",
            reason="invalid zero",
            ready_at=datetime.now(timezone.utc),
        ))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
