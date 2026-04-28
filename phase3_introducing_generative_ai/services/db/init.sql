-- Create DB (safe)
DO $$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ragdb') THEN
      CREATE DATABASE ragdb;
   END IF;
END
$$;

\c ragdb;

-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- 🔥 Ensure correct table schema (force reset in lab)
DROP TABLE IF EXISTS documents;

CREATE TABLE documents (
    id BIGSERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    embedding VECTOR(768),
    metadata JSONB
);

-- 🔥 Recreate index to avoid dimension mismatch
DROP INDEX IF EXISTS documents_embedding_idx;

CREATE INDEX documents_embedding_idx
ON documents
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
