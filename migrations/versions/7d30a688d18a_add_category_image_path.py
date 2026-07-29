"""add_image_path_to_categories

Revision ID: 7d30a688d18a
Revises: 66ff732b3fd7
Create Date: 2026-07-29 21:53:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '7d30a688d18a'
down_revision = '66ff732b3fd7'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column('categories', sa.Column('image_path', sa.String(255), nullable=True))

def downgrade() -> None:
    op.drop_column('categories', 'image_path')
