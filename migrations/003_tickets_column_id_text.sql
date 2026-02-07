-- Allow column_id to store string column keys (e.g. "backlog", "in_progress", "done")
-- as used by the frontend and kanban default columns.
ALTER TABLE tickets
ALTER COLUMN column_id TYPE TEXT USING column_id::text;
