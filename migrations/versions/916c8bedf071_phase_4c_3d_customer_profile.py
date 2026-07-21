"""phase_4c_3d_customer_profile

Revision ID: 916c8bedf071
Revises: 0a6d0158839c
Create Date: 2026-07-18 19:30:19.197939

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '916c8bedf071'
down_revision: Union[str, None] = '0a6d0158839c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('telegram_username', sa.String(length=64), nullable=True))
    op.add_column('users', sa.Column('first_name', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('last_name', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('profile_updated_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_users_telegram_username'), 'users', ['telegram_username'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_users_telegram_username'), table_name='users')
    op.drop_column('users', 'profile_updated_at')
    op.drop_column('users', 'last_name')
    op.drop_column('users', 'first_name')
    op.drop_column('users', 'telegram_username')
