"""Add project source metadata fields

Revision ID: 018_project_source_metadata
Revises: 017_ticket_base_leaf
Create Date: 2026-06-11
"""

from alembic import op


revision = "018_project_source_metadata"
down_revision = "017_ticket_base_leaf"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS source_type VARCHAR(50)")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS github_ref VARCHAR(255)")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS github_resolved_sha VARCHAR(255)")
    op.execute("UPDATE projects SET source_type = 'local_path' WHERE source_type IS NULL OR TRIM(source_type) = ''")
    op.execute("ALTER TABLE projects ALTER COLUMN source_type SET DEFAULT 'local_path'")
    op.execute("ALTER TABLE projects ALTER COLUMN source_type SET NOT NULL")


def downgrade():
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS github_resolved_sha")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS github_ref")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS source_type")
