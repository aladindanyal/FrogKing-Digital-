"""phase_6a_broadcast_tables

Revision ID: 4a2b3c4d5e6f
Revises: 3f820c7a5211
Create Date: 2026-08-18 16:04:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4a2b3c4d5e6f'
down_revision = '3f820c7a5211'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create broadcast_campaigns table
    op.create_table('broadcast_campaigns',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('admin_id', sa.BigInteger(), nullable=True),
        sa.Column('target_locale', sa.String(length=16), nullable=True),
        sa.Column('message_text', sa.Text(), nullable=True),
        sa.Column('photo_file_id', sa.Text(), nullable=True),
        sa.Column('parse_mode', sa.String(length=16), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('run_started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('run_ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('draft', 'confirmed', 'running', 'completed', 'cancelled', 'failed')", name='ck_broadcast_campaign_status'),
        sa.ForeignKeyConstraint(['admin_id'], ['users.telegram_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_broadcast_campaign_single_active ON broadcast_campaigns ((1)) WHERE status IN ('confirmed', 'running');"
    )

    # Create broadcast_recipients table
    op.create_table('broadcast_recipients',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('campaign_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'sending', 'sent', 'failed', 'blocked', 'cancelled', 'uncertain')", name='ck_broadcast_recipient_status'),
        sa.ForeignKeyConstraint(['campaign_id'], ['broadcast_campaigns.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.telegram_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('campaign_id', 'user_id', name='uq_broadcast_recipient_campaign_user')
    )
    op.create_index('ix_broadcast_recipients_campaign_status', 'broadcast_recipients', ['campaign_id', 'status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_broadcast_recipients_campaign_status', table_name='broadcast_recipients')
    op.drop_table('broadcast_recipients')
    op.execute('DROP INDEX IF EXISTS uq_broadcast_campaign_single_active;')
    op.drop_table('broadcast_campaigns')
