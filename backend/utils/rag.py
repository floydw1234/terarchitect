"""
RAG embedding upsert: embed content and store in rag_embeddings for semantic search.
Failures (e.g. embedding service down) are logged and skipped so create/update still succeeds.
"""
from uuid import UUID

from flask import current_app

from models.db import db, RAGEmbedding
from utils.embedding_client import embed_single


def upsert_embedding(project_id: UUID, source_type: str, source_id: UUID, content: str) -> None:
    """Replace existing RAG row for this source with a new embedding; no-op if content is blank."""
    content = (content or "").strip()
    RAGEmbedding.query.filter_by(
        project_id=project_id, source_type=source_type, source_id=source_id
    ).delete()
    if not content:
        db.session.commit()
        return
    try:
        vector = embed_single(content)
    except Exception as e:
        current_app.logger.warning("RAG embed skipped for %s %s: %s", source_type, source_id, e)
        db.session.commit()
        return
    row = RAGEmbedding(
        project_id=project_id,
        source_type=source_type,
        source_id=source_id,
        content=content,
        embedding=vector,
    )
    db.session.add(row)
    db.session.commit()


def delete_embeddings_for_source(project_id: UUID, source_type: str, source_id: UUID) -> None:
    """Remove all RAG rows for the given source."""
    RAGEmbedding.query.filter_by(
        project_id=project_id, source_type=source_type, source_id=source_id
    ).delete()
    db.session.commit()
