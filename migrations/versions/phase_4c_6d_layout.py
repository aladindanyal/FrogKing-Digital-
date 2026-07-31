"""Add category button layout

Revision ID: phase_4c_6d
Revises: 7d30a688d18a
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'phase_4c_6d'
down_revision: Union[str, None] = '7d30a688d18a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add StoreSettings.root_category_buttons_per_row
    op.add_column('store_settings', sa.Column('root_category_buttons_per_row', sa.Integer(), server_default='1', nullable=False))

    # 2. Add Categories.children_buttons_per_row
    op.add_column('categories', sa.Column('children_buttons_per_row', sa.Integer(), server_default='1', nullable=False))

    # 5. Add database-level validation ensuring only 1 or 2 are accepted.
    op.create_check_constraint('ck_store_settings_root_btns', 'store_settings', 'root_category_buttons_per_row IN (1, 2)')
    op.create_check_constraint('ck_categories_children_btns', 'categories', 'children_buttons_per_row IN (1, 2)')


def downgrade() -> None:
    # 9. Downgrade must remove only: the two new constraints, the two new columns
    op.drop_constraint('ck_categories_children_btns', 'categories', type_='check')
    op.drop_constraint('ck_store_settings_root_btns', 'store_settings', type_='check')
    op.drop_column('categories', 'children_buttons_per_row')
    op.drop_column('store_settings', 'root_category_buttons_per_row')
