# RFC: SQLite State Management for Octopoid

**Status:** Draft
**Target:** octopoid v2.0

## Summary

Replace octopoid's file-based state management with SQLite, adding provisional completion validation and automatic planning escalation for complex tasks.

---

## Motivation

Current file-based approach has issues:
- Tasks can end up in multiple queues (race conditions)
- Stale state persists after cleanup
- No atomic operations
- BLOCKED_BY dependencies not enforced
- Agents mark tasks "done" without validation
- Complex tasks fail repeatedly without adaptation

---

## Core Changes

### 1. Single Task Directory

All task files live in one directory. Database tracks state.

```
.orchestrator/
├── state.db              # SQLite database (source of truth for state)
├── shared/
│   └── tasks/            # ALL task markdown files (never move)
├── plans/                # Planning phase output documents
└── agents/
    └── {agent}/
        └── worktree/
```

Files are content. DB is state. No file moves.

### 2. Task Lifecycle

```
incoming → claimed → provisional → done
                         ↓
                    rejected → incoming (retry)
                         ↓
                    escalated → planning → micro-tasks
```

**Key change:** Agents submit to `provisional`, not `done`. A validator checks before accepting.

### 3. Dependency Enforcement

BLOCKED_BY is enforced by the database:

```python
def claim_task(agent_name: str) -> Optional[Task]:
    with db.transaction():
        task = db.execute('''
            SELECT * FROM tasks
            WHERE queue = 'incoming'
              AND (blocked_by IS NULL
                   OR blocked_by IN (SELECT id FROM tasks WHERE queue = 'done'))
            ORDER BY priority, created_at
            LIMIT 1
        ''').fetchone()

        if task:
            db.update_task(task.id, queue='claimed', claimed_by=agent_name)
        return task
```

### 4. Provisional Completion & Validation

Agents submit completion with metrics:

```python
def submit_completion(agent_name: str, task_id: str, metrics: dict):
    db.update_task(task_id,
        queue='provisional',
        commits_count=metrics['commits'],
        turns_used=metrics['turns'],
    )
```

Validator checks before accepting:

```python
def validate_completion(task_id: str) -> str:
    task = db.get_task(task_id)

    # Must have commits
    if task.commits_count == 0:
        return reject(task_id, 'no_commits')

    # Check for exploration exhaustion (used most turns, no output)
    if task.turns_used > 40 and task.commits_count == 0:
        return escalate_to_planning(task_id)

    return accept(task_id)
```

### 5. Planning Escalation

When tasks repeatedly fail or exhaust turns without output:

1. Original task blocked
2. Planning task created (high turn limit, output = plan document)
3. Plan parsed into micro-tasks
4. Micro-tasks queued with dependencies

---

## Database Schema

```sql
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    queue TEXT NOT NULL DEFAULT 'incoming',
    priority TEXT DEFAULT 'P2',
    complexity TEXT,
    role TEXT,
    branch TEXT DEFAULT 'main',
    skip_pr BOOLEAN DEFAULT FALSE,
    blocked_by TEXT,
    claimed_by TEXT,
    claimed_at DATETIME,
    submitted_at DATETIME,
    completed_at DATETIME,

    -- Metrics (populated on provisional)
    commits_count INTEGER DEFAULT 0,
    turns_used INTEGER,

    -- Escalation tracking
    attempt_count INTEGER DEFAULT 0,
    has_plan BOOLEAN DEFAULT FALSE,
    plan_id TEXT,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (blocked_by) REFERENCES tasks(id)
);

CREATE TABLE agents (
    name TEXT PRIMARY KEY,
    role TEXT,
    running BOOLEAN DEFAULT FALSE,
    paused BOOLEAN DEFAULT FALSE,
    pid INTEGER,
    current_task_id TEXT,
    last_run_start DATETIME,
    last_run_end DATETIME
);

CREATE TABLE task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    event TEXT NOT NULL,
    agent TEXT,
    details TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_tasks_queue ON tasks(queue);
CREATE INDEX idx_tasks_blocked_by ON tasks(blocked_by);
```

---

## New Validator Role

Lightweight agent that runs frequently:

```yaml
agents:
  - name: validator
    role: validator
    interval_seconds: 30
    lightweight: true  # No worktree needed
```

Responsibilities:
- Validate provisional completions
- Reject tasks with no commits
- Escalate to planning when appropriate
- Reset stuck claimed tasks
- Auto-promote when blockers complete

---

## Migration

1. Update to new octopoid version
2. Run `octopoid migrate init` - creates DB, imports existing tasks
3. If issues: `git checkout` to revert, `octopoid migrate rollback`

No shadow mode. Just switch and rollback if needed.

---

## Configuration

```yaml
# agents.yaml additions

database:
  path: .orchestrator/state.db

validation:
  require_commits: true
  max_attempts_before_planning: 2

agents:
  - name: validator
    role: validator
    interval_seconds: 30
    lightweight: true
```

---

## API Changes

### queue_utils.py

```python
# Old: file moves
def complete_task(task_id):
    move_file(task_path, 'done/')

# New: DB update, validator accepts
def submit_completion(agent_name, task_id, metrics):
    db.update_task(task_id, queue='provisional', **metrics)
    # Validator will accept/reject
```

### New: completion metrics

Agent wrapper captures metrics automatically:

```python
def run_agent(agent_name, task_id):
    commits_before = git_commit_count(worktree)

    result = run_claude(...)

    commits_after = git_commit_count(worktree)

    submit_completion(agent_name, task_id, {
        'commits': commits_after - commits_before,
        'turns': parse_turns(result),
    })
```

---

## Benefits

1. **No false completions** - Validator checks before accepting
2. **Dependencies enforced** - BLOCKED_BY actually works
3. **Automatic escalation** - Failed tasks become planning tasks
4. **Atomic operations** - No race conditions
5. **Queryable state** - Easy dashboard, debugging
6. **Audit trail** - Full history

---

## Implementation Order

1. Schema + basic DB operations
2. Migration tool (import existing tasks)
3. Update queue_utils to use DB
4. Validator role
5. Planning escalation
6. Micro-task generation from plans
