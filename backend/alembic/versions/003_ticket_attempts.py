"""Add ticket_attempts table

Revision ID: 003
Revises: 002
Create Date: 2026-05-22

Replaces prs.commit_hash as the store for AgentHub swarm-mode output.
Each ticket can have multiple attempts; one accepted attempt is selected into a promotion candidate.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS ticket_attempts (
            id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            project_id            UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            ticket_id             UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
            agenthub_commit_hash  VARCHAR(255),
            base_hash             VARCHAR(255),
            attempt_num           INTEGER NOT NULL DEFAULT 1,
            agent_id              VARCHAR(255),
            status                VARCHAR(50) NOT NULL DEFAULT 'proposed',
            summary               TEXT,
            test_status           VARCHAR(50),
            test_output           TEXT,
            created_at            TIMESTAMP DEFAULT NOW(),
            updated_at            TIMESTAMP DEFAULT NOW()
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS idx_ticket_attempts_project ON ticket_attempts(project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ticket_attempts_ticket ON ticket_attempts(ticket_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ticket_attempts_commit ON ticket_attempts(agenthub_commit_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ticket_attempts_status ON ticket_attempts(status)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ticket_attempts")
