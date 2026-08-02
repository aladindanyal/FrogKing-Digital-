"""phase 5b add bilingual fields

Revision ID: ba8005f1874a
Revises: 23ba1f978d38
Create Date: 2026-08-03 01:25:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ba8005f1874a'
down_revision = '23ba1f978d38'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "store_settings",
        sa.Column("shop_root_title_en", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "store_settings",
        sa.Column("shop_root_title_ar", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "store_settings",
        sa.Column("shop_root_description_en", sa.Text(), nullable=True),
    )
    op.add_column(
        "store_settings",
        sa.Column("shop_root_description_ar", sa.Text(), nullable=True),
    )
    op.add_column(
        "store_settings",
        sa.Column("main_menu_title_en", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "store_settings",
        sa.Column("main_menu_title_ar", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "store_settings",
        sa.Column("main_menu_description_en", sa.Text(), nullable=True),
    )
    op.add_column(
        "store_settings",
        sa.Column("main_menu_description_ar", sa.Text(), nullable=True),
    )
    op.add_column(
        "store_settings",
        sa.Column("main_menu_footer_en", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "store_settings",
        sa.Column("main_menu_footer_ar", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "categories",
        sa.Column("name_en", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "categories",
        sa.Column("name_ar", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "categories",
        sa.Column("description_en", sa.Text(), nullable=True),
    )
    op.add_column(
        "categories",
        sa.Column("description_ar", sa.Text(), nullable=True),
    )
    op.add_column(
        "goods",
        sa.Column("name_en", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "goods",
        sa.Column("name_ar", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "goods",
        sa.Column("description_en", sa.Text(), nullable=True),
    )
    op.add_column(
        "goods",
        sa.Column("description_ar", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("goods", "description_ar")
    op.drop_column("goods", "description_en")
    op.drop_column("goods", "name_ar")
    op.drop_column("goods", "name_en")

    op.drop_column("categories", "description_ar")
    op.drop_column("categories", "description_en")
    op.drop_column("categories", "name_ar")
    op.drop_column("categories", "name_en")

    op.drop_column("store_settings", "main_menu_footer_ar")
    op.drop_column("store_settings", "main_menu_footer_en")
    op.drop_column("store_settings", "main_menu_description_ar")
    op.drop_column("store_settings", "main_menu_description_en")
    op.drop_column("store_settings", "main_menu_title_ar")
    op.drop_column("store_settings", "main_menu_title_en")
    op.drop_column("store_settings", "shop_root_description_ar")
    op.drop_column("store_settings", "shop_root_description_en")
    op.drop_column("store_settings", "shop_root_title_ar")
    op.drop_column("store_settings", "shop_root_title_en")
