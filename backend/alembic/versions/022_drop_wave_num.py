"""Drop unused wave_num columns from ticket_attempts and ship_runs.

Revision ID: 022_drop_wave_num
Revises: 021_project_workflow_file
Create Date: 2026-08-31
"""

from alembic import op


revision = "022_drop_wave_num"
down_revision = "021_project_workflow_file"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DROP INDEX IF EXISTS idx_ticket_attempts_wave")
    op.execute("ALTER TABLE ticket_attempts DROP COLUMN IF EXISTS wave_num")
    op.execute("ALTER TABLE ship_runs DROP COLUMN IF EXISTS wave_num")


def downgrade():
    op.execute("ALTER TABLE ticket_attempts ADD COLUMN IF NOT EXISTS wave_num INTEGER DEFAULT 0")
    op.execute("ALTER TABLE ship_runs ADD COLUMN IF NOT EXISTS wave_num INTEGER NOT NULL DEFAULT 0")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ticket_attempts_wave ON ticket_attempts(project_id, wave_num)"
    )
