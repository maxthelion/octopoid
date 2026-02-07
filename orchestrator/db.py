"""SQLite database backend for orchestrator state management.

This module provides atomic database operations for task queue management,
replacing the file-based system with SQLite for better concurrency and
dependency tracking.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

from .config import get_orchestrator_dir


# Schema version for migrations
SCHEMA_VERSION = 4


def get_database_path() -> Path:
    """Get path to the SQLite database file."""
    return get_orchestrator_dir() / "state.db"


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    """Get a database connection with proper settings.

    Configures:
    - WAL mode for better concurrent read/write
    - Foreign keys enforcement
    - Row factory for dict-like access

    Yields:
        SQLite connection with transaction management
    """
    db_path = get_database_path()
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row

    try:
        # Enable WAL mode for better concurrency
        conn.execute("PRAGMA journal_mode=WAL")
        # Enable foreign key constraints
        conn.execute("PRAGMA foreign_keys=ON")
        # Ensure writes are durable
        conn.execute("PRAGMA synchronous=NORMAL")

        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema() -> None:
    """Initialize the database schema.

    Creates all required tables if they don't exist.
    Safe to call multiple times.
    """
    with get_connection() as conn:
        # Tasks table - main queue state
        conn.execute("""
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
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        """)

        # Create indexes for common queries
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_queue ON tasks(queue)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_claimed_by ON tasks(claimed_by)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks(project_id)
        """)

        # Projects table - containers for multi-task features
        conn.execute("""
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
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status)
        """)

        # Agents table - runtime state
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                name TEXT PRIMARY KEY,
                role TEXT,
                running BOOLEAN DEFAULT FALSE,
                pid INTEGER,
                current_task_id TEXT,
                last_run_start DATETIME,
                last_run_end DATETIME
            )
        """)

        # Task history for audit trail
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                event TEXT NOT NULL,
                agent TEXT,
                details TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_task_history_task_id
            ON task_history(task_id)
        """)

        # Schema version tracking
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_info (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        conn.execute(
            "INSERT OR REPLACE INTO schema_info (key, value) VALUES (?, ?)",
            ("version", str(SCHEMA_VERSION)),
        )


def get_schema_version() -> int | None:
    """Get current schema version from database.

    Returns:
        Schema version number or None if not initialized
    """
    db_path = get_database_path()
    if not db_path.exists():
        return None

    try:
        with get_connection() as conn:
            cursor = conn.execute(
                "SELECT value FROM schema_info WHERE key = 'version'"
            )
            row = cursor.fetchone()
            return int(row["value"]) if row else None
    except sqlite3.OperationalError:
        return None


def migrate_schema() -> bool:
    """Migrate database schema to current version.

    Returns:
        True if migration was performed, False if already current
    """
    current = get_schema_version()
    if current is None:
        init_schema()
        return True

    if current >= SCHEMA_VERSION:
        return False

    with get_connection() as conn:
        # Migration from v1 to v2: Add projects table and project_id column
        if current < 2:
            # Create projects table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT,
                    status TEXT DEFAULT 'draft',
                    branch TEXT,
                    base_branch TEXT DEFAULT 'main',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    created_by TEXT,
                    completed_at DATETIME
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status)
            """)

            # Add project_id column to tasks (SQLite ADD COLUMN is limited but works here)
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN project_id TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks(project_id)
            """)

        # Migration from v2 to v3: Add auto_accept column
        if current < 3:
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN auto_accept BOOLEAN DEFAULT FALSE")
            except sqlite3.OperationalError:
                pass  # Column already exists
            try:
                conn.execute("ALTER TABLE projects ADD COLUMN auto_accept BOOLEAN DEFAULT FALSE")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Migration from v3 to v4: Add review rejection tracking columns
        if current < 4:
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN rejection_count INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # Column already exists
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN pr_number INTEGER")
            except sqlite3.OperationalError:
                pass  # Column already exists
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN pr_url TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Update schema version
        conn.execute(
            "INSERT OR REPLACE INTO schema_info (key, value) VALUES (?, ?)",
            ("version", str(SCHEMA_VERSION)),
        )

    return True


# =============================================================================
# Task Operations
# =============================================================================


def create_task(
    task_id: str,
    file_path: str,
    priority: str = "P2",
    role: str | None = None,
    branch: str = "main",
    complexity: str | None = None,
    blocked_by: str | None = None,
    project_id: str | None = None,
    auto_accept: bool | None = None,
) -> dict[str, Any]:
    """Create a new task in the database.

    Args:
        task_id: Unique task identifier
        file_path: Path to the task markdown file
        priority: P0, P1, or P2
        role: Target role (implement, test, etc.)
        branch: Base branch
        complexity: Estimated complexity
        blocked_by: Comma-separated list of blocking task IDs
        project_id: Optional parent project ID
        auto_accept: Skip provisional queue, go straight to done (inherits from project if None)

    Returns:
        Created task as dictionary
    """
    # If task belongs to a project, inherit branch and auto_accept from project if not specified
    if project_id:
        project = get_project(project_id)
        if project:
            if branch == "main" and project.get("branch"):
                branch = project["branch"]
            if auto_accept is None:
                auto_accept = project.get("auto_accept", False)

    if auto_accept is None:
        auto_accept = False

    # Normalize blocked_by: ensure None/empty/string-"None" all become SQL NULL
    if not blocked_by or blocked_by == "None":
        blocked_by = None

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO tasks (id, file_path, priority, role, branch, complexity, blocked_by, project_id, auto_accept)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, file_path, priority, role, branch, complexity, blocked_by, project_id, auto_accept),
        )

        # Log creation event
        conn.execute(
            """
            INSERT INTO task_history (task_id, event, details)
            VALUES (?, 'created', ?)
            """,
            (task_id, f"priority={priority}, role={role}, project={project_id}"),
        )

    return get_task(task_id)


def get_task(task_id: str) -> dict[str, Any] | None:
    """Get a task by ID.

    Args:
        task_id: Task identifier

    Returns:
        Task as dictionary or None if not found.
        Note: Task identifier is returned as 'id' key (not 'task_id').
    """
    with get_connection() as conn:
        cursor = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_task_by_path(file_path: str) -> dict[str, Any] | None:
    """Get a task by its file path.

    Args:
        file_path: Path to the task file

    Returns:
        Task as dictionary or None if not found
    """
    with get_connection() as conn:
        cursor = conn.execute("SELECT * FROM tasks WHERE file_path = ?", (file_path,))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_task(task_id: str, **fields) -> dict[str, Any] | None:
    """Update task fields.

    Args:
        task_id: Task identifier
        **fields: Fields to update

    Returns:
        Updated task or None if not found
    """
    if not fields:
        return get_task(task_id)

    # Normalize blocked_by: ensure None/empty/string-"None" all become SQL NULL
    if "blocked_by" in fields and (not fields["blocked_by"] or fields["blocked_by"] == "None"):
        fields["blocked_by"] = None

    # Build SET clause dynamically
    set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
    values = list(fields.values())
    values.append(datetime.now().isoformat())  # updated_at
    values.append(task_id)

    with get_connection() as conn:
        conn.execute(
            f"UPDATE tasks SET {set_clause}, updated_at = ? WHERE id = ?",
            values,
        )

    return get_task(task_id)


def delete_task(task_id: str) -> bool:
    """Delete a task from the database.

    Args:
        task_id: Task identifier

    Returns:
        True if deleted, False if not found
    """
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cursor.rowcount > 0


def list_tasks(
    queue: str | None = None,
    role: str | None = None,
    claimed_by: str | None = None,
    include_blocked: bool = True,
) -> list[dict[str, Any]]:
    """List tasks with optional filters.

    Args:
        queue: Filter by queue (incoming, claimed, provisional, done, failed)
        role: Filter by target role
        claimed_by: Filter by claiming agent
        include_blocked: Whether to include blocked tasks

    Returns:
        List of task dictionaries sorted by priority then created_at.
        Note: Task identifier is returned as 'id' key (not 'task_id').
    """
    conditions = []
    params = []

    if queue:
        conditions.append("queue = ?")
        params.append(queue)

    if role:
        conditions.append("role = ?")
        params.append(role)

    if claimed_by:
        conditions.append("claimed_by = ?")
        params.append(claimed_by)

    if not include_blocked:
        conditions.append("(blocked_by IS NULL OR blocked_by = '')")

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    with get_connection() as conn:
        cursor = conn.execute(
            f"""
            SELECT * FROM tasks
            WHERE {where_clause}
            ORDER BY
                CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 ELSE 2 END,
                created_at ASC
            """,
            params,
        )
        return [dict(row) for row in cursor.fetchall()]


def count_tasks(queue: str | None = None) -> int:
    """Count tasks in a queue.

    Args:
        queue: Queue name or None for all tasks

    Returns:
        Number of tasks
    """
    with get_connection() as conn:
        if queue:
            cursor = conn.execute(
                "SELECT COUNT(*) as count FROM tasks WHERE queue = ?", (queue,)
            )
        else:
            cursor = conn.execute("SELECT COUNT(*) as count FROM tasks")
        return cursor.fetchone()["count"]


# =============================================================================
# Task Lifecycle Operations
# =============================================================================


def claim_task(
    role_filter: str | None = None,
    agent_name: str | None = None,
    from_queue: str = "incoming",
) -> dict[str, Any] | None:
    """Atomically claim the highest priority available task.

    This checks dependencies before claiming - a task with unresolved
    blocked_by entries cannot be claimed.

    Args:
        role_filter: Only claim tasks with this role
        agent_name: Name of claiming agent
        from_queue: Queue to claim from (default 'incoming', also supports 'breakdown')

    Returns:
        Claimed task or None if no suitable task available
    """
    with get_connection() as conn:
        # Build query for claimable tasks
        conditions = [
            "queue = ?",
            "(blocked_by IS NULL OR blocked_by = '')",
        ]
        params = [from_queue]

        if role_filter:
            conditions.append("role = ?")
            params.append(role_filter)

        where_clause = " AND ".join(conditions)

        # Use a transaction to atomically check and claim
        # LIMIT 1 with ORDER BY gives us the highest priority task
        # Tasks with rejection_count > 0 are prioritized (review feedback
        # should be addressed before starting fresh work)
        cursor = conn.execute(
            f"""
            SELECT id FROM tasks
            WHERE {where_clause}
            ORDER BY
                CASE WHEN rejection_count > 0 THEN 0 ELSE 1 END,
                CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 ELSE 2 END,
                created_at ASC
            LIMIT 1
            """,
            params,
        )

        row = cursor.fetchone()
        if not row:
            return None

        task_id = row["id"]
        now = datetime.now().isoformat()

        # Claim the task
        conn.execute(
            """
            UPDATE tasks
            SET queue = 'claimed',
                claimed_by = ?,
                claimed_at = ?,
                updated_at = ?
            WHERE id = ? AND queue = ?
            """,
            (agent_name, now, now, task_id, from_queue),
        )

        # Log the claim
        conn.execute(
            """
            INSERT INTO task_history (task_id, event, agent, details)
            VALUES (?, 'claimed', ?, NULL)
            """,
            (task_id, agent_name),
        )

    return get_task(task_id)


def submit_completion(
    task_id: str,
    commits_count: int = 0,
    turns_used: int | None = None,
) -> dict[str, Any] | None:
    """Submit a task for validation (move to provisional queue).

    The task stays in provisional until the scheduler processes it.
    If auto_accept is enabled, the scheduler will move it to done.

    Args:
        task_id: Task identifier
        commits_count: Number of commits made
        turns_used: Number of Claude turns used

    Returns:
        Updated task or None if not found
    """
    now = datetime.now().isoformat()

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET queue = 'provisional',
                commits_count = ?,
                turns_used = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (commits_count, turns_used, now, task_id),
        )

        conn.execute(
            """
            INSERT INTO task_history (task_id, event, details)
            VALUES (?, 'submitted', ?)
            """,
            (task_id, f"commits={commits_count}, turns={turns_used}"),
        )

    return get_task(task_id)


def accept_completion(task_id: str, validator: str | None = None) -> dict[str, Any] | None:
    """Accept a provisional task and move it to done.

    Args:
        task_id: Task identifier
        validator: Name of the validator agent

    Returns:
        Updated task or None if not found
    """
    now = datetime.now().isoformat()

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET queue = 'done',
                claimed_by = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (now, task_id),
        )

        conn.execute(
            """
            INSERT INTO task_history (task_id, event, agent, details)
            VALUES (?, 'accepted', ?, NULL)
            """,
            (task_id, validator),
        )

        # Check if any tasks were blocked by this one
        _unblock_dependent_tasks(conn, task_id)

    return get_task(task_id)


def reject_completion(
    task_id: str,
    reason: str,
    validator: str | None = None,
) -> dict[str, Any] | None:
    """Reject a provisional task and move it back to incoming for retry.

    Increments attempt_count to track how many times the task has been tried.

    Args:
        task_id: Task identifier
        reason: Rejection reason
        validator: Name of the validator agent

    Returns:
        Updated task or None if not found
    """
    now = datetime.now().isoformat()

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET queue = 'incoming',
                claimed_by = NULL,
                claimed_at = NULL,
                commits_count = 0,
                turns_used = NULL,
                attempt_count = attempt_count + 1,
                updated_at = ?
            WHERE id = ?
            """,
            (now, task_id),
        )

        conn.execute(
            """
            INSERT INTO task_history (task_id, event, agent, details)
            VALUES (?, 'rejected', ?, ?)
            """,
            (task_id, validator, reason),
        )

    return get_task(task_id)


def review_reject_completion(
    task_id: str,
    reason: str,
    reviewer: str | None = None,
) -> dict[str, Any] | None:
    """Reject a task after gatekeeper review.

    Unlike reject_completion() which increments attempt_count (for validation
    failures like no commits), this increments rejection_count (for code
    quality issues found by reviewers).

    The task is moved back to incoming for re-implementation. The existing
    branch is preserved so the implementer can push fixes.

    Args:
        task_id: Task identifier
        reason: Review feedback / rejection reason
        reviewer: Name of the reviewer

    Returns:
        Updated task or None if not found
    """
    now = datetime.now().isoformat()

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET queue = 'incoming',
                claimed_by = NULL,
                claimed_at = NULL,
                rejection_count = rejection_count + 1,
                updated_at = ?
            WHERE id = ?
            """,
            (now, task_id),
        )

        conn.execute(
            """
            INSERT INTO task_history (task_id, event, agent, details)
            VALUES (?, 'review_rejected', ?, ?)
            """,
            (task_id, reviewer, reason),
        )

    return get_task(task_id)


def escalate_to_planning(task_id: str, plan_id: str) -> dict[str, Any] | None:
    """Escalate a failed task to planning by creating a planning task.

    Args:
        task_id: Original task identifier
        plan_id: ID of the new planning task

    Returns:
        Updated original task or None if not found
    """
    now = datetime.now().isoformat()

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET queue = 'escalated',
                has_plan = TRUE,
                plan_id = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (plan_id, now, task_id),
        )

        conn.execute(
            """
            INSERT INTO task_history (task_id, event, details)
            VALUES (?, 'escalated', ?)
            """,
            (task_id, f"plan_id={plan_id}"),
        )

    return get_task(task_id)


def fail_task(task_id: str, error: str) -> dict[str, Any] | None:
    """Move a task to the failed queue.

    Args:
        task_id: Task identifier
        error: Error message

    Returns:
        Updated task or None if not found
    """
    now = datetime.now().isoformat()

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET queue = 'failed',
                updated_at = ?
            WHERE id = ?
            """,
            (now, task_id),
        )

        conn.execute(
            """
            INSERT INTO task_history (task_id, event, details)
            VALUES (?, 'failed', ?)
            """,
            (task_id, error),
        )

    return get_task(task_id)


def _unblock_dependent_tasks(conn: sqlite3.Connection, completed_task_id: str) -> None:
    """Remove completed task from blocked_by lists of dependent tasks.

    Args:
        conn: Active database connection
        completed_task_id: ID of the completed task
    """
    # Find tasks that were blocked by this one
    cursor = conn.execute(
        "SELECT id, blocked_by FROM tasks WHERE blocked_by LIKE ?",
        (f"%{completed_task_id}%",),
    )

    for row in cursor.fetchall():
        task_id = row["id"]
        blocked_by = row["blocked_by"] or ""

        # Remove the completed task from the blocked_by list
        blockers = [b.strip() for b in blocked_by.split(",") if b.strip()]
        blockers = [b for b in blockers if b != completed_task_id]
        new_blocked_by = ",".join(blockers) if blockers else None

        conn.execute(
            "UPDATE tasks SET blocked_by = ? WHERE id = ?",
            (new_blocked_by, task_id),
        )

        if not new_blocked_by:
            conn.execute(
                """
                INSERT INTO task_history (task_id, event, details)
                VALUES (?, 'unblocked', ?)
                """,
                (task_id, f"dependency {completed_task_id} completed"),
            )


def check_dependencies_resolved(task_id: str) -> bool:
    """Check if all dependencies for a task are resolved.

    Args:
        task_id: Task identifier

    Returns:
        True if task has no unresolved dependencies
    """
    task = get_task(task_id)
    if not task:
        return False

    blocked_by = task.get("blocked_by")
    if not blocked_by:
        return True

    # Check each blocker
    blockers = [b.strip() for b in blocked_by.split(",") if b.strip()]
    for blocker_id in blockers:
        blocker = get_task(blocker_id)
        if blocker and blocker.get("queue") != "done":
            return False

    return True


def reset_stuck_claimed(timeout_minutes: int = 60) -> list[str]:
    """Reset tasks that have been claimed too long back to incoming.

    Args:
        timeout_minutes: How long a task can be claimed before reset

    Returns:
        List of task IDs that were reset
    """
    reset_ids = []

    with get_connection() as conn:
        # Find stuck tasks
        cursor = conn.execute(
            """
            SELECT id FROM tasks
            WHERE queue = 'claimed'
            AND claimed_at < datetime('now', ?)
            """,
            (f"-{timeout_minutes} minutes",),
        )

        for row in cursor.fetchall():
            task_id = row["id"]
            reset_ids.append(task_id)

            conn.execute(
                """
                UPDATE tasks
                SET queue = 'incoming',
                    claimed_by = NULL,
                    claimed_at = NULL,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (task_id,),
            )

            conn.execute(
                """
                INSERT INTO task_history (task_id, event, details)
                VALUES (?, 'reset', 'claimed timeout exceeded')
                """,
                (task_id,),
            )

    return reset_ids


# =============================================================================
# Agent Operations
# =============================================================================


def upsert_agent(
    name: str,
    role: str | None = None,
    running: bool = False,
    pid: int | None = None,
    current_task_id: str | None = None,
) -> dict[str, Any]:
    """Create or update an agent record.

    Args:
        name: Agent name (primary key)
        role: Agent role
        running: Whether agent is currently running
        pid: Process ID if running
        current_task_id: Task ID if working on one

    Returns:
        Agent record as dictionary
    """
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO agents (name, role, running, pid, current_task_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                role = excluded.role,
                running = excluded.running,
                pid = excluded.pid,
                current_task_id = excluded.current_task_id
            """,
            (name, role, running, pid, current_task_id),
        )

    return get_agent(name)


def get_agent(name: str) -> dict[str, Any] | None:
    """Get an agent record.

    Args:
        name: Agent name

    Returns:
        Agent record or None if not found
    """
    with get_connection() as conn:
        cursor = conn.execute("SELECT * FROM agents WHERE name = ?", (name,))
        row = cursor.fetchone()
        return dict(row) if row else None


def mark_agent_started(name: str, pid: int, task_id: str | None = None) -> dict[str, Any]:
    """Mark an agent as started.

    Args:
        name: Agent name
        pid: Process ID
        task_id: Optional task being worked on

    Returns:
        Updated agent record
    """
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE agents
            SET running = TRUE,
                pid = ?,
                current_task_id = ?,
                last_run_start = datetime('now')
            WHERE name = ?
            """,
            (pid, task_id, name),
        )

    return get_agent(name)


def mark_agent_finished(name: str) -> dict[str, Any] | None:
    """Mark an agent as finished.

    Args:
        name: Agent name

    Returns:
        Updated agent record or None
    """
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE agents
            SET running = FALSE,
                pid = NULL,
                current_task_id = NULL,
                last_run_end = datetime('now')
            WHERE name = ?
            """,
            (name,),
        )

    return get_agent(name)


def list_agents(running_only: bool = False) -> list[dict[str, Any]]:
    """List all agents.

    Args:
        running_only: Only include running agents

    Returns:
        List of agent records
    """
    with get_connection() as conn:
        if running_only:
            cursor = conn.execute("SELECT * FROM agents WHERE running = TRUE")
        else:
            cursor = conn.execute("SELECT * FROM agents")
        return [dict(row) for row in cursor.fetchall()]


# =============================================================================
# Task History
# =============================================================================


def get_task_history(task_id: str) -> list[dict[str, Any]]:
    """Get history for a task.

    Args:
        task_id: Task identifier

    Returns:
        List of history events in chronological order
    """
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT * FROM task_history
            WHERE task_id = ?
            ORDER BY timestamp ASC
            """,
            (task_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def add_history_event(
    task_id: str,
    event: str,
    agent: str | None = None,
    details: str | None = None,
) -> None:
    """Add a history event for a task.

    Args:
        task_id: Task identifier
        event: Event type (created, claimed, submitted, etc.)
        agent: Agent that triggered the event
        details: Additional details
    """
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO task_history (task_id, event, agent, details)
            VALUES (?, ?, ?, ?)
            """,
            (task_id, event, agent, details),
        )


# =============================================================================
# Project Operations
# =============================================================================


def create_project(
    project_id: str,
    title: str,
    description: str | None = None,
    branch: str | None = None,
    base_branch: str = "main",
    created_by: str = "human",
) -> dict[str, Any]:
    """Create a new project.

    Args:
        project_id: Unique project identifier (PROJ-xxx)
        title: Project title
        description: Optional description
        branch: Feature branch name (defaults to feature/{project_id})
        base_branch: Base branch to create from
        created_by: Who created the project

    Returns:
        Created project as dictionary
    """
    if branch is None:
        # Generate branch name from project ID
        branch = f"feature/{project_id.lower().replace('proj-', '')}"

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO projects (id, title, description, branch, base_branch, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (project_id, title, description, branch, base_branch, created_by),
        )

    return get_project(project_id)


def get_project(project_id: str) -> dict[str, Any] | None:
    """Get a project by ID.

    Args:
        project_id: Project identifier

    Returns:
        Project as dictionary or None if not found
    """
    with get_connection() as conn:
        cursor = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def list_projects(status: str | None = None) -> list[dict[str, Any]]:
    """List projects, optionally filtered by status.

    Args:
        status: Filter by status (draft, active, complete, abandoned)

    Returns:
        List of project dictionaries
    """
    with get_connection() as conn:
        if status:
            cursor = conn.execute(
                "SELECT * FROM projects WHERE status = ? ORDER BY created_at DESC",
                (status,),
            )
        else:
            cursor = conn.execute("SELECT * FROM projects ORDER BY created_at DESC")
        return [dict(row) for row in cursor.fetchall()]


def update_project(project_id: str, **fields) -> dict[str, Any] | None:
    """Update project fields.

    Args:
        project_id: Project identifier
        **fields: Fields to update

    Returns:
        Updated project or None if not found
    """
    if not fields:
        return get_project(project_id)

    set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
    values = list(fields.values())
    values.append(project_id)

    with get_connection() as conn:
        conn.execute(
            f"UPDATE projects SET {set_clause} WHERE id = ?",
            values,
        )

    return get_project(project_id)


def activate_project(project_id: str) -> dict[str, Any] | None:
    """Activate a project (set status to active).

    Args:
        project_id: Project identifier

    Returns:
        Updated project or None if not found
    """
    return update_project(project_id, status="active")


def complete_project(project_id: str) -> dict[str, Any] | None:
    """Mark a project as complete.

    Args:
        project_id: Project identifier

    Returns:
        Updated project or None if not found
    """
    return update_project(
        project_id,
        status="complete",
        completed_at=datetime.now().isoformat(),
    )


def get_project_tasks(project_id: str) -> list[dict[str, Any]]:
    """Get all tasks belonging to a project.

    Args:
        project_id: Project identifier

    Returns:
        List of task dictionaries sorted by priority
    """
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT * FROM tasks
            WHERE project_id = ?
            ORDER BY
                CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 ELSE 2 END,
                created_at ASC
            """,
            (project_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def check_project_completion(project_id: str) -> bool:
    """Check if all tasks in a project are done.

    If all tasks are done, updates project status to 'ready-for-pr'.

    Args:
        project_id: Project identifier

    Returns:
        True if project is complete (all tasks done)
    """
    tasks = get_project_tasks(project_id)
    if not tasks:
        return False

    all_done = all(t.get("queue") == "done" for t in tasks)

    if all_done:
        update_project(project_id, status="ready-for-pr")

    return all_done


def delete_project(project_id: str) -> bool:
    """Delete a project.

    Note: Does not delete associated tasks - they become orphaned.

    Args:
        project_id: Project identifier

    Returns:
        True if deleted, False if not found
    """
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cursor.rowcount > 0
