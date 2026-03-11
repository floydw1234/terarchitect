-- Add failed_count to tickets so the UI can show a "previously failed" badge
-- and the backend can return failed tickets to backlog automatically.
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS failed_count INTEGER NOT NULL DEFAULT 0;
