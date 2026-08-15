import pytest
from bot.i18n.dynamic import get_localized_field, get_localized_jsonb, NormalizedDynamicItem
from bot.database.models.main import Categories, Goods, StoreSettings, MainMenuButtonSettings, Order, OrderItem, BoughtGoods
import contextvars
from bot.i18n.main import current_locale

@pytest.fixture
def isolated_locale():
    token = current_locale.set("en")
    yield current_locale
    current_locale.reset(token)

def test_dynamic_localization_orm_and_mapping(isolated_locale):
    """Test Mapping/dict and ORM resolution"""
    class DummyORM:
        name_en = "ORM EN"
        name_ar = "ORM AR"
        name = "ORM Base"
        description_en = "  " # whitespace only
        description_ar = None
        description = "ORM Desc Base"

    orm_obj = DummyORM()
    dict_obj = {
        "name_en": "Dict EN",
        "name_ar": "Dict AR",
        "name": "Dict Base",
        "description_en": "  ",
        "description_ar": None,
        "description": "Dict Desc Base"
    }

    isolated_locale.set("ar")
    assert get_localized_field(orm_obj, "name") == "ORM AR"
    assert get_localized_field(dict_obj, "name") == "Dict AR"

    # Fallback to base because _en is whitespace and _ar is None
    assert get_localized_field(orm_obj, "description") == "ORM Desc Base"
    assert get_localized_field(dict_obj, "description") == "Dict Desc Base"

def test_dynamic_localization_fallback_behavior(isolated_locale):
    """Test Arabic, English, other-locale, and complete fallback behavior"""
    data = {"title_ar": "AR Title", "title_en": "EN Title", "title": "Base Title"}

    isolated_locale.set("ar")
    assert get_localized_field(data, "title") == "AR Title"

    isolated_locale.set("en")
    assert get_localized_field(data, "title") == "EN Title"

    isolated_locale.set("ru")
    # ru is activated so it tries title_ru (missing) -> title_en
    assert get_localized_field(data, "title") == "EN Title"

    # Missing AR falls back to EN
    data2 = {"title_ar": "", "title_en": "EN Title", "title": "Base Title"}
    isolated_locale.set("ar")
    assert get_localized_field(data2, "title") == "EN Title"

    # Missing AR and EN falls back to Base
    data3 = {"title_ar": None, "title_en": "  ", "title": "Base Title"}
    assert get_localized_field(data3, "title") == "Base Title"

def test_dynamic_localization_whitespace_preservation(isolated_locale):
    """Test None, empty, whitespace-only, and whitespace preservation"""
    data = {"name_ar": "  AR Spaced  ", "name_en": "EN"}
    isolated_locale.set("ar")
    assert get_localized_field(data, "name") == "  AR Spaced  "

def test_dynamic_localization_jsonb(isolated_locale):
    """Test ProductCustomerField JSONB localization"""
    jsonb_data = {"en": "EN Label", "ar": "AR Label", "ru": "RU Label"}

    isolated_locale.set("ar")
    assert get_localized_jsonb(jsonb_data) == "AR Label"

    isolated_locale.set("en")
    assert get_localized_jsonb(jsonb_data) == "EN Label"

    isolated_locale.set("fr") # other locale
    assert get_localized_jsonb(jsonb_data) == "EN Label" # falls back to EN

    # Missing EN and requested locale returns empty string in 5c2
    jsonb_data2 = {"ru": "RU Label"}
    isolated_locale.set("fr")
    assert get_localized_jsonb(jsonb_data2) == ""

def test_legacy_tuple_compatibility(isolated_locale):
    """Test compatibility with legacy tuple query results"""
    # Simulate a raw legacy tuple (no translations)
    legacy = (1, "Legacy Category")

    # Since legacy tuple is not Mapping and has no name_ar attribute,
    # it safely returns None or "" via getattr fallback
    # The caller uses `or cat[1]` to fallback to the legacy positional element
    assert get_localized_field(legacy, "name") == ""
    assert (get_localized_field(legacy, "name") or legacy[1]) == "Legacy Category"

def test_normalized_dynamic_item_compatibility(isolated_locale):
    """Test NormalizedDynamicItem compatibility"""
    # Simulate an ORM object
    class DummyRow:
        id = 1
        name = "Base Cat"
        name_ar = "AR Cat"
        name_en = "EN Cat"

    row = DummyRow()

    # adapter wraps it
    item = NormalizedDynamicItem((row.id, row.name), row)

    # Acts as a tuple!
    assert item[0] == 1
    assert item[1] == "Base Cat"
    assert len(item) == 2
    id_val, name_val = item
    assert id_val == 1
    assert name_val == "Base Cat"

    # Supports get_localized_field because it delegates getattr
    isolated_locale.set("ar")
    assert get_localized_field(item, "name") == "AR Cat"

    isolated_locale.set("en")
    assert get_localized_field(item, "name") == "EN Cat"

@pytest.mark.asyncio
async def test_locale_isolation(isolated_locale):
    """Test locale isolation between requests"""
    async def process_request(locale, data, expected):
        token = current_locale.set(locale)
        assert get_localized_field(data, "name") == expected
        current_locale.reset(token)

    data = {"name_ar": "AR", "name_en": "EN", "name": "Base"}
    import asyncio
    await asyncio.gather(
        process_request("ar", data, "AR"),
        process_request("en", data, "EN"),
        # ru falls back to en
        process_request("ru", data, "EN")
    )

from bot.database.methods.transactions import buy_item_transaction, checkout_cart_transaction

@pytest.mark.asyncio
async def test_buy_item_transaction_localized_snapshot(isolated_locale, user_factory, item_factory):
    # Test that buy_item_transaction captures the localized name and description in OrderItem
    await user_factory(telegram_id=100010, balance=500)

    from bot.database.main import Database
    from sqlalchemy import select

    item = await item_factory(name="BaseName", price=100, values=[("val1", False)])

    async with Database().session() as s:
        db_item = (await s.execute(select(Goods).where(Goods.name == "BaseName"))).scalar_one()
        db_item.name_ar = "ARName"
        db_item.name_en = "ENName"
        db_item.description = "BaseDesc"
        db_item.description_ar = "ARDesc"
        db_item.description_en = "ENDesc"
        await s.commit()

    isolated_locale.set("ar")
    success, msg, data = await buy_item_transaction(100010, "BaseName")
    assert success is True

    async with Database().session() as s:
        # Check OrderItem since this is where the new localized snapshots are kept
        order_item = (await s.execute(select(OrderItem).join(Order).where(Order.user_id == 100010))).scalars().first()
        assert order_item.product_name_snapshot == "ARName"
        assert order_item.product_description_snapshot == "ARDesc"

@pytest.mark.asyncio
async def test_checkout_cart_transaction_localized_snapshot(isolated_locale, user_factory, item_factory):
    # Tests that checkout_cart_transaction captures the localized name in BoughtGoods
    await user_factory(telegram_id=100011, balance=500)

    from bot.database.main import Database
    from sqlalchemy import select
    from bot.database.methods.create import add_to_cart

    await item_factory(name="CartItem", price=100, values=[("val1", False)])

    async with Database().session() as s:
        db_item = (await s.execute(select(Goods).where(Goods.name == "CartItem"))).scalar_one()
        db_item.name_en = "ENCartName"
        db_item.description_en = "ENCartDesc"
        item_id = db_item.id
        await s.commit()

    # add_to_cart uses item_name
    await add_to_cart(100011, "CartItem")

    isolated_locale.set("en")
    success, msg, data = await checkout_cart_transaction(100011)
    assert success is True

    async with Database().session() as s:
        # checkout_cart_transaction stores the localized name in BoughtGoods.item_name
        bought = (await s.execute(select(BoughtGoods).where(BoughtGoods.buyer_id == 100011))).scalars().first()
        assert bought.item_name == "ENCartName"

def test_store_and_menu_settings_localization(isolated_locale):
    store_settings = StoreSettings(
        shop_root_title="Base Title", shop_root_title_en="EN Title", shop_root_title_ar="AR Title",
        shop_root_description="Base Desc", shop_root_description_en="EN Desc", shop_root_description_ar="AR Desc"
    )
    isolated_locale.set("ar")
    assert get_localized_field(store_settings, "shop_root_title") == "AR Title"
    assert get_localized_field(store_settings, "shop_root_description") == "AR Desc"

    menu_settings = MainMenuButtonSettings(
        action_key="test_key", label_en="EN Label", label_ar="AR Label"
    )
    isolated_locale.set("en")
    # Even though label doesn't exist, getting 'label' returns 'label_en' under English locale
    assert get_localized_field(menu_settings, "label") == "EN Label"
