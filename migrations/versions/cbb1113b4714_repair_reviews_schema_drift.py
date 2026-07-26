"""repair_reviews_schema_drift

Revision ID: cbb1113b4714
Revises: 1eadcdac923e
Create Date: 2026-07-22 12:50:31.858375

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision: str = 'cbb1113b4714'
down_revision: Union[str, None] = '1eadcdac923e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    
    columns = [col['name'] for col in inspector.get_columns('reviews')]
    
    # Handle text -> comment rename
    if 'text' in columns and 'comment' not in columns:
        op.alter_column('reviews', 'text', new_column_name='comment')
    elif 'text' in columns and 'comment' in columns:
        # Both exist? Unlikely unless messed up, just drop text if comment is there
        pass

    # Re-inspect after column changes
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('reviews')]

    # Add missing columns
    if 'product_id' not in columns:
        op.add_column('reviews', sa.Column('product_id', sa.Integer(), nullable=True))
    if 'order_id' not in columns:
        op.add_column('reviews', sa.Column('order_id', sa.Integer(), nullable=True))
    if 'order_item_id' not in columns:
        op.add_column('reviews', sa.Column('order_item_id', sa.Integer(), nullable=True))
    if 'status' not in columns:
        op.add_column('reviews', sa.Column('status', sa.String(length=20), server_default='pending', nullable=False))
    if 'is_featured' not in columns:
        op.add_column('reviews', sa.Column('is_featured', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    if 'admin_reply' not in columns:
        op.add_column('reviews', sa.Column('admin_reply', sa.Text(), nullable=True))
    if 'updated_at' not in columns:
        op.add_column('reviews', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    if 'moderated_at' not in columns:
        op.add_column('reviews', sa.Column('moderated_at', sa.DateTime(timezone=True), nullable=True))
    if 'moderated_by' not in columns:
        op.add_column('reviews', sa.Column('moderated_by', sa.BigInteger(), nullable=True))

    # Re-inspect to check constraints, indexes, fks
    inspector = sa.inspect(conn)
    
    # Nullability for item_name
    item_name_col = next((c for c in inspector.get_columns('reviews') if c['name'] == 'item_name'), None)
    if item_name_col and not item_name_col.get('nullable', True):
        op.alter_column('reviews', 'item_name', nullable=True)

    # Unique Constraints
    unique_constraints = [uc['name'] for uc in inspector.get_unique_constraints('reviews')]
    if 'uq_review_per_user_item' in unique_constraints:
        op.drop_constraint('uq_review_per_user_item', 'reviews', type_='unique')
    
    # Indexes
    indexes = [ix['name'] for ix in inspector.get_indexes('reviews')]
    if 'ix_reviews_created_at' not in indexes:
        op.create_index('ix_reviews_created_at', 'reviews', ['created_at'], unique=False)
    if 'ix_reviews_is_featured' not in indexes:
        op.create_index('ix_reviews_is_featured', 'reviews', ['is_featured'], unique=False)
    if 'ix_reviews_order_id' not in indexes:
        op.create_index('ix_reviews_order_id', 'reviews', ['order_id'], unique=False)
    if 'ix_reviews_order_item_id' not in indexes:
        op.create_index('ix_reviews_order_item_id', 'reviews', ['order_item_id'], unique=True)
    if 'ix_reviews_product_id' not in indexes:
        op.create_index('ix_reviews_product_id', 'reviews', ['product_id'], unique=False)
    if 'ix_reviews_status' not in indexes:
        op.create_index('ix_reviews_status', 'reviews', ['status'], unique=False)

    # Check Constraint for Status
    # SQLAlchemy reflection for Check Constraints can be tricky, but we can query pg_catalog
    status_ck_exists = False
    has_correct_status_ck = False
    status_ck_name = None

    if conn.dialect.name == 'postgresql':
        check_query = sa.text('''
            SELECT conname, pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            JOIN pg_class t ON c.conrelid = t.oid
            WHERE t.relname = 'reviews' AND c.contype = 'c';
        ''')
        check_constraints = conn.execute(check_query).fetchall()
        
        for name, definition in check_constraints:
            if 'status' in definition.lower() or name == 'ck_review_status_enum':
                status_ck_exists = True
                status_ck_name = name
                if "'pending'" in definition and "'approved'" in definition and "'hidden'" in definition and "'rejected'" in definition:
                    has_correct_status_ck = True

    if status_ck_exists and not has_correct_status_ck:
        op.drop_constraint(status_ck_name, 'reviews', type_='check')
        status_ck_exists = False

    if not status_ck_exists:
        op.create_check_constraint('ck_review_status_enum', 'reviews', "status IN ('pending', 'approved', 'hidden', 'rejected')")

    # Foreign Keys
    fks = [fk['name'] for fk in inspector.get_foreign_keys('reviews')]
    if 'fk_reviews_moderated_by' not in fks:
        op.create_foreign_key('fk_reviews_moderated_by', 'reviews', 'users', ['moderated_by'], ['telegram_id'], ondelete='SET NULL')
    if 'fk_reviews_product_id' not in fks:
        op.create_foreign_key('fk_reviews_product_id', 'reviews', 'goods', ['product_id'], ['id'], ondelete='CASCADE')
    if 'fk_reviews_order_id' not in fks:
        op.create_foreign_key('fk_reviews_order_id', 'reviews', 'orders', ['order_id'], ['id'], ondelete='CASCADE')
    if 'fk_reviews_order_item_id' not in fks:
        op.create_foreign_key('fk_reviews_order_item_id', 'reviews', 'order_items', ['order_item_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    # Downgrade is a no-op to preserve data.
    # The schema drift fix should not destroy legacy reviews if rolled back.
    pass
