"""Add asynchronous evidence run queue

Revision ID: 014
Revises: 013
Create Date: 2026-05-24

Phase 14: queue evidence execution outside UI request paths.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS evidence_runs (
            id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            evidence_bundle_id  UUID REFERENCES evidence_bundles(id) ON DELETE SET NULL,
            run_type            VARCHAR(50) NOT NULL,
            status              VARCHAR(50) NOT NULL DEFAULT 'queued',
            target_type         VARCHAR(50) NOT NULL,
            target_id           UUID NOT NULL,
            check_type          VARCHAR(50),
            request_data        JSONB DEFAULT '{}',
            error               TEXT,
            created_at          TIMESTAMP DEFAULT NOW(),
            started_at          TIMESTAMP,
            finished_at         TIMESTAMP,
            updated_at          TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_evidence_runs_project ON evidence_runs(project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_evidence_runs_status ON evidence_runs(status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_evidence_runs_target ON evidence_runs(target_type, target_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS evidence_runs")
