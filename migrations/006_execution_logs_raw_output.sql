-- Add raw_output for storing full Claude Code responses (debugging)
ALTER TABLE execution_logs
ADD COLUMN IF NOT EXISTS raw_output TEXT;
