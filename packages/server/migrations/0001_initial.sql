-- Initial database schema for Octopoid v2.0
-- Based on orchestrator/db.py schema with client-server additions

-- Tasks table - main queue state
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL UNIQUE,
    queue TEXT NOT NULL DEFAULT 'incoming',
    priority TEXT DEFAULT 'P2',
    complexity TEXT,
    role TEXT,
    branch TEXT DEFAULT 'main',
    blocked_by TEXT,
    claimed_by TEXT,
    claimed_at DATETIME,
    commits_count INTEGER DEFAULT 0,
    turns_used INTEGER,
    attempt_count INTEGER DEFAULT 0,
    has_plan BOOLEAN DEFAULT FALSE,
    plan_id TEXT,
    project_id TEXT,
    auto_accept BOOLEAN DEFAULT FALSE,
    rejection_count INTEGER DEFAULT 0,
    pr_number INTEGER,
    pr_url TEXT,
    checks TEXT,
    check_results TEXT,
    needs_rebase BOOLEAN DEFAULT FALSE,
    last_rebase_attempt_at DATETIME,
    staging_url TEXT,
    submitted_at TEXT,
    completed_at TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    -- Client-server fields (v2.0)
    orchestrator_id TEXT,
    lease_expires_at DATETIME,
    version INTEGER DEFAULT 1,

    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (orchestrator_id) REFERENCES orchestrators(id)
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_tasks_queue ON tasks(queue);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
CREATE INDEX IF NOT EXISTS idx_tasks_claimed_by ON tasks(claimed_by);
CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_orchestrator_id ON tasks(orchestrator_id);
CREATE INDEX IF NOT EXISTS idx_tasks_lease_expires_at ON tasks(lease_expires_at);

-- Projects table - containers for multi-task features
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'draft',
    branch TEXT,
    base_branch TEXT DEFAULT 'main',
    auto_accept BOOLEAN DEFAULT FALSE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT,
    completed_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);

-- Agents table - runtime state
CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    role TEXT,
    running BOOLEAN DEFAULT FALSE,
    pid INTEGER,
    current_task_id TEXT,
    last_run_start DATETIME,
    last_run_end DATETIME
);

-- Task history for audit trail
CREATE TABLE IF NOT EXISTS task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    event TEXT NOT NULL,
    agent TEXT,
    details TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_history_task_id ON task_history(task_id);
CREATE INDEX IF NOT EXISTS idx_task_history_event ON task_history(event);

-- Drafts table - tracks lifecycle of draft documents
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT DEFAULT 'idea',
    author TEXT NOT NULL,
    domain TEXT,
    file_path TEXT,
    created_at TEXT,
    updated_at TEXT,
    linked_task_id TEXT,
    linked_project_id TEXT,
    tags TEXT
);

CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status);
CREATE INDEX IF NOT EXISTS idx_drafts_author ON drafts(author);
CREATE INDEX IF NOT EXISTS idx_drafts_domain ON drafts(domain);

-- Orchestrators table - client registrations (v2.0)
CREATE TABLE IF NOT EXISTS orchestrators (
    id TEXT PRIMARY KEY,              -- cluster-machine_id (e.g., prod-mac-studio-001)
    cluster TEXT NOT NULL,            -- Logical cluster (prod, dev, staging)
    machine_id TEXT NOT NULL,         -- Machine identifier
    hostname TEXT,                    -- Hostname for debugging
    repo_url TEXT NOT NULL,           -- Git repository URL
    registered_at DATETIME NOT NULL,
    last_heartbeat DATETIME NOT NULL,
    status TEXT DEFAULT 'active',     -- active, offline, maintenance
    version TEXT,                     -- Client version
    capabilities TEXT,                -- JSON: Supported roles, max agents, etc.
    UNIQUE(cluster, machine_id)
);

CREATE INDEX IF NOT EXISTS idx_orchestrators_cluster ON orchestrators(cluster);
CREATE INDEX IF NOT EXISTS idx_orchestrators_status ON orchestrators(status);
CREATE INDEX IF NOT EXISTS idx_orchestrators_last_heartbeat ON orchestrators(last_heartbeat);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_info (
    key TEXT PRIMARY KEY,
    value TEXT
);

INSERT INTO schema_info (key, value) VALUES ('version', '1');
