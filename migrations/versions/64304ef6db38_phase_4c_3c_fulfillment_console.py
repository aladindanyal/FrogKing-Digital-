"""phase_4c_3c_fulfillment_console

Revision ID: 64304ef6db38
Revises: 64b3e0f731ab
Create Date: 2026-07-16 14:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '64304ef6db38'
down_revision: Union[str, None] = '64b3e0f731ab'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns to manual_fulfillment_jobs
    op.add_column('manual_fulfillment_jobs', sa.Column('started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('manual_fulfillment_jobs', sa.Column('waiting_customer_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('manual_fulfillment_jobs', sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('manual_fulfillment_jobs', sa.Column('started_by', sa.BigInteger(), nullable=True))
    op.add_column('manual_fulfillment_jobs', sa.Column('completed_by', sa.BigInteger(), nullable=True))
    op.create_foreign_key(None, 'manual_fulfillment_jobs', 'users', ['started_by'], ['telegram_id'], ondelete='SET NULL')
    op.create_foreign_key(None, 'manual_fulfillment_jobs', 'users', ['completed_by'], ['telegram_id'], ondelete='SET NULL')

    # Update check constraint
    op.execute("ALTER TABLE manual_fulfillment_jobs DROP CONSTRAINT ck_mfj_status")
    op.execute("ALTER TABLE manual_fulfillment_jobs ADD CONSTRAINT ck_mfj_status CHECK (status IN ('queued', 'in_progress', 'waiting_customer', 'completed', 'failed', 'cancelled'))")

    # Create manual_order_interactions table
    op.create_table('manual_order_interactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('fulfillment_job_id', sa.Integer(), nullable=False),
        sa.Column('direction', sa.String(length=20), nullable=False),
        sa.Column('kind', sa.String(length=30), nullable=False),
        sa.Column('encrypted_content', sa.Text(), nullable=True),
        sa.Column('safe_preview', sa.Text(), nullable=True),
        sa.Column('is_sensitive', sa.Boolean(), nullable=False, default=False),
        sa.Column('telegram_message_id', sa.BigInteger(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        sa.CheckConstraint("direction IN ('admin_to_customer', 'customer_to_admin', 'system')", name='ck_moi_direction'),
        sa.CheckConstraint("kind IN ('message', 'verification_request', 'customer_reply', 'status_change', 'completion')", name='ck_moi_kind'),
        sa.ForeignKeyConstraint(['created_by'], ['users.telegram_id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['fulfillment_job_id'], ['manual_fulfillment_jobs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_manual_order_interactions_fulfillment_job_id'), 'manual_order_interactions', ['fulfillment_job_id'], unique=False)
    op.create_index(op.f('ix_manual_order_interactions_order_id'), 'manual_order_interactions', ['order_id'], unique=False)

    # Create manual_order_notifications table
    op.create_table('manual_order_notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('fulfillment_job_id', sa.Integer(), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name='ck_mon_status'),
        sa.ForeignKeyConstraint(['fulfillment_job_id'], ['manual_fulfillment_jobs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_manual_order_notifications_fulfillment_job_id'), 'manual_order_notifications', ['fulfillment_job_id'], unique=False)
    op.create_index(op.f('ix_manual_order_notifications_idempotency_key'), 'manual_order_notifications', ['idempotency_key'], unique=True)
    op.create_index(op.f('ix_manual_order_notifications_order_id'), 'manual_order_notifications', ['order_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_manual_order_notifications_order_id'), table_name='manual_order_notifications')
    op.drop_index(op.f('ix_manual_order_notifications_idempotency_key'), table_name='manual_order_notifications')
    op.drop_index(op.f('ix_manual_order_notifications_fulfillment_job_id'), table_name='manual_order_notifications')
    op.drop_table('manual_order_notifications')

    op.drop_index(op.f('ix_manual_order_interactions_order_id'), table_name='manual_order_interactions')
    op.drop_index(op.f('ix_manual_order_interactions_fulfillment_job_id'), table_name='manual_order_interactions')
    op.drop_table('manual_order_interactions')

    op.execute("ALTER TABLE manual_fulfillment_jobs DROP CONSTRAINT ck_mfj_status")
    op.execute("ALTER TABLE manual_fulfillment_jobs ADD CONSTRAINT ck_mfj_status CHECK (status IN ('queued', 'processing', 'completed', 'failed', 'cancelled'))")

    op.drop_constraint(None, 'manual_fulfillment_jobs', type_='foreignkey')
    op.drop_constraint(None, 'manual_fulfillment_jobs', type_='foreignkey')
    op.drop_column('manual_fulfillment_jobs', 'completed_by')
    op.drop_column('manual_fulfillment_jobs', 'started_by')
    op.drop_column('manual_fulfillment_jobs', 'completed_at')
    op.drop_column('manual_fulfillment_jobs', 'waiting_customer_at')
    op.drop_column('manual_fulfillment_jobs', 'started_at')
