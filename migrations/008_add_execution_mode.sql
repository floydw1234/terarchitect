-- Per-project agent execution: docker (clone in container) or local (run on host at project_path)
ALTER TABLE projects
ADD COLUMN IF NOT EXISTS execution_mode VARCHAR(50) NOT NULL DEFAULT 'docker';
