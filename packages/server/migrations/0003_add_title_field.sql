-- Add title field to tasks table for better display in dashboards
ALTER TABLE tasks ADD COLUMN title TEXT;

-- Create index for title searches
CREATE INDEX IF NOT EXISTS idx_tasks_title ON tasks(title);
