"""Add project workflow file

Revision ID: 021_project_workflow_file
Revises: 020_ticket_attempt_lifecycle_columns
Create Date: 2026-06-19
"""

from alembic import op


revision = "021_project_workflow_file"
down_revision = "020_ticket_attempt_lifecycle_columns"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS workflow_file TEXT")


def downgrade():
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS workflow_file")
