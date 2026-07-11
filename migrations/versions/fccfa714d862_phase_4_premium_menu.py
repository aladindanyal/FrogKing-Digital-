"""phase_4_premium_menu

Revision ID: fccfa714d862
Revises: ae15bd167ffb
Create Date: 2026-07-09 23:13:48.987059

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
"""phase_4_premium_menu

Revision ID: fccfa714d862
Revises: ae15bd167ffb
Create Date: 2026-07-09 23:13:48.987059

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fccfa714d862'
down_revision: Union[str, None] = 'ae15bd167ffb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('store_settings', sa.Column('main_menu_title', sa.String(length=255), nullable=True))
    op.add_column('store_settings', sa.Column('main_menu_description', sa.Text(), nullable=True))
    op.add_column('store_settings', sa.Column('main_menu_image_path', sa.String(length=500), nullable=True))
    op.add_column('store_settings', sa.Column('main_menu_image_url', sa.String(length=500), nullable=True))
    op.add_column('store_settings', sa.Column('main_menu_footer', sa.String(length=255), nullable=True))

def downgrade() -> None:
    op.drop_column('store_settings', 'main_menu_footer')
    op.drop_column('store_settings', 'main_menu_image_url')
    op.drop_column('store_settings', 'main_menu_image_path')
    op.drop_column('store_settings', 'main_menu_description')
    op.drop_column('store_settings', 'main_menu_title')
