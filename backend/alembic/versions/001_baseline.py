"""Baseline: full current schema

Revision ID: 001
Revises:
Create Date: 2026-05-22

This migration records the schema that previously existed only in db.create_all().
It uses IF NOT EXISTS throughout so it is safe to run against an existing database.
New databases will be fully initialized; existing databases will be no-ops here and
should run `alembic stamp 001` if they have not already done so.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            name        VARCHAR(255) NOT NULL,
            description TEXT,
            project_path TEXT,
            github_url  TEXT,
            execution_mode VARCHAR(50) NOT NULL DEFAULT 'docker',
            git_mode    VARCHAR(20) NOT NULL DEFAULT 'structured',
            created_at  TIMESTAMP DEFAULT NOW(),
            updated_at  TIMESTAMP DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS graphs (
            id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            nodes       JSONB DEFAULT '[]',
            edges       JSONB DEFAULT '[]',
            version     INTEGER DEFAULT 1,
            created_at  TIMESTAMP DEFAULT NOW(),
            updated_at  TIMESTAMP DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS kanban_boards (
            id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            columns     JSONB DEFAULT '[]',
            created_at  TIMESTAMP DEFAULT NOW(),
            updated_at  TIMESTAMP DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            project_id            UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            column_id             TEXT NOT NULL,
            title                 VARCHAR(255) NOT NULL,
            description           TEXT,
            associated_node_ids   JSONB DEFAULT '[]',
            associated_edge_ids   JSONB DEFAULT '[]',
            priority              VARCHAR(50) DEFAULT 'medium',
            status                VARCHAR(50) DEFAULT 'todo',
            failed_count          INTEGER NOT NULL DEFAULT 0,
            depends_on_ticket_ids JSONB DEFAULT '[]',
            created_at            TIMESTAMP DEFAULT NOW(),
            updated_at            TIMESTAMP DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS ticket_comments (
            id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            ticket_id  UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
            content    TEXT NOT NULL,
            is_summary BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            node_id    TEXT,
            edge_id    TEXT,
            title      VARCHAR(255),
            content    TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS execution_logs (
            id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            project_id    UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            ticket_id     UUID REFERENCES tickets(id) ON DELETE CASCADE,
            session_id    VARCHAR(255),
            step          VARCHAR(100),
            summary       TEXT,
            raw_output    TEXT,
            input_tokens  INTEGER,
            output_tokens INTEGER,
            success       BOOLEAN DEFAULT TRUE,
            created_at    TIMESTAMP DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS prs (
            id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            ticket_id   UUID REFERENCES tickets(id) ON DELETE CASCADE,
            pr_number   INTEGER,
            pr_url      TEXT,
            commit_hash VARCHAR(255),
            created_at  TIMESTAMP DEFAULT NOW()
        )
    """)

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

    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_jobs (
            id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            ticket_id         UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
            project_id        UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            kind              VARCHAR(50) NOT NULL,
            status            VARCHAR(50) NOT NULL DEFAULT 'pending',
            cancel_requested  BOOLEAN NOT NULL DEFAULT FALSE,
            created_at        TIMESTAMP DEFAULT NOW(),
            updated_at        TIMESTAMP DEFAULT NOW(),
            pr_number         INTEGER,
            comment_body      TEXT,
            github_comment_id BIGINT
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS merge_runs (
            id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            wave_num    INTEGER NOT NULL,
            status      VARCHAR(50) NOT NULL DEFAULT 'queued',
            commit_hash VARCHAR(255),
            pr_url      TEXT,
            error       TEXT,
            created_at  TIMESTAMP DEFAULT NOW(),
            updated_at  TIMESTAMP DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS rag_embeddings (
            id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            project_id  UUID NOT NULL REFERENCES projects(id),
            source_type VARCHAR(50) NOT NULL,
            source_id   UUID NOT NULL,
            content     TEXT NOT NULL,
            embedding   vector(768) NOT NULL,
            created_at  TIMESTAMP DEFAULT NOW()
        )
    """)

    # Indexes
    op.execute("CREATE INDEX IF NOT EXISTS idx_tickets_project ON tickets(project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_jobs_ticket ON agent_jobs(ticket_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_jobs_project_status ON agent_jobs(project_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_merge_runs_project ON merge_runs(project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_execution_logs_ticket ON execution_logs(ticket_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_rag_embeddings_project ON rag_embeddings(project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_rag_embeddings_source ON rag_embeddings(source_type, source_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rag_embeddings")
    op.execute("DROP TABLE IF EXISTS merge_runs")
    op.execute("DROP TABLE IF EXISTS agent_jobs")
    op.execute("DROP TABLE IF EXISTS pr_review_comments")
    op.execute("DROP TABLE IF EXISTS prs")
    op.execute("DROP TABLE IF EXISTS execution_logs")
    op.execute("DROP TABLE IF EXISTS notes")
    op.execute("DROP TABLE IF EXISTS ticket_comments")
    op.execute("DROP TABLE IF EXISTS tickets")
    op.execute("DROP TABLE IF EXISTS kanban_boards")
    op.execute("DROP TABLE IF EXISTS graphs")
    op.execute("DROP TABLE IF EXISTS projects")
