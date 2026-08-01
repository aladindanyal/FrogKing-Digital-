"""add Goods.is_enabled

Revision ID: 9d4ec8dd04f1
Revises: d37f2dfbf779
Create Date: 2026-07-31 16:38:38.296652

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d4ec8dd04f1'
down_revision: Union[str, None] = 'd37f2dfbf779'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('goods', sa.Column('is_enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False))


def downgrade() -> None:
    op.drop_column('goods', 'is_enabled')
