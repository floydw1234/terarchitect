"""Rename merge_runs → ship_runs, drop prs table

Revision ID: 011
Revises: 010
Create Date: 2026-05-22

Phase 11 cleanup:
- merge_runs renamed to ship_runs — the table tracks candidate-backed ship runs, not merges.
- prs table dropped — per-ticket PRs are gone; release PR data lives on ship_runs directly.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE merge_runs RENAME TO ship_runs")
    op.execute("DROP TABLE IF EXISTS prs")


def downgrade() -> None:
    op.execute("ALTER TABLE ship_runs RENAME TO merge_runs")
    op.execute("""
        CREATE TABLE IF NOT EXISTS prs (
            id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            ticket_id   UUID REFERENCES tickets(id) ON DELETE CASCADE,
            pr_number   INTEGER,
            pr_url      TEXT,
            commit_hash VARCHAR(255),
            created_at  TIMESTAMP DEFAULT NOW()
        )
    """)
