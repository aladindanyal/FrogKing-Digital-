"""phase 5c-2 locales

Revision ID: 3f820c7a5211
Revises: ba8005f1874a
Create Date: 2026-08-12 14:42:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f820c7a5211'
down_revision: Union[str, None] = 'ba8005f1874a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # StoreSettings (25)
    op.add_column('store_settings', sa.Column('shop_root_title_ru', sa.String(length=255), nullable=True))
    op.add_column('store_settings', sa.Column('shop_root_title_zh', sa.String(length=255), nullable=True))
    op.add_column('store_settings', sa.Column('shop_root_title_vi', sa.String(length=255), nullable=True))
    op.add_column('store_settings', sa.Column('shop_root_title_tr', sa.String(length=255), nullable=True))
    op.add_column('store_settings', sa.Column('shop_root_title_es', sa.String(length=255), nullable=True))

    op.add_column('store_settings', sa.Column('shop_root_description_ru', sa.Text(), nullable=True))
    op.add_column('store_settings', sa.Column('shop_root_description_zh', sa.Text(), nullable=True))
    op.add_column('store_settings', sa.Column('shop_root_description_vi', sa.Text(), nullable=True))
    op.add_column('store_settings', sa.Column('shop_root_description_tr', sa.Text(), nullable=True))
    op.add_column('store_settings', sa.Column('shop_root_description_es', sa.Text(), nullable=True))

    op.add_column('store_settings', sa.Column('main_menu_title_ru', sa.String(length=255), nullable=True))
    op.add_column('store_settings', sa.Column('main_menu_title_zh', sa.String(length=255), nullable=True))
    op.add_column('store_settings', sa.Column('main_menu_title_vi', sa.String(length=255), nullable=True))
    op.add_column('store_settings', sa.Column('main_menu_title_tr', sa.String(length=255), nullable=True))
    op.add_column('store_settings', sa.Column('main_menu_title_es', sa.String(length=255), nullable=True))

    op.add_column('store_settings', sa.Column('main_menu_description_ru', sa.Text(), nullable=True))
    op.add_column('store_settings', sa.Column('main_menu_description_zh', sa.Text(), nullable=True))
    op.add_column('store_settings', sa.Column('main_menu_description_vi', sa.Text(), nullable=True))
    op.add_column('store_settings', sa.Column('main_menu_description_tr', sa.Text(), nullable=True))
    op.add_column('store_settings', sa.Column('main_menu_description_es', sa.Text(), nullable=True))

    op.add_column('store_settings', sa.Column('main_menu_footer_ru', sa.String(length=255), nullable=True))
    op.add_column('store_settings', sa.Column('main_menu_footer_zh', sa.String(length=255), nullable=True))
    op.add_column('store_settings', sa.Column('main_menu_footer_vi', sa.String(length=255), nullable=True))
    op.add_column('store_settings', sa.Column('main_menu_footer_tr', sa.String(length=255), nullable=True))
    op.add_column('store_settings', sa.Column('main_menu_footer_es', sa.String(length=255), nullable=True))

    # MainMenuButtonSettings (5)
    op.add_column('main_menu_button_settings', sa.Column('label_ru', sa.String(length=255), nullable=True))
    op.add_column('main_menu_button_settings', sa.Column('label_zh', sa.String(length=255), nullable=True))
    op.add_column('main_menu_button_settings', sa.Column('label_vi', sa.String(length=255), nullable=True))
    op.add_column('main_menu_button_settings', sa.Column('label_tr', sa.String(length=255), nullable=True))
    op.add_column('main_menu_button_settings', sa.Column('label_es', sa.String(length=255), nullable=True))

    # Categories (10)
    op.add_column('categories', sa.Column('name_ru', sa.String(length=100), nullable=True))
    op.add_column('categories', sa.Column('name_zh', sa.String(length=100), nullable=True))
    op.add_column('categories', sa.Column('name_vi', sa.String(length=100), nullable=True))
    op.add_column('categories', sa.Column('name_tr', sa.String(length=100), nullable=True))
    op.add_column('categories', sa.Column('name_es', sa.String(length=100), nullable=True))

    op.add_column('categories', sa.Column('description_ru', sa.Text(), nullable=True))
    op.add_column('categories', sa.Column('description_zh', sa.Text(), nullable=True))
    op.add_column('categories', sa.Column('description_vi', sa.Text(), nullable=True))
    op.add_column('categories', sa.Column('description_tr', sa.Text(), nullable=True))
    op.add_column('categories', sa.Column('description_es', sa.Text(), nullable=True))

    # Goods (10)
    op.add_column('goods', sa.Column('name_ru', sa.String(length=100), nullable=True))
    op.add_column('goods', sa.Column('name_zh', sa.String(length=100), nullable=True))
    op.add_column('goods', sa.Column('name_vi', sa.String(length=100), nullable=True))
    op.add_column('goods', sa.Column('name_tr', sa.String(length=100), nullable=True))
    op.add_column('goods', sa.Column('name_es', sa.String(length=100), nullable=True))

    op.add_column('goods', sa.Column('description_ru', sa.Text(), nullable=True))
    op.add_column('goods', sa.Column('description_zh', sa.Text(), nullable=True))
    op.add_column('goods', sa.Column('description_vi', sa.Text(), nullable=True))
    op.add_column('goods', sa.Column('description_tr', sa.Text(), nullable=True))
    op.add_column('goods', sa.Column('description_es', sa.Text(), nullable=True))


def downgrade() -> None:
    # Goods (10)
    op.drop_column('goods', 'description_es')
    op.drop_column('goods', 'description_tr')
    op.drop_column('goods', 'description_vi')
    op.drop_column('goods', 'description_zh')
    op.drop_column('goods', 'description_ru')

    op.drop_column('goods', 'name_es')
    op.drop_column('goods', 'name_tr')
    op.drop_column('goods', 'name_vi')
    op.drop_column('goods', 'name_zh')
    op.drop_column('goods', 'name_ru')

    # Categories (10)
    op.drop_column('categories', 'description_es')
    op.drop_column('categories', 'description_tr')
    op.drop_column('categories', 'description_vi')
    op.drop_column('categories', 'description_zh')
    op.drop_column('categories', 'description_ru')

    op.drop_column('categories', 'name_es')
    op.drop_column('categories', 'name_tr')
    op.drop_column('categories', 'name_vi')
    op.drop_column('categories', 'name_zh')
    op.drop_column('categories', 'name_ru')

    # MainMenuButtonSettings (5)
    op.drop_column('main_menu_button_settings', 'label_es')
    op.drop_column('main_menu_button_settings', 'label_tr')
    op.drop_column('main_menu_button_settings', 'label_vi')
    op.drop_column('main_menu_button_settings', 'label_zh')
    op.drop_column('main_menu_button_settings', 'label_ru')

    # StoreSettings (25)
    op.drop_column('store_settings', 'main_menu_footer_es')
    op.drop_column('store_settings', 'main_menu_footer_tr')
    op.drop_column('store_settings', 'main_menu_footer_vi')
    op.drop_column('store_settings', 'main_menu_footer_zh')
    op.drop_column('store_settings', 'main_menu_footer_ru')

    op.drop_column('store_settings', 'main_menu_description_es')
    op.drop_column('store_settings', 'main_menu_description_tr')
    op.drop_column('store_settings', 'main_menu_description_vi')
    op.drop_column('store_settings', 'main_menu_description_zh')
    op.drop_column('store_settings', 'main_menu_description_ru')

    op.drop_column('store_settings', 'main_menu_title_es')
    op.drop_column('store_settings', 'main_menu_title_tr')
    op.drop_column('store_settings', 'main_menu_title_vi')
    op.drop_column('store_settings', 'main_menu_title_zh')
    op.drop_column('store_settings', 'main_menu_title_ru')

    op.drop_column('store_settings', 'shop_root_description_es')
    op.drop_column('store_settings', 'shop_root_description_tr')
    op.drop_column('store_settings', 'shop_root_description_vi')
    op.drop_column('store_settings', 'shop_root_description_zh')
    op.drop_column('store_settings', 'shop_root_description_ru')

    op.drop_column('store_settings', 'shop_root_title_es')
    op.drop_column('store_settings', 'shop_root_title_tr')
    op.drop_column('store_settings', 'shop_root_title_vi')
    op.drop_column('store_settings', 'shop_root_title_zh')
    op.drop_column('store_settings', 'shop_root_title_ru')
