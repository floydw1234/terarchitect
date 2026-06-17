"""Add ticket attempt defaults and job attempt metadata

Revision ID: 019_ticket_attempt_defaults_metadata
Revises: 018_project_source_metadata
Create Date: 2026-06-16
"""

from alembic import op


revision = "019_ticket_attempt_defaults_metadata"
down_revision = "018_project_source_metadata"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS default_attempt_count INTEGER")
    op.execute("UPDATE tickets SET default_attempt_count = 3 WHERE default_attempt_count IS NULL OR default_attempt_count < 1")
    op.execute("ALTER TABLE tickets ALTER COLUMN default_attempt_count SET DEFAULT 3")
    op.execute("ALTER TABLE tickets ALTER COLUMN default_attempt_count SET NOT NULL")

    op.execute("ALTER TABLE agent_jobs ADD COLUMN IF NOT EXISTS attempt_metadata JSONB DEFAULT '{}'::jsonb")
    op.execute("UPDATE agent_jobs SET attempt_metadata = '{}'::jsonb WHERE attempt_metadata IS NULL")
    op.execute("ALTER TABLE agent_jobs ALTER COLUMN attempt_metadata SET DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE agent_jobs ALTER COLUMN attempt_metadata SET NOT NULL")


def downgrade():
    op.execute("ALTER TABLE agent_jobs DROP COLUMN IF EXISTS attempt_metadata")
    op.execute("ALTER TABLE tickets DROP COLUMN IF EXISTS default_attempt_count")
