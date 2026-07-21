"""repair_schema_drift

Revision ID: 7229a30d75b6
Revises: 916c8bedf071
Create Date: 2026-07-19 02:28:44.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

# revision identifiers, used by Alembic.
revision: str = '7229a30d75b6'
down_revision: Union[str, None] = '916c8bedf071'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    columns = [c['name'] for c in inspector.get_columns('users')]
    indexes = [i['name'] for i in inspector.get_indexes('users')]

    if 'telegram_username' not in columns:
        op.add_column('users', sa.Column('telegram_username', sa.String(length=64), nullable=True))
    if 'first_name' not in columns:
        op.add_column('users', sa.Column('first_name', sa.String(length=255), nullable=True))
    if 'last_name' not in columns:
        op.add_column('users', sa.Column('last_name', sa.String(length=255), nullable=True))
    if 'profile_updated_at' not in columns:
        op.add_column('users', sa.Column('profile_updated_at', sa.DateTime(timezone=True), nullable=True))
        
    if 'ix_users_telegram_username' not in indexes:
        op.create_index(op.f('ix_users_telegram_username'), 'users', ['telegram_username'], unique=False)

def downgrade() -> None:
    # Revision 916c8bedf071 already defines these objects.
    # Removing them here would recreate the schema drift.
    pass
