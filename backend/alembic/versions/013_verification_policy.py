"""Add project verification policy

Revision ID: 013
Revises: 012
Create Date: 2026-05-23

Phase 14: project-level verification policy configuration.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS verification_policy JSONB DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS verification_policy")
