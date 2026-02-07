-- Align rag_embeddings.embedding with embedding service (768-dim, e.g. mpnet-multilingual)
-- DROP/ADD loses existing embedding data; re-embed after migration if needed.
ALTER TABLE rag_embeddings DROP COLUMN IF EXISTS embedding;
ALTER TABLE rag_embeddings ADD COLUMN embedding vector(768);
