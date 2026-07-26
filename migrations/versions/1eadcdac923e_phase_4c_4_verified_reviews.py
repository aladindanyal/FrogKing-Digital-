"""phase_4c_4_verified_reviews

Revision ID: 1eadcdac923e
Revises: 7229a30d75b6
Create Date: 2026-07-21 18:48:33.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '1eadcdac923e'
down_revision: Union[str, None] = '7229a30d75b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop old constraint
    op.drop_constraint('uq_review_per_user_item', 'reviews', type_='unique')
    
    # Rename text to comment
    op.alter_column('reviews', 'text', new_column_name='comment')
    
    # Add new columns
    op.add_column('reviews', sa.Column('product_id', sa.Integer(), nullable=True))
    op.add_column('reviews', sa.Column('order_id', sa.Integer(), nullable=True))
    op.add_column('reviews', sa.Column('order_item_id', sa.Integer(), nullable=True))
    op.add_column('reviews', sa.Column('status', sa.String(length=20), server_default='pending', nullable=False))
    op.add_column('reviews', sa.Column('is_featured', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('reviews', sa.Column('admin_reply', sa.Text(), nullable=True))
    op.add_column('reviews', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    op.add_column('reviews', sa.Column('moderated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('reviews', sa.Column('moderated_by', sa.BigInteger(), nullable=True))
    
    # Allow item_name to be nullable
    op.alter_column('reviews', 'item_name', existing_type=sa.String(length=100), nullable=True)

    # Add foreign keys
    op.create_foreign_key('fk_reviews_product_id', 'reviews', 'goods', ['product_id'], ['id'], ondelete='CASCADE')
    op.create_foreign_key('fk_reviews_order_id', 'reviews', 'orders', ['order_id'], ['id'], ondelete='CASCADE')
    op.create_foreign_key('fk_reviews_order_item_id', 'reviews', 'order_items', ['order_item_id'], ['id'], ondelete='CASCADE')
    op.create_foreign_key('fk_reviews_moderated_by', 'reviews', 'users', ['moderated_by'], ['telegram_id'], ondelete='SET NULL')
    
    # Add unique constraint on order_item_id
    op.create_unique_constraint('uq_review_order_item_id', 'reviews', ['order_item_id'])
    
    # Add constraints and indexes
    op.create_check_constraint('ck_review_status_enum', 'reviews', "status IN ('pending', 'approved', 'hidden')")
    op.create_index('ix_reviews_product_id', 'reviews', ['product_id'], unique=False)
    op.create_index('ix_reviews_order_id', 'reviews', ['order_id'], unique=False)
    op.create_index('ix_reviews_order_item_id', 'reviews', ['order_item_id'], unique=False)
    op.create_index('ix_reviews_status', 'reviews', ['status'], unique=False)
    op.create_index('ix_reviews_is_featured', 'reviews', ['is_featured'], unique=False)
    op.create_index('ix_reviews_created_at', 'reviews', ['created_at'], unique=False)

def downgrade() -> None:
    op.drop_index('ix_reviews_created_at', table_name='reviews')
    op.drop_index('ix_reviews_is_featured', table_name='reviews')
    op.drop_index('ix_reviews_status', table_name='reviews')
    op.drop_index('ix_reviews_order_item_id', table_name='reviews')
    op.drop_index('ix_reviews_order_id', table_name='reviews')
    op.drop_index('ix_reviews_product_id', table_name='reviews')
    
    op.drop_constraint('ck_review_status_enum', 'reviews', type_='check')
    op.drop_constraint('uq_review_order_item_id', 'reviews', type_='unique')
    
    op.drop_constraint('fk_reviews_moderated_by', 'reviews', type_='foreignkey')
    op.drop_constraint('fk_reviews_order_item_id', 'reviews', type_='foreignkey')
    op.drop_constraint('fk_reviews_order_id', 'reviews', type_='foreignkey')
    op.drop_constraint('fk_reviews_product_id', 'reviews', type_='foreignkey')
    
    op.alter_column('reviews', 'item_name', existing_type=sa.String(length=100), nullable=False)
    
    op.drop_column('reviews', 'moderated_by')
    op.drop_column('reviews', 'moderated_at')
    op.drop_column('reviews', 'updated_at')
    op.drop_column('reviews', 'admin_reply')
    op.drop_column('reviews', 'is_featured')
    op.drop_column('reviews', 'status')
    op.drop_column('reviews', 'order_item_id')
    op.drop_column('reviews', 'order_id')
    op.drop_column('reviews', 'product_id')
    
    op.alter_column('reviews', 'comment', new_column_name='text')
    
    op.create_unique_constraint('uq_review_per_user_item', 'reviews', ['user_id', 'item_name'])
