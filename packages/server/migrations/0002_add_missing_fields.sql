-- Add missing fields for complete requirements implementation

-- Add needs_breakdown flag for breakdown agent
ALTER TABLE tasks ADD COLUMN needs_breakdown BOOLEAN DEFAULT FALSE;

-- Add review_round for gatekeeper multi-check workflow
ALTER TABLE tasks ADD COLUMN review_round INTEGER DEFAULT 0;

-- Add execution_notes for agent summaries
ALTER TABLE tasks ADD COLUMN execution_notes TEXT;

-- Create index for needs_breakdown queries
CREATE INDEX IF NOT EXISTS idx_tasks_needs_breakdown ON tasks(needs_breakdown);
