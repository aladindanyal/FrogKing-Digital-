import pytest
import pytest_asyncio
import asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from alembic.config import Config
from alembic import command
import uuid
import os

from bot.database.dsn import dsn

def get_base_url():
    url = dsn()
    # url is like postgresql+asyncpg://user:pass@host:port/dbname
    # we need to replace dbname with 'postgres' for the administrative connection
    base = url.rsplit('/', 1)[0]
    return f"{base}/postgres"

@pytest_asyncio.fixture
async def temp_db():
    base_url = get_base_url()
    engine = create_async_engine(base_url, isolation_level="AUTOCOMMIT")
    db_name = f"test_migration_{uuid.uuid4().hex[:8]}"
    
    async with engine.connect() as conn:
        # Close open connections to telegram_shop so it can be used as a template
        await conn.execute(sa.text("""
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pg_stat_activity.datname = 'telegram_shop'
              AND pid <> pg_backend_pid();
        """))
        await conn.execute(sa.text(f'CREATE DATABASE {db_name} TEMPLATE telegram_shop'))
    await engine.dispose()
    
    url = dsn()
    base = url.rsplit('/', 1)[0]
    test_db_url = f"{base}/{db_name}"
    yield test_db_url
    
    # Re-connect to postgres to drop the temp db
    engine = create_async_engine(base_url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(sa.text(f'DROP DATABASE {db_name} WITH (FORCE)'))
    await engine.dispose()

from unittest.mock import patch

def run_alembic_upgrade(db_url, revision):
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    with patch('bot.database.dsn.dsn', return_value=db_url):
        command.upgrade(alembic_cfg, revision)

def run_alembic_downgrade(db_url, revision):
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    with patch('bot.database.dsn.dsn', return_value=db_url):
        command.downgrade(alembic_cfg, revision)

@pytest.mark.asyncio
async def test_migration_repair_idempotency(temp_db):
    """Test full schema already exists (it is idempotent) and partially repaired schema."""

    # 1. Downgrade to the base state (1eadcdac923e) because the template db is likely at head
    await asyncio.to_thread(run_alembic_downgrade, temp_db, "1eadcdac923e")

    # Connect to the temp_db and manually modify the schema to simulate a partial application
    engine = create_async_engine(temp_db, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        # We manually add 'product_id' column to 'reviews' which is one part of the migration cbb1113b4714
        # We don't add the constraint or change order_id nullability.
        await conn.execute(sa.text("""
            ALTER TABLE reviews 
            ADD COLUMN IF NOT EXISTS product_id BIGINT;
        """))
    await engine.dispose()

    # Insert some dummy old drifted schema data
    engine = create_async_engine(temp_db)
    async with engine.begin() as conn:
        # 1eadcdac923e creates the 'reviews' table.
        # We manually insert a legacy row
        await conn.execute(sa.text("INSERT INTO users (telegram_id) VALUES (123) ON CONFLICT DO NOTHING"))
        await conn.execute(sa.text("INSERT INTO categories (id, name) VALUES (9999, 'Cat') ON CONFLICT DO NOTHING"))
        await conn.execute(sa.text("INSERT INTO goods (id, name, price, description, category_id) VALUES (9999, 'Product 1', 10, 'Desc', 9999) ON CONFLICT DO NOTHING"))
        await conn.execute(sa.text("INSERT INTO reviews (user_id, item_name, rating, comment) VALUES (123, 'Product 1', 5, 'Great product')"))
    await engine.dispose()

    # 2. Run the migration we want to repair (cbb1113b4714)
    # The migration script cbb1113b4714 should contain IF NOT EXISTS / IF EXISTS logic
    # so it does not fail when part of its DDL has already been applied.
    await asyncio.to_thread(run_alembic_upgrade, temp_db, "cbb1113b4714")

    # 3. Verify the final schema is correct
    engine = create_async_engine(temp_db)
    
    # We must use run_sync to inspect
    def inspect_schema(conn):
        inspector = sa.inspect(conn)
        columns = [c["name"] for c in inspector.get_columns("reviews")]
        return columns
    
    async with engine.connect() as conn:
        columns = await conn.run_sync(inspect_schema)
        
        # Verify legacy row is preserved
        res = (await conn.execute(sa.text("SELECT * FROM reviews WHERE comment = 'Great product'"))).mappings().fetchall()
        assert len(res) >= 1
        assert res[0]['status'] == 'pending' # Default
        
    await engine.dispose()

    assert "product_id" in columns
    assert "order_item_id" in columns
    
    # 4. Try downgrading to verify idempotency in downgrade
    await asyncio.to_thread(run_alembic_downgrade, temp_db, "1eadcdac923e")
    
    # Run upgrade again on the partially/fully repaired schema
    await asyncio.to_thread(run_alembic_upgrade, temp_db, "cbb1113b4714")
    
    engine = create_async_engine(temp_db)
    async with engine.connect() as conn:
        res = (await conn.execute(sa.text("SELECT * FROM reviews WHERE comment = 'Great product'"))).mappings().fetchall()
        assert len(res) >= 1
        assert res[0]['status'] == 'pending'
    await engine.dispose()
