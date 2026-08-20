import pytest
from starlette.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
from bot.web.admin import create_admin_app
from bot.misc.env import EnvKeys
from bot.database.models.main import Permission
from sqlalchemy import select
from bot.database.main import Database
from bot.database.models.main import BroadcastCampaign

@pytest.fixture
def admin_app():
    return create_admin_app()

def test_unauthenticated_rejection(admin_app):
    client = TestClient(admin_app)
    response = client.get("/admin/broadcasts/new", follow_redirects=False)
    assert response.status_code in (302, 303, 401, 403)

def test_invalid_identity_mapping(admin_app, monkeypatch):
    monkeypatch.setattr(EnvKeys, "DASHBOARD_ADMIN_TELEGRAM_ID", "99999999")
    monkeypatch.setattr(EnvKeys, "ADMIN_USERNAME", "testadmin")
    monkeypatch.setattr(EnvKeys, "ADMIN_PASSWORD", "testpass")

    client = TestClient(admin_app)
    client.post("/admin/login", data={"username": "testadmin", "password": "testpass"}, follow_redirects=False)
    response = client.get("/admin/broadcasts/new", follow_redirects=False)
    assert response.status_code in (302, 303, 401, 403)

def test_missing_user_mapping(admin_app, monkeypatch):
    monkeypatch.setattr(EnvKeys, "DASHBOARD_ADMIN_TELEGRAM_ID", "")
    monkeypatch.setattr(EnvKeys, "OWNER_ID", "99999999")
    monkeypatch.setattr(EnvKeys, "ADMIN_USERNAME", "testadmin")
    monkeypatch.setattr(EnvKeys, "ADMIN_PASSWORD", "testpass")

    client = TestClient(admin_app)
    client.post("/admin/login", data={"username": "testadmin", "password": "testpass"}, follow_redirects=False)
    response = client.get("/admin/broadcasts/new", follow_redirects=False)
    assert response.status_code in (302, 303, 401, 403)

@pytest.mark.asyncio
async def test_missing_broadcast_permission(admin_app, monkeypatch, user_factory, role_factory):
    role = await role_factory(name="NoBC", permissions=Permission.USE)
    user = await user_factory(telegram_id=991, role_id=role)

    monkeypatch.setattr(EnvKeys, "DASHBOARD_ADMIN_TELEGRAM_ID", "991")
    monkeypatch.setattr(EnvKeys, "ADMIN_USERNAME", "testadmin")
    monkeypatch.setattr(EnvKeys, "ADMIN_PASSWORD", "testpass")

    client = TestClient(admin_app)
    client.post("/admin/login", data={"username": "testadmin", "password": "testpass"}, follow_redirects=False)
    response = client.get("/admin/broadcasts/new", follow_redirects=False)
    assert response.status_code in (302, 303, 401, 403)

@pytest.mark.asyncio
async def test_authorized_access(admin_app, monkeypatch, user_factory, role_factory):
    role = await role_factory(name="AdminBC", permissions=Permission.BROADCAST)
    user = await user_factory(telegram_id=992, role_id=role)

    monkeypatch.setattr(EnvKeys, "DASHBOARD_ADMIN_TELEGRAM_ID", "992")
    monkeypatch.setattr(EnvKeys, "ADMIN_USERNAME", "testadmin")
    monkeypatch.setattr(EnvKeys, "ADMIN_PASSWORD", "testpass")

    client = TestClient(admin_app)
    client.post("/admin/login", data={"username": "testadmin", "password": "testpass"}, follow_redirects=False)
    response = client.get("/admin/broadcasts/new", follow_redirects=False)
    assert response.status_code == 200

@pytest.mark.asyncio
async def test_missing_csrf(admin_app, monkeypatch, user_factory, role_factory):
    role = await role_factory(name="AdminBC2", permissions=Permission.BROADCAST)
    user = await user_factory(telegram_id=993, role_id=role)

    monkeypatch.setattr(EnvKeys, "DASHBOARD_ADMIN_TELEGRAM_ID", "993")
    monkeypatch.setattr(EnvKeys, "ADMIN_USERNAME", "testadmin")
    monkeypatch.setattr(EnvKeys, "ADMIN_PASSWORD", "testpass")

    client = TestClient(admin_app)
    client.post("/admin/login", data={"username": "testadmin", "password": "testpass"}, follow_redirects=False)

    response = client.post("/admin/broadcasts/new", data={"message_text": "Hello"}, follow_redirects=False)
    assert response.status_code in (302, 303, 401, 403)

@pytest.mark.asyncio
async def test_invalid_csrf(admin_app, monkeypatch, user_factory, role_factory):
    role = await role_factory(name="AdminBC3", permissions=Permission.BROADCAST)
    user = await user_factory(telegram_id=994, role_id=role)

    monkeypatch.setattr(EnvKeys, "DASHBOARD_ADMIN_TELEGRAM_ID", "994")
    monkeypatch.setattr(EnvKeys, "ADMIN_USERNAME", "testadmin")
    monkeypatch.setattr(EnvKeys, "ADMIN_PASSWORD", "testpass")

    client = TestClient(admin_app)
    client.post("/admin/login", data={"username": "testadmin", "password": "testpass"}, follow_redirects=False)
    client.get("/admin/broadcasts/new", follow_redirects=False) # Generates a token in session

    response = client.post("/admin/broadcasts/new", data={"message_text": "Hello", "csrf_token": "wrong"}, follow_redirects=False)
    assert response.status_code in (302, 303, 401, 403)

@pytest.mark.asyncio
async def test_valid_csrf_and_rotation(admin_app, monkeypatch, user_factory, role_factory):
    role = await role_factory(name="AdminBC4", permissions=Permission.BROADCAST)
    user = await user_factory(telegram_id=995, role_id=role)

    monkeypatch.setattr(EnvKeys, "DASHBOARD_ADMIN_TELEGRAM_ID", "995")
    monkeypatch.setattr(EnvKeys, "ADMIN_USERNAME", "testadmin")
    monkeypatch.setattr(EnvKeys, "ADMIN_PASSWORD", "testpass")

    client = TestClient(admin_app)
    client.post("/admin/login", data={"username": "testadmin", "password": "testpass"}, follow_redirects=False)
    res = client.get("/admin/broadcasts/new", follow_redirects=False)
    token = res.text.split('name="csrf_token" value="')[1].split('"')[0]

    # Valid CSRF
    res2 = client.post("/admin/broadcasts/new", data={"message_text": "Hello", "csrf_token": token, "target_locale": "en"}, follow_redirects=False)
    assert res2.status_code == 303

    # Replay/Rotation Check
    res3 = client.post("/admin/broadcasts/new", data={"message_text": "Hello", "csrf_token": token, "target_locale": "en"}, follow_redirects=False)
    assert res3.status_code == 403

@pytest.mark.asyncio
async def test_get_mutation_rejection(admin_app, monkeypatch, user_factory, role_factory):
    role = await role_factory(name="AdminBC5", permissions=Permission.BROADCAST)
    user = await user_factory(telegram_id=996, role_id=role)

    monkeypatch.setattr(EnvKeys, "DASHBOARD_ADMIN_TELEGRAM_ID", "996")
    monkeypatch.setattr(EnvKeys, "ADMIN_USERNAME", "testadmin")
    monkeypatch.setattr(EnvKeys, "ADMIN_PASSWORD", "testpass")

    client = TestClient(admin_app)
    client.post("/admin/login", data={"username": "testadmin", "password": "testpass"}, follow_redirects=False)
    # Confirm endpoint should reject GET
    res = client.get("/admin/broadcasts/confirm?id=1", follow_redirects=False)
    assert res.status_code == 405

@pytest.mark.asyncio
async def test_read_only_recipient_campaign_modelviews(admin_app, monkeypatch, user_factory, role_factory):
    role = await role_factory(name="AdminBC6", permissions=Permission.BROADCAST)
    user = await user_factory(telegram_id=997, role_id=role)

    monkeypatch.setattr(EnvKeys, "DASHBOARD_ADMIN_TELEGRAM_ID", "997")
    monkeypatch.setattr(EnvKeys, "ADMIN_USERNAME", "testadmin")
    monkeypatch.setattr(EnvKeys, "ADMIN_PASSWORD", "testpass")

    client = TestClient(admin_app)
    client.post("/admin/login", data={"username": "testadmin", "password": "testpass"}, follow_redirects=False)

    res = client.get("/admin/broadcastcampaign/create", follow_redirects=False)
    assert res.status_code == 404 # can_create = False

    res = client.get("/admin/broadcastrecipient/create", follow_redirects=False)
    assert res.status_code == 404 # can_create = False

@pytest.mark.asyncio
async def test_media_upload_validation(admin_app, monkeypatch, user_factory, role_factory):
    role = await role_factory(name="AdminBC7", permissions=Permission.BROADCAST)
    user = await user_factory(telegram_id=998, role_id=role)

    monkeypatch.setattr(EnvKeys, "DASHBOARD_ADMIN_TELEGRAM_ID", "998")
    monkeypatch.setattr(EnvKeys, "ADMIN_USERNAME", "testadmin")
    monkeypatch.setattr(EnvKeys, "ADMIN_PASSWORD", "testpass")
    monkeypatch.setattr(EnvKeys, "BROADCAST_STORAGE_CHAT_ID", "-100123")

    client = TestClient(admin_app)
    client.post("/admin/login", data={"username": "testadmin", "password": "testpass"}, follow_redirects=False)
    res = client.get("/admin/broadcasts/new", follow_redirects=False)
    token = res.text.split('name="csrf_token" value="')[1].split('"')[0]

    # Spoofed file (not an image)
    res2 = client.post(
        "/admin/broadcasts/new",
        data={"message_text": "Hello", "csrf_token": token, "target_locale": "en"},
        files={"photo_upload": ("test.jpg", b"fake_content", "image/jpeg")},
        follow_redirects=False
    )
    assert res2.status_code == 303
    # Follow redirect to get error
    res3 = client.get("/admin/broadcasts/new", follow_redirects=False)
    assert "Only JPEG, PNG, or WEBP are allowed" in res3.text

@pytest.mark.asyncio
async def test_storage_chat_upload_occurs_once(admin_app, monkeypatch, user_factory, role_factory):
    role = await role_factory(name="AdminBC8", permissions=Permission.BROADCAST)
    user = await user_factory(telegram_id=1001, role_id=role)

    monkeypatch.setattr(EnvKeys, "DASHBOARD_ADMIN_TELEGRAM_ID", "1001")
    monkeypatch.setattr(EnvKeys, "ADMIN_USERNAME", "testadmin")
    monkeypatch.setattr(EnvKeys, "ADMIN_PASSWORD", "testpass")
    monkeypatch.setattr(EnvKeys, "BROADCAST_STORAGE_CHAT_ID", "-100123")

    client = TestClient(admin_app)
    client.post("/admin/login", data={"username": "testadmin", "password": "testpass"}, follow_redirects=False)
    res = client.get("/admin/broadcasts/new", follow_redirects=False)
    token = res.text.split('name="csrf_token" value="')[1].split('"')[0]

    mock_msg = MagicMock()
    mock_msg.photo = [MagicMock(file_id="abc"), MagicMock(file_id="reusable_file_id_123")]

    with patch("bot.misc.services.broadcast_dispatcher.broadcast_dispatcher") as mock_dispatcher:
        mock_send_photo = AsyncMock()
        mock_dispatcher.bot.send_photo = mock_send_photo
        mock_send_photo.return_value = mock_msg
        mock_send_photo.return_value = mock_msg

        # Valid JPEG
        res2 = client.post(
            "/admin/broadcasts/new",
            data={"message_text": "Hello", "csrf_token": token, "target_locale": "en"},
            files={"photo_upload": ("test.jpg", b"\xff\xd8\xffvalidjpeg", "image/jpeg")},
            follow_redirects=False
        )
        assert res2.status_code == 303

        mock_send_photo.assert_called_once()

        # Verify it created campaign with photo_file_id
        async with Database().session() as session:
            result = await session.execute(select(BroadcastCampaign).order_by(BroadcastCampaign.id.desc()))
            campaign = result.scalars().first()
            assert campaign.photo_file_id == "reusable_file_id_123"

@pytest.mark.asyncio
async def test_dashboard_uses_shared_service(admin_app, monkeypatch, user_factory, role_factory):
    role = await role_factory(name="AdminBC9", permissions=Permission.BROADCAST)
    user = await user_factory(telegram_id=1002, role_id=role)

    monkeypatch.setattr(EnvKeys, "DASHBOARD_ADMIN_TELEGRAM_ID", "1002")
    monkeypatch.setattr(EnvKeys, "ADMIN_USERNAME", "testadmin")
    monkeypatch.setattr(EnvKeys, "ADMIN_PASSWORD", "testpass")

    client = TestClient(admin_app)
    client.post("/admin/login", data={"username": "testadmin", "password": "testpass"}, follow_redirects=False)
    res = client.get("/admin/broadcasts/new", follow_redirects=False)
    token = res.text.split('name="csrf_token" value="')[1].split('"')[0]

    with patch("bot.web.broadcast_admin.create_draft", new_callable=AsyncMock) as mock_create_draft:
        mock_create_draft.return_value = MagicMock(id=99)
        res2 = client.post(
            "/admin/broadcasts/new",
            data={"message_text": "Hello Service", "csrf_token": token, "target_locale": "en"},
            follow_redirects=False
        )
        mock_create_draft.assert_called_once_with(user["telegram_id"], "en", "Hello Service", None)
