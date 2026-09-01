"""PostgreSQL-only acceptance test for the Phase 6B migration."""

import hashlib
import json
import os
import re
import subprocess
import uuid

import asyncpg
import pytest


BASE_REVISION = "4a2b3c4d5e6f"
REJECTED_PROTOTYPE_REVISION = "24d778704bb6"
PHASE6B_REVISION = "c7d8e9f0a1b2"


def _database_parts():
    url = os.environ.get("DATABASE_URL")
    assert url, "DATABASE_URL must point to an isolated PostgreSQL test database"
    base, database = url.rsplit("/", 1)
    database = database.split("?", 1)[0]
    assert re.fullmatch(r"[A-Za-z0-9_]+", database)
    assert "test" in database.lower(), "Phase 6B migration tests refuse a non-test database"
    admin_url = base.replace("postgresql+asyncpg", "postgresql") + "/postgres"
    return url, admin_url


def _run_alembic(database_url: str, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    result = subprocess.run(
        ["python", "-m", "alembic", *args],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def _fingerprint(rows) -> str:
    payload = "\n".join(f"{row['telegram_id']}:{row['balance']}" for row in rows)
    return hashlib.sha256(payload.encode()).hexdigest()


@pytest.mark.asyncio
async def test_phase6b_upgrade_backfill_and_roundtrip():
    source_url, admin_url = _database_parts()
    base_url = source_url.rsplit("/", 1)[0]
    database = f"phase6b_test_{uuid.uuid4().hex[:10]}"
    test_url = f"{base_url}/{database}"

    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
    finally:
        await admin.close()

    try:
        _run_alembic(test_url, "upgrade", BASE_REVISION)
        connection = await asyncpg.connect(test_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            await connection.execute(
                "INSERT INTO roles (id, name, permissions, \"default\") "
                "VALUES (1, 'USER', 1, true) ON CONFLICT (id) DO NOTHING"
            )
            await connection.execute(
                "INSERT INTO users (telegram_id, role_id, balance) "
                "VALUES (910001, 1, 12.34), (910002, 1, 56.78)"
            )
            await connection.execute(
                "INSERT INTO referral_earnings "
                "(referrer_id, referral_id, amount, original_amount) "
                "VALUES (910001, 910002, 2.50, 50.00)"
            )
            await connection.execute(
                "INSERT INTO store_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING"
            )
            before = _fingerprint(await connection.fetch(
                "SELECT telegram_id, balance FROM users ORDER BY telegram_id"
            ))
        finally:
            await connection.close()

        _run_alembic(test_url, "upgrade", "head")
        current = _run_alembic(test_url, "current").stdout
        assert PHASE6B_REVISION in current
        # The approved baseline contains unrelated legacy ORM/schema drift.
        # Phase 6B parity is verified explicitly against its own objects below.

        connection = await asyncpg.connect(test_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            after = _fingerprint(await connection.fetch(
                "SELECT telegram_id, balance FROM users ORDER BY telegram_id"
            ))
            assert after == before
            legacy = await connection.fetchrow(
                "SELECT status, earning_type, ready_at FROM referral_earnings"
            )
            assert dict(legacy) == {
                "status": "settled",
                "earning_type": "legacy_topup",
                "ready_at": None,
            }
            assert await connection.fetchval(
                "SELECT referral_debt FROM users WHERE telegram_id = 910001"
            ) == 0
            assert await connection.fetchval(
                "SELECT referral_percent FROM store_settings WHERE id = 1"
            ) == 5
            assert await connection.fetchval(
                "SELECT to_regclass('public.referral_conversions') IS NOT NULL"
            ) is True

            with pytest.raises(asyncpg.CheckViolationError):
                await connection.execute(
                    "INSERT INTO referral_earnings "
                    "(referrer_id, amount, original_amount, status, earning_type, "
                    "admin_identity, reason, ready_at) "
                    "VALUES (910001, 0, 0, 'available', 'manual_adjustment', "
                    "'test', 'zero rejected', now())"
                )
        finally:
            await connection.close()

        _run_alembic(test_url, "downgrade", BASE_REVISION)
        _run_alembic(test_url, "upgrade", "head")
        connection = await asyncpg.connect(test_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            assert _fingerprint(await connection.fetch(
                "SELECT telegram_id, balance FROM users ORDER BY telegram_id"
            )) == before
            row = await connection.fetchrow(
                "SELECT status, earning_type FROM referral_earnings"
            )
            assert tuple(row.values()) == ("settled", "legacy_topup")
        finally:
            await connection.close()
    finally:
        admin = await asyncpg.connect(admin_url)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        finally:
            await admin.close()


@pytest.mark.asyncio
async def test_rejected_prototype_converges_without_data_changes():
    """The orphaned live revision is normalized without stamping or rewrites."""

    source_url, admin_url = _database_parts()
    base_url = source_url.rsplit("/", 1)[0]
    database = f"phase6b_convergence_test_{uuid.uuid4().hex[:10]}"
    test_url = f"{base_url}/{database}"
    asyncpg_url = test_url.replace("postgresql+asyncpg", "postgresql")

    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
    finally:
        await admin.close()

    try:
        _run_alembic(test_url, "upgrade", REJECTED_PROTOTYPE_REVISION)
        connection = await asyncpg.connect(asyncpg_url)
        try:
            await connection.execute(
                "INSERT INTO roles (id, name, permissions, \"default\") "
                "VALUES (1, 'USER', 1, true) ON CONFLICT (id) DO NOTHING"
            )
            await connection.execute(
                "INSERT INTO users (telegram_id, role_id, balance, referral_debt) "
                "VALUES (930001, 1, 1234.56, 0.00), "
                "(930002, 1, 2065.44, 0.00)"
            )
            await connection.execute(
                "INSERT INTO store_settings (id, referral_percent) "
                "VALUES (1, 5.00) ON CONFLICT (id) DO NOTHING"
            )
            await connection.execute(
                "INSERT INTO referral_earnings "
                "(id, referrer_id, referral_id, amount, original_amount, "
                "status, earning_type, reason) "
                "VALUES (6, 930001, 930002, 10.00, 10.00, 'settled', "
                "'legacy_topup', 'Pre-Phase 6B legacy earning')"
            )
            before = _fingerprint(await connection.fetch(
                "SELECT telegram_id, balance FROM users ORDER BY telegram_id"
            ))

            # Reproduce the material schema differences discovered on the
            # rejected live prototype.  The Alembic revision remains 24d...
            # throughout; no stamp is involved.
            for table, constraint in (
                ("users", "ck_users_referral_debt_nonnegative"),
                ("referral_conversions", "ck_rc_debt_offset_nonnegative"),
                ("referral_conversions", "ck_rc_balance_credit_nonnegative"),
                ("referral_conversions", "ck_rc_balance_operation"),
                ("referral_earnings", "ck_re_status"),
                ("referral_earnings", "ck_re_type"),
                ("referral_earnings", "ck_re_status_matrix"),
                ("referral_earnings", "ck_re_lifecycle_fields"),
                ("referral_earnings", "ck_re_debit_audit_math"),
                ("referral_earnings", "ck_re_sources_order_only"),
            ):
                await connection.execute(
                    f'ALTER TABLE "{table}" DROP CONSTRAINT "{constraint}"'
                )
            await connection.execute(
                "ALTER TABLE users ADD CONSTRAINT ck_user_referral_debt_positive "
                "CHECK (referral_debt >= 0)"
            )
            await connection.execute(
                "ALTER TABLE referral_conversions "
                "ADD CONSTRAINT ck_rc_debt_offset_positive CHECK (debt_offset >= 0), "
                "ADD CONSTRAINT ck_rc_balance_credit_positive CHECK (balance_credit >= 0), "
                "ADD CONSTRAINT ck_rc_balance_op_req CHECK "
                "(balance_credit = 0 OR "
                "(balance_credit > 0 AND balance_operation_id IS NOT NULL))"
            )
            await connection.execute(
                "ALTER TABLE referral_earnings "
                "ADD CONSTRAINT ck_re_comm_rate CHECK "
                "(commission_rate >= 0 AND commission_rate <= 100), "
                "ADD CONSTRAINT ck_re_converted_has_conversion CHECK "
                "(NOT (status = 'converted' AND conversion_id IS NULL)), "
                "ADD CONSTRAINT ck_re_status_matrix CHECK ("
                "(earning_type = 'order_purchase' AND status IN "
                "('pending', 'available', 'converted', 'reversed')) OR "
                "(earning_type = 'legacy_topup' AND status = 'settled') OR "
                "(earning_type = 'manual_adjustment' AND "
                "((amount > 0 AND status IN ('available', 'converted')) OR "
                "(amount < 0 AND status = 'applied'))) OR "
                "(earning_type = 'compensating_reversal' AND "
                "amount < 0 AND status = 'applied'))"
            )
            await connection.execute(
                "ALTER TABLE referral_earnings "
                "ALTER COLUMN earning_type TYPE VARCHAR(20), "
                "ALTER COLUMN idempotency_key TYPE VARCHAR(255)"
            )

            for table, constraint in (
                ("referral_conversions", "referral_conversions_user_id_fkey"),
                ("referral_conversions", "referral_conversions_balance_operation_id_fkey"),
                ("referral_earnings", "referral_earnings_referrer_id_fkey"),
                ("referral_earnings", "referral_earnings_referral_id_fkey"),
                ("referral_earnings", "referral_earnings_bought_goods_id_fkey"),
                ("referral_earnings", "referral_earnings_order_item_id_fkey"),
                ("referral_earnings", "referral_earnings_reversal_of_id_fkey"),
                ("referral_earnings", "referral_earnings_conversion_id_fkey"),
            ):
                await connection.execute(
                    f'ALTER TABLE "{table}" DROP CONSTRAINT "{constraint}"'
                )
            await connection.execute(
                "ALTER TABLE referral_conversions "
                "ADD CONSTRAINT referral_conversions_user_id_fkey "
                "FOREIGN KEY (user_id) REFERENCES users(telegram_id) ON DELETE CASCADE, "
                "ADD CONSTRAINT referral_conversions_balance_operation_id_fkey "
                "FOREIGN KEY (balance_operation_id) REFERENCES operations(id) "
                "ON DELETE SET NULL"
            )
            await connection.execute(
                "ALTER TABLE referral_earnings "
                "ADD CONSTRAINT referral_earnings_referrer_id_fkey "
                "FOREIGN KEY (referrer_id) REFERENCES users(telegram_id) ON DELETE CASCADE, "
                "ADD CONSTRAINT referral_earnings_referral_id_fkey "
                "FOREIGN KEY (referral_id) REFERENCES users(telegram_id) ON DELETE CASCADE, "
                "ADD CONSTRAINT referral_earnings_bought_goods_id_fkey "
                "FOREIGN KEY (bought_goods_id) REFERENCES bought_goods(id), "
                "ADD CONSTRAINT referral_earnings_order_item_id_fkey "
                "FOREIGN KEY (order_item_id) REFERENCES order_items(id), "
                "ADD CONSTRAINT referral_earnings_reversal_of_id_fkey "
                "FOREIGN KEY (reversal_of_id) REFERENCES referral_earnings(id) "
                "ON DELETE SET NULL, "
                "ADD CONSTRAINT referral_earnings_conversion_id_fkey "
                "FOREIGN KEY (conversion_id) REFERENCES referral_conversions(id) "
                "ON DELETE SET NULL"
            )

            for index in (
                "uq_ref_earning_bought_goods",
                "uq_ref_earning_order_item",
                "uq_ref_earning_reversal_of",
                "uq_ref_earning_idempotency",
                "ix_referral_earnings_referrer_status",
                "ix_referral_conversions_user_created",
            ):
                await connection.execute(f'DROP INDEX "{index}"')
            await connection.execute(
                "ALTER TABLE referral_earnings "
                "ADD CONSTRAINT referral_earnings_bought_goods_id_key "
                "UNIQUE (bought_goods_id), "
                "ADD CONSTRAINT referral_earnings_order_item_id_key "
                "UNIQUE (order_item_id)"
            )
            await connection.execute(
                "CREATE UNIQUE INDEX uq_ref_earning_bought_goods "
                "ON referral_earnings (bought_goods_id) "
                "WHERE bought_goods_id IS NOT NULL; "
                "CREATE UNIQUE INDEX uq_ref_earning_order_item "
                "ON referral_earnings (order_item_id) "
                "WHERE order_item_id IS NOT NULL; "
                "CREATE UNIQUE INDEX uq_reversal_of "
                "ON referral_earnings (reversal_of_id) "
                "WHERE reversal_of_id IS NOT NULL; "
                "CREATE UNIQUE INDEX uq_idempotency_key "
                "ON referral_earnings (idempotency_key) "
                "WHERE idempotency_key IS NOT NULL"
            )
        finally:
            await connection.close()

        _run_alembic(test_url, "upgrade", "head")
        assert PHASE6B_REVISION in _run_alembic(test_url, "current").stdout

        connection = await asyncpg.connect(asyncpg_url)
        try:
            assert _fingerprint(await connection.fetch(
                "SELECT telegram_id, balance FROM users ORDER BY telegram_id"
            )) == before
            assert await connection.fetchval("SELECT sum(balance) FROM users") == 3300
            assert await connection.fetchval(
                "SELECT count(*) FROM referral_earnings"
            ) == 1
            row = await connection.fetchrow(
                "SELECT id, amount, status, earning_type, reason "
                "FROM referral_earnings"
            )
            assert tuple(row.values()) == (
                6,
                10,
                "settled",
                "legacy_topup",
                "Pre-Phase 6B legacy earning",
            )
            assert await connection.fetchval(
                "SELECT count(*) FROM referral_conversions"
            ) == 0
            assert await connection.fetchval(
                "SELECT sum(referral_debt) FROM users"
            ) == 0

            constraints = set(await connection.fetchval(
                "SELECT array_agg(conname ORDER BY conname) "
                "FROM pg_constraint WHERE conrelid = 'referral_earnings'::regclass"
            ))
            assert {
                "ck_re_status",
                "ck_re_type",
                "ck_re_status_matrix",
                "ck_re_lifecycle_fields",
                "ck_re_debit_audit_math",
                "ck_re_sources_order_only",
            } <= constraints
            assert {
                "ck_re_comm_rate",
                "ck_re_converted_has_conversion",
                "referral_earnings_bought_goods_id_key",
                "referral_earnings_order_item_id_key",
            }.isdisjoint(constraints)

            delete_actions = dict(await connection.fetch(
                "SELECT conname, confdeltype::text FROM pg_constraint "
                "WHERE conrelid IN ("
                "'referral_earnings'::regclass, "
                "'referral_conversions'::regclass) AND contype = 'f'"
            ))
            assert set(delete_actions.values()) == {"r"}

            lengths = dict(await connection.fetch(
                "SELECT column_name, character_maximum_length "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'referral_earnings' "
                "AND column_name IN ('status', 'earning_type', 'idempotency_key')"
            ))
            assert lengths == {
                "status": 32,
                "earning_type": 32,
                "idempotency_key": 100,
            }

            index_definitions = {
                row["indexname"]: row["indexdef"]
                for row in await connection.fetch(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND tablename IN "
                    "('referral_earnings', 'referral_conversions')"
                )
            }
            assert "earning_type" in index_definitions["uq_ref_earning_bought_goods"]
            assert "earning_type" in index_definitions["uq_ref_earning_order_item"]
            assert "earning_type" in index_definitions["uq_ref_earning_reversal_of"]
            assert "ix_referral_earnings_referrer_status" in index_definitions
            assert "ix_referral_conversions_user_created" in index_definitions
            assert "uq_idempotency_key" not in index_definitions
            assert "uq_reversal_of" not in index_definitions
        finally:
            await connection.close()
    finally:
        admin = await asyncpg.connect(admin_url)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        finally:
            await admin.close()


@pytest.mark.asyncio
async def test_concurrent_conversion_uses_postgres_row_locks():
    """Three simultaneous conversions may credit the wallet only once."""

    source_url, admin_url = _database_parts()
    base_url = source_url.rsplit("/", 1)[0]
    database = f"phase6b_lock_test_{uuid.uuid4().hex[:10]}"
    test_url = f"{base_url}/{database}"
    asyncpg_url = test_url.replace("postgresql+asyncpg", "postgresql")

    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
    finally:
        await admin.close()

    try:
        _run_alembic(test_url, "upgrade", "head")
        connection = await asyncpg.connect(asyncpg_url)
        try:
            await connection.execute(
                "INSERT INTO roles (id, name, permissions, \"default\") "
                "VALUES (1, 'USER', 1, true) ON CONFLICT (id) DO NOTHING"
            )
            await connection.execute(
                "INSERT INTO users (telegram_id, role_id, balance, referral_debt) "
                "VALUES (920001, 1, 0.00, 2.00)"
            )
            await connection.execute(
                "INSERT INTO referral_earnings "
                "(referrer_id, referral_id, amount, original_amount, status, "
                "earning_type, admin_identity, reason, ready_at) "
                "VALUES (920001, NULL, 10.00, 10.00, 'available', "
                "'manual_adjustment', 'phase6b-test', 'concurrency proof', now())"
            )
        finally:
            await connection.close()

        program = r'''
import asyncio
import json
import os

import bot.database.dsn as dsn_module

dsn_module.dsn = lambda: os.environ["DATABASE_URL"]

from bot.database.main import Database
from bot.database.methods.referrals import convert_referral_earnings


async def main():
    results = await asyncio.gather(*(
        convert_referral_earnings(920001) for _ in range(3)
    ))
    await asyncio.sleep(0.05)
    print("RESULTS_JSON=" + json.dumps([
        [success, code, str(amount)] for success, code, amount in results
    ]))
    await Database().dispose()


asyncio.run(main())
'''
        env = os.environ.copy()
        env["DATABASE_URL"] = test_url
        env["REDIS_ENABLED"] = "0"
        result = subprocess.run(
            ["python", "-c", program],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        output_line = next(
            line for line in result.stdout.splitlines()
            if line.startswith("RESULTS_JSON=")
        )
        results = json.loads(output_line.split("=", 1)[1])
        assert sum(1 for success, _, _ in results if success) == 1
        assert sorted(code for _, code, _ in results) == [
            "no_earnings",
            "no_earnings",
            "success",
        ]

        connection = await asyncpg.connect(asyncpg_url)
        try:
            state = await connection.fetchrow(
                "SELECT balance, referral_debt FROM users WHERE telegram_id = 920001"
            )
            assert tuple(state.values()) == (8, 0)
            assert await connection.fetchval(
                "SELECT count(*) FROM referral_conversions WHERE user_id = 920001"
            ) == 1
            assert await connection.fetchval(
                "SELECT count(*) FROM operations WHERE user_id = 920001"
            ) == 1
            assert await connection.fetchval(
                "SELECT status FROM referral_earnings WHERE referrer_id = 920001"
            ) == "converted"
        finally:
            await connection.close()
    finally:
        admin = await asyncpg.connect(admin_url)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        finally:
            await admin.close()
