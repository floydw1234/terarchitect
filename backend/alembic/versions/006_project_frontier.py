"""Add shipped_frontier to projects

Revision ID: 006
Revises: 005
Create Date: 2026-05-22

The shipped_frontier is the last known main commit from which all new agent work
should build. It is updated whenever a wave is successfully shipped to main.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS shipped_frontier VARCHAR(255)")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS shipped_frontier_updated_at TIMESTAMP")


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS shipped_frontier")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS shipped_frontier_updated_at")
