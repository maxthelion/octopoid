"""Backwards-compatible re-exports from refactored modules.

Import from specific modules instead:
- orchestrator.sdk: get_sdk, get_orchestrator_id
- orchestrator.tasks: Task lifecycle and query functions
- orchestrator.projects: Project management
- orchestrator.breakdowns: Breakdown approval and recycling
- orchestrator.agent_markers: Agent task markers
- orchestrator.task_notes: Task notes persistence
- orchestrator.backpressure: Queue limits and status
"""

from .sdk import get_orchestrator_id, get_sdk
from .tasks import (
    accept_completion, approve_and_merge, claim_task, complete_task, create_task,
    escalate_to_planning, fail_task, find_task_by_id, get_continuation_tasks,
    get_review_feedback, get_task_by_id, hold_task, is_task_still_valid, list_tasks,
    mark_needs_continuation, reject_completion, reject_task, reset_task, resume_task,
    retry_task, review_reject_task, submit_completion, unclaim_task,
)
from .projects import (
    activate_project, create_project, get_project, get_project_status,
    get_project_tasks, get_projects_dir, list_projects, send_to_breakdown,
)
from .breakdowns import (
    approve_breakdown, get_breakdowns_dir, is_burned_out,
    list_pending_breakdowns, recycle_to_breakdown,
)
from .agent_markers import (
    clear_task_marker, clear_task_marker_for, read_task_marker,
    read_task_marker_for, write_task_marker,
)
from .task_notes import (
    NOTES_STDOUT_LIMIT, cleanup_task_notes, get_task_notes, save_task_notes,
)
from .backpressure import (
    can_claim_task, can_create_task, count_open_prs, count_queue, get_queue_status,
)
from .config import get_queue_dir, get_queue_limits
from .git_utils import cleanup_task_worktree
