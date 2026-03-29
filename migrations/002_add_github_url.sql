-- Add github_url column to projects table
ALTER TABLE projects
ADD COLUMN IF NOT EXISTS github_url TEXT;

-- Rename git_repo_path to project_path (idempotent: 001 schema already uses project_path)
DO $$ BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='projects' AND column_name='git_repo_path'
  ) THEN
    ALTER TABLE projects RENAME COLUMN git_repo_path TO project_path;
  END IF;
END $$;
