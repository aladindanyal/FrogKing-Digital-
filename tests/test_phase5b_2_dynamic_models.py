import pytest
from sqlalchemy import inspect, select, String, Text, Integer, Numeric
from bot.database.models.main import StoreSettings, Categories, Goods, MainMenuButtonSettings, ProductCustomerField
from bot.database.methods.read import _obj_to_dict
from bot.web.admin import StoreSettingsAdmin, CategoryAdmin, GoodsAdmin, MainMenuButtonSettingsAdmin, ProductCustomerFieldAdmin
import asyncio
from alembic.config import Config
from alembic import command
from bot.database.main import Database

def get_alembic_config():
    alembic_cfg = Config("alembic.ini")
    return alembic_cfg

def test_orm_columns():
    # StoreSettings (10)
    ss_cols = inspect(StoreSettings).columns
    for col in ['shop_root_title_en', 'shop_root_title_ar', 'main_menu_title_en', 'main_menu_title_ar', 'main_menu_footer_en', 'main_menu_footer_ar']:
        assert col in ss_cols
        assert isinstance(ss_cols[col].type, String)
        assert ss_cols[col].type.length == 255
        assert ss_cols[col].nullable is True
        assert ss_cols[col].default is None
        assert ss_cols[col].server_default is None
        assert not ss_cols[col].unique
        assert not ss_cols[col].index

    for col in ['shop_root_description_en', 'shop_root_description_ar', 'main_menu_description_en', 'main_menu_description_ar']:
        assert col in ss_cols
        assert isinstance(ss_cols[col].type, Text)
        assert ss_cols[col].nullable is True
        assert ss_cols[col].default is None
        assert ss_cols[col].server_default is None
        assert not ss_cols[col].unique
        assert not ss_cols[col].index

    # Categories (4)
    cat_cols = inspect(Categories).columns
    for col in ['name_en', 'name_ar']:
        assert col in cat_cols
        assert isinstance(cat_cols[col].type, String)
        assert cat_cols[col].type.length == 100
        assert cat_cols[col].nullable is True
        assert cat_cols[col].default is None
        assert cat_cols[col].server_default is None
        assert not cat_cols[col].unique
        assert not cat_cols[col].index

    for col in ['description_en', 'description_ar']:
        assert col in cat_cols
        assert isinstance(cat_cols[col].type, Text)
        assert cat_cols[col].nullable is True
        assert cat_cols[col].default is None
        assert cat_cols[col].server_default is None
        assert not cat_cols[col].unique
        assert not cat_cols[col].index

    # Goods (4)
    g_cols = inspect(Goods).columns
    for col in ['name_en', 'name_ar']:
        assert col in g_cols
        assert isinstance(g_cols[col].type, String)
        assert g_cols[col].type.length == 100
        assert g_cols[col].nullable is True
        assert g_cols[col].default is None
        assert g_cols[col].server_default is None
        assert not g_cols[col].unique
        assert not g_cols[col].index

    for col in ['description_en', 'description_ar']:
        assert col in g_cols
        assert isinstance(g_cols[col].type, Text)
        assert g_cols[col].nullable is True
        assert g_cols[col].default is None
        assert g_cols[col].server_default is None
        assert not g_cols[col].unique
        assert not g_cols[col].index

def test_obj_to_dict_exposes_columns():
    g = Goods(name="test", price=10, description="desc")
    d = _obj_to_dict(g, Goods)
    assert 'name_en' in d
    assert d['name_en'] is None
    assert 'name_ar' in d
    assert d['name_ar'] is None

def test_sqladmin_config():
    # StoreSettingsAdmin
    assert not any(col.name.endswith('_en') or col.name.endswith('_ar') for col in StoreSettingsAdmin.column_list)
    assert len(StoreSettingsAdmin.column_details_list) == 22 + 25  # Phase 5C-2 adds 25 localized columns (5 fields * 5 new locales)
    assert StoreSettingsAdmin.column_labels[StoreSettings.shop_root_title_en] == "Shop Root Title (English)"

    # CategoryAdmin
    assert not any(col.name.endswith('_en') or col.name.endswith('_ar') for col in CategoryAdmin.column_list)
    assert len(CategoryAdmin.column_details_list) == 11 + 10  # Phase 5C-2 adds 10 localized columns (2 fields * 5 new locales)

    # GoodsAdmin
    assert not any(col.name.endswith('_en') or col.name.endswith('_ar') for col in GoodsAdmin.column_list)
    assert len(GoodsAdmin.column_details_list) == 17 + 10  # Phase 5C-2 adds 10 localized columns (2 fields * 5 new locales)

def test_unmodified_admins():
    assert 'label_en' in [col.name for col in MainMenuButtonSettingsAdmin.form_columns]
    assert 'label_ar' in [col.name for col in MainMenuButtonSettingsAdmin.form_columns]

@pytest.mark.asyncio
async def test_migration_cycle():
    import subprocess
    import os
    import asyncpg

    # We must run migration against an isolated postgres db.

    env = os.environ.copy()
    test_db = "alembic_cycle_test_db"
    env["POSTGRES_DB"] = test_db
    env["POSTGRES_USER"] = env.get("POSTGRES_USER", "postgres")
    env["POSTGRES_PASSWORD"] = env.get("POSTGRES_PASSWORD", "postgres")
    env["POSTGRES_HOST"] = "db"

    # Create isolated DB using asyncpg
    conn = await asyncpg.connect(user=env["POSTGRES_USER"], password=env["POSTGRES_PASSWORD"], host=env["POSTGRES_HOST"], database="postgres")
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {test_db};")
        await conn.execute(f"CREATE DATABASE {test_db};")
    finally:
        await conn.close()

    try:
        # 1. Upgrade to head
        res = subprocess.run(["alembic", "upgrade", "head"], capture_output=True, text=True, env=env)
        assert res.returncode == 0, res.stderr

        # 2. Check heads
        res = subprocess.run(["alembic", "heads"], capture_output=True, text=True, env=env)
        assert res.returncode == 0
        assert "3f820c7a5211" in res.stdout  # Phase 5C-2 advances the head to 3f820c7a5211

        # 3. Downgrade to 23ba1f978d38
        res = subprocess.run(["alembic", "downgrade", "23ba1f978d38"], capture_output=True, text=True, env=env)
        assert res.returncode == 0, res.stderr

        # 4. Re-upgrade
        res = subprocess.run(["alembic", "upgrade", "head"], capture_output=True, text=True, env=env)
        assert res.returncode == 0, res.stderr
    finally:
        conn = await asyncpg.connect(user=env["POSTGRES_USER"], password=env["POSTGRES_PASSWORD"], host=env["POSTGRES_HOST"], database="postgres")
        try:
            await conn.execute(f"DROP DATABASE IF EXISTS {test_db};")
        finally:
            await conn.close()
