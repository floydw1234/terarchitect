"""Add Composite Workspace preview process metadata

Revision ID: 015
Revises: 014
Create Date: 2026-05-24

Phase 14: preserve preview process orchestration context for browser evidence.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE composite_workspaces ADD COLUMN IF NOT EXISTS preview_status VARCHAR(50)")
    op.execute("ALTER TABLE composite_workspaces ADD COLUMN IF NOT EXISTS preview_command JSONB DEFAULT '[]'")
    op.execute("ALTER TABLE composite_workspaces ADD COLUMN IF NOT EXISTS preview_error TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE composite_workspaces DROP COLUMN IF EXISTS preview_error")
    op.execute("ALTER TABLE composite_workspaces DROP COLUMN IF EXISTS preview_command")
    op.execute("ALTER TABLE composite_workspaces DROP COLUMN IF EXISTS preview_status")
