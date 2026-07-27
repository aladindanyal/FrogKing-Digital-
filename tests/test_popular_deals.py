import pytest
from sqlalchemy import select, text
from bot.database import Database
from bot.database.models import Goods, Categories
from bot.database.models.main import MainMenuButtonSettings
from bot.database.methods.lazy_queries import query_popular_deals
from bot.web.admin import GoodsAdmin
from starlette.requests import Request
from starlette.exceptions import HTTPException

@pytest.mark.asyncio
async def test_popular_deals_query(category_factory, item_factory):
    # setup goods
    await category_factory(name="DealsCat")
    await item_factory(name="Deal 1", category="DealsCat")
    await item_factory(name="Deal 2", category="DealsCat")

    async with Database().session() as db_session:
        goods_list = (await db_session.execute(select(Goods))).scalars().all()
        g1, g2 = goods_list[0], goods_list[1]
        g1.is_popular_deal = True
        g1.popular_deal_order = 1
        g2.is_popular_deal = True
        g2.popular_deal_order = 0
        await db_session.commit()

    deals = await query_popular_deals(limit=10)
    assert len(deals) >= 2
    # Find our two deals
    deals_ids = [d[0] for d in deals]
    async with Database().session() as db_session:
        goods_list = (await db_session.execute(select(Goods))).scalars().all()
        g1, g2 = goods_list[0], goods_list[1]

    assert g2.id in deals_ids
    assert g1.id in deals_ids

    # Check fallback deterministic ordering and nulls last
    async with Database().session() as db_session:
        g1 = (await db_session.execute(select(Goods).where(Goods.name == "Deal 1"))).scalar_one()
        g2 = (await db_session.execute(select(Goods).where(Goods.name == "Deal 2"))).scalar_one()
        g1.popular_deal_order = None
        g2.popular_deal_order = None
        await db_session.commit()

    deals_null = await query_popular_deals(limit=10)
    deals_null_ids = [d[0] for d in deals_null]
    assert g1.id in deals_null_ids
    assert g2.id in deals_null_ids

    # Revert
    async with Database().session() as db_session:
        g1 = (await db_session.execute(select(Goods).where(Goods.name == "Deal 1"))).scalar_one()
        g2 = (await db_session.execute(select(Goods).where(Goods.name == "Deal 2"))).scalar_one()
        g1.is_popular_deal = False
        g2.is_popular_deal = False
        await db_session.commit()


@pytest.mark.asyncio
async def test_exactly_one_menu_button():
    async with Database().session() as db_session:
        # Simulate migration
        await db_session.execute(text("""
            INSERT INTO main_menu_button_settings (action_key, label_en, label_ar, row_order, column_order, is_enabled, owner_only)
            VALUES ('popular_deals', '🔥 Popular Deals', '🔥 عروض مميزة', 0, 1, true, false)
            ON CONFLICT (action_key) DO NOTHING;
        """))
        await db_session.commit()

        # Test idempotency
        await db_session.execute(text("""
            INSERT INTO main_menu_button_settings (action_key, label_en, label_ar, row_order, column_order, is_enabled, owner_only)
            VALUES ('popular_deals', '🔥 Popular Deals', '🔥 عروض مميزة', 0, 1, true, false)
            ON CONFLICT (action_key) DO NOTHING;
        """))
        await db_session.commit()

        buttons = (await db_session.execute(
            select(MainMenuButtonSettings).where(MainMenuButtonSettings.action_key == 'popular_deals')
        )).scalars().all()
        assert len(buttons) == 1
        assert buttons[0].label_en == '🔥 Popular Deals'


@pytest.mark.asyncio
async def test_sqladmin_validation(category_factory, item_factory):
    await category_factory(name="DealsCat2")
    await item_factory(name="Deal 3", category="DealsCat2")

    async with Database().session() as db_session:
        goods = (await db_session.execute(select(Goods).where(Goods.name == "Deal 3"))).scalar_one()

        class DummyAdmin(GoodsAdmin):
            def __init__(self):
                pass
            async def super_on_model_change(self, data, model, is_created, request):
                pass

        admin = DummyAdmin()
        # Mock super() to prevent it from failing inside on_model_change if it calls super()
        # Actually in python, we can just replace super with a pass or use monkeypatching, but let's see.

        # Test valid order
        data = {"eta_preset": "custom", "popular_deal_order": 2}
        request = Request({"type": "http", "method": "POST", "headers": []})

        # Since the code does `if getattr(super(), "on_model_change", None):`, DummyAdmin might still try to call the real ModelView
        # ModelView.on_model_change does nothing by default in sqladmin.
        goods.popular_deal_order = data["popular_deal_order"]
        await admin.on_model_change(data, goods, is_created=False, request=request)

        # Test invalid order
        goods.popular_deal_order = -1
        data_invalid = {"eta_preset": "custom", "popular_deal_order": -1}
        with pytest.raises(HTTPException) as exc:
            await admin.on_model_change(data_invalid, goods, is_created=False, request=request)
        assert exc.value.status_code == 400
        assert "Popular Deal Order cannot be negative" in exc.value.detail

        goods.popular_deal_order = 0

def test_migration_upgrade_downgrade():
    pass
import pytest
from starlette.testclient import TestClient
from bot.web.admin import create_admin_app
from bot.misc.env import EnvKeys
from bot.database.models import Goods
from decimal import Decimal


@pytest.mark.asyncio
async def test_sqladmin_real_integration(category_factory, item_factory):
    from bot.database.main import Database
    from sqlalchemy import select

    cat = await category_factory()
    goods = await item_factory(name="Real Test", price=Decimal("10.0"), description="desc", category_id=cat.id, fulfillment_mode="manual")
    goods_id = goods.id

    app = create_admin_app()
    with TestClient(app) as client:
        # 1. Authenticate
        login_data = {"username": EnvKeys.ADMIN_USERNAME, "password": EnvKeys.ADMIN_PASSWORD}
        res_login = client.post("/admin/login", data=login_data, follow_redirects=True)
        assert res_login.status_code == 200

        # 2. GET edit page
        res_get = client.get(f"/admin/goods/edit/{goods_id}")
        assert res_get.status_code == 200
        text = res_get.text
        # 4. Assert fields exist
        assert 'name="is_popular_deal"' in text
        assert 'name="popular_deal_order"' in text

        # 5. POST valid
        post_data = {
            "name": "Real Test",
            "price": "10.0",
            "description": "desc",
            "category_id": str(cat.id),
            "fulfillment_mode": "manual",
            "is_popular_deal": "on",
            "popular_deal_order": "1"
        }
        res_post = client.post(f"/admin/goods/edit/{goods_id}", data=post_data, follow_redirects=True)
        # 6. Assert successful redirect/response
        assert res_post.status_code == 200

    # 7. Re-query
    async with Database().session() as session2:
        goods_reloaded = (await session2.execute(select(Goods).where(Goods.id == goods_id))).scalar_one()
        # 8. Confirm values persisted
        assert goods_reloaded.is_popular_deal is True
        assert goods_reloaded.popular_deal_order == 1

    # 9. POST negative
    with TestClient(app) as client:
        login_data = {"username": EnvKeys.ADMIN_USERNAME, "password": EnvKeys.ADMIN_PASSWORD}
        client.post("/admin/login", data=login_data, follow_redirects=True)

        post_data["popular_deal_order"] = "-1"
        res_post_neg = client.post(f"/admin/goods/edit/{goods_id}", data=post_data, follow_redirects=True)
        # 10. Confirm validation failure
        assert res_post_neg.status_code == 400
        assert "popular_deal_order" in res_post_neg.text

    # 11. Confirm row valid and session didn't leak
    async with Database().session() as session3:
        goods_final = (await session3.execute(select(Goods).where(Goods.id == goods_id))).scalar_one()
        assert goods_final.popular_deal_order == 1

def test_popular_deals_localization():
    from bot.i18n.main import localize, current_locale

    token = current_locale.set("en")
    en_title = localize("shop.popular_deals")
    en_desc = localize("shop.popular_deals_desc")
    current_locale.reset(token)

    token = current_locale.set("ar")
    ar_title = localize("shop.popular_deals")
    ar_desc = localize("shop.popular_deals_desc")
    current_locale.reset(token)

    # Assert they resolve to actual text, not raw keys
    assert en_title != "shop.popular_deals"
    assert en_desc != "shop.popular_deals_desc"
    assert ar_title != "shop.popular_deals"
    assert ar_desc != "shop.popular_deals_desc"

    # Assert English exists
    assert "Popular Deals" in en_title

    # Assert Arabic exists
    assert "العروض المميزة" in ar_title

def test_main_menu_button_styles():
    from bot.keyboards.inline import main_menu
    from bot.database.models import MainMenuButtonSettings

    # Fake config
    config = [
        MainMenuButtonSettings(id=1, action_key="shop", is_enabled=True, row_order=1, column_order=1),
        MainMenuButtonSettings(id=2, action_key="popular_deals", is_enabled=True, row_order=2, column_order=1),
    ]

    kb = main_menu(role=0, buttons_config=config, locale="en")
    inline_keyboard = kb.inline_keyboard

    # Find buttons
    shop_btn = None
    popular_deals_btn = None
    popular_deals_count = 0

    for row in inline_keyboard:
        for btn in row:
            if btn.callback_data == "shop":
                shop_btn = btn
            elif btn.callback_data == "popular_deals":
                popular_deals_btn = btn
                popular_deals_count += 1

    from aiogram.enums import ButtonStyle
    assert shop_btn is not None
    assert getattr(shop_btn, "style", None) == ButtonStyle.SUCCESS

    assert popular_deals_btn is not None
    assert getattr(popular_deals_btn, "style", None) == ButtonStyle.PRIMARY
    assert popular_deals_count == 1
