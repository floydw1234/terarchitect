"""Add ticket attempt lifecycle columns

Revision ID: 020_ticket_attempt_lifecycle_columns
Revises: 019_ticket_attempt_defaults_metadata
Create Date: 2026-06-16
"""

from alembic import op


revision = "020_ticket_attempt_lifecycle_columns"
down_revision = "019_ticket_attempt_defaults_metadata"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE ticket_attempts ADD COLUMN IF NOT EXISTS validated_at TIMESTAMP")
    op.execute("ALTER TABLE ticket_attempts ADD COLUMN IF NOT EXISTS is_winner BOOLEAN")
    op.execute("ALTER TABLE ticket_attempts ADD COLUMN IF NOT EXISTS winner_chosen_at TIMESTAMP")
    op.execute("ALTER TABLE ticket_attempts ADD COLUMN IF NOT EXISTS integrated_at TIMESTAMP")
    op.execute("ALTER TABLE ticket_attempts ADD COLUMN IF NOT EXISTS integrated_frontier_id VARCHAR(255)")


def downgrade():
    op.execute("ALTER TABLE ticket_attempts DROP COLUMN IF EXISTS integrated_frontier_id")
    op.execute("ALTER TABLE ticket_attempts DROP COLUMN IF EXISTS integrated_at")
    op.execute("ALTER TABLE ticket_attempts DROP COLUMN IF EXISTS winner_chosen_at")
    op.execute("ALTER TABLE ticket_attempts DROP COLUMN IF EXISTS is_winner")
    op.execute("ALTER TABLE ticket_attempts DROP COLUMN IF EXISTS validated_at")
