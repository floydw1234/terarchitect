"""Migrate existing swarm PR commit hashes into ticket_attempts

Revision ID: 004
Revises: 003
Create Date: 2026-05-22

For every prs row where:
  - commit_hash is not null (i.e. it was used for AgentHub output in swarm mode)
  - the project's git_mode is 'swarm'
  - no ticket_attempt already exists for that ticket
we insert a ticket_attempts row.

Status mapping:
  - ticket column_id = 'done' → 'accepted'
  - otherwise → 'proposed'

Existing structured-mode PR rows (pr_number / pr_url) are left untouched.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO ticket_attempts (
            project_id,
            ticket_id,
            agenthub_commit_hash,
            attempt_num,
            status,
            created_at,
            updated_at
        )
        SELECT
            p.project_id,
            p.ticket_id,
            p.commit_hash,
            1,
            CASE WHEN t.column_id = 'done' THEN 'accepted' ELSE 'proposed' END,
            p.created_at,
            p.created_at
        FROM prs p
        JOIN tickets t  ON t.id  = p.ticket_id
        JOIN projects pr ON pr.id = p.project_id
        WHERE p.commit_hash IS NOT NULL
          AND pr.git_mode = 'swarm'
          AND NOT EXISTS (
              SELECT 1 FROM ticket_attempts ta
              WHERE ta.ticket_id = p.ticket_id
          )
    """)


def downgrade() -> None:
    # Remove only the rows that were inserted by this migration
    # (attempt_num=1, created from a prs row)
    op.execute("""
        DELETE FROM ticket_attempts ta
        WHERE ta.attempt_num = 1
          AND EXISTS (
              SELECT 1 FROM prs p
              WHERE p.ticket_id = ta.ticket_id
                AND p.commit_hash = ta.agenthub_commit_hash
          )
    """)
