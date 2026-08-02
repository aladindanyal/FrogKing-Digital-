"""add_language_code_to_user

Revision ID: 23ba1f978d38
Revises: 9d4ec8dd04f1
Create Date: 2026-08-02 14:21:16.847239

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '23ba1f978d38'
down_revision: Union[str, None] = '9d4ec8dd04f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('language_code', sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'language_code')
