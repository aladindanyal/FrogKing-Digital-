"""Phase 6B referral earnings ledger.

Revision ID: b6e3f4a5c6d7
Revises: 4a2b3c4d5e6f
Create Date: 2026-08-31 14:00:00.000000

The downgrade intentionally removes Phase 6B rows because the legacy schema
cannot represent their state or audit data. It must not be used on production
after Phase 6B has accepted real earnings.
"""

from alembic import op
import sqlalchemy as sa


revision = 'b6e3f4a5c6d7'
down_revision = '4a2b3c4d5e6f'
branch_labels = None
depends_on = None


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


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('referral_debt', sa.Numeric(12, 2), server_default=sa.text('0.00'), nullable=False),
    )
    op.create_check_constraint(
        'ck_users_referral_debt_nonnegative', 'users', 'referral_debt >= 0'
    )

    op.add_column(
        'store_settings',
        sa.Column('referral_percent', sa.Numeric(5, 2), server_default=sa.text('5.00'), nullable=False),
    )
    op.create_check_constraint(
        'ck_store_settings_referral_percent',
        'store_settings',
        'referral_percent >= 0 AND referral_percent <= 100',
    )

    op.create_table(
        'referral_conversions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('gross_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('debt_offset', sa.Numeric(12, 2), nullable=False),
        sa.Column('balance_credit', sa.Numeric(12, 2), nullable=False),
        sa.Column('balance_operation_id', sa.Integer(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.CheckConstraint('gross_amount > 0', name='ck_rc_gross_positive'),
        sa.CheckConstraint('debt_offset >= 0', name='ck_rc_debt_offset_nonnegative'),
        sa.CheckConstraint('balance_credit >= 0', name='ck_rc_balance_credit_nonnegative'),
        sa.CheckConstraint(
            'gross_amount = debt_offset + balance_credit', name='ck_rc_gross_math'
        ),
        sa.CheckConstraint(
            '(balance_credit = 0 AND balance_operation_id IS NULL) OR '
            '(balance_credit > 0 AND balance_operation_id IS NOT NULL)',
            name='ck_rc_balance_operation',
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.telegram_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['balance_operation_id'], ['operations.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_referral_conversions_user_id', 'referral_conversions', ['user_id']
    )
    op.create_index(
        'ix_referral_conversions_user_created',
        'referral_conversions', ['user_id', 'created_at'],
    )

    # Expand first. Legacy rows are backfilled before NOT NULL/state guards.
    op.add_column('referral_earnings', sa.Column('status', sa.String(32), nullable=True))
    op.add_column('referral_earnings', sa.Column('earning_type', sa.String(32), nullable=True))
    op.add_column('referral_earnings', sa.Column('bought_goods_id', sa.Integer(), nullable=True))
    op.add_column('referral_earnings', sa.Column('order_item_id', sa.Integer(), nullable=True))
    op.add_column('referral_earnings', sa.Column('admin_identity', sa.String(100), nullable=True))
    op.add_column('referral_earnings', sa.Column('reason', sa.Text(), nullable=True))
    op.add_column('referral_earnings', sa.Column('ready_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('referral_earnings', sa.Column('converted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('referral_earnings', sa.Column('reversed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('referral_earnings', sa.Column('commission_base_amount', sa.Numeric(12, 2), nullable=True))
    op.add_column('referral_earnings', sa.Column('commission_rate', sa.Numeric(5, 2), nullable=True))
    op.add_column('referral_earnings', sa.Column('balance_recovered', sa.Numeric(12, 2), nullable=True))
    op.add_column('referral_earnings', sa.Column('debt_added', sa.Numeric(12, 2), nullable=True))
    op.add_column('referral_earnings', sa.Column('reversal_of_id', sa.Integer(), nullable=True))
    op.add_column('referral_earnings', sa.Column('conversion_id', sa.Integer(), nullable=True))
    op.add_column('referral_earnings', sa.Column('idempotency_key', sa.String(100), nullable=True))

    op.execute(
        "UPDATE referral_earnings "
        "SET status = 'settled', earning_type = 'legacy_topup', "
        "reason = 'Pre-Phase 6B legacy earning' "
        "WHERE status IS NULL AND earning_type IS NULL"
    )

    op.alter_column(
        'referral_earnings', 'status', nullable=False,
        server_default=sa.text("'pending'"), existing_type=sa.String(32),
    )
    op.alter_column(
        'referral_earnings', 'earning_type', nullable=False,
        server_default=sa.text("'order_purchase'"), existing_type=sa.String(32),
    )
    op.alter_column(
        'referral_earnings', 'referral_id', nullable=True,
        existing_type=sa.BigInteger(),
    )

    op.drop_constraint(
        'referral_earnings_referral_id_fkey', 'referral_earnings', type_='foreignkey'
    )
    op.drop_constraint(
        'referral_earnings_referrer_id_fkey', 'referral_earnings', type_='foreignkey'
    )
    op.create_foreign_key(
        'referral_earnings_referrer_id_fkey',
        'referral_earnings', 'users', ['referrer_id'], ['telegram_id'],
        ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'referral_earnings_referral_id_fkey',
        'referral_earnings', 'users', ['referral_id'], ['telegram_id'],
        ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'referral_earnings_bought_goods_id_fkey',
        'referral_earnings', 'bought_goods', ['bought_goods_id'], ['id'],
        ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'referral_earnings_order_item_id_fkey',
        'referral_earnings', 'order_items', ['order_item_id'], ['id'],
        ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'referral_earnings_reversal_of_id_fkey',
        'referral_earnings', 'referral_earnings', ['reversal_of_id'], ['id'],
        ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'referral_earnings_conversion_id_fkey',
        'referral_earnings', 'referral_conversions', ['conversion_id'], ['id'],
        ondelete='RESTRICT',
    )

    op.create_check_constraint(
        'ck_re_amount_not_zero', 'referral_earnings', 'amount <> 0'
    )
    op.create_check_constraint(
        'ck_re_status', 'referral_earnings',
        "status IN ('pending', 'available', 'converted', 'settled', 'applied', 'reversed')",
    )
    op.create_check_constraint(
        'ck_re_type', 'referral_earnings',
        "earning_type IN ('order_purchase', 'legacy_topup', 'manual_adjustment', 'compensating_reversal')",
    )
    op.create_check_constraint(
        'ck_re_status_matrix', 'referral_earnings', STATUS_MATRIX
    )
    op.create_check_constraint(
        'ck_re_lifecycle_fields', 'referral_earnings',
        "(status = 'pending') OR (status = 'available' AND ready_at IS NOT NULL) "
        "OR (status = 'converted' AND conversion_id IS NOT NULL AND converted_at IS NOT NULL) "
        "OR (status = 'reversed' AND reversed_at IS NOT NULL) "
        "OR status IN ('settled', 'applied')",
    )
    op.create_check_constraint(
        'ck_re_debit_audit_math', 'referral_earnings',
        "earning_type NOT IN ('manual_adjustment', 'compensating_reversal') "
        "OR amount > 0 "
        "OR (balance_recovered IS NOT NULL AND debt_added IS NOT NULL "
        "AND balance_recovered >= 0 AND debt_added >= 0 "
        "AND balance_recovered + debt_added = -amount)",
    )
    op.create_check_constraint(
        'ck_re_sources_order_only', 'referral_earnings',
        "earning_type = 'order_purchase' OR (bought_goods_id IS NULL AND order_item_id IS NULL)",
    )

    op.create_index(
        'ix_referral_earnings_referrer_status',
        'referral_earnings', ['referrer_id', 'status'],
    )
    op.create_index(
        'ix_referral_earnings_status_ready_at',
        'referral_earnings', ['status', 'ready_at', 'id'],
    )
    op.create_index(
        'ix_referral_earnings_conversion_id',
        'referral_earnings', ['conversion_id'],
    )
    op.create_index(
        'uq_ref_earning_bought_goods', 'referral_earnings', ['bought_goods_id'],
        unique=True,
        postgresql_where=sa.text(
            "earning_type = 'order_purchase' AND bought_goods_id IS NOT NULL"
        ),
    )
    op.create_index(
        'uq_ref_earning_order_item', 'referral_earnings', ['order_item_id'],
        unique=True,
        postgresql_where=sa.text(
            "earning_type = 'order_purchase' AND order_item_id IS NOT NULL"
        ),
    )
    op.create_index(
        'uq_ref_earning_reversal_of', 'referral_earnings', ['reversal_of_id'],
        unique=True,
        postgresql_where=sa.text(
            "earning_type = 'compensating_reversal' AND reversal_of_id IS NOT NULL"
        ),
    )
    op.create_index(
        'uq_ref_earning_idempotency', 'referral_earnings', ['idempotency_key'],
        unique=True,
        postgresql_where=sa.text('idempotency_key IS NOT NULL'),
    )


def downgrade() -> None:
    # Explicitly discard rows the legacy schema cannot represent.
    op.execute("DELETE FROM referral_earnings WHERE earning_type <> 'legacy_topup'")

    op.drop_index('uq_ref_earning_idempotency', table_name='referral_earnings')
    op.drop_index('uq_ref_earning_reversal_of', table_name='referral_earnings')
    op.drop_index('uq_ref_earning_order_item', table_name='referral_earnings')
    op.drop_index('uq_ref_earning_bought_goods', table_name='referral_earnings')
    op.drop_index('ix_referral_earnings_conversion_id', table_name='referral_earnings')
    op.drop_index('ix_referral_earnings_status_ready_at', table_name='referral_earnings')
    op.drop_index('ix_referral_earnings_referrer_status', table_name='referral_earnings')

    op.drop_constraint('ck_re_sources_order_only', 'referral_earnings', type_='check')
    op.drop_constraint('ck_re_debit_audit_math', 'referral_earnings', type_='check')
    op.drop_constraint('ck_re_lifecycle_fields', 'referral_earnings', type_='check')
    op.drop_constraint('ck_re_status_matrix', 'referral_earnings', type_='check')
    op.drop_constraint('ck_re_type', 'referral_earnings', type_='check')
    op.drop_constraint('ck_re_status', 'referral_earnings', type_='check')
    op.drop_constraint('ck_re_amount_not_zero', 'referral_earnings', type_='check')

    op.drop_constraint('referral_earnings_conversion_id_fkey', 'referral_earnings', type_='foreignkey')
    op.drop_constraint('referral_earnings_reversal_of_id_fkey', 'referral_earnings', type_='foreignkey')
    op.drop_constraint('referral_earnings_order_item_id_fkey', 'referral_earnings', type_='foreignkey')
    op.drop_constraint('referral_earnings_bought_goods_id_fkey', 'referral_earnings', type_='foreignkey')
    op.drop_constraint('referral_earnings_referral_id_fkey', 'referral_earnings', type_='foreignkey')
    op.drop_constraint('referral_earnings_referrer_id_fkey', 'referral_earnings', type_='foreignkey')

    op.alter_column(
        'referral_earnings', 'referral_id', nullable=False,
        existing_type=sa.BigInteger(),
    )
    op.create_foreign_key(
        'referral_earnings_referral_id_fkey',
        'referral_earnings', 'users', ['referral_id'], ['telegram_id'],
        ondelete='CASCADE',
    )
    op.create_foreign_key(
        'referral_earnings_referrer_id_fkey',
        'referral_earnings', 'users', ['referrer_id'], ['telegram_id'],
        ondelete='CASCADE',
    )

    for column in (
        'idempotency_key', 'conversion_id', 'reversal_of_id', 'debt_added',
        'balance_recovered', 'commission_rate', 'commission_base_amount',
        'reversed_at', 'converted_at', 'ready_at', 'reason', 'admin_identity',
        'order_item_id', 'bought_goods_id', 'earning_type', 'status',
    ):
        op.drop_column('referral_earnings', column)

    op.drop_index('ix_referral_conversions_user_created', table_name='referral_conversions')
    op.drop_index('ix_referral_conversions_user_id', table_name='referral_conversions')
    op.drop_table('referral_conversions')

    op.drop_constraint(
        'ck_store_settings_referral_percent', 'store_settings', type_='check'
    )
    op.drop_column('store_settings', 'referral_percent')
    op.drop_constraint(
        'ck_users_referral_debt_nonnegative', 'users', type_='check'
    )
    op.drop_column('users', 'referral_debt')
