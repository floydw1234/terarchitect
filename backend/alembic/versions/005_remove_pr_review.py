"""Remove PR-per-ticket infrastructure

Revision ID: 005
Revises: 004
Create Date: 2026-05-22

Drops the pr_review_comments table and the review-job columns from agent_jobs.
PR-per-ticket is gone. The prs table is kept for future release PRs (one per ShipRun).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pr_review_comments")
    op.execute("ALTER TABLE agent_jobs DROP COLUMN IF EXISTS pr_number")
    op.execute("ALTER TABLE agent_jobs DROP COLUMN IF EXISTS comment_body")
    op.execute("ALTER TABLE agent_jobs DROP COLUMN IF EXISTS github_comment_id")
    # Set default kind to 'ticket' now that 'review' no longer exists
    op.execute("UPDATE agent_jobs SET kind = 'ticket' WHERE kind = 'review'")
    op.execute("ALTER TABLE agent_jobs ALTER COLUMN kind SET DEFAULT 'ticket'")
    # Move any tickets stuck in 'in_review' to 'done' — per-ticket PR review is gone
    op.execute("UPDATE tickets SET column_id = 'done' WHERE column_id = 'in_review'")


def downgrade() -> None:
    op.execute("ALTER TABLE agent_jobs ADD COLUMN IF NOT EXISTS pr_number INTEGER")
    op.execute("ALTER TABLE agent_jobs ADD COLUMN IF NOT EXISTS comment_body TEXT")
    op.execute("ALTER TABLE agent_jobs ADD COLUMN IF NOT EXISTS github_comment_id BIGINT")
    op.execute("""
        CREATE TABLE IF NOT EXISTS pr_review_comments (
            id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            project_id        UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            ticket_id         UUID REFERENCES tickets(id),
            pr_number         INTEGER NOT NULL,
            github_comment_id BIGINT NOT NULL,
            author_login      VARCHAR(255),
            body              TEXT,
            comment_created_at TIMESTAMP,
            addressed_at      TIMESTAMP,
            created_at        TIMESTAMP DEFAULT NOW(),
            updated_at        TIMESTAMP DEFAULT NOW(),
            CONSTRAINT _pr_review_comment_uniq UNIQUE (project_id, pr_number, github_comment_id)
        )
    """)
