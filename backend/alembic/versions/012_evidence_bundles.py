"""Add evidence bundles and checks

Revision ID: 012
Revises: 011
Create Date: 2026-05-23

Phase 14: Verification Engine evidence storage.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS evidence_bundles (
            id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            project_id            UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            target_type           VARCHAR(50) NOT NULL,
            target_id             UUID NOT NULL,
            base_hash             VARCHAR(255),
            candidate_hash        VARCHAR(255),
            selected_attempt_ids  JSONB DEFAULT '[]',
            selected_leaf_hashes  JSONB DEFAULT '[]',
            status                VARCHAR(50) NOT NULL DEFAULT 'collecting',
            risk_level            VARCHAR(50) NOT NULL DEFAULT 'unknown',
            summary               TEXT,
            created_at            TIMESTAMP DEFAULT NOW(),
            updated_at            TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS evidence_checks (
            id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            evidence_bundle_id  UUID NOT NULL REFERENCES evidence_bundles(id) ON DELETE CASCADE,
            check_type          VARCHAR(50) NOT NULL,
            status              VARCHAR(50) NOT NULL DEFAULT 'skipped',
            tool_name           VARCHAR(255),
            command             TEXT,
            output              TEXT,
            artifact_url        TEXT,
            check_metadata      JSONB DEFAULT '{}',
            started_at          TIMESTAMP,
            finished_at         TIMESTAMP,
            created_at          TIMESTAMP DEFAULT NOW(),
            updated_at          TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_evidence_bundles_project ON evidence_bundles(project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_evidence_bundles_target ON evidence_bundles(target_type, target_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_evidence_checks_bundle ON evidence_checks(evidence_bundle_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_evidence_checks_type ON evidence_checks(check_type)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS evidence_checks")
    op.execute("DROP TABLE IF EXISTS evidence_bundles")
