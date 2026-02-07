-- Add github_url column to projects table
ALTER TABLE projects
ADD COLUMN IF NOT EXISTS github_url TEXT;

-- Rename git_repo_path to project_path
ALTER TABLE projects
RENAME COLUMN git_repo_path TO project_path;
