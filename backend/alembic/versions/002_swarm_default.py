"""Change git_mode default to swarm

Revision ID: 002
Revises: 001
Create Date: 2026-05-22

New projects default to swarm mode. Existing projects are left as-is
(structured projects already created continue to work under legacy mode).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE projects
        ALTER COLUMN git_mode SET DEFAULT 'swarm'
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE projects
        ALTER COLUMN git_mode SET DEFAULT 'structured'
    """)
