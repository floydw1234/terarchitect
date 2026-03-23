-- Add dependency tracking to tickets so a ticket can declare it must run after others.
-- depends_on_ticket_ids: JSONB array of ticket UUIDs that must be in 'done' before this ticket can run.
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS depends_on_ticket_ids JSONB NOT NULL DEFAULT '[]';
