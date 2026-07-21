import pytest
import unittest.mock as mock
from starlette.testclient import TestClient
from bot.web.admin import create_admin_app
from bot.database.models.main import ManualFulfillmentJob

@pytest.fixture
def admin_client():
    app = create_admin_app()
    with TestClient(app) as client:
        yield client

@pytest.fixture
def mock_auth():
    with mock.patch("bot.web.fulfillment.is_authenticated", return_value=True), \
         mock.patch("sqladmin.authentication.AuthenticationBackend.authenticate", return_value=True):
        yield

def test_routes_exist_and_order():
    app = create_admin_app()
    paths = [r.path for r in app.routes if hasattr(r, 'path')]
    assert "/admin/fulfillment" in paths, "Fulfillment route is missing"

    # Ensure /admin/fulfillment is evaluated before the catch-all /admin (or we use explicit routes)
    fulfillment_index = paths.index("/admin/fulfillment")
    if "/admin" in paths:
        admin_index = paths.index("/admin")
        assert fulfillment_index < admin_index, "Fulfillment must route before SQLAdmin catch-all"

def test_unauthenticated_dashboard_denied(admin_client):
    response = admin_client.get("/admin/fulfillment")
    # Starlette might return 401 depending on implementation
    assert response.status_code == 401

def test_unauthenticated_queue_denied(admin_client):
    response = admin_client.get("/admin/fulfillment/api/queue")
    assert response.status_code == 401

def test_unauthenticated_workspace_denied(admin_client):
    response = admin_client.get("/admin/fulfillment/order/1")
    assert response.status_code == 401

def test_sqladmin_fallback_reachable(admin_client):
    # Unauthenticated /admin/manual-fulfillment-job/list should redirect to login (303 or 302)
    response = admin_client.get("/admin/manual-fulfillment-job/list", follow_redirects=False)
    assert response.status_code in [302, 303], f"SQLAdmin fallback failed: {response.status_code}"

def test_authenticated_dashboard_renders(admin_client, mock_auth):
    response = admin_client.get("/admin/fulfillment")
    assert response.status_code == 200
    # custom templates render
    assert "Fulfillment Console" in response.text
    # no secrets in dashboard HTML
    assert "password" not in response.text.lower() # basic check

def test_authenticated_queue_reachable(admin_client, mock_auth):
    response = admin_client.get("/admin/fulfillment/api/queue")
    assert response.status_code == 200
    data = response.json()
    assert "queue" in data
    # no secrets in queue JSON
    assert "password" not in response.text.lower()

def test_sidebar_includes_fulfillment_console():
    # Read the layout template to ensure the sidebar includes the link
    with open("bot/web/templates/layout.html", "r", encoding="utf-8") as f:
        content = f.read()
    assert "⚡ Fulfillment Console" in content
    assert "/admin/fulfillment" in content
    assert "fa-bolt" in content

@pytest.mark.asyncio
async def test_workspace_integration(admin_client, mock_auth):
    from bot.database.models import User, Goods, Categories, Order, OrderItem, OrderCustomerInput
    from bot.database.main import Database
    from decimal import Decimal

    async with Database().session() as s:
        user = User(telegram_id=99992, balance=Decimal('0'))
        s.add(user)
        cat = Categories(name='Test Cat', description='test')
        s.add(cat)
        await s.flush()
        goods = Goods(name='Test Product', price=Decimal('10'), description='desc', category_id=cat.id, fulfillment_mode='manual')
        s.add(goods)
        await s.flush()
        order = Order(user_id=99992, currency='USD', total=10, subtotal=10, public_id='TEST-999')
        s.add(order)
        await s.flush()
        order_item = OrderItem(order_id=order.id, item_id=goods.id, quantity=3, unit_price=10, subtotal=30, total=30, product_name_snapshot='Test Product')
        s.add(order_item)
        await s.flush()
        job = ManualFulfillmentJob(order_item_id=order_item.id)
        s.add(job)
        await s.flush()

        customer_input = OrderCustomerInput(
            order_id=order.id,
            order_item_id=order_item.id,
            field_key_snapshot="password",
            label_i18n_snapshot={"en": "Password"},
            field_type_snapshot="text",
            scope_snapshot="per_order",
            unit_index=0,
            is_sensitive=True,
            encrypted_value="ciphertext_blob_123",
            masked_preview="••••••••",
            encryption_version=1
        )
        s.add(customer_input)
        await s.commit()

        job_id = job.id
        order_pub_id = order.public_id

    response = admin_client.get(f"/admin/fulfillment/order/{job_id}")
    assert response.status_code == 200

    html = response.text
    assert order_pub_id in html
    assert "Test Product" in html
    assert "99992" in html or "Test User" in html

    assert "real_password" not in html
    assert "ciphertext_blob_123" not in html

    response_404 = admin_client.get(f"/admin/fulfillment/order/999999")
    assert response_404.status_code == 404

@pytest.mark.asyncio
async def test_api_reveal_integration(admin_client, mock_auth):
    from bot.database.models import User, Goods, Categories, Order, OrderItem, OrderCustomerInput, AuditLog
    from bot.database.main import Database
    from decimal import Decimal
    from bot.misc.encryption import encrypt_text
    import json

    async with Database().session() as s:
        user = User(telegram_id=99993, balance=Decimal('0'))
        s.add(user)
        cat = Categories(name='Test Cat', description='test')
        s.add(cat)
        await s.flush()
        goods = Goods(name='Test Product', price=Decimal('10'), description='desc', category_id=cat.id, fulfillment_mode='manual')
        s.add(goods)
        await s.flush()
        order = Order(user_id=99993, currency='USD', total=10, subtotal=10, public_id='TEST-999-REVEAL')
        s.add(order)
        await s.flush()
        order_item = OrderItem(order_id=order.id, item_id=goods.id, quantity=1, unit_price=10, subtotal=10, total=10, product_name_snapshot='Test Product')
        s.add(order_item)
        await s.flush()
        job = ManualFulfillmentJob(order_item_id=order_item.id)
        s.add(job)
        await s.flush()

        enc_dict = encrypt_text("my_super_secret")
        enc_value = enc_dict["ciphertext"]
        enc_version = enc_dict["version"]

        customer_input = OrderCustomerInput(
            order_id=order.id,
            order_item_id=order_item.id,
            field_key_snapshot="password",
            label_i18n_snapshot={"en": "Password"},
            field_type_snapshot="text",
            scope_snapshot="per_order",
            unit_index=0,
            is_sensitive=True,
            encrypted_value=enc_value,
            masked_preview="••••••••",
            encryption_version=enc_version
        )
        s.add(customer_input)
        await s.commit()

        job_id = job.id
        input_id = customer_input.id

    response = admin_client.post(f"/admin/fulfillment/api/order/{job_id}/reveal", json={"input_id": input_id})
    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == "no-store"
    data = response.json()
    assert data["ok"] is True
    assert data["plaintext"] == "my_super_secret"

    async with Database().session() as s:
        from sqlalchemy import select
        audit_res = await s.execute(select(AuditLog).filter(AuditLog.resource_id == str(input_id)))
        audit = audit_res.scalar_one_or_none()
        assert audit is not None
        assert audit.action == "secret_revealed"
        assert audit.resource_type == "OrderCustomerInput"

        details = json.loads(audit.details)
        assert details["job_id"] == job_id
        assert "actor_label" in details
        assert "my_super_secret" not in audit.details

@pytest.mark.asyncio
async def test_api_start_integration(admin_client, mock_auth):
    from bot.database.models import User, Goods, Categories, Order, OrderItem, ManualOrderInteraction
    from bot.database.main import Database
    from decimal import Decimal

    async with Database().session() as s:
        user = User(telegram_id=99994, balance=Decimal('0'))
        s.add(user)
        cat = Categories(name='Test Cat', description='test')
        s.add(cat)
        await s.flush()
        goods = Goods(name='Test Product', price=Decimal('10'), description='desc', category_id=cat.id, fulfillment_mode='manual')
        s.add(goods)
        await s.flush()
        order = Order(user_id=99994, currency='USD', total=10, subtotal=10, public_id='TEST-999-START')
        s.add(order)
        await s.flush()
        order_item = OrderItem(order_id=order.id, item_id=goods.id, quantity=1, unit_price=10, subtotal=10, total=10, product_name_snapshot='Test Product')
        s.add(order_item)
        await s.flush()
        job = ManualFulfillmentJob(order_item_id=order_item.id, status='queued')
        s.add(job)
        await s.commit()

        job_id = job.id

    response = admin_client.post(f"/admin/fulfillment/api/order/{job_id}/start")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["status"] == "in_progress"

    async with Database().session() as s:
        from sqlalchemy import select
        db_job = (await s.execute(select(ManualFulfillmentJob).filter(ManualFulfillmentJob.id == job_id))).scalar_one()
        assert db_job.status == "in_progress"
        assert db_job.started_at is not None
        started_at = db_job.started_at

        interactions = (await s.execute(select(ManualOrderInteraction).filter(ManualOrderInteraction.fulfillment_job_id == job_id))).scalars().all()
        assert len(interactions) == 1
        assert interactions[0].kind == "status_change"

    # Duplicate call
    response2 = admin_client.post(f"/admin/fulfillment/api/order/{job_id}/start")
    assert response2.status_code == 200

    async with Database().session() as s:
        db_job2 = (await s.execute(select(ManualFulfillmentJob).filter(ManualFulfillmentJob.id == job_id))).scalar_one()
        assert db_job2.status == "in_progress"
        assert db_job2.started_at == started_at

@pytest.mark.asyncio
async def test_frontend_modal_behavior(admin_client, mock_auth):
    from bot.database.models import User, Goods, Categories, Order, OrderItem, ManualFulfillmentJob
    from bot.database.main import Database
    from decimal import Decimal

    async with Database().session() as s:
        user = User(telegram_id=99996, balance=Decimal('0'))
        s.add(user)
        cat = Categories(name='Test Cat', description='test')
        s.add(cat)
        await s.flush()
        goods = Goods(name='Test Product', price=Decimal('10'), description='desc', category_id=cat.id, fulfillment_mode='manual')
        s.add(goods)
        await s.flush()
        order = Order(user_id=99996, currency='USD', total=10, subtotal=10, public_id='TEST-999-MODAL')
        s.add(order)
        await s.flush()
        order_item = OrderItem(order_id=order.id, item_id=goods.id, quantity=1, unit_price=10, subtotal=10, total=10, product_name_snapshot='Test Product')
        s.add(order_item)
        await s.flush()
        job = ManualFulfillmentJob(order_item_id=order_item.id, status='queued')
        s.add(job)
        await s.commit()

        job_id = job.id

    response = admin_client.get(f"/admin/fulfillment/order/{job_id}")
    assert response.status_code == 200
    html = response.text

    # Prove there are no raw prompt() or alert() calls
    assert "prompt(" not in html
    assert "alert(" not in html

    # Prove there are no duplicate modal IDs
    assert html.count('id="messageModal"') == 1
    assert html.count('id="verificationModal"') == 1
    assert html.count('id="completeModal"') == 1

    # Prove no malformed URL with double slash
    assert "/order//" not in html

    # Check that event delegation is present instead of inline onclick for replyToCustomer
    assert 'onclick="replyToCustomer()"' not in html
    assert "timelineContainer.addEventListener('click'" in html or "timelineContainer.addEventListener('click'" in html or "document.getElementById('timeline-container')" in html

    # Check that closeMessageModal resets things properly and lacks isRequestRunning blocker
    assert "function closeVerificationModal()" in html
    assert "document.body.style.overflow = '';" in html
    assert "document.body.classList.remove('modal-open');" in html

@pytest.mark.asyncio
async def test_auto_reveal_customer_reply(admin_client, mock_auth):
    from bot.database.models import User, Goods, Categories, Order, OrderItem, ManualFulfillmentJob, ManualOrderInteraction
    from bot.database.main import Database
    from decimal import Decimal
    from bot.misc.encryption import encrypt_text
    import json

    async with Database().session() as s:
        user = User(telegram_id=99997, balance=Decimal('0'))
        s.add(user)
        cat = Categories(name='Test Cat', description='test')
        s.add(cat)
        await s.flush()
        goods = Goods(name='Test Product', price=Decimal('10'), description='desc', category_id=cat.id, fulfillment_mode='manual')
        s.add(goods)
        await s.flush()
        order = Order(user_id=99997, currency='USD', total=10, subtotal=10, public_id='TEST-999-REPLY')
        s.add(order)
        await s.flush()
        order_item = OrderItem(order_id=order.id, item_id=goods.id, quantity=1, unit_price=10, subtotal=10, total=10, product_name_snapshot='Test Product')
        s.add(order_item)
        await s.flush()
        job = ManualFulfillmentJob(order_item_id=order_item.id, status='waiting_customer')
        s.add(job)
        await s.flush()

        enc_dict = encrypt_text("Real Plaintext Reply")
        ia = ManualOrderInteraction(
            order_id=order.id,
            fulfillment_job_id=job.id,
            direction='customer_to_admin',
            kind='customer_reply',
            encrypted_content=json.dumps(enc_dict),
            safe_preview="Customer reply received",
            is_sensitive=True
        )
        s.add(ia)
        await s.commit()

        job_id = job.id

    # 1. Check initial HTML
    response = admin_client.get(f"/admin/fulfillment/order/{job_id}")
    assert response.status_code == 200
    html = response.text

    # Plaintext reply absent from initial HTML
    assert "Real Plaintext Reply" not in html
    # Prove that innerHTML is not used for reply content
    assert "contentDiv.innerHTML" not in html
    assert "contentDiv.textContent = plaintext" in html

    # Prove autoRevealReplies calls revealCustomerReply correctly
    assert "const autoRevealedInteractions = new Set();" in html
    assert "revealCustomerReply(JOB_ID, interactionId, true)" in html

    # 2. Check state JSON
    state_response = admin_client.get(f"/admin/fulfillment/api/order/{job_id}/state")
    assert state_response.status_code == 200
    state_data = state_response.json()
    state_str = json.dumps(state_data)
    # Plaintext absent from state JSON
    assert "Real Plaintext Reply" not in state_str

    # 3. Simulate an auto-reveal API call
    interaction_id = state_data["conversation_messages"][-1]["id"]
    reveal_res = admin_client.post(f"/admin/fulfillment/api/order/{job_id}/interaction/{interaction_id}/reveal", json={})
    assert reveal_res.status_code == 200
    reveal_data = reveal_res.json()
    assert reveal_data["ok"] is True
    assert reveal_data["reply"] == "Real Plaintext Reply"

    # Prove New Reply becomes read (badge removed from next state fetch)
    state_res2 = admin_client.get(f"/admin/fulfillment/api/order/{job_id}/state")
    state_data2 = state_res2.json()
    ia_updated = state_data2["conversation_messages"][-1]
    assert ia_updated["is_unread"] is False

    # Job remains waiting_customer
    assert state_data2["status"] == "waiting_customer"
