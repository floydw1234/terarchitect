-- Add git_mode to projects: 'structured' (GitHub branches + PRs) or 'swarm' (agenthub DAG).
ALTER TABLE projects ADD COLUMN IF NOT EXISTS git_mode VARCHAR(20) NOT NULL DEFAULT 'structured';
