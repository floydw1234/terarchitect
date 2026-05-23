"""Add intent fields to tickets (Phase 2 — tickets as intents)

Revision ID: 009
Revises: 008
Create Date: 2026-05-22

A ticket is now an intent object: goal, rationale, acceptance criteria,
constraints, architecture scope, dependencies, priority/value.
Execution state (attempts, ship runs) is tracked separately.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # intent_status: planning state owned by the human/PM, not by the agent
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS intent_status VARCHAR(50) NOT NULL DEFAULT 'ready'")
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS rationale TEXT")
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS acceptance_criteria TEXT")
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS constraints TEXT")
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS value_score INTEGER")
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS risk_level VARCHAR(50)")
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS created_source VARCHAR(50)")

    # Seed: tickets currently in_progress were actively being worked on → active
    op.execute("UPDATE tickets SET intent_status = 'active' WHERE column_id = 'in_progress'")

    op.execute("CREATE INDEX IF NOT EXISTS idx_tickets_intent_status ON tickets(intent_status)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_tickets_intent_status")
    for col in ("intent_status", "rationale", "acceptance_criteria", "constraints",
                "value_score", "risk_level", "created_source"):
        op.execute(f"ALTER TABLE tickets DROP COLUMN IF EXISTS {col}")
