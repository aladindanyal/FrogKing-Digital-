"""fix_bought_goods_schema

Revision ID: aed0950a0170
Revises: 88fa0a98da99
Create Date: 2026-07-12 19:51:14.870386

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aed0950a0170'
down_revision: Union[str, None] = '88fa0a98da99'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Repair drift without duplicating the schema created by 88fa0a98da99."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("bought_goods")}

    if "order_id" not in columns:
        op.add_column(
            "bought_goods",
            sa.Column("order_id", sa.Integer(), nullable=True),
        )

    if "order_item_id" not in columns:
        op.add_column(
            "bought_goods",
            sa.Column("order_item_id", sa.Integer(), nullable=True),
        )

    inspector = sa.inspect(bind)
    indexes = {
        index["name"]
        for index in inspector.get_indexes("bought_goods")
        if index.get("name")
    }

    if "ix_bought_goods_order_id" not in indexes:
        op.create_index(
            "ix_bought_goods_order_id",
            "bought_goods",
            ["order_id"],
            unique=False,
        )

    if "ix_bought_goods_order_item_id" not in indexes:
        op.create_index(
            "ix_bought_goods_order_item_id",
            "bought_goods",
            ["order_item_id"],
            unique=False,
        )

    inspector = sa.inspect(bind)
    foreign_keys = {
        (
            tuple(foreign_key.get("constrained_columns") or ()),
            foreign_key.get("referred_table"),
            tuple(foreign_key.get("referred_columns") or ()),
        )
        for foreign_key in inspector.get_foreign_keys("bought_goods")
    }

    order_fk = (("order_id",), "orders", ("id",))
    if order_fk not in foreign_keys:
        op.create_foreign_key(
            "fk_bought_goods_order_id",
            "bought_goods",
            "orders",
            ["order_id"],
            ["id"],
            ondelete="SET NULL",
        )

    order_item_fk = (("order_item_id",), "order_items", ("id",))
    if order_item_fk not in foreign_keys:
        op.create_foreign_key(
            "fk_bought_goods_order_item_id",
            "bought_goods",
            "order_items",
            ["order_item_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    # Revision 88fa0a98da99 owns these columns, indexes, and foreign keys.
    # Returning to that revision must preserve its schema.
    pass
