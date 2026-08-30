"""Add project workflow file

Revision ID: 021_project_workflow_file
Revises: 021_promotion_candidates
Create Date: 2026-06-19
"""

from alembic import op


revision = "021_project_workflow_file"
down_revision = "021_promotion_candidates"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS workflow_file TEXT")


def downgrade():
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS workflow_file")
