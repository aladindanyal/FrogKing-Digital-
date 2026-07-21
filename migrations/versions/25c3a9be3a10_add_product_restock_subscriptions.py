"""add_product_restock_subscriptions

Revision ID: 25c3a9be3a10
Revises: 00047f478dbb
Create Date: 2026-07-12 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '25c3a9be3a10'
down_revision: Union[str, None] = '00047f478dbb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'product_restock_subscriptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('processing_started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['item_id'], ['goods.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.telegram_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'item_id', name='uq_restock_sub_user_item')
    )
    op.create_index('ix_restock_sub_item_status', 'product_restock_subscriptions', ['item_id', 'status'], unique=False)
    op.create_index('ix_restock_sub_status', 'product_restock_subscriptions', ['status'], unique=False)
    op.create_index(op.f('ix_product_restock_subscriptions_item_id'), 'product_restock_subscriptions', ['item_id'], unique=False)
    op.create_index(op.f('ix_product_restock_subscriptions_user_id'), 'product_restock_subscriptions', ['user_id'], unique=False)
    op.create_index(op.f('ix_product_restock_subscriptions_status'), 'product_restock_subscriptions', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_product_restock_subscriptions_status'), table_name='product_restock_subscriptions')
    op.drop_index(op.f('ix_product_restock_subscriptions_user_id'), table_name='product_restock_subscriptions')
    op.drop_index(op.f('ix_product_restock_subscriptions_item_id'), table_name='product_restock_subscriptions')
    op.drop_index('ix_restock_sub_status', table_name='product_restock_subscriptions')
    op.drop_index('ix_restock_sub_item_status', table_name='product_restock_subscriptions')
    op.drop_table('product_restock_subscriptions')
