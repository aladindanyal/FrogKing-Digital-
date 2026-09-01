"""Normalize rejected Phase 6B schemas to the canonical ledger schema.

Revision ID: c7d8e9f0a1b2
Revises: 24d778704bb6
Create Date: 2026-09-01 15:10:00.000000

This migration is intentionally data-preserving.  It only normalizes column
definitions, constraints, foreign-key actions, and indexes.  PostgreSQL runs
the upgrade transactionally, so invalid legacy data aborts the whole upgrade
instead of being silently rewritten or deleted.
"""

from alembic import op
import sqlalchemy as sa

revision = "c7d8e9f0a1b2"
down_revision = "24d778704bb6"
branch_labels = None
depends_on = None


# Keep migration history self-contained.  Importing an older migration module
# is fragile because Alembic loads revision files outside a regular package.
STATUS_MATRIX = """
(earning_type = 'order_purchase' AND amount > 0
 AND referral_id IS NOT NULL
 AND ((bought_goods_id IS NOT NULL AND order_item_id IS NULL)
      OR (bought_goods_id IS NULL AND order_item_id IS NOT NULL))
 AND commission_base_amount > 0
 AND commission_rate >= 0 AND commission_rate <= 100
 AND status IN ('pending', 'available', 'converted', 'reversed'))
OR
(earning_type = 'legacy_topup' AND amount > 0 AND referral_id IS NOT NULL
 AND status = 'settled' AND bought_goods_id IS NULL AND order_item_id IS NULL)
OR
(earning_type = 'manual_adjustment' AND admin_identity IS NOT NULL
 AND reason IS NOT NULL AND length(trim(reason)) > 0
 AND ((amount > 0 AND status IN ('available', 'converted'))
      OR (amount < 0 AND status = 'applied')))
OR
(earning_type = 'compensating_reversal' AND amount < 0
 AND status = 'applied' AND reversal_of_id IS NOT NULL)
"""


_REFERRAL_EARNING_CHECKS = (
    "ck_re_amount_not_zero",
    "ck_re_comm_rate",
    "ck_re_converted_has_conversion",
    "ck_re_status",
    "ck_re_type",
    "ck_re_status_matrix",
    "ck_re_lifecycle_fields",
    "ck_re_debit_audit_math",
    "ck_re_sources_order_only",
    "ck_referral_earnings_no_self_referral",
)

_REFERRAL_CONVERSION_CHECKS = (
    "ck_rc_gross_positive",
    "ck_rc_debt_offset_positive",
    "ck_rc_debt_offset_nonnegative",
    "ck_rc_balance_credit_positive",
    "ck_rc_balance_credit_nonnegative",
    "ck_rc_gross_math",
    "ck_rc_balance_op_req",
    "ck_rc_balance_operation",
)


def _drop_constraint(table: str, name: str) -> None:
    op.execute(
        sa.text(f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS "{name}"')
    )


def _drop_index(name: str) -> None:
    op.execute(sa.text(f'DROP INDEX IF EXISTS "{name}"'))


def upgrade() -> None:
    # Never shorten a populated idempotency key.  A violation aborts the DDL
    # transaction before any schema normalization becomes visible.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM referral_earnings
                WHERE length(idempotency_key) > 100
            ) THEN
                RAISE EXCEPTION
                    'Phase 6B convergence refused: idempotency_key exceeds 100 characters';
            END IF;
        END
        $$
        """
    )

    op.execute(
        "ALTER TABLE referral_earnings "
        "ALTER COLUMN status TYPE VARCHAR(32), "
        "ALTER COLUMN status SET DEFAULT 'pending', "
        "ALTER COLUMN status SET NOT NULL, "
        "ALTER COLUMN earning_type TYPE VARCHAR(32), "
        "ALTER COLUMN earning_type SET DEFAULT 'order_purchase', "
        "ALTER COLUMN earning_type SET NOT NULL, "
        "ALTER COLUMN idempotency_key TYPE VARCHAR(100), "
        "ALTER COLUMN referral_id DROP NOT NULL"
    )

    for name in ("ck_user_referral_debt_positive", "ck_users_referral_debt_nonnegative"):
        _drop_constraint("users", name)
    op.create_check_constraint(
        "ck_users_referral_debt_nonnegative", "users", "referral_debt >= 0"
    )

    _drop_constraint("store_settings", "ck_store_settings_referral_percent")
    op.create_check_constraint(
        "ck_store_settings_referral_percent",
        "store_settings",
        "referral_percent >= 0 AND referral_percent <= 100",
    )

    for name in _REFERRAL_CONVERSION_CHECKS:
        _drop_constraint("referral_conversions", name)
    op.create_check_constraint(
        "ck_rc_gross_positive", "referral_conversions", "gross_amount > 0"
    )
    op.create_check_constraint(
        "ck_rc_debt_offset_nonnegative",
        "referral_conversions",
        "debt_offset >= 0",
    )
    op.create_check_constraint(
        "ck_rc_balance_credit_nonnegative",
        "referral_conversions",
        "balance_credit >= 0",
    )
    op.create_check_constraint(
        "ck_rc_gross_math",
        "referral_conversions",
        "gross_amount = debt_offset + balance_credit",
    )
    op.create_check_constraint(
        "ck_rc_balance_operation",
        "referral_conversions",
        "(balance_credit = 0 AND balance_operation_id IS NULL) OR "
        "(balance_credit > 0 AND balance_operation_id IS NOT NULL)",
    )

    for name in (
        "referral_conversions_user_id_fkey",
        "referral_conversions_balance_operation_id_fkey",
    ):
        _drop_constraint("referral_conversions", name)
    op.create_foreign_key(
        "referral_conversions_user_id_fkey",
        "referral_conversions",
        "users",
        ["user_id"],
        ["telegram_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "referral_conversions_balance_operation_id_fkey",
        "referral_conversions",
        "operations",
        ["balance_operation_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    for name in _REFERRAL_EARNING_CHECKS:
        _drop_constraint("referral_earnings", name)
    op.create_check_constraint(
        "ck_referral_earnings_no_self_referral",
        "referral_earnings",
        "referral_id IS NULL OR referrer_id != referral_id",
    )
    op.create_check_constraint(
        "ck_re_amount_not_zero", "referral_earnings", "amount <> 0"
    )
    op.create_check_constraint(
        "ck_re_status",
        "referral_earnings",
        "status IN ('pending', 'available', 'converted', 'settled', "
        "'applied', 'reversed')",
    )
    op.create_check_constraint(
        "ck_re_type",
        "referral_earnings",
        "earning_type IN ('order_purchase', 'legacy_topup', "
        "'manual_adjustment', 'compensating_reversal')",
    )
    op.create_check_constraint(
        "ck_re_status_matrix", "referral_earnings", STATUS_MATRIX
    )
    op.create_check_constraint(
        "ck_re_lifecycle_fields",
        "referral_earnings",
        "(status = 'pending') OR (status = 'available' AND ready_at IS NOT NULL) "
        "OR (status = 'converted' AND conversion_id IS NOT NULL "
        "AND converted_at IS NOT NULL) "
        "OR (status = 'reversed' AND reversed_at IS NOT NULL) "
        "OR status IN ('settled', 'applied')",
    )
    op.create_check_constraint(
        "ck_re_debit_audit_math",
        "referral_earnings",
        "earning_type NOT IN ('manual_adjustment', 'compensating_reversal') "
        "OR amount > 0 "
        "OR (balance_recovered IS NOT NULL AND debt_added IS NOT NULL "
        "AND balance_recovered >= 0 AND debt_added >= 0 "
        "AND balance_recovered + debt_added = -amount)",
    )
    op.create_check_constraint(
        "ck_re_sources_order_only",
        "referral_earnings",
        "earning_type = 'order_purchase' "
        "OR (bought_goods_id IS NULL AND order_item_id IS NULL)",
    )

    for name in (
        "referral_earnings_referrer_id_fkey",
        "referral_earnings_referral_id_fkey",
        "referral_earnings_bought_goods_id_fkey",
        "referral_earnings_order_item_id_fkey",
        "referral_earnings_reversal_of_id_fkey",
        "referral_earnings_conversion_id_fkey",
    ):
        _drop_constraint("referral_earnings", name)
    for name, remote_table, local_column, remote_column in (
        ("referral_earnings_referrer_id_fkey", "users", "referrer_id", "telegram_id"),
        ("referral_earnings_referral_id_fkey", "users", "referral_id", "telegram_id"),
        ("referral_earnings_bought_goods_id_fkey", "bought_goods", "bought_goods_id", "id"),
        ("referral_earnings_order_item_id_fkey", "order_items", "order_item_id", "id"),
        ("referral_earnings_reversal_of_id_fkey", "referral_earnings", "reversal_of_id", "id"),
        ("referral_earnings_conversion_id_fkey", "referral_conversions", "conversion_id", "id"),
    ):
        op.create_foreign_key(
            name,
            "referral_earnings",
            remote_table,
            [local_column],
            [remote_column],
            ondelete="RESTRICT",
        )

    # Remove prototype uniqueness before installing the canonical predicates.
    for name in (
        "referral_earnings_bought_goods_id_key",
        "referral_earnings_order_item_id_key",
    ):
        _drop_constraint("referral_earnings", name)
    for name in (
        "uq_idempotency_key",
        "uq_reversal_of",
        "uq_ref_earning_bought_goods",
        "uq_ref_earning_order_item",
        "uq_ref_earning_reversal_of",
        "uq_ref_earning_idempotency",
        "ix_referral_earnings_referrer_status",
        "ix_referral_earnings_status_ready_at",
        "ix_referral_earnings_conversion_id",
        "ix_referral_conversions_user_id",
        "ix_referral_conversions_user_created",
    ):
        _drop_index(name)

    op.create_index(
        "ix_referral_conversions_user_id",
        "referral_conversions",
        ["user_id"],
    )
    op.create_index(
        "ix_referral_conversions_user_created",
        "referral_conversions",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_referral_earnings_referrer_status",
        "referral_earnings",
        ["referrer_id", "status"],
    )
    op.create_index(
        "ix_referral_earnings_status_ready_at",
        "referral_earnings",
        ["status", "ready_at", "id"],
    )
    op.create_index(
        "ix_referral_earnings_conversion_id",
        "referral_earnings",
        ["conversion_id"],
    )
    op.create_index(
        "uq_ref_earning_bought_goods",
        "referral_earnings",
        ["bought_goods_id"],
        unique=True,
        postgresql_where=sa.text(
            "earning_type = 'order_purchase' AND bought_goods_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_ref_earning_order_item",
        "referral_earnings",
        ["order_item_id"],
        unique=True,
        postgresql_where=sa.text(
            "earning_type = 'order_purchase' AND order_item_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_ref_earning_reversal_of",
        "referral_earnings",
        ["reversal_of_id"],
        unique=True,
        postgresql_where=sa.text(
            "earning_type = 'compensating_reversal' AND reversal_of_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_ref_earning_idempotency",
        "referral_earnings",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    # This revision is a one-way compatibility boundary.  A subsequent
    # downgrade through b6e3f4a5c6d7 remains deliberately destructive and is
    # forbidden on production once Phase 6B contains real data.
    pass
