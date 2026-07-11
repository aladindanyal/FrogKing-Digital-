"""phase4b buttons

Revision ID: cbef3cb3bc82
Revises: fccfa714d862
Create Date: 2026-07-11 10:16:43.900070

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'cbef3cb3bc82'
down_revision: Union[str, None] = 'fccfa714d862'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Add root_category_columns to store_settings
    op.add_column('store_settings', sa.Column('root_category_columns', sa.Integer(), server_default='1', nullable=False))
    
    # CheckConstraint for root_category_columns
    op.create_check_constraint('ck_store_settings_root_cols', 'store_settings', 'root_category_columns IN (1, 2)')
    
    # Create main_menu_button_settings table
    main_menu_table = op.create_table('main_menu_button_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('action_key', sa.String(length=50), nullable=False),
        sa.Column('label_en', sa.String(length=255), nullable=True),
        sa.Column('label_ar', sa.String(length=255), nullable=True),
        sa.Column('is_enabled', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('row_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('column_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('owner_only', sa.Boolean(), server_default='false', nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_main_menu_button_settings_action_key'), 'main_menu_button_settings', ['action_key'], unique=True)
    
    op.execute("""
        INSERT INTO main_menu_button_settings (action_key, label_en, label_ar, row_order, column_order, is_enabled, owner_only)
        VALUES 
            ('shop', '🛒 Shop', '🛒 المتجر', 1, 1, true, false),
            ('wallet', '💳 Wallet', '💳 المحفظة', 2, 1, true, false),
            ('profile', '👤 Profile', '👤 حسابي', 2, 2, true, false),
            ('support', '🆘 Support', '🆘 الدعم', 3, 1, true, false),
            ('language', '🌐 Language', '🌐 اللغة', 3, 2, true, false),
            ('terms', '📜 Terms', '📜 الشروط', 4, 1, true, false),
            ('promo', '🔥 Promo Code', '🔥 كود الخصم', 4, 2, true, false),
            ('admin', '🎛 Admin Panel', '🎛 لوحة الإدارة', 5, 1, true, true)
        ON CONFLICT (action_key) DO NOTHING;
    """)

def downgrade() -> None:
    op.drop_index(op.f('ix_main_menu_button_settings_action_key'), table_name='main_menu_button_settings')
    op.drop_table('main_menu_button_settings')
    op.drop_constraint('ck_store_settings_root_cols', 'store_settings', type_='check')
    op.drop_column('store_settings', 'root_category_columns')
