"""phase_4c_3c_durable_conversation

Revision ID: 0a6d0158839c
Revises: 64304ef6db38
Create Date: 2026-07-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0a6d0158839c'
down_revision: Union[str, None] = '64304ef6db38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('manual_order_conversation_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('fulfillment_job_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='active', nullable=False),
        sa.Column('opened_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_activity_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('active', 'closed', 'expired')", name='ck_mocs_status'),
        sa.ForeignKeyConstraint(['fulfillment_job_id'], ['manual_fulfillment_jobs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['telegram_id'], ['users.telegram_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_manual_order_conversation_sessions_fulfillment_job_id'), 'manual_order_conversation_sessions', ['fulfillment_job_id'], unique=False)
    op.create_index(op.f('ix_manual_order_conversation_sessions_order_id'), 'manual_order_conversation_sessions', ['order_id'], unique=False)
    op.create_index(op.f('ix_manual_order_conversation_sessions_telegram_id'), 'manual_order_conversation_sessions', ['telegram_id'], unique=False)
    op.create_index('ix_manual_order_conversation_sessions_active_user', 'manual_order_conversation_sessions', ['telegram_id'], unique=True, postgresql_where=sa.text("status = 'active'"))


def downgrade() -> None:
    op.drop_index('ix_manual_order_conversation_sessions_active_user', table_name='manual_order_conversation_sessions', postgresql_where=sa.text("status = 'active'"))
    op.drop_index(op.f('ix_manual_order_conversation_sessions_telegram_id'), table_name='manual_order_conversation_sessions')
    op.drop_index(op.f('ix_manual_order_conversation_sessions_order_id'), table_name='manual_order_conversation_sessions')
    op.drop_index(op.f('ix_manual_order_conversation_sessions_fulfillment_job_id'), table_name='manual_order_conversation_sessions')
    op.drop_table('manual_order_conversation_sessions')
