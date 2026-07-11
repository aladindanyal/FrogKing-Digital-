"""storefront layout

Revision ID: 00047f478dbb
Revises: cbef3cb3bc82
Create Date: 2026-07-11 12:27:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '00047f478dbb'
down_revision: Union[str, None] = 'cbef3cb3bc82'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add columns with server_default so existing rows are safely populated
    op.add_column('store_settings', sa.Column('subcategory_columns', sa.Integer(), server_default="2", nullable=False))
    op.add_column('store_settings', sa.Column('product_columns', sa.Integer(), server_default="1", nullable=False))

    # Add constraints
    op.create_check_constraint('ck_store_settings_subcat_cols', 'store_settings', 'subcategory_columns IN (1, 2)')
    op.create_check_constraint('ck_store_settings_product_cols', 'store_settings', 'product_columns IN (1, 2)')


def downgrade() -> None:
    op.drop_constraint('ck_store_settings_product_cols', 'store_settings', type_='check')
    op.drop_constraint('ck_store_settings_subcat_cols', 'store_settings', type_='check')
    
    op.drop_column('store_settings', 'product_columns')
    op.drop_column('store_settings', 'subcategory_columns')
