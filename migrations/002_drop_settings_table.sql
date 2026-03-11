-- Drop the unused settings table (project config lives on projects.execution_mode and projects.project_path).
-- Run after 001_create_schema.sql on existing DBs.

DROP TRIGGER IF EXISTS update_settings_updated_at ON settings;
DROP TABLE IF EXISTS settings;
