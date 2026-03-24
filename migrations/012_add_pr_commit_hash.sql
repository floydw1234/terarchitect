-- Ensure prs.commit_hash exists (used for agenthub swarm commit hash storage).
-- commit_hash was added in the original schema but may be absent in older installs.
ALTER TABLE prs ADD COLUMN IF NOT EXISTS commit_hash VARCHAR(255);
