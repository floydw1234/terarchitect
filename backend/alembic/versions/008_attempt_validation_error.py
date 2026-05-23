"""Add validation_error to ticket_attempts

Revision ID: 008
Revises: 007
Create Date: 2026-05-22

Stores the reason an attempt failed validation (e.g. commit not found in AgentHub).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE ticket_attempts ADD COLUMN IF NOT EXISTS validation_error TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE ticket_attempts DROP COLUMN IF EXISTS validation_error")
