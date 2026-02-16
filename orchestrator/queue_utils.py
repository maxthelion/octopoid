"""Queue management backwards-compatible re-exports.

v2.1: Refactored into focused entity modules. Import from specific modules instead:
- orchestrator.sdk: get_sdk, get_orchestrator_id
- orchestrator.tasks: Task lifecycle and query functions
- orchestrator.projects: Project management
- orchestrator.breakdowns: Breakdown approval and recycling
- orchestrator.agent_markers: Agent task markers
- orchestrator.task_notes: Task notes persistence
- orchestrator.backpressure: Queue limits and status

This module provides backwards compatibility during migration.
"""

# SDK initialization
from .sdk import get_orchestrator_id, get_sdk

# Task lifecycle and CRUD
from .tasks import (
    accept_completion,
    approve_and_merge,
    claim_task,
    complete_task,
    create_task,
    escalate_to_planning,
    fail_task,
    find_task_by_id,
    get_continuation_tasks,
    get_review_feedback,
    get_task_by_id,
    hold_task,
    is_task_still_valid,
    list_tasks,
    mark_needs_continuation,
    reject_completion,
    reject_task,
    reset_task,
    resume_task,
    retry_task,
    review_reject_task,
    submit_completion,
    unclaim_task,
)

# Project management
from .projects import (
    activate_project,
    create_project,
    get_project,
    get_project_status,
    get_project_tasks,
    get_projects_dir,
    list_projects,
    send_to_breakdown,
)

# Breakdown management
from .breakdowns import (
    approve_breakdown,
    get_breakdowns_dir,
    is_burned_out,
    list_pending_breakdowns,
    recycle_to_breakdown,
)

# Agent markers
from .agent_markers import (
    clear_task_marker,
    clear_task_marker_for,
    read_task_marker,
    read_task_marker_for,
    write_task_marker,
)

# Task notes
from .task_notes import (
    NOTES_STDOUT_LIMIT,
    cleanup_task_notes,
    get_task_notes,
    save_task_notes,
)

# Backpressure and status
from .backpressure import (
    can_claim_task,
    can_create_task,
    count_open_prs,
    count_queue,
    get_queue_status,
)

# Legacy/backwards compatibility helpers
from .compat import (
    ALL_QUEUE_DIRS,
    find_task_file,
    get_queue_subdir,
    parse_task_file,
    resolve_task_file,
)

__all__ = [
    # SDK
    "get_sdk",
    "get_orchestrator_id",
    # Tasks
    "claim_task",
    "unclaim_task",
    "complete_task",
    "submit_completion",
    "accept_completion",
    "reject_completion",
    "review_reject_task",
    "get_review_feedback",
    "escalate_to_planning",
    "fail_task",
    "reject_task",
    "retry_task",
    "reset_task",
    "hold_task",
    "mark_needs_continuation",
    "resume_task",
    "find_task_by_id",
    "get_continuation_tasks",
    "create_task",
    "is_task_still_valid",
    "get_task_by_id",
    "list_tasks",
    "approve_and_merge",
    # Projects
    "create_project",
    "get_project",
    "list_projects",
    "activate_project",
    "get_project_tasks",
    "get_project_status",
    "send_to_breakdown",
    "get_projects_dir",
    # Breakdowns
    "get_breakdowns_dir",
    "list_pending_breakdowns",
    "approve_breakdown",
    "is_burned_out",
    "recycle_to_breakdown",
    # Agent markers
    "write_task_marker",
    "read_task_marker_for",
    "clear_task_marker_for",
    "read_task_marker",
    "clear_task_marker",
    # Task notes
    "get_task_notes",
    "save_task_notes",
    "cleanup_task_notes",
    "NOTES_STDOUT_LIMIT",
    # Backpressure
    "count_queue",
    "count_open_prs",
    "can_create_task",
    "can_claim_task",
    "get_queue_status",
    # Legacy compat
    "parse_task_file",
    "resolve_task_file",
    "find_task_file",
    "get_queue_subdir",
    "ALL_QUEUE_DIRS",
]
