"""Add composite_workspaces table and blessed_workspace_id to projects

Revision ID: 010
Revises: 009
Create Date: 2026-05-22

Phase 9: Composite Workspace / No-Main Differentiator.
A composite workspace is a candidate codebase state composed from selected
AgentHub leaves. It can be previewed, tested, blessed, and optionally promoted
to a ShipRun — without requiring a commit to main.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS blessed_workspace_id VARCHAR(255)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS composite_workspaces (
            id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            project_id            UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            base_root_hash        VARCHAR(255),
            selected_attempt_ids  JSONB DEFAULT '[]',
            selected_leaf_hashes  JSONB DEFAULT '[]',
            status                VARCHAR(50) NOT NULL DEFAULT 'draft',
            composed_commit_hash  VARCHAR(255),
            conflict_summary      TEXT,
            changed_files         JSONB DEFAULT '[]',
            summary               TEXT,
            test_status           VARCHAR(50),
            test_output           TEXT,
            preview_url           TEXT,
            created_by            VARCHAR(255),
            created_at            TIMESTAMP DEFAULT NOW(),
            updated_at            TIMESTAMP DEFAULT NOW()
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS idx_composite_workspaces_project ON composite_workspaces(project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_composite_workspaces_status ON composite_workspaces(status)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS composite_workspaces")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS blessed_workspace_id")
