"""add_image_path_to_goods

Revision ID: 66ff732b3fd7
Revises: 89a1b2c3d4e5
Create Date: 2026-07-28 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '66ff732b3fd7'
down_revision = '89a1b2c3d4e5'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column('goods', sa.Column('image_path', sa.String(), nullable=True))

def downgrade() -> None:
    op.drop_column('goods', 'image_path')
