import pytest
import pytest_asyncio
from sqlalchemy import select

from bot.database.main import Database
from bot.database.models.main import (
    ManualFulfillmentJob,
    ManualOrderInteraction,
    ManualOrderNotification
)

@pytest_asyncio.fixture
async def setup_test_job(db_session):
    # Just to have a fixture definition; tests will run basic queries to verify schemas.
    pass

@pytest.mark.asyncio
async def test_actor_fields_are_integers():
    """Verify that started_by, completed_by, created_by are BigInteger (which map to ints in python)."""
    async with Database().session() as session:
        # Check types on the model classes themselves
        assert ManualFulfillmentJob.started_by.type.python_type == int
        assert ManualFulfillmentJob.completed_by.type.python_type == int
        assert ManualOrderInteraction.created_by.type.python_type == int

@pytest.fixture
def mock_auth():
    import unittest.mock as mock
    with mock.patch("bot.web.fulfillment.is_authenticated", return_value=True), \
         mock.patch("sqladmin.authentication.AuthenticationBackend.authenticate", return_value=True):
        yield

@pytest.fixture
def admin_client():
    from starlette.testclient import TestClient
    from bot.web.admin import create_admin_app
    app = create_admin_app()
    with TestClient(app) as client:
        yield client

@pytest.mark.asyncio
async def test_full_fulfillment_flow_actor_types(admin_client, mock_auth):
    from bot.database.models import User, Goods, Categories, Order, OrderItem
    from decimal import Decimal

    async with Database().session() as s:
        user = User(telegram_id=99995, balance=Decimal('100.0'))
        s.add(user)
        cat = Categories(name='Test Cat Flow', description='test')
        s.add(cat)
        await s.flush()
        goods = Goods(name='Test Product Flow', price=Decimal('10'), description='desc', category_id=cat.id, fulfillment_mode='manual')
        s.add(goods)
        await s.flush()
        order = Order(user_id=99995, currency='USD', total=10, subtotal=10, public_id='TEST-FLOW')
        s.add(order)
        await s.flush()
        order_item = OrderItem(order_id=order.id, item_id=goods.id, quantity=1, unit_price=10, subtotal=10, total=10, product_name_snapshot='Test Product Flow')
        s.add(order_item)
        await s.flush()
        job = ManualFulfillmentJob(order_item_id=order_item.id, status='queued')
        s.add(job)
        await s.commit()

        job_id = job.id

    # 1. Start Processing
    response = admin_client.post(f"/admin/fulfillment/api/order/{job_id}/start")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["status"] == "in_progress"

    # Verify Start Database State
    async with Database().session() as s:
        db_job = (await s.execute(select(ManualFulfillmentJob).filter(ManualFulfillmentJob.id == job_id))).scalar_one()
        assert db_job.status == "in_progress"
        assert db_job.started_at is not None
        # Should be None since we didn't mock a numeric admin_id in session
        assert db_job.started_by is None

        interactions = (await s.execute(select(ManualOrderInteraction).filter(ManualOrderInteraction.fulfillment_job_id == job_id))).scalars().all()
        assert len(interactions) == 1
        assert interactions[0].kind == "status_change"
        assert interactions[0].created_by is None

        user_balance = (await s.execute(select(User.balance).filter(User.telegram_id == 99995))).scalar_one()
        assert user_balance == Decimal('100.0') # no second balance deduction

    # Idempotency of Start
    response2 = admin_client.post(f"/admin/fulfillment/api/order/{job_id}/start")
    assert response2.status_code == 200
    async with Database().session() as s:
        interactions = (await s.execute(select(ManualOrderInteraction).filter(ManualOrderInteraction.fulfillment_job_id == job_id))).scalars().all()
        assert len(interactions) == 1 # still 1 event

    # 2. Request Verification
    # (assuming bot.send_message is mocked or fails silently)
    import unittest.mock as mock
    with mock.patch("aiogram.Bot.send_message", return_value=mock.Mock(message_id=1234)):
        response = admin_client.post(f"/admin/fulfillment/api/order/{job_id}/request-verification", json={"message": "please verify"})

    assert response.status_code == 200
    async with Database().session() as s:
        db_job = (await s.execute(select(ManualFulfillmentJob).filter(ManualFulfillmentJob.id == job_id))).scalar_one()
        assert db_job.status == "waiting_customer"
        interactions = (await s.execute(select(ManualOrderInteraction).filter(ManualOrderInteraction.fulfillment_job_id == job_id))).scalars().all()
        assert len(interactions) == 2
        assert interactions[1].created_by is None

    # 3. Resume
    response = admin_client.post(f"/admin/fulfillment/api/order/{job_id}/resume")
    assert response.status_code == 200
    async with Database().session() as s:
        db_job = (await s.execute(select(ManualFulfillmentJob).filter(ManualFulfillmentJob.id == job_id))).scalar_one()
        assert db_job.status == "in_progress"

    # 4. Message
    with mock.patch("aiogram.Bot.send_message", return_value=mock.Mock(message_id=1235)):
        response = admin_client.post(f"/admin/fulfillment/api/order/{job_id}/message", json={"message": "hello"})
    assert response.status_code == 200

    # 5. Complete
    with mock.patch("aiogram.Bot.send_message", return_value=mock.Mock(message_id=1236)):
        response = admin_client.post(f"/admin/fulfillment/api/order/{job_id}/complete", json={"note": "done"})
    assert response.status_code == 200

    async with Database().session() as s:
        db_job = (await s.execute(select(ManualFulfillmentJob).filter(ManualFulfillmentJob.id == job_id))).scalar_one()
        assert db_job.status == "completed"
        assert db_job.completed_at is not None
        assert db_job.completed_by is None

        interactions = (await s.execute(select(ManualOrderInteraction).filter(ManualOrderInteraction.fulfillment_job_id == job_id))).scalars().all()
        assert len(interactions) == 5
        assert all(i.created_by is None for i in interactions)

        # Check BoughtGoods was not created automatically (should be manual logic or already handled by normal flow if needed, but not in api_complete)
        from bot.database.models import BoughtGoods
        from sqlalchemy import func
        bought_goods_count = (await s.execute(select(func.count(BoughtGoods.id)).filter(BoughtGoods.order_item_id == db_job.order_item_id))).scalar_one()
        assert bought_goods_count == 0


@pytest.mark.asyncio
async def test_manual_order_interaction_no_encryption_version():
    """Regression test: ManualOrderInteraction must not accept encryption_version."""
    from bot.database.models.main import ManualOrderInteraction

    with pytest.raises(TypeError) as exc_info:
        ManualOrderInteraction(
            order_id=1,
            fulfillment_job_id=1,
            direction='customer_to_admin',
            kind='customer_reply',
            encrypted_content='{"ciphertext": "abc", "version": 1}',
            encryption_version=1,
            safe_preview="Customer reply received",
            is_sensitive=True
        )
    assert "encryption_version" in str(exc_info.value)

    # Verify it succeeds without encryption_version
    interaction = ManualOrderInteraction(
        order_id=1,
        fulfillment_job_id=1,
        direction='customer_to_admin',
        kind='customer_reply',
        encrypted_content='{"ciphertext": "abc", "version": 1}',
        safe_preview="Customer reply received",
        is_sensitive=True
    )
    assert interaction.encrypted_content == '{"ciphertext": "abc", "version": 1}'

@pytest.mark.asyncio
async def test_api_reveal_interaction(admin_client, mock_auth):
    from sqlalchemy import select
    from bot.database.main import Database
    from bot.database.models.main import ManualOrderInteraction, ManualFulfillmentJob, Order, OrderItem
    import json
    from bot.misc.encryption import encrypt_text

    async with Database().session() as s:
        job = (await s.execute(select(ManualFulfillmentJob))).scalars().first()
        if not job:
            return
        job.status = 'in_progress'
        await s.flush()
        item = (await s.execute(select(OrderItem).where(OrderItem.id == job.order_item_id))).scalars().first()

        job_id = job.id

        # Create a real encrypted interaction
        encrypted = encrypt_text("Real OTP 998877")
        interaction = ManualOrderInteraction(
            order_id=item.order_id,
            fulfillment_job_id=job.id,
            direction='customer_to_admin',
            kind='customer_reply',
            encrypted_content=json.dumps(encrypted),
            safe_preview="Customer reply received",
            is_sensitive=True
        )
        s.add(interaction)
        await s.commit()

        interaction_id = interaction.id

    # Test unauthorized
    import unittest.mock as mock
    with mock.patch("bot.web.fulfillment.is_authenticated", return_value=False):
        resp = admin_client.post(f"/admin/fulfillment/api/order/{job_id}/interaction/{interaction_id}/reveal", json={})
        assert resp.status_code == 401

    # Test success
    resp = admin_client.post(f"/admin/fulfillment/api/order/{job_id}/interaction/{interaction_id}/reveal", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["reply"] == "Real OTP 998877"
    assert "no-store" in resp.headers.get("cache-control", "").lower()

    # Test read_at is populated
    async with Database().session() as s:
        ia = (await s.execute(select(ManualOrderInteraction).filter_by(id=interaction_id))).scalar_one()
        assert ia.read_at is not None

        from bot.database.models.main import AuditLog
        audit = (await s.execute(select(AuditLog).filter_by(resource_type="ManualOrderInteraction", resource_id=str(interaction_id)))).scalars().first()
        assert audit is not None
        assert audit.action == "customer_reply_revealed"
        assert "Real OTP" not in audit.details

def test_workspace_html_compiles():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("bot/web/templates"))
    template = env.get_template("fulfillment/workspace.html")
    assert template is not None

@pytest.mark.asyncio
async def test_manual_order_notification_no_safe_preview():
    from bot.database.models.main import ManualOrderNotification
    notif = ManualOrderNotification()
    import pytest
    with pytest.raises(AttributeError):
        _ = getattr(notif, "safe_preview")
