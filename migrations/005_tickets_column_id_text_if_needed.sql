-- Allow column_id to store string column keys (e.g. "backlog", "in_progress", "done")
-- Idempotent: only runs if column is still UUID (e.g. 003 was skipped or DB predates it)
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'tickets' AND column_name = 'column_id'
    AND data_type = 'uuid'
  ) THEN
    ALTER TABLE tickets ALTER COLUMN column_id TYPE TEXT USING column_id::text;
  END IF;
END $$;
