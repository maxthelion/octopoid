-- Add type field to tasks for hook resolution and agent filtering
ALTER TABLE tasks ADD COLUMN type TEXT;

-- Index for filtering tasks by type
CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(type);
