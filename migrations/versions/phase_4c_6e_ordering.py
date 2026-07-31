"""phase_4c_6e

Revision ID: d37f2dfbf779
Revises: phase_4c_6d
Create Date: 2026-07-31 12:52:26.098001

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd37f2dfbf779'
down_revision: Union[str, None] = 'phase_4c_6d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add column nullable
    op.add_column('categories', sa.Column('display_order', sa.Integer(), nullable=True))

    # 2. Backfill existing categories maintaining current name ASC ordering
    connection = op.get_bind()
    connection.execute(sa.text("""
        UPDATE categories
        SET display_order = sub.new_order
        FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY COALESCE(parent_id, 0) ORDER BY name ASC) * 10 as new_order
            FROM categories
        ) sub
        WHERE categories.id = sub.id;
    """))

    # 3. Alter column to non-null and set default
    op.alter_column('categories', 'display_order',
                    existing_type=sa.Integer(),
                    nullable=False,
                    server_default='10')

    # 4. Add check constraint
    op.create_check_constraint('ck_categories_display_order', 'categories', 'display_order >= 0')


def downgrade() -> None:
    op.drop_constraint('ck_categories_display_order', 'categories', type_='check')
    op.drop_column('categories', 'display_order')
