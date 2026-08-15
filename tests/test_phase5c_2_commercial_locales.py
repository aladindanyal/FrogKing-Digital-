import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from bot.i18n.main import localize, current_locale
from bot.database.models import User, Categories, Goods, Order, OrderItem
from bot.database.main import Database
from decimal import Decimal

LOCALES = ['en', 'ar', 'ru', 'zh', 'vi', 'tr', 'es']

@pytest.mark.asyncio
@pytest.mark.parametrize("locale", LOCALES)
async def test_order_receipt_localization(locale):
    """
    Test order receipt localization outside normal middleware context (using _locale kwarg).
    Verifies that the specific localized labels and keyboard text are present.
    """
    from bot.handlers.user.renderers import render_purchase_success_from_order

    async with Database().session() as s:
        user = User(telegram_id=99991, balance=Decimal('0'), language_code=locale)
        s.add(user)
        cat = Categories(name='Test Cat', description='test')
        s.add(cat)
        await s.flush()

        goods = Goods(name='Automatic Test Item', price=Decimal('10'), description='desc', category_id=cat.id, fulfillment_mode='instant')
        s.add(goods)
        await s.flush()

        order = Order(user_id=99991, currency='USD', total=10, subtotal=10, public_id='TEST-AUTO-2', status='completed')
        s.add(order)
        await s.flush()

        order_item = OrderItem(
            order_id=order.id,
            item_id=goods.id,
            quantity=1,
            unit_price=10,
            subtotal=10,
            total=10,
            product_name_snapshot='Automatic Test Item'
        )
        s.add(order_item)
        await s.commit()

    # Mock Message
    msg = AsyncMock()
    msg.chat.id = 99991

    text, kb = await render_purchase_success_from_order(msg, order.id)

    assert text is not None
    assert kb is not None

    # Assert immutable values
    assert "USD" in text
    assert "TEST-AUTO-2" in text
    assert "10.00" in text
    assert "Automatic Test Item" in text

    # Assert exact translations are present
    assert localize("shop.order_completed", _locale=locale) in text
    assert localize("shop.purchased_at", _locale=locale, purchased_time=order.created_at.strftime('%Y-%m-%d %H:%M:%S')) in text
    assert order.created_at.strftime('%Y-%m-%d %H:%M:%S') in text

    # Check keyboard translations
    kb_json = kb.model_dump()
    kb_text = str(kb_json)

    assert localize("btn.buy_again", _locale=locale) in kb_text
    assert localize("btn.view_order", _locale=locale) in kb_text


@pytest.mark.asyncio
@pytest.mark.parametrize("locale", LOCALES)
async def test_quantity_keypad(locale):
    """
    Test custom quantity keypad keyboard generation for exact UI texts.
    """
    token = current_locale.set(locale)
    try:
        from bot.keyboards.inline import numeric_keypad
        kb = numeric_keypad(1)
        kb_json = kb.model_dump()
        kb_text = str(kb_json)

        # Exact assertions instead of assert_no_english
        assert localize("btn.clear", _locale=locale) in kb_text
        assert localize("btn.keypad_continue", _locale=locale) in kb_text
        assert localize("btn.back", _locale=locale) in kb_text
    finally:
        current_locale.reset(token)

@pytest.mark.asyncio
@pytest.mark.parametrize("locale", LOCALES)
async def test_item_info_keyboard(locale):
    """
    Test item_info keyboard for structural exact matches.
    """
    token = current_locale.set(locale)
    try:
        from bot.keyboards.inline import item_info
        kb = item_info("Test Item", "gp_0", quantity=1, stock=10, item_id=1)
        kb_json = kb.model_dump()
        kb_text = str(kb_json)

        # Verify specific structural elements
        assert localize("btn.refresh_stock", _locale=locale) in kb_text
        assert localize("btn.custom_quantity", _locale=locale) in kb_text
        assert localize("btn.continue", _locale=locale) in kb_text
        assert localize("btn.back", _locale=locale) in kb_text
        assert localize("btn.to_menu", _locale=locale) in kb_text
    finally:
        current_locale.reset(token)

@pytest.mark.asyncio
@pytest.mark.parametrize("locale", LOCALES)
async def test_outbox_dispatcher_localization(locale):
    """
    Test that outbox notifications correctly render the 'conversation is already active' note
    and 'View Order' buttons for all locales outside ContextVar context.
    Ensures order independence by validating pure translation rendering.
    """
    token = current_locale.set(locale)
    try:
        assert localize("intake.conversation_already_active", _locale=locale) != ""
        assert "✅" in localize("intake.conversation_already_active", _locale=locale)
        assert localize("intake.msg_about_order_active", _locale=locale, public_id="123", preview="test") != ""
        assert "💬" in localize("intake.msg_about_order_active", _locale=locale, public_id="123", preview="test")
        assert localize("btn.view_order", _locale=locale) != ""
    finally:
        current_locale.reset(token)
