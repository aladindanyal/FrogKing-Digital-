import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

from aiogram.types import User as AiogramUser

from bot.database.models.main import User
from bot.database.main import Database
from bot.database.methods.profile import sync_telegram_user_profile, normalize_profile
from bot.database.methods.create import create_user
from bot.middleware.security import AuthenticationMiddleware

@pytest.mark.asyncio
async def test_username_normalization():
    # Test normalization rules
    u, f, l = normalize_profile(username="@aladin_oqab", first_name="Aladin", last_name=None)
    assert u == "aladin_oqab"
    assert f == "Aladin"
    assert l is None

    u, f, l = normalize_profile(username="  aladin_oqab  ", first_name="  Aladin  ", last_name="  Oqab  ")
    assert u == "aladin_oqab"
    assert f == "Aladin"
    assert l == "Oqab"

    u, f, l = normalize_profile(username="", first_name="  ", last_name=None)
    assert u is None
    assert f is None

@pytest.mark.asyncio
async def test_arabic_unicode_names():
    # Arabic/Unicode
    u, f, l = normalize_profile(username="aladin", first_name="علاء الدين", last_name="عقاب")
    assert f == "علاء الدين"
    assert l == "عقاب"

@pytest.mark.asyncio
async def test_first_interaction_update():
    await create_user(telegram_id=999, registration_date=datetime.now(timezone.utc), referral_id=None)
    
    await sync_telegram_user_profile(telegram_id=999, username="testuser", first_name="Test", last_name=None)
    
    async with Database().session() as session:
        db_user = await session.get(User, 999)
        assert db_user.telegram_username == "testuser"
        assert db_user.first_name == "Test"
        assert db_user.profile_updated_at is not None
        
        # Save old update time
        old_time = db_user.profile_updated_at
        
    # Unchanged profile no-op
    await sync_telegram_user_profile(telegram_id=999, username="testuser", first_name="Test", last_name=None)
    async with Database().session() as session:
        db_user = await session.get(User, 999)
        assert db_user.profile_updated_at == old_time  # Unchanged!

    # Changed username
    await sync_telegram_user_profile(telegram_id=999, username="newuser", first_name="Test", last_name=None)
    async with Database().session() as session:
        db_user = await session.get(User, 999)
        assert db_user.telegram_username == "newuser"
        assert db_user.profile_updated_at > old_time
        
        old_time = db_user.profile_updated_at

    # Removed username
    await sync_telegram_user_profile(telegram_id=999, username=None, first_name="Test", last_name=None)
    async with Database().session() as session:
        db_user = await session.get(User, 999)
        assert db_user.telegram_username is None
        assert db_user.profile_updated_at > old_time
        
        old_time = db_user.profile_updated_at

    # Changed name
    await sync_telegram_user_profile(telegram_id=999, username=None, first_name="Test2", last_name=None)
    async with Database().session() as session:
        db_user = await session.get(User, 999)
        assert db_user.first_name == "Test2"
        assert db_user.profile_updated_at > old_time

@pytest.mark.asyncio
async def test_middleware_message_coverage(make_message):
    msg = make_message(user_id=123, first_name="MiddlewareUser")
    msg.from_user.username = "mwuser"
    msg.from_user.last_name = None
    
    middleware = AuthenticationMiddleware()
    handler = AsyncMock(return_value="OK")
    
    await create_user(telegram_id=123, registration_date=datetime.now(timezone.utc), referral_id=None)
    
    with patch("bot.database.methods.cache_utils.safe_create_task") as mock_task:
        await middleware(handler, msg, {})
        assert mock_task.called, "safe_create_task was not called!"
        if mock_task.call_args:
            await mock_task.call_args[0][0]
    
    async with Database().session() as session:
        db_user = await session.get(User, 123)
        assert db_user.telegram_username == "mwuser"

@pytest.mark.asyncio
async def test_middleware_callback_coverage(make_callback_query):
    call = make_callback_query(user_id=124, first_name="CBUser")
    call.from_user.username = "cbuser"
    call.from_user.last_name = None
    
    middleware = AuthenticationMiddleware()
    handler = AsyncMock(return_value="OK")
    
    await create_user(telegram_id=124, registration_date=datetime.now(timezone.utc), referral_id=None)
    
    with patch("bot.database.methods.cache_utils.safe_create_task") as mock_task:
        await middleware(handler, call, {})
        assert mock_task.called, "safe_create_task was not called!"
        if mock_task.call_args:
            await mock_task.call_args[0][0]
            
    async with Database().session() as session:
        db_user = await session.get(User, 124)
        assert db_user.telegram_username == "cbuser"

@pytest.mark.asyncio
async def test_create_user_profile_sync():
    # Verify create_user correctly stores profile and prevents duplicate logic needed
    now = datetime.now(timezone.utc)
    await create_user(
        telegram_id=125,
        registration_date=now,
        referral_id=None,
        telegram_username="startuser",
        first_name="Start",
        last_name="User"
    )
    
    async with Database().session() as session:
        db_user = await session.get(User, 125)
        assert db_user.telegram_username == "startuser"
        assert db_user.profile_updated_at is not None

@pytest.mark.asyncio
async def test_sync_failure_does_not_block():
    # Force failure in session
    with patch("bot.database.main.Database.session", side_effect=Exception("DB Error")):
        # Should catch exception and not raise
        await sync_telegram_user_profile(telegram_id=9999, username=None, first_name="WillFail", last_name=None)

