"""phase_4c_5a_popular_deals

Revision ID: 58d07df9fd2c
Revises: cbb1113b4714
Create Date: 2026-07-27 14:27:52.691603

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '58d07df9fd2c'
down_revision: Union[str, None] = 'cbb1113b4714'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add columns to goods
    op.add_column('goods', sa.Column('is_popular_deal', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('goods', sa.Column('popular_deal_order', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_goods_is_popular_deal'), 'goods', ['is_popular_deal'], unique=False)
    op.create_check_constraint('ck_goods_popular_deal_order', 'goods', 'popular_deal_order IS NULL OR popular_deal_order >= 0')

    # Insert popular_deals button idempotently
    op.execute("""
        INSERT INTO main_menu_button_settings (action_key, label_en, label_ar, row_order, column_order, is_enabled, owner_only)
        VALUES ('popular_deals', '🔥 Popular Deals', '🔥 عروض مميزة', 0, 1, true, false)
        ON CONFLICT (action_key) DO NOTHING;
    """)

def downgrade() -> None:
    # Remove popular_deals button
    op.execute("DELETE FROM main_menu_button_settings WHERE action_key = 'popular_deals'")

    # Remove columns from goods
    op.drop_constraint('ck_goods_popular_deal_order', 'goods', type_='check')
    op.drop_index(op.f('ix_goods_is_popular_deal'), table_name='goods')
    op.drop_column('goods', 'popular_deal_order')
    op.drop_column('goods', 'is_popular_deal')
