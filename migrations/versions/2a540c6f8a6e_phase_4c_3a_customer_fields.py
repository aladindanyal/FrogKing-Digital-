"""phase_4c_3a_customer_fields

Revision ID: 2a540c6f8a6e
Revises: aed0950a0170
Create Date: 2026-07-13 12:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '2a540c6f8a6e'
down_revision: Union[str, None] = 'aed0950a0170'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add columns to goods
    op.add_column('goods', sa.Column('fulfillment_mode', sa.String(length=20), server_default='instant', nullable=False))
    op.add_column('goods', sa.Column('fulfillment_eta_minutes', sa.Integer(), nullable=True))
    op.add_column('goods', sa.Column('manual_instructions_i18n', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('goods', sa.Column('customer_input_intro_i18n', postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    # Add Check Constraints
    op.create_check_constraint('ck_goods_fulfillment_mode', 'goods', "fulfillment_mode IN ('instant', 'manual')")
    op.create_check_constraint('ck_goods_fulfillment_eta_positive', 'goods', "fulfillment_eta_minutes IS NULL OR fulfillment_eta_minutes > 0")

    # 2. Create product_customer_fields table
    op.create_table('product_customer_fields',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('goods_id', sa.Integer(), nullable=False),
        sa.Column('field_key', sa.String(length=64), nullable=False),
        sa.Column('field_type', sa.String(length=20), nullable=False),
        sa.Column('label_i18n', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('placeholder_i18n', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('help_text_i18n', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('required', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('is_sensitive', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('scope', sa.String(length=20), server_default='per_order', nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('min_length', sa.Integer(), nullable=True),
        sa.Column('max_length', sa.Integer(), nullable=True),
        sa.Column('select_options_i18n', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("field_type IN ('text', 'textarea', 'email', 'phone', 'username', 'url', 'select', 'secret')", name='ck_prod_cust_field_type'),
        sa.CheckConstraint("scope IN ('per_order', 'per_unit')", name='ck_prod_cust_field_scope'),
        sa.CheckConstraint('max_length IS NULL OR max_length > 0', name='ck_prod_cust_field_max_len'),
        sa.CheckConstraint('min_length IS NULL OR min_length >= 0', name='ck_prod_cust_field_min_len'),
        sa.CheckConstraint('sort_order >= 0', name='ck_prod_cust_field_sort_pos'),
        sa.ForeignKeyConstraint(['goods_id'], ['goods.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('goods_id', 'field_key', name='uq_product_customer_field_key')
    )
    op.create_index(op.f('ix_product_customer_fields_goods_id'), 'product_customer_fields', ['goods_id'], unique=False)
    op.create_index('ix_prod_cust_field_goods_active_sort', 'product_customer_fields', ['goods_id', 'is_active', 'sort_order'], unique=False)


def downgrade() -> None:
    # Safely refuse downgrade if data exists that prevents it
    conn = op.get_bind()
    
    res = conn.execute(sa.text("SELECT COUNT(*) FROM product_customer_fields")).scalar()
    if res and res > 0:
        raise RuntimeError("Cannot downgrade: ProductCustomerField table contains data. Remove data first.")
        
    res2 = conn.execute(sa.text("SELECT COUNT(*) FROM goods WHERE fulfillment_mode != 'instant'")).scalar()
    if res2 and res2 > 0:
        raise RuntimeError("Cannot downgrade: Some goods are set to manual fulfillment mode. Switch them to instant first.")

    op.drop_index('ix_prod_cust_field_goods_active_sort', table_name='product_customer_fields')
    op.drop_index(op.f('ix_product_customer_fields_goods_id'), table_name='product_customer_fields')
    op.drop_table('product_customer_fields')
    
    op.drop_constraint('ck_goods_fulfillment_eta_positive', 'goods', type_='check')
    op.drop_constraint('ck_goods_fulfillment_mode', 'goods', type_='check')
    op.drop_column('goods', 'customer_input_intro_i18n')
    op.drop_column('goods', 'manual_instructions_i18n')
    op.drop_column('goods', 'fulfillment_eta_minutes')
    op.drop_column('goods', 'fulfillment_mode')
