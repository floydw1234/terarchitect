"""Add base_leaf_id to tickets

Revision ID: 017_ticket_base_leaf
Revises: 016_project_accepted_frontier
Create Date: 2026-06-11
"""

from alembic import op


revision = "017_ticket_base_leaf"
down_revision = "016_project_accepted_frontier"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS base_leaf_id VARCHAR(255)")


def downgrade():
    op.execute("ALTER TABLE tickets DROP COLUMN IF EXISTS base_leaf_id")
