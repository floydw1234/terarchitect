"""Add release PR and ship fields to merge_runs

Revision ID: 007
Revises: 006
Create Date: 2026-05-22

Evolves merge_runs from a swarm-branch merge tracker into a full ship-run record
that tracks release branch composition, PR creation, and final ship to main.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE merge_runs ADD COLUMN IF NOT EXISTS release_branch TEXT")
    op.execute("ALTER TABLE merge_runs ADD COLUMN IF NOT EXISTS base_main_hash VARCHAR(255)")
    op.execute("ALTER TABLE merge_runs ADD COLUMN IF NOT EXISTS composed_commit_hash VARCHAR(255)")
    op.execute("ALTER TABLE merge_runs ADD COLUMN IF NOT EXISTS changed_files JSONB DEFAULT '[]'")
    op.execute("ALTER TABLE merge_runs ADD COLUMN IF NOT EXISTS summary TEXT")
    op.execute("ALTER TABLE merge_runs ADD COLUMN IF NOT EXISTS test_status VARCHAR(50)")
    op.execute("ALTER TABLE merge_runs ADD COLUMN IF NOT EXISTS test_output TEXT")
    op.execute("ALTER TABLE merge_runs ADD COLUMN IF NOT EXISTS release_pr_url TEXT")
    op.execute("ALTER TABLE merge_runs ADD COLUMN IF NOT EXISTS release_pr_number INTEGER")
    op.execute("ALTER TABLE merge_runs ADD COLUMN IF NOT EXISTS shipped_at TIMESTAMP")
    op.execute("ALTER TABLE merge_runs ADD COLUMN IF NOT EXISTS shipped_commit_hash VARCHAR(255)")


def downgrade() -> None:
    for col in (
        "release_branch", "base_main_hash", "composed_commit_hash", "changed_files",
        "summary", "test_status", "test_output", "release_pr_url", "release_pr_number",
        "shipped_at", "shipped_commit_hash",
    ):
        op.execute(f"ALTER TABLE merge_runs DROP COLUMN IF EXISTS {col}")
