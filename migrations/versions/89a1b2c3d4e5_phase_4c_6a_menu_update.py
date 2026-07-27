"""phase_4c_6a_menu_update

Revision ID: 89a1b2c3d4e5
Revises: 58d07df9fd2c
Create Date: 2026-07-27 22:07:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '89a1b2c3d4e5'
down_revision: Union[str, None] = '58d07df9fd2c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Disable terms, support, and operation history (if present in main menu)
    op.execute("""
        UPDATE main_menu_button_settings 
        SET is_enabled = false 
        WHERE action_key IN ('terms', 'support', 'wallet_history', 'operation_history')
    """)

    # Update positions to match exactly:
    # 1. 🔥 Popular Deals (popular_deals) -> row 1, col 1
    # 2. 🛍️ Shop (shop) -> row 2, col 1
    # 3. 💳 Add Funds (wallet) -> row 3, col 1
    # 4. 👑 My Account (profile) -> row 3, col 2
    # 5. 🌐 Language (language) -> row 4, col 1
    # 6. 🚀 Promo Code (promo) -> row 4, col 2

    # Popular Deals
    op.execute("UPDATE main_menu_button_settings SET row_order = 1, column_order = 1 WHERE action_key = 'popular_deals'")
    
    # Shop
    op.execute("UPDATE main_menu_button_settings SET row_order = 2, column_order = 1 WHERE action_key = 'shop'")
    
    # Wallet / Add Funds
    op.execute("UPDATE main_menu_button_settings SET label_en = '💳 Add Funds', label_ar = '💳 إضافة رصيد', row_order = 3, column_order = 1 WHERE action_key = 'wallet'")
    
    # Profile / My Account
    op.execute("UPDATE main_menu_button_settings SET label_en = '👑 My Account', label_ar = '👑 حسابي', row_order = 3, column_order = 2 WHERE action_key = 'profile'")
    
    # Language
    op.execute("UPDATE main_menu_button_settings SET row_order = 4, column_order = 1 WHERE action_key = 'language'")
    
    # Promo Code
    op.execute("UPDATE main_menu_button_settings SET row_order = 4, column_order = 2 WHERE action_key = 'promo'")
    
    # Admin Panel (keep on row 5)
    op.execute("UPDATE main_menu_button_settings SET row_order = 5, column_order = 1 WHERE action_key = 'admin'")


def downgrade() -> None:
    # Just a best-effort rollback to the old layout
    op.execute("""
        UPDATE main_menu_button_settings 
        SET is_enabled = true 
        WHERE action_key IN ('terms', 'support')
    """)
    
    op.execute("UPDATE main_menu_button_settings SET row_order = 0, column_order = 1 WHERE action_key = 'popular_deals'")
    op.execute("UPDATE main_menu_button_settings SET row_order = 1, column_order = 1 WHERE action_key = 'shop'")
    op.execute("UPDATE main_menu_button_settings SET row_order = 2, column_order = 1 WHERE action_key = 'wallet'")
    op.execute("UPDATE main_menu_button_settings SET row_order = 2, column_order = 2 WHERE action_key = 'profile'")
    op.execute("UPDATE main_menu_button_settings SET row_order = 3, column_order = 1 WHERE action_key = 'support'")
    op.execute("UPDATE main_menu_button_settings SET row_order = 3, column_order = 2 WHERE action_key = 'language'")
    op.execute("UPDATE main_menu_button_settings SET row_order = 4, column_order = 1 WHERE action_key = 'terms'")
    op.execute("UPDATE main_menu_button_settings SET row_order = 4, column_order = 2 WHERE action_key = 'promo'")
    op.execute("UPDATE main_menu_button_settings SET row_order = 5, column_order = 1 WHERE action_key = 'admin'")
