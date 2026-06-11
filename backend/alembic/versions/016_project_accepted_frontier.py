"""Add accepted_frontier_id to projects

Revision ID: 016
Revises: 015
Create Date: 2026-06-11
"""
from typing import Sequence, Union

from alembic import op

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS accepted_frontier_id VARCHAR(255)")


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS accepted_frontier_id")
