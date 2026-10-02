"""Store auto-ship LLM winner decision on tickets.

Revision ID: 025_ticket_auto_ship_winner_decision
Revises: 024_project_auto_ship
Create Date: 2026-10-02
"""

from alembic import op

revision = "025_ticket_auto_ship_winner_decision"
down_revision = "024_project_auto_ship"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS auto_ship_winner_decision JSONB"
    )


def downgrade():
    op.execute("ALTER TABLE tickets DROP COLUMN IF EXISTS auto_ship_winner_decision")
