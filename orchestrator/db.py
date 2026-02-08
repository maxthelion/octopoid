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


# Sentinel value for keyword arguments where None is a valid value
_SENTINEL = object()

# Schema version for migrations
SCHEMA_VERSION = 9


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
                checks TEXT,
                check_results TEXT,
                needs_rebase BOOLEAN DEFAULT FALSE,
                last_rebase_attempt_at DATETIME,
                staging_url TEXT,
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

        # Migration from v4 to v5: Add checks column
        if current < 5:
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN checks TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Migration from v5 to v6: Add check_results column
        if current < 6:
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN check_results TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists


        # Migration from v6 to v7: Add needs_rebase column
        if current < 7:
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN needs_rebase BOOLEAN DEFAULT FALSE")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Migration from v7 to v8: Add last_rebase_attempt_at column
        if current < 8:
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN last_rebase_attempt_at DATETIME")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Migration from v8 to v9: Add staging_url column
        if current < 9:
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN staging_url TEXT")
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
    checks: list[str] | None = None,
    staging_url: str | None = None,
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
        checks: Optional list of check names (e.g. ['gk-testing-octopoid'])
        staging_url: Optional staging/preview URL

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

    # Store checks as comma-separated string, or NULL if empty/None
    checks_str = ",".join(checks) if checks else None

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO tasks (id, file_path, priority, role, branch, complexity, blocked_by, project_id, auto_accept, checks, staging_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, file_path, priority, role, branch, complexity, blocked_by, project_id, auto_accept, checks_str, staging_url),
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


def _parse_checks(raw: str | None) -> list[str]:
    """Parse a comma-separated checks string into a list.

    Args:
        raw: Comma-separated checks string from DB, or None

    Returns:
        List of check names, empty list if None/empty
    """
    if not raw:
        return []
    return [c.strip() for c in raw.split(",") if c.strip()]


def _parse_check_results(raw: str | None) -> dict[str, dict]:
    """Parse a JSON check_results string into a dict.

    Args:
        raw: JSON string from DB, or None

    Returns:
        Dict mapping check name to result dict {status, summary, timestamp}
    """
    import json
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _serialize_check_results(results: dict[str, dict]) -> str | None:
    """Serialize check results dict to JSON string for DB storage.

    Args:
        results: Dict mapping check name to result dict

    Returns:
        JSON string or None if empty
    """
    import json
    if not results:
        return None
    return json.dumps(results)


def get_task(task_id: str) -> dict[str, Any] | None:
    """Get a task by ID.

    Args:
        task_id: Task identifier

    Returns:
        Task as dictionary or None if not found.
        Note: Task identifier is returned as 'id' key (not 'task_id').
        The 'checks' field is returned as a list of strings (empty list if NULL).
        The 'check_results' field is returned as a dict (empty dict if NULL).
    """
    with get_connection() as conn:
        cursor = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        task = dict(row)
        task["checks"] = _parse_checks(task.get("checks"))
        task["check_results"] = _parse_check_results(task.get("check_results"))
        return task


def get_task_by_path(file_path: str) -> dict[str, Any] | None:
    """Get a task by its file path.

    Args:
        file_path: Path to the task file

    Returns:
        Task as dictionary or None if not found.
        The 'checks' field is returned as a list of strings (empty list if NULL).
        The 'check_results' field is returned as a dict (empty dict if NULL).
    """
    with get_connection() as conn:
        cursor = conn.execute("SELECT * FROM tasks WHERE file_path = ?", (file_path,))
        row = cursor.fetchone()
        if row is None:
            return None
        task = dict(row)
        task["checks"] = _parse_checks(task.get("checks"))
        task["check_results"] = _parse_check_results(task.get("check_results"))
        return task


def update_task(task_id: str, **fields) -> dict[str, Any] | None:
    """Update task fields (except queue — use update_task_queue for that).

    To change a task's queue, use update_task_queue() which guarantees
    that side effects (unblocking dependents, clearing claimed_by, etc.)
    are always applied.

    Args:
        task_id: Task identifier
        **fields: Fields to update (must not include 'queue')

    Returns:
        Updated task or None if not found

    Raises:
        ValueError: If 'queue' is passed — use update_task_queue() instead
    """
    if "queue" in fields:
        raise ValueError(
            "Cannot update 'queue' via update_task(). "
            "Use update_task_queue(task_id, new_queue, ...) instead, "
            "which guarantees side effects like unblocking dependents."
        )

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


def update_task_queue(
    task_id: str,
    new_queue: str,
    *,
    claimed_by: str | None = _SENTINEL,
    claimed_at: str | None = _SENTINEL,
    commits_count: int | None = None,
    turns_used: int | None = None,
    attempt_count_increment: bool = False,
    rejection_count_increment: bool = False,
    has_plan: bool | None = None,
    plan_id: str | None = None,
    file_path: str | None = None,
    history_event: str | None = None,
    history_agent: str | None = None,
    history_details: str | None = None,
) -> dict[str, Any] | None:
    """Transition a task to a new queue, applying mandatory side effects.

    This is the ONLY function that should change a task's queue column.
    All queue transitions must go through here to guarantee that:
    - Transitioning to 'done' always unblocks dependent tasks
    - Transitioning to 'done' always clears claimed_by
    - History events are recorded

    Args:
        task_id: Task identifier
        new_queue: Target queue name
        claimed_by: New claimed_by value (_SENTINEL = don't change, None = clear)
        claimed_at: New claimed_at value (_SENTINEL = don't change, None = clear)
        commits_count: Set commits_count if provided
        turns_used: Set turns_used if provided
        attempt_count_increment: If True, increment attempt_count by 1
        rejection_count_increment: If True, increment rejection_count by 1
        has_plan: Set has_plan if provided
        plan_id: Set plan_id if provided
        file_path: Set file_path if provided
        history_event: Event name for task_history (auto-generated if None)
        history_agent: Agent name for history event
        history_details: Details for history event

    Returns:
        Updated task or None if not found
    """
    now = datetime.now().isoformat()

    # --- Build the SET clause dynamically ---
    set_parts = ["queue = ?", "updated_at = ?"]
    params: list[Any] = [new_queue, now]

    # Side effects for 'done' queue: always clear claimed_by
    if new_queue == "done":
        if claimed_by is _SENTINEL:
            claimed_by = None

    if claimed_by is not _SENTINEL:
        set_parts.append("claimed_by = ?")
        params.append(claimed_by)

    if claimed_at is not _SENTINEL:
        set_parts.append("claimed_at = ?")
        params.append(claimed_at)

    if commits_count is not None:
        set_parts.append("commits_count = ?")
        params.append(commits_count)

    if turns_used is not None:
        set_parts.append("turns_used = ?")
        params.append(turns_used)

    if attempt_count_increment:
        set_parts.append("attempt_count = attempt_count + 1")

    if rejection_count_increment:
        set_parts.append("rejection_count = rejection_count + 1")

    if has_plan is not None:
        set_parts.append("has_plan = ?")
        params.append(has_plan)

    if plan_id is not None:
        set_parts.append("plan_id = ?")
        params.append(plan_id)

    if file_path is not None:
        set_parts.append("file_path = ?")
        params.append(file_path)

    set_clause = ", ".join(set_parts)
    params.append(task_id)  # WHERE clause

    with get_connection() as conn:
        conn.execute(
            f"UPDATE tasks SET {set_clause} WHERE id = ?",
            params,
        )

        # --- Side effect: unblock dependents when moving to 'done' ---
        if new_queue == "done":
            _unblock_dependent_tasks(conn, task_id)

        # --- Record history event ---
        if history_event is None:
            # Auto-generate event name from queue transition
            history_event = f"queue_changed_to_{new_queue}"

        conn.execute(
            """
            INSERT INTO task_history (task_id, event, agent, details)
            VALUES (?, ?, ?, ?)
            """,
            (task_id, history_event, history_agent, history_details),
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
        tasks = []
        for row in cursor.fetchall():
            task = dict(row)
            task["checks"] = _parse_checks(task.get("checks"))
            task["check_results"] = _parse_check_results(task.get("check_results"))
            tasks.append(task)
        return tasks


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
    return update_task_queue(
        task_id,
        "claimed",
        claimed_by=agent_name,
        claimed_at=now,
        history_event="claimed",
        history_agent=agent_name,
    )


def submit_completion(
    task_id: str,
    commits_count: int = 0,
    turns_used: int | None = None,
) -> dict[str, Any] | None:
    """Submit a task for pre-check (move to provisional queue).

    The task stays in provisional until the scheduler processes it.
    If auto_accept is enabled, the scheduler will move it to done.

    Args:
        task_id: Task identifier
        commits_count: Number of commits made
        turns_used: Number of Claude turns used

    Returns:
        Updated task or None if not found
    """
    return update_task_queue(
        task_id,
        "provisional",
        commits_count=commits_count,
        turns_used=turns_used,
        history_event="submitted",
        history_details=f"commits={commits_count}, turns={turns_used}",
    )


def accept_completion(task_id: str, accepted_by: str | None = None) -> dict[str, Any] | None:
    """Accept a provisional task and move it to done.

    Side effects (guaranteed by update_task_queue):
    - claimed_by is cleared
    - Dependent tasks are unblocked

    Args:
        task_id: Task identifier
        accepted_by: Name of the agent or system that accepted (e.g. "scheduler", "gatekeeper", "human")

    Returns:
        Updated task or None if not found
    """
    return update_task_queue(
        task_id,
        "done",
        history_event="accepted",
        history_agent=accepted_by,
    )


def reject_completion(
    task_id: str,
    reason: str,
    rejected_by: str | None = None,
) -> dict[str, Any] | None:
    """Reject a provisional task and move it back to incoming for retry.

    Increments attempt_count to track how many times the task has been tried.

    Args:
        task_id: Task identifier
        reason: Rejection reason
        rejected_by: Name of the agent or system that rejected

    Returns:
        Updated task or None if not found
    """
    return update_task_queue(
        task_id,
        "incoming",
        claimed_by=None,
        claimed_at=None,
        commits_count=0,
        turns_used=0,
        attempt_count_increment=True,
        history_event="rejected",
        history_agent=rejected_by,
        history_details=reason,
    )


def review_reject_completion(
    task_id: str,
    reason: str,
    reviewer: str | None = None,
) -> dict[str, Any] | None:
    """Reject a task after gatekeeper review.

    Unlike reject_completion() which increments attempt_count (for pre-check
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
    return update_task_queue(
        task_id,
        "incoming",
        claimed_by=None,
        claimed_at=None,
        rejection_count_increment=True,
        history_event="review_rejected",
        history_agent=reviewer,
        history_details=reason,
    )


def record_check_result(
    task_id: str,
    check_name: str,
    status: str,
    summary: str = "",
) -> dict[str, Any] | None:
    """Record the result of an automated check for a task.

    Updates the check_results JSON field on the task. Each check result
    is keyed by check_name and contains {status, summary, timestamp}.

    Args:
        task_id: Task identifier
        check_name: Name of the check (e.g. 'gk-testing-octopoid')
        status: Result status ('pass' or 'fail')
        summary: Brief description of the result

    Returns:
        Updated task or None if not found
    """
    task = get_task(task_id)
    if not task:
        return None

    results = task.get("check_results", {})
    results[check_name] = {
        "status": status,
        "summary": summary,
        "timestamp": datetime.now().isoformat(),
    }

    serialized = _serialize_check_results(results)
    with get_connection() as conn:
        conn.execute(
            "UPDATE tasks SET check_results = ?, updated_at = ? WHERE id = ?",
            (serialized, datetime.now().isoformat(), task_id),
        )
        conn.execute(
            """
            INSERT INTO task_history (task_id, event, details)
            VALUES (?, ?, ?)
            """,
            (task_id, f"check_{status}", f"{check_name}: {summary[:200]}"),
        )

    return get_task(task_id)


def all_checks_passed(task_id: str) -> tuple[bool, list[str]]:
    """Check if all required checks for a task have passed.

    Args:
        task_id: Task identifier

    Returns:
        Tuple of (all_passed, list_of_failed_or_pending_check_names)
    """
    task = get_task(task_id)
    if not task:
        return False, []

    checks = task.get("checks", [])
    if not checks:
        return True, []

    results = task.get("check_results", {})
    not_passed = []
    for check_name in checks:
        result = results.get(check_name, {})
        if result.get("status") != "pass":
            not_passed.append(check_name)

    return len(not_passed) == 0, not_passed


def get_check_feedback(task_id: str) -> str:
    """Aggregate feedback from failed checks into markdown.

    Args:
        task_id: Task identifier

    Returns:
        Formatted markdown feedback string (empty if all passed)
    """
    task = get_task(task_id)
    if not task:
        return ""

    checks = task.get("checks", [])
    results = task.get("check_results", {})
    feedback_parts = []

    for check_name in checks:
        result = results.get(check_name, {})
        status = result.get("status", "pending")
        summary = result.get("summary", "")
        timestamp = result.get("timestamp", "")

        if status == "fail":
            part = f"### {check_name} ({timestamp})\n\n**FAILED** — {summary}\n"
            feedback_parts.append(part)

    return "\n".join(feedback_parts)


def escalate_to_planning(task_id: str, plan_id: str) -> dict[str, Any] | None:
    """Escalate a failed task to planning by creating a planning task.

    Args:
        task_id: Original task identifier
        plan_id: ID of the new planning task

    Returns:
        Updated original task or None if not found
    """
    return update_task_queue(
        task_id,
        "escalated",
        has_plan=True,
        plan_id=plan_id,
        history_event="escalated",
        history_details=f"plan_id={plan_id}",
    )


def fail_task(task_id: str, error: str) -> dict[str, Any] | None:
    """Move a task to the failed queue.

    Args:
        task_id: Task identifier
        error: Error message

    Returns:
        Updated task or None if not found
    """
    return update_task_queue(
        task_id,
        "failed",
        history_event="failed",
        history_details=error,
    )


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


def reconcile_stale_blockers() -> list[dict[str, Any]]:
    """Clear stale blockers from tasks where all referenced blockers are done.

    Scans all tasks with non-null blocked_by and checks whether each referenced
    blocker task has queue='done'. If ALL blockers for a task are done, clears
    blocked_by and records a history event. Tasks with a mix of done and non-done
    blockers are left unchanged.

    Returns:
        List of dicts describing each unblocked task:
        [{"task_id": str, "stale_blockers": [str]}]
    """
    unblocked = []

    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT id, blocked_by FROM tasks WHERE blocked_by IS NOT NULL AND blocked_by != ''"
        )
        blocked_tasks = cursor.fetchall()

        for row in blocked_tasks:
            task_id = row["id"]
            blocked_by = row["blocked_by"]
            blockers = [b.strip() for b in blocked_by.split(",") if b.strip()]

            if not blockers:
                continue

            # Check each blocker's status
            all_done = True
            stale_ids = []
            for blocker_id in blockers:
                bcursor = conn.execute(
                    "SELECT queue FROM tasks WHERE id = ?", (blocker_id,)
                )
                blocker_row = bcursor.fetchone()
                if blocker_row and blocker_row["queue"] == "done":
                    stale_ids.append(blocker_id)
                else:
                    all_done = False

            if all_done and stale_ids:
                conn.execute(
                    "UPDATE tasks SET blocked_by = NULL, updated_at = ? WHERE id = ?",
                    (datetime.now().isoformat(), task_id),
                )
                conn.execute(
                    """
                    INSERT INTO task_history (task_id, event, details)
                    VALUES (?, 'unblocked', ?)
                    """,
                    (task_id, f"stale blockers cleared: {', '.join(stale_ids)}"),
                )
                unblocked.append({
                    "task_id": task_id,
                    "stale_blockers": stale_ids,
                })

    return unblocked


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

        stuck_ids = [row["id"] for row in cursor.fetchall()]

    # Use update_task_queue for each (outside the connection to avoid nesting)
    for task_id in stuck_ids:
        update_task_queue(
            task_id,
            "incoming",
            claimed_by=None,
            claimed_at=None,
            history_event="reset",
            history_details="claimed timeout exceeded",
        )
        reset_ids.append(task_id)

    return reset_ids



# =============================================================================
# Rebaser Operations
# =============================================================================


def mark_for_rebase(task_id: str, reason: str = 'stale') -> dict[str, Any] | None:
    """Mark a task as needing rebase.

    Args:
        task_id: Task identifier
        reason: Why rebase is needed (e.g. 'stale', 'manual')

    Returns:
        Updated task or None if not found
    """
    task = get_task(task_id)
    if not task:
        return None

    result = update_task(task_id, needs_rebase=True)

    # Record history event
    add_history_event(
        task_id,
        'marked_for_rebase',
        details=f'reason={reason}',
    )

    return result


def clear_rebase_flag(task_id: str) -> dict[str, Any] | None:
    """Clear the needs_rebase flag after successful rebase.

    Args:
        task_id: Task identifier

    Returns:
        Updated task or None if not found
    """
    result = update_task(task_id, needs_rebase=False)

    add_history_event(
        task_id,
        'rebase_completed',
    )

    return result


def record_rebase_attempt(task_id: str) -> dict[str, Any] | None:
    """Record that a rebase was attempted for a task.

    Sets last_rebase_attempt_at to the current time.

    Args:
        task_id: Task identifier

    Returns:
        Updated task or None if not found
    """
    now = datetime.now().isoformat()
    return update_task(task_id, last_rebase_attempt_at=now)


def is_rebase_throttled(task_id: str, cooldown_minutes: int = 10) -> bool:
    """Check if a task's rebase is throttled (attempted too recently).

    Args:
        task_id: Task identifier
        cooldown_minutes: Minutes to wait between rebase attempts

    Returns:
        True if the task should not be rebased yet
    """
    task = get_task(task_id)
    if not task:
        return False

    last_attempt = task.get("last_rebase_attempt_at")
    if not last_attempt:
        return False

    try:
        last_dt = datetime.fromisoformat(last_attempt)
        elapsed = (datetime.now() - last_dt).total_seconds()
        return elapsed < (cooldown_minutes * 60)
    except (ValueError, TypeError):
        return False


def get_tasks_needing_rebase(
    queue: str | None = None,
) -> list[dict[str, Any]]:
    """Get tasks that have been marked for rebase.

    Args:
        queue: Optional queue filter (e.g. 'provisional')

    Returns:
        List of tasks needing rebase
    """
    with get_connection() as conn:
        conditions = ['needs_rebase = TRUE']
        params: list[Any] = []

        if queue:
            conditions.append('queue = ?')
            params.append(queue)

        where_clause = ' AND '.join(conditions)
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

        tasks = []
        for row in cursor.fetchall():
            task = dict(row)
            task['checks'] = _parse_checks(task.get('checks'))
            task['check_results'] = _parse_check_results(task.get('check_results'))
            tasks.append(task)
        return tasks


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
