from decimal import Decimal

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from bot.database import Database
from bot.database.models.main import Permission, ReferralEarnings
from bot.misc import EnvKeys
from bot.web.admin import create_admin_app


def _login(client, monkeypatch, user_id: int):
    monkeypatch.setattr(EnvKeys, "DASHBOARD_ADMIN_TELEGRAM_ID", str(user_id))
    monkeypatch.setattr(EnvKeys, "ADMIN_USERNAME", "referral-admin")
    monkeypatch.setattr(EnvKeys, "ADMIN_PASSWORD", "safe-test-password")
    response = client.post(
        "/admin/login",
        data={"username": "referral-admin", "password": "safe-test-password"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


@pytest.mark.asyncio
async def test_referral_adjustment_requires_balance_permission(
    monkeypatch, user_factory, role_factory
):
    role = await role_factory(name="ReferralReadOnly", permissions=Permission.USE)
    admin = await user_factory(telegram_id=710001, role_id=role)
    app = create_admin_app()
    with TestClient(app) as client:
        _login(client, monkeypatch, admin["telegram_id"])
        response = client.get("/admin/referrals/adjust", follow_redirects=False)
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_referral_adjustment_csrf_is_required(
    monkeypatch, user_factory, role_factory
):
    role = await role_factory(name="ReferralBalanceAdmin", permissions=Permission.BALANCE_MANAGE)
    admin = await user_factory(telegram_id=710002, role_id=role)
    await user_factory(telegram_id=710003)
    app = create_admin_app()
    with TestClient(app) as client:
        _login(client, monkeypatch, admin["telegram_id"])
        response = client.post(
            "/admin/referrals/adjust",
            data={"target_user_id": "710003", "amount": "5", "reason": "test"},
            follow_redirects=False,
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_referral_adjustment_form_creates_audited_row(
    monkeypatch, user_factory, role_factory
):
    role = await role_factory(name="ReferralAdjustmentAdmin", permissions=Permission.BALANCE_MANAGE)
    admin = await user_factory(telegram_id=710004, role_id=role)
    await user_factory(telegram_id=710005)
    app = create_admin_app()
    with TestClient(app) as client:
        _login(client, monkeypatch, admin["telegram_id"])
        form = client.get("/admin/referrals/adjust")
        assert form.status_code == 200
        csrf = form.text.split('name="csrf_token" value="')[1].split('"')[0]
        key = form.text.split('name="idempotency_key" value="')[1].split('"')[0]
        response = client.post(
            "/admin/referrals/adjust",
            data={
                "csrf_token": csrf,
                "idempotency_key": key,
                "target_user_id": "710005",
                "amount": "12.34",
                "reason": "customer care credit",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

    async with Database().session() as session:
        row = (await session.execute(select(ReferralEarnings))).scalar_one()
        assert row.amount == Decimal("12.34")
        assert row.status == "available"
        assert row.admin_identity == "telegram:710004"
        assert row.reason == "customer care credit"
        assert row.idempotency_key == key
