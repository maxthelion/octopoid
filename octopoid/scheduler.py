#!/usr/bin/env python3
"""Main scheduler - runs on 1-minute ticks to evaluate and spawn agents."""

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path



from .config import (
    find_parent_project,
    get_agents,
    get_agents_runtime_dir,
    get_global_instructions_path,
    get_jobs_dir,
    get_base_branch,
    get_orchestrator_dir,
    get_scope,
    get_tasks_dir,
    is_system_paused,
)
from .git_utils import get_task_branch, get_worktree_path
from .lock_utils import locked_or_skip
from .port_utils import get_port_env_vars
from . import queue_utils
from .state_utils import (
    AgentState,
    is_overdue,
    load_state,
    mark_started,
    save_state,
)
from .pool import (
    count_running_instances,
    find_pid_for_task,
    get_active_task_ids,
    load_blueprint_pids,
    register_instance_pid,
)
from .result_handler import (
    extract_stdout_text,
    handle_agent_result,
    handle_agent_result_via_flow,
    handle_fixer_result,
)
from .prompt_renderer import (
    _load_global_instructions,
    _parse_agent_hooks,
    _build_required_steps,
    _load_review_section,
    _load_continuation_section,
    _load_intervention_context_for_prompt,
    _render_prompt,
)
from .system_health import (
    SYSTEMIC_FAILURE_THRESHOLD,
    _get_system_health_path,
    _load_system_health,
    _save_system_health,
    _record_systemic_failure,
    _spawn_diagnostic_agent,
    _auto_pause_and_diagnose,
    _handle_systemic_failure,
    _requeue_task_blameless,
    _reset_systemic_failure_counter,
)
from .housekeeping import (
    HOUSEKEEPING_JOBS,
    QUEUE_HEALTH_CHECK_INTERVAL_SECONDS,
    _check_queue_health_throttled,
    _evaluate_project_script_condition,
    _execute_project_flow_transition,
    _log_pid_snapshot,
    _register_orchestrator,
    _sweep_task_resources,
    _task_past_grace,
    check_and_evaluate_checks,
    check_and_requeue_expired_leases,
    check_and_update_finished_agents,
    check_project_completion,
    check_queue_health,
    renew_active_leases,
    send_heartbeat,
    sweep_stale_resources,
)

logger = logging.getLogger("octopoid.scheduler")


@dataclass
class AgentContext:
    """Everything the filter chain needs to evaluate and spawn an agent."""
    agent_config: dict
    agent_name: str
    role: str
    interval: int
    state: AgentState
    state_path: Path
    claimed_task: dict | None = None
    queue_counts: dict | None = None  # Pre-fetched from poll endpoint; None → individual API calls


def guard_enabled(ctx: AgentContext) -> tuple[bool, str]:
    """Check if agent is paused.

    Args:
        ctx: AgentContext containing agent configuration

    Returns:
        (should_proceed, reason_if_blocked)
    """
    if ctx.agent_config.get("paused", False):
        return (False, "paused")
    return (True, "")



def guard_pool_capacity(ctx: AgentContext) -> tuple[bool, str]:
    """Check if blueprint can spawn another instance (pool capacity guard).

    Replaces guard_not_running for the pool model. Cleans up dead PIDs first,
    then checks if running instances < max_instances.

    Args:
        ctx: AgentContext containing agent configuration

    Returns:
        (should_proceed, reason_if_blocked)
    """
    blueprint_name = ctx.agent_config.get("blueprint_name", ctx.agent_name)
    max_inst = ctx.agent_config.get("max_instances", 1)
    # NOTE: Do NOT call cleanup_dead_pids() here. count_running_instances
    # already ignores dead PIDs. Removing dead PIDs must only happen in
    # check_and_update_finished_agents, which processes the agent result first.
    # Calling cleanup here races with result processing and causes orphaned tasks.
    running = count_running_instances(blueprint_name)
    if running >= max_inst:
        return (False, f"at_capacity ({running}/{max_inst})")
    return (True, "")


def guard_interval(ctx: AgentContext) -> tuple[bool, str]:
    """Check if agent is due to run.

    Args:
        ctx: AgentContext containing agent state and interval

    Returns:
        (should_proceed, reason_if_blocked)
    """
    if not is_overdue(ctx.state, ctx.interval):
        return (False, "not due yet")
    return (True, "")


# =============================================================================
# Scheduler-specific backpressure checks
# =============================================================================


def guard_backpressure(ctx: AgentContext) -> tuple[bool, str]:
    """Check backpressure based on agent's claim_from queue.

    Uses pre-fetched queue_counts from ctx when available (poll-based path),
    falling back to individual API calls when not.

    Args:
        ctx: AgentContext containing agent config, state, and optional queue_counts

    Returns:
        (should_proceed, reason_if_blocked)
    """
    from .backpressure import count_queue, can_claim_task

    claim_from = ctx.agent_config.get("claim_from", "incoming")

    if claim_from == "incoming":
        # Get incoming count from pre-fetched data or via API
        if ctx.queue_counts is not None:
            incoming = ctx.queue_counts.get("incoming", 0)
        else:
            incoming = count_queue("incoming")
        if incoming == 0:
            return (False, "backpressure: no_tasks")
        can_proceed, reason = can_claim_task(ctx.queue_counts)
        if not can_proceed:
            return (False, f"backpressure: {reason}")
        return (True, "")
    elif claim_from == "intervention":
        # Fixer: check for tasks with needs_intervention=True
        try:
            sdk = queue_utils.get_sdk()
            tasks = sdk.tasks.list(needs_intervention=True)
            if not tasks:
                return (False, "backpressure: no_intervention_tasks")
        except Exception as e:
            logger.warning(f"guard_backpressure: failed to query needs_intervention tasks: {e}")
            return (False, "backpressure: intervention_query_failed")
        return (True, "")
    else:
        # Non-incoming queues (provisional, breakdown, etc.)
        if ctx.queue_counts is not None:
            count = ctx.queue_counts.get(claim_from, 0)
        else:
            count = count_queue(claim_from)
        if count == 0:
            return (False, f"backpressure: no_{claim_from}_tasks")
        return (True, "")


def guard_pre_check(ctx: AgentContext) -> tuple[bool, str]:
    """Run pre-check for work availability.

    Args:
        ctx: AgentContext containing agent name and config

    Returns:
        (should_proceed, reason_if_blocked)
    """
    if not run_pre_check(ctx.agent_name, ctx.agent_config):
        return (False, "pre-check: no work")
    return (True, "")


def guard_claim_task(ctx: AgentContext) -> tuple[bool, str]:
    """Claim a task for scripts-mode agents (sets ctx.claimed_task).

    Only active for agents with spawn_mode=scripts.
    Reads claim queue from the flow definition based on agent role.
    Falls back to 'incoming' if flow is not found.

    Args:
        ctx: AgentContext containing agent config

    Returns:
        (should_proceed, reason_if_blocked)
    """
    spawn_mode = ctx.agent_config.get("spawn_mode", "scripts")
    if spawn_mode != "scripts":
        # Not a scripts-mode agent — skip claim, let the role module claim
        return (True, "")

    claim_from = ctx.agent_config.get("claim_from", "incoming")
    type_filter = ctx.agent_config.get("type_filter")
    # When claiming from a non-incoming queue (e.g. provisional), do not filter
    # by the agent's own role — the tasks there may have a different original role.
    role_filter = ctx.role if claim_from == "incoming" else None

    if claim_from == "intervention":
        # Fixer: find a task with needs_intervention=True without transitioning it
        blueprint_name = ctx.agent_config.get("blueprint_name", ctx.agent_name)
        active_task_ids = get_active_task_ids(blueprint_name)
        try:
            sdk = queue_utils.get_sdk()
            tasks = sdk.tasks.list(needs_intervention=True)
        except Exception as e:
            logger.warning(f"guard_claim_task: failed to list needs_intervention tasks: {e}")
            return (False, "intervention_query_failed")

        MAX_FIXER_ATTEMPTS = 3

        task = None
        for candidate in tasks:
            if candidate.get("id") in active_task_ids:
                continue

            # Circuit breaker: count previous fixer attempts via intervention_reply messages.
            # If a task has been through the fixer loop too many times, move it to
            # terminal failed and notify the user instead of spawning another fixer.
            cid = candidate.get("id", "")

            # Skip tasks already moved to failed — the update may have succeeded
            # even if the subsequent message post failed, so re-check queue state.
            if candidate.get("queue") == "failed":
                continue

            # Skip done tasks — needs_intervention=True on a done task is stale.
            # Clear the flag so the task is not picked up again.
            if candidate.get("queue") == "done":
                try:
                    sdk.tasks.update(cid, needs_intervention=False)
                    logger.debug(f"Fixer circuit breaker: cleared stale needs_intervention for done task {cid}")
                except Exception as clear_e:
                    logger.debug(f"Fixer circuit breaker: failed to clear needs_intervention for done task {cid}: {clear_e}")
                continue

            try:
                msgs = sdk._request("GET", f"/api/v1/tasks/{cid}/messages")
                fixer_replies = [
                    m for m in msgs.get("messages", [])
                    if m.get("type") == "intervention_reply"
                ]
                # Skip if circuit breaker already fired for this task — prevents
                # duplicate messages when the task remains in intervention state
                # across multiple scheduler ticks.
                circuit_breaker_msgs = [
                    m for m in msgs.get("messages", [])
                    if m.get("type") == "circuit_breaker"
                ]
                if circuit_breaker_msgs:
                    continue
                # Fail immediately on systemic escalation — if the fixer already
                # identified a systemic issue, another fixer run won't help.
                systemic_msgs = [
                    m for m in msgs.get("messages", [])
                    if m.get("type") == "intervention_systemic"
                ]
            except Exception as msg_e:
                logger.debug(f"Fixer circuit breaker: failed to check messages for {cid}: {msg_e}")
                fixer_replies = []
                systemic_msgs = []

            if systemic_msgs:
                logger.warning(
                    f"Fixer circuit breaker: task {cid} has systemic escalation, moving to failed immediately"
                )
                try:
                    from .tasks import fail_task  # noqa: PLC0415
                    fail_task(cid, reason="Fixer circuit breaker: systemic escalation — task cannot be auto-fixed",
                              source="fixer-circuit-breaker-systemic")
                except Exception as update_e:
                    logger.error(f"Fixer circuit breaker: failed to move task {cid} to failed (systemic): {update_e}")
                try:
                    sdk.messages.create(
                        task_id=cid,
                        from_actor="scheduler",
                        to_actor="human",
                        type="circuit_breaker",
                        content=(
                            f"Task {cid} had a systemic escalation and has been moved to failed. "
                            f"The fixer identified a systemic infrastructure issue. Please investigate manually."
                        ),
                    )
                except Exception as msg_post_e:
                    logger.error(f"Fixer circuit breaker: failed to post circuit_breaker message for {cid} (systemic): {msg_post_e}")
                continue  # Skip this candidate, try the next one

            if len(fixer_replies) >= MAX_FIXER_ATTEMPTS:
                logger.warning(
                    f"Fixer circuit breaker: task {cid} has {len(fixer_replies)} "
                    f"fixer attempts (max {MAX_FIXER_ATTEMPTS}), moving to failed"
                )
                try:
                    from .tasks import fail_task  # noqa: PLC0415
                    fail_task(cid, reason=f"Fixer circuit breaker: {len(fixer_replies)} attempts exhausted",
                              source="fixer-circuit-breaker")
                except Exception as update_e:
                    logger.error(f"Fixer circuit breaker: failed to move task {cid} to failed: {update_e}")
                try:
                    sdk.messages.create(
                        task_id=cid,
                        from_actor="scheduler",
                        to_actor="human",
                        type="circuit_breaker",
                        content=(
                            f"Task {cid} exhausted {len(fixer_replies)} fixer attempts and has been "
                            f"moved to failed. The fixer kept reporting 'fixed' but the flow resume "
                            f"kept failing — this likely indicates an infrastructure issue rather than "
                            f"a code problem. Please investigate manually."
                        ),
                    )
                except Exception as msg_post_e:
                    logger.error(f"Fixer circuit breaker: failed to post circuit_breaker message for {cid}: {msg_post_e}")
                continue  # Skip this candidate, try the next one

            task = candidate
            break

        if task is None:
            return (False, "no_task_to_claim")

        # Write task to agent runtime dir (same as claim_and_prepare_task does)
        agent_dir = get_agents_runtime_dir() / ctx.agent_name
        agent_dir.mkdir(parents=True, exist_ok=True)
        import json as _json
        (agent_dir / "claimed_task.json").write_text(_json.dumps(task, indent=2))

        ctx.claimed_task = task
        return (True, "")

    task = claim_and_prepare_task(
        agent_name=ctx.agent_name,
        role=ctx.role,
        role_filter=role_filter,
        type_filter=type_filter,
        claim_from=claim_from,
    )

    if task is None:
        return (False, "no_task_to_claim")

    # Dedup check: skip if another running instance of this blueprint is already
    # working on the same task. This prevents two pool instances from racing to
    # claim the same task when only one provisional/incoming task exists.
    #
    # IMPORTANT: Do NOT requeue to incoming here. The task is already being
    # handled by a running instance. Requeuing would yank it out from under
    # the running agent, causing the result to be processed against the wrong
    # queue state (e.g. gatekeeper approves but task is now in 'claimed').
    blueprint_name = ctx.agent_config.get("blueprint_name", ctx.agent_name)
    active_task_ids = get_active_task_ids(blueprint_name)
    if task["id"] in active_task_ids:
        logger.debug(
            f"guard_claim_task: task {task['id']} already being processed by "
            f"another {blueprint_name} instance, skipping (not requeuing)"
        )
        return (False, f"duplicate_task: {task['id']} already being processed")

    ctx.claimed_task = task
    return (True, "")


def guard_task_description_nonempty(ctx: AgentContext) -> tuple[bool, str]:
    """Guard against spawning agents for tasks with empty or missing descriptions.

    Only active for scripts-mode agents with a claimed task. Checks that the
    task's content (from the server) is non-empty. If content is missing or empty,
    the task is moved to the failed queue and no agent is spawned.

    Args:
        ctx: AgentContext containing the claimed task

    Returns:
        (should_proceed, reason_if_blocked)
    """
    if not ctx.claimed_task:
        return (True, "")

    spawn_mode = ctx.agent_config.get("spawn_mode", "scripts")
    if spawn_mode != "scripts":
        return (True, "")

    content = ctx.claimed_task.get("content", "")
    if content and content.strip():
        return (True, "")

    # Content is empty — task has no description on the server
    task_id = ctx.claimed_task.get("id", "unknown")
    reason = f"Task description is empty — TASK-{task_id}.md has no content on server"

    logger.debug(f"guard_task_description_nonempty: {reason}")

    try:
        queue_utils.fail_task(task_id, reason=reason, source='guard-empty-description', claimed_by=None)
        logger.debug(f"Moved task {task_id} to failed: {reason}")
    except Exception as e:
        logger.error(f"move-to-failed failed for {task_id}: {e}")
        logger.debug(f"guard_task_description_nonempty: failed to update task {task_id}: {e}")

    return (False, f"empty_description: {reason}")


# Guard chain: cheapest checks first, expensive checks last
AGENT_GUARDS = [
    guard_enabled,
    guard_pool_capacity,
    guard_interval,
    guard_backpressure,
    guard_pre_check,
    guard_claim_task,
    guard_task_description_nonempty,
]


def evaluate_agent(ctx: AgentContext) -> bool:
    """Run the guard chain. Returns True if agent should be spawned.

    Args:
        ctx: AgentContext containing all agent evaluation state

    Returns:
        bool: True if all guards pass and agent should spawn, False otherwise
    """
    for guard in AGENT_GUARDS:
        proceed, reason = guard(ctx)
        if not proceed:
            logger.debug(f"Agent {ctx.agent_name}: BLOCKED by {guard.__name__}: {reason}")
            return False
        logger.debug(f"Agent {ctx.agent_name}: {guard.__name__} passed")
    logger.debug(f"Agent {ctx.agent_name}: all guards passed, spawning")
    return True



def run_pre_check(agent_name: str, agent_config: dict) -> bool:
    """Run the agent's pre-check command to see if there's work available.

    Args:
        agent_name: Name of the agent
        agent_config: Agent configuration dict

    Returns:
        True if agent should be spawned (work available or no pre-check configured)
        False if pre-check indicates no work available
    """
    pre_check_cmd = agent_config.get("pre_check")
    if not pre_check_cmd:
        # No pre-check configured, always spawn
        return True

    trigger = agent_config.get("pre_check_trigger", "non_empty")
    logger.debug(f"Running pre-check for {agent_name}: {pre_check_cmd}")

    try:
        # Run from the parent project directory
        result = subprocess.run(
            pre_check_cmd,
            shell=True,
            cwd=find_parent_project(),
            capture_output=True,
            text=True,
            timeout=10,  # Pre-checks should be fast
        )

        if trigger == "non_empty":
            has_work = bool(result.stdout.strip())
        elif trigger == "exit_zero":
            has_work = result.returncode == 0
        elif trigger == "exit_nonzero":
            has_work = result.returncode != 0
        else:
            logger.debug(f"Unknown pre_check_trigger: {trigger}, defaulting to spawn")
            has_work = True

        logger.debug(f"Pre-check for {agent_name}: has_work={has_work} (stdout={result.stdout.strip()!r})")
        return has_work

    except subprocess.TimeoutExpired:
        logger.debug(f"Pre-check for {agent_name} timed out, spawning anyway")
        return True
    except Exception as e:
        logger.debug(f"Pre-check for {agent_name} failed: {e}, spawning anyway")
        return True


def _verify_submodule_isolation(sub_path: Path, agent_name: str) -> None:
    """Verify that a worktree's submodule has its own git object store.

    Orchestrator_impl agents work in a submodule inside their worktree.
    The worktree's submodule and the main checkout's submodule have
    SEPARATE git object stores. A commit in one is invisible from the
    other. This function verifies the submodule .git pointer is correct.

    If the submodule's .git points to the main checkout's object store
    (instead of the worktree's), the agent would commit to the wrong
    location and the approve script would not find the commits.

    Args:
        sub_path: Path to the submodule directory in the worktree
        agent_name: Agent name for logging
    """
    git_pointer = sub_path / ".git"
    if not git_pointer.exists():
        logger.warning(f"Agent {agent_name} submodule has no .git at {git_pointer}")
        return

    content = git_pointer.read_text().strip()

    # A submodule .git is a file containing "gitdir: <path>"
    if not content.startswith("gitdir:"):
        logger.warning(f"Agent {agent_name} submodule .git is not a gitdir pointer: {content[:80]}")
        return

    gitdir = content.split("gitdir:", 1)[1].strip()

    # The gitdir should reference the worktree's modules directory, NOT
    # the main checkout's modules. A healthy worktree submodule points to
    # something like: ../../.git/worktrees/<name>/modules/orchestrator
    # A BROKEN one would point to: ../../.git/modules/orchestrator
    # (which is the main checkout's object store).
    if "worktrees" in gitdir or "worktree" in gitdir:
        logger.debug(f"Agent {agent_name} submodule .git correctly points to worktree store: {gitdir}")
    else:
        # This is the dangerous case — submodule shares the main checkout's store
        logger.warning(
            f"Agent {agent_name} submodule may share git store with main checkout. "
            f"gitdir={gitdir}"
        )


def peek_task_branch(role: str) -> str | None:
    """Peek at the next task for a role and return its branch.

    Used by the scheduler to create worktrees on the correct branch.
    For breakdown agents, this peeks at the breakdown queue.
    For implement agents, this peeks at incoming queue.

    orchestrator_impl agents always use a Boxen worktree based on main.
    They work inside the orchestrator/ submodule within that worktree,
    so the worktree itself must be on main (not a submodule branch).

    Args:
        role: Agent role (breakdown, implement, etc.)

    Returns:
        Branch name if a task is available, None otherwise
    """
    # orchestrator_impl always uses main — the agent works inside the
    # orchestrator/ submodule, not on a Boxen feature branch.
    if role == "orchestrator_impl":
        return None

    # Map roles to the queues they pull from
    role_queues = {
        "breakdown": "breakdown",
        "implement": "incoming",
        "test": "incoming",
    }

    queue = role_queues.get(role)
    if not queue:
        return None

    tasks = queue_utils.list_tasks(queue)
    if not tasks:
        return None

    # Return the branch of the first (highest priority) task
    branch = tasks[0].get("branch")
    return branch if branch and branch != "main" else None


def check_continuation_for_agent(agent_name: str) -> dict | None:
    """Check if an agent has continuation work to resume.

    Looks for:
    1. Task marker file (current_task.json) linking agent to a task
    2. Tasks in needs_continuation queue assigned to this agent

    Args:
        agent_name: Name of the agent to check

    Returns:
        Task dict with '_continuation' flag if work found, None otherwise
    """
    from .config import ACTIVE_QUEUES

    # Check task marker first - most reliable signal
    marker = queue_utils.read_task_marker_for(agent_name)
    if marker:
        task_id = marker.get("task_id")
        if task_id and queue_utils.is_task_still_valid(task_id):
            task = queue_utils.find_task_by_id(task_id, queues=ACTIVE_QUEUES)
            if task:
                task["_continuation"] = True
                return task
        else:
            # Task is done/failed - clear stale marker
            queue_utils.clear_task_marker_for(agent_name)

    # Check needs_continuation queue
    continuation_tasks = queue_utils.get_continuation_tasks(agent_name=agent_name)
    if continuation_tasks:
        task = continuation_tasks[0]
        task["_continuation"] = True
        return task

    return None


_UNSET = object()


def claim_and_prepare_task(
    agent_name: str,
    role: str,
    type_filter: str | None = None,
    claim_from: str = "incoming",
    role_filter: str | None = _UNSET,  # type: ignore[assignment]
) -> dict | None:
    """Claim a task and write it to the agent's runtime dir.

    Checks for continuation work first, then tries to claim a fresh task.
    Writes the full task dict (including file content) to claimed_task.json
    so the agent can read it without resolving file paths.

    Args:
        agent_name: Name of the agent
        role: Agent role (e.g. 'implement')
        type_filter: Only claim tasks with this type (from agent config)
        claim_from: Queue to claim from (default: 'incoming'). Gatekeeper uses 'provisional'.
        role_filter: Role to filter tasks by. Defaults to `role` when unset.
            Pass None explicitly to claim tasks regardless of their original
            role (e.g. gatekeeper reviewing provisional tasks with role='implement').

    Returns:
        Task dict if work is available, None otherwise
    """
    # 1. Check for continuation work (only for incoming queue claims)
    task = None
    if claim_from == "incoming":
        task = check_continuation_for_agent(agent_name)

    # 2. If no continuation, claim a fresh task
    if task is None:
        effective_role_filter = role if role_filter is _UNSET else role_filter
        task = queue_utils.claim_task(
            role_filter=effective_role_filter,
            agent_name=agent_name,
            type_filter=type_filter,
            from_queue=claim_from,
        )

    if task is None:
        return None

    # 3. Write full task dict to agent runtime dir
    agent_dir = get_agents_runtime_dir() / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    task_file = agent_dir / "claimed_task.json"
    with open(task_file, "w") as f:
        json.dump(task, f, indent=2)

    return task


# =============================================================================
# Per-job interval management
# =============================================================================


def get_scheduler_state_path() -> Path:
    """Get path to the per-job scheduler state file."""
    from .config import get_runtime_dir
    return get_runtime_dir() / "scheduler_state.json"


def load_scheduler_state() -> dict:
    """Load per-job last-run timestamps from disk.

    Returns:
        Dict with structure {"jobs": {"job_name": "2026-01-01T00:00:00", ...}}
        Returns empty structure if file is missing or unreadable.
    """
    path = get_scheduler_state_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"jobs": {}}


def save_scheduler_state(state: dict) -> None:
    """Persist per-job last-run timestamps to disk."""
    path = get_scheduler_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def is_job_due(scheduler_state: dict, job_name: str, interval_seconds: int) -> bool:
    """Check whether a named job is due to run.

    A job is due if it has never run, or if more than interval_seconds have
    elapsed since its last run.

    Args:
        scheduler_state: Loaded state dict from load_scheduler_state()
        job_name: Canonical name of the job (usually the function name)
        interval_seconds: Minimum seconds between runs

    Returns:
        True if the job should run now
    """
    last_run_str = scheduler_state.get("jobs", {}).get(job_name)
    if not last_run_str:
        return True
    try:
        last_run = datetime.fromisoformat(last_run_str)
        elapsed = (datetime.now() - last_run).total_seconds()
        return elapsed >= interval_seconds
    except (ValueError, TypeError):
        return True


def record_job_run(scheduler_state: dict, job_name: str) -> None:
    """Record that a job ran right now."""
    if "jobs" not in scheduler_state:
        scheduler_state["jobs"] = {}
    scheduler_state["jobs"][job_name] = datetime.now().isoformat()


def get_scheduler_lock_path() -> Path:
    """Get path to the global scheduler lock file."""
    from .config import get_runtime_dir
    return get_runtime_dir() / "scheduler.lock"


def get_agent_lock_path(agent_name: str) -> Path:
    """Get path to an agent's lock file."""
    return get_agents_runtime_dir() / agent_name / "lock"


def get_agent_state_path(agent_name: str) -> Path:
    """Get path to an agent's state file."""
    return get_agents_runtime_dir() / agent_name / "state.json"


def get_agent_env_path(agent_name: str) -> Path:
    """Get path to an agent's env.sh file."""
    return get_agents_runtime_dir() / agent_name / "env.sh"


def write_agent_env(agent_name: str, agent_id: int, role: str, agent_config: dict | None = None) -> Path:
    """Write environment variables file for an agent.

    Args:
        agent_name: Name of the agent
        agent_id: Numeric ID of the agent
        role: Agent role
        agent_config: Optional agent configuration for extra vars

    Returns:
        Path to env.sh file
    """
    env_path = get_agent_env_path(agent_name)
    env_path.parent.mkdir(parents=True, exist_ok=True)

    from .config import get_shared_dir
    parent_project = find_parent_project()
    worktree_path = get_worktree_path(agent_name)
    shared_dir = get_shared_dir()

    port_vars = get_port_env_vars(agent_id)

    lines = [
        "#!/bin/bash",
        f"export AGENT_NAME='{agent_name}'",
        f"export AGENT_ID='{agent_id}'",
        f"export AGENT_ROLE='{role}'",
        f"export PARENT_PROJECT='{parent_project}'",
        f"export WORKTREE='{worktree_path}'",
        f"export SHARED_DIR='{shared_dir}'",
        f"export ORCHESTRATOR_DIR='{get_orchestrator_dir()}'",
    ]

    # Add model override from agent config
    if agent_config and "model" in agent_config:
        lines.append(f"export AGENT_MODEL='{agent_config['model']}'")

    # Add focus for specialist agents (configured in agents.yaml)
    if agent_config and "focus" in agent_config:
        lines.append(f"export AGENT_FOCUS='{agent_config['focus']}'")

    # Pass debug mode — propagate if octopoid logger is at DEBUG level
    if logging.getLogger("octopoid").isEnabledFor(logging.DEBUG):
        lines.append("export ORCHESTRATOR_DEBUG='1'")

    for key, value in port_vars.items():
        lines.append(f"export {key}='{value}'")

    env_path.write_text("\n".join(lines) + "\n")
    return env_path


def _get_server_url_from_config() -> str:
    """Read server URL from .octopoid/config.yaml."""
    try:
        import yaml
        config_path = get_orchestrator_dir() / "config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f)
            return config.get("server", {}).get("url", "")
    except Exception:
        pass
    return ""


# Prompt rendering functions live in prompt_renderer.py (imported at the top of this module)


def prepare_task_directory(
    task: dict,
    agent_name: str,
    agent_config: dict,
) -> Path:
    """Prepare a self-contained task directory for script-based agents.

    Creates:
        {task_dir}/worktree/     - git worktree (agent's cwd)
        {task_dir}/task.json     - task metadata
        {task_dir}/prompt.md     - rendered prompt
        {task_dir}/env.sh        - environment for scripts
        {task_dir}/scripts/      - executable agent scripts
        {task_dir}/stdout.log    - agent stdout (read by scheduler for result inference)
        {task_dir}/notes.md      - progress notes
    """
    from .git_utils import create_task_worktree

    task_id = task["id"]
    task_dir = get_tasks_dir() / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    # Archive previous stdout for debugging and save tail for continuation context.
    # The archived file (stdout-{role}-{attempt}.log) preserves each agent run's
    # full output. The continuer agent's _render_prompt reads prev_stdout.log to
    # build the continuation section so the agent knows where the previous run left off.
    stdout_path = task_dir / "stdout.log"
    if stdout_path.exists():
        try:
            prev_content = stdout_path.read_text(errors="replace")
            prev_content = extract_stdout_text(prev_content)
            tail = prev_content[-3000:]
            (task_dir / "prev_stdout.log").write_text(tail)
            # Archive as stdout-{blueprint}-{attempt}.log to preserve per-attempt output
            blueprint_name = agent_config.get("blueprint_name", agent_name)
            attempt = task.get("attempt_count", 0)
            archived_path = task_dir / f"stdout-{blueprint_name}-{attempt}.log"
            stdout_path.rename(archived_path)
        except OSError:
            pass

    # Clean stale artifacts from previous runs
    for stale_file in ['stdout.log', 'notes.md']:
        stale_path = task_dir / stale_file
        if stale_path.exists():
            stale_path.unlink()
            logger.debug(f"Cleaned stale {stale_file} from {task_dir}")

    # Create worktree in detached HEAD state (worktrees must never checkout a named branch).
    # The agent creates a task-specific branch via create_task_branch when ready to push.
    base_branch = task.get("branch") or get_base_branch()
    worktree_path = create_task_worktree(task)

    # Compute task_branch for env.sh only — do NOT checkout the branch in the worktree.
    task_branch = get_task_branch(task)

    # Write task.json
    import json
    (task_dir / "task.json").write_text(json.dumps(task, indent=2))

    # Copy and template scripts from agent directory
    agent_dir = agent_config.get("agent_dir")
    if not agent_dir or not (Path(agent_dir) / "scripts").exists():
        raise ValueError(f"Agent directory or scripts not found: {agent_dir}")

    scripts_src = Path(agent_dir) / "scripts"
    logger.debug(f"Using scripts from agent directory: {scripts_src}")

    scripts_dest = task_dir / "scripts"
    scripts_dest.mkdir(exist_ok=True)

    venv_python = sys.executable  # Use the scheduler's Python

    for script in scripts_src.iterdir():
        if script.name.startswith("."):
            continue
        dest = scripts_dest / script.name
        content = script.read_text()
        # Replace shebang with explicit venv python
        if content.startswith("#!/usr/bin/env python3"):
            content = f"#!{venv_python}\n" + content.split("\n", 1)[1]
        dest.write_text(content)
        dest.chmod(0o755)

    # Write env.sh (task_branch already computed above)
    orchestrator_submodule = find_parent_project() / "orchestrator"
    env_lines = [
        "#!/bin/bash",
        f"export TASK_ID='{task_id}'",
        f"export TASK_TITLE='{task.get('title', '')}'",
        f"export BASE_BRANCH='{base_branch}'",
        f"export TASK_BRANCH='{task_branch}'",
        f"export OCTOPOID_SERVER_URL='{os.environ.get('OCTOPOID_SERVER_URL') or _get_server_url_from_config()}'",
        f"export AGENT_NAME='{agent_name}'",
        f"export WORKTREE='{worktree_path}'",
        f"export ORCHESTRATOR_PYTHONPATH='{orchestrator_submodule}'",
        f"export NOTES_FILE='{task_dir / 'notes.md'}'",
    ]
    (task_dir / "env.sh").write_text("\n".join(env_lines) + "\n")

    # Render and write prompt
    (task_dir / "prompt.md").write_text(_render_prompt(task, agent_config))

    logger.debug(f"Prepared task directory: {task_dir}")
    return task_dir


def invoke_claude(task_dir: Path, agent_config: dict) -> int:
    """Invoke claude -p directly for a script-based agent.

    Args:
        task_dir: Path to the prepared task directory
        agent_config: Agent configuration dict

    Returns:
        PID of the spawned claude process
    """
    import json

    worktree_path = task_dir / "worktree"
    if not worktree_path.exists():
        # Worktree may be at a different location - check task.json
        task_data = json.loads((task_dir / "task.json").read_text())
        task_id = task_data.get("id", "")
        worktree_path = get_tasks_dir() / task_id / "worktree"

    prompt_path = task_dir / "prompt.md"
    prompt = prompt_path.read_text()

    model = agent_config.get("model", "sonnet")
    max_turns = agent_config.get("max_turns", 200)

    cmd = [
        "claude",
        "-p", prompt,
        "--allowedTools", "Read,Write,Edit,Glob,Grep,Bash,Skill",
        "--max-turns", str(max_turns),
        "--model", model,
        "--output-format", "json",
    ]

    # Write PostToolUse hook into worktree so the agent tracks turn counts.
    # File size of tool_counter = number of tool calls = turns used.
    claude_dir = worktree_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    hooks_dir = claude_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_script = hooks_dir / "count-tool-use.sh"
    hook_script.write_text(
        '#!/bin/bash\n'
        '[ -n "$OCTOPOID_TASK_DIR" ] && printf x >> "$OCTOPOID_TASK_DIR/tool_counter"\n'
        'exit 0\n'
    )
    hook_script.chmod(0o755)
    settings_path = claude_dir / "settings.json"
    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    turn_counter_hook = {
        "matcher": "",
        "hooks": [{"type": "command", "command": ".claude/hooks/count-tool-use.sh"}],
    }
    settings.setdefault("hooks", {}).setdefault("PostToolUse", []).append(turn_counter_hook)
    # Restrict file operations to the worktree (project-relative patterns).
    # Prevents agents from writing to the main tree via absolute paths.
    settings.setdefault("permissions", {})["allow"] = [
        "Bash(*)", "Read(/**)", "Write(/**)", "Edit(/**)",
        "Glob", "Grep", "Skill",
    ]
    settings_path.write_text(json.dumps(settings))

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env["OCTOPOID_TASK_DIR"] = str(task_dir)
    # Source env.sh values using shlex to handle quoted values correctly
    env_sh = task_dir / "env.sh"
    if env_sh.exists():
        import shlex
        for line in env_sh.read_text().splitlines():
            if line.startswith("export "):
                assignment = line[7:]  # strip "export "
                eq_pos = assignment.find("=")
                if eq_pos < 1:
                    continue
                key = assignment[:eq_pos]
                raw_val = assignment[eq_pos + 1:]
                try:
                    val = shlex.split(raw_val)[0] if raw_val else ""
                except ValueError:
                    val = raw_val.strip("'\"")
                env[key] = val

    # Set up log files
    stdout_log = task_dir / "stdout.log"
    stderr_log = task_dir / "stderr.log"
    stdout_file = open(stdout_log, "w")
    stderr_file = open(stderr_log, "w")

    process = subprocess.Popen(
        cmd,
        cwd=worktree_path,
        env=env,
        stdout=stdout_file,
        stderr=stderr_file,
        start_new_session=True,
    )

    logger.debug(f"Invoked claude for task dir {task_dir} with PID {process.pid}")
    return process.pid


def prepare_job_directory(job_name: str, agent_config: dict) -> Path:
    """Prepare a self-contained directory for a taskless agent job.

    Unlike prepare_task_directory(), this does NOT create a git worktree,
    write task.json, or set task-specific env vars. It is for agent jobs
    (type: agent in jobs.yaml) that run scripts without claiming a task.

    Creates:
        {job_dir}/worktree/    - plain working directory (no git worktree)
        {job_dir}/env.sh       - environment for scripts (no TASK_ID/TASK_BRANCH)
        {job_dir}/scripts/     - executable agent scripts
        {job_dir}/prompt.md    - rendered prompt
        {job_dir}/stdout.log   - agent stdout (scheduler infers result from this)
        {job_dir}/notes.md     - progress notes

    The worktree/ directory sits inside .octopoid/runtime/jobs/ which is
    itself inside the parent git repo, so git commands (e.g. git rev-parse
    --show-toplevel) work correctly from within workdir.

    Args:
        job_name: Name of the job (e.g. "codebase_analyst").
        agent_config: Agent configuration dict from the job definition.

    Returns:
        Path to the prepared job directory.
    """
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    jobs_base = get_jobs_dir()
    job_dir = jobs_base / f"{job_name}-{timestamp}"
    job_dir.mkdir(parents=True, exist_ok=True)

    # Create a plain working directory (not a git worktree).
    # Named "worktree" so invoke_claude() finds it at task_dir / "worktree".
    # Its location inside .octopoid/runtime/jobs/ is still inside the parent
    # git repo, so git rev-parse --show-toplevel returns the project root.
    workdir = job_dir / "worktree"
    workdir.mkdir(exist_ok=True)

    # Copy scripts from agent_dir
    agent_dir = agent_config.get("agent_dir")
    if not agent_dir or not (Path(agent_dir) / "scripts").exists():
        raise ValueError(f"Agent directory or scripts not found: {agent_dir}")

    scripts_src = Path(agent_dir) / "scripts"
    scripts_dest = job_dir / "scripts"
    scripts_dest.mkdir(exist_ok=True)

    venv_python = sys.executable
    for script in scripts_src.iterdir():
        if script.name.startswith("."):
            continue
        dest = scripts_dest / script.name
        content = script.read_text()
        if content.startswith("#!/usr/bin/env python3"):
            content = f"#!{venv_python}\n" + content.split("\n", 1)[1]
        dest.write_text(content)
        dest.chmod(0o755)

    # Write env.sh — taskless agents get ORCHESTRATOR_PYTHONPATH and server URL
    # but no TASK_ID, TASK_BRANCH, or WORKTREE (those are task-specific).
    orchestrator_submodule = find_parent_project() / "orchestrator"
    env_lines = [
        "#!/bin/bash",
        f"export AGENT_NAME='{job_name}'",
        f"export OCTOPOID_SERVER_URL='{os.environ.get('OCTOPOID_SERVER_URL') or _get_server_url_from_config()}'",
        f"export ORCHESTRATOR_PYTHONPATH='{orchestrator_submodule}'",
        f"export NOTES_FILE='{job_dir / 'notes.md'}'",
    ]
    (job_dir / "env.sh").write_text("\n".join(env_lines) + "\n")

    # Render prompt from agent directory
    prompt_template_path = Path(agent_dir) / "prompt.md"
    if not prompt_template_path.exists():
        raise ValueError(f"Agent prompt.md not found: {prompt_template_path}")

    prompt_template = prompt_template_path.read_text()

    global_instructions = ""
    gi_path = get_global_instructions_path()
    if gi_path.exists():
        global_instructions = gi_path.read_text()

    instructions_md_path = Path(agent_dir) / "instructions.md"
    if instructions_md_path.exists():
        global_instructions = global_instructions + "\n\n" + instructions_md_path.read_text()

    from string import Template
    template = Template(prompt_template)
    prompt = template.safe_substitute(global_instructions=global_instructions)
    (job_dir / "prompt.md").write_text(prompt)

    logger.debug(f"Prepared job directory: {job_dir}")
    return job_dir


def spawn_job_agent(ctx: AgentContext) -> int:
    """Spawn a taskless agent job: prepare job dir, invoke claude directly.

    Used for agent jobs (type: agent in jobs.yaml) that do not claim a task
    from the queue. ctx.claimed_task is None for these agents.

    Args:
        ctx: AgentContext with agent_config and no claimed_task.

    Returns:
        PID of the spawned claude process.
    """
    blueprint_name = ctx.agent_config.get("blueprint_name", ctx.agent_name)
    instance_name = _next_instance_name(blueprint_name)

    job_dir = prepare_job_directory(ctx.agent_name, ctx.agent_config)
    pid = invoke_claude(job_dir, ctx.agent_config)

    # Register with empty task_id — check_and_update_finished_agents will
    # clean up the PID without trying to process any task result.
    register_instance_pid(blueprint_name, pid, "", instance_name)

    new_state = mark_started(ctx.state, pid)
    new_state.extra["agent_mode"] = "job"
    new_state.extra["job_dir"] = str(job_dir)
    save_state(new_state, ctx.state_path)
    return pid


# ---------------------------------------------------------------------------
# Result-handling and flow-transition functions live in result_handler.py.
# handle_agent_result_via_flow and handle_agent_result are imported at the
# top of this module and re-exported for backwards compat.
# ---------------------------------------------------------------------------


# Housekeeping functions live in housekeeping.py (imported at the top of this module)
# System health functions live in system_health.py (imported at the top of this module)


def _init_submodule(agent_name: str) -> None:
    """Initialize the orchestrator submodule in an agent's worktree."""
    worktree_path = get_worktree_path(agent_name)
    try:
        subprocess.run(
            ["git", "submodule", "update", "--init", "orchestrator"],
            cwd=worktree_path,
            capture_output=True, text=True, timeout=120,
        )
        sub_path = worktree_path / "orchestrator"
        subprocess.run(["git", "checkout", "main"], cwd=sub_path, capture_output=True, text=True, timeout=30)
        subprocess.run(["git", "fetch", "origin", "main"], cwd=sub_path, capture_output=True, text=True, timeout=60)
        subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=sub_path, capture_output=True, text=True, timeout=30)
        _verify_submodule_isolation(sub_path, agent_name)
        logger.debug(f"Submodule initialized for {agent_name}")
    except Exception as e:
        logger.debug(f"Submodule init failed for {agent_name}: {e}")


def _next_instance_name(blueprint_name: str) -> str:
    """Generate the next available instance name for a blueprint.

    Returns '{blueprint_name}-{N}' where N is the lowest positive integer
    not already in use by a currently-tracked instance.

    Args:
        blueprint_name: Blueprint name (e.g. 'implementer')

    Returns:
        Instance name (e.g. 'implementer-1', 'implementer-2')
    """
    pids = load_blueprint_pids(blueprint_name)
    existing_names = {info.get("instance_name", "") for info in pids.values()}
    n = 1
    while f"{blueprint_name}-{n}" in existing_names:
        n += 1
    return f"{blueprint_name}-{n}"


def spawn_implementer(ctx: AgentContext) -> int:
    """Spawn an implementer: prepare task dir, invoke claude directly."""
    blueprint_name = ctx.agent_config.get("blueprint_name", ctx.agent_name)
    instance_name = _next_instance_name(blueprint_name)

    task_dir = prepare_task_directory(ctx.claimed_task, instance_name, ctx.agent_config)
    pid = invoke_claude(task_dir, ctx.agent_config)

    register_instance_pid(blueprint_name, pid, ctx.claimed_task["id"], instance_name)

    new_state = mark_started(ctx.state, pid)
    new_state.extra["agent_mode"] = "scripts"
    new_state.extra["claim_from"] = ctx.agent_config.get("claim_from", "incoming")
    new_state.extra["task_dir"] = str(task_dir)
    new_state.extra["current_task_id"] = ctx.claimed_task["id"]
    save_state(new_state, ctx.state_path)
    return pid


def get_spawn_strategy(ctx: AgentContext) -> Callable:
    """Select spawn strategy based on whether a task has been claimed.

    - claimed_task is None → taskless job agent → spawn_job_agent
    - claimed_task is set  → task-based implementer → spawn_implementer
    """
    if ctx.claimed_task is None:
        return spawn_job_agent
    return spawn_implementer


def _fetch_poll_data() -> dict | None:
    """Fetch combined scheduler state from the poll endpoint.

    Returns the poll response dict, or None if the call failed.
    Logs a debug warning on failure so callers can fall back gracefully.
    """
    try:
        orch_id = queue_utils.get_orchestrator_id()
        sdk = queue_utils.get_sdk()
        poll_data = sdk.poll(orch_id)
        logger.debug(f"Poll response: queue_counts={poll_data.get('queue_counts')}, "
                  f"provisional_tasks={len(poll_data.get('provisional_tasks') or [])}, "
                  f"orchestrator_registered={poll_data.get('orchestrator_registered')}")
        return poll_data
    except Exception as e:
        logger.debug(f"Poll endpoint unavailable, falling back to individual API calls: {e}")
        return None


def _run_agent_evaluation_loop(queue_counts: dict | None) -> None:
    """Evaluate and spawn agents for one tick.

    Args:
        queue_counts: Pre-fetched queue counts from poll (or None to use individual calls).
    """
    try:
        agents = get_agents()
        logger.debug(f"Loaded {len(agents)} agents from config")
    except FileNotFoundError as e:
        logger.error(f"Failed to load agents config: {e}")
        return

    if not agents:
        logger.debug("No agents configured")
        return

    for agent_config in agents:
        agent_name = agent_config.get("name")
        role = agent_config.get("role") or agent_config.get("type")

        # Skip job-scheduled agents — they are invoked by jobs.yaml, not the pool loop.
        if agent_config.get("job_agent"):
            logger.debug(f"Skipping job agent {agent_name} (managed by jobs.yaml)")
            continue

        # Skip on-demand agents — spawned directly by the scheduler when needed
        # (e.g. diagnostic agent on auto-pause), not by pool evaluation.
        if agent_config.get("on_demand"):
            logger.debug(f"Skipping on-demand agent {agent_name}")
            continue

        if not agent_name or not role:
            missing = []
            if not agent_name:
                missing.append("name")
            if not role:
                missing.append("role")
            logger.warning(f"Skipping agent config (missing {', '.join(missing)}): {agent_config}")
            continue

        logger.debug(f"Evaluating agent {agent_name}: role={role}")

        # Acquire agent lock
        agent_lock_path = get_agent_lock_path(agent_name)
        with locked_or_skip(agent_lock_path) as acquired:
            if not acquired:
                logger.warning(f"Agent {agent_name} is locked (another instance running?)")
                continue

            # Build context — pass poll-fetched queue_counts so guards skip per-agent API calls
            state_path = get_agent_state_path(agent_name)
            ctx = AgentContext(
                agent_config=agent_config,
                agent_name=agent_name,
                role=role,
                interval=agent_config.get("interval_seconds", 300),
                state=load_state(state_path),
                state_path=state_path,
                queue_counts=queue_counts,
            )

            # Evaluate guards
            if not evaluate_agent(ctx):
                continue

            # Spawn
            logger.info(f"Starting agent {agent_name} (role: {role})")

            strategy = get_spawn_strategy(ctx)
            try:
                pid = strategy(ctx)
                logger.info(f"Agent {agent_name} started with PID {pid}")
                _reset_systemic_failure_counter()
            except Exception as e:
                logger.error(f"Spawn failed for {agent_name}: {e}")
                if ctx.claimed_task:
                    source_queue = ctx.agent_config.get("claim_from", "incoming")
                    _requeue_task_blameless(ctx.claimed_task["id"], source_queue=source_queue)
                _handle_systemic_failure(f"Spawn failure for {agent_name}: {e}")


def run_scheduler() -> None:
    """Main scheduler loop - evaluate and spawn agents.

    Job intervals and grouping are defined declaratively in .octopoid/jobs.yaml.
    run_due_jobs() handles the poll-batching optimisation: it fetches poll data
    once if any remote job is due, avoiding ~14 individual API calls per tick.
    """
    from .jobs import run_due_jobs

    logger.info("Scheduler starting")

    # Fail loudly if scope is not configured — prevents cross-project task claiming
    scope = get_scope()
    if not scope:
        logger.error(
            "FATAL: 'scope' is not set in .octopoid/config.yaml. "
            "Add 'scope: <project-name>' to prevent cross-project task claiming."
        )
        sys.exit(1)
    logger.debug(f"Scope: {scope}")

    # Check global pause flag
    if is_system_paused():
        pause_file = get_orchestrator_dir() / "PAUSE"
        health = _load_system_health()
        is_auto_paused = (
            pause_file.exists()
            and health.get("consecutive_systemic_failures", 0) > 0
        )
        if is_auto_paused:
            # Auto-pause: ensure the diagnostic agent is running
            # (it may have been killed or the scheduler restarted while paused)
            if count_running_instances("diagnostic") == 0:
                reason = health.get("last_failure_reason") or "System was auto-paused"
                logger.info("System is auto-paused and no diagnostic agent running — re-spawning diagnostic")
                _spawn_diagnostic_agent(reason)
            else:
                logger.info("System is auto-paused, diagnostic agent is running")
        else:
            logger.info("System is paused (rm .octopoid/PAUSE or set 'paused: false' in agents.yaml)")
        return

    # Load per-job scheduler state (persists last_run across launchd invocations)
    scheduler_state = load_scheduler_state()

    # Sleep detection: if gap since last tick exceeds threshold, laptop likely slept.
    # renew_active_leases (which runs first in jobs.yaml) handles recovery automatically.
    _SLEEP_DETECTION_THRESHOLD_SECONDS = 300  # 5 minutes
    last_tick_str = scheduler_state.get("last_tick")
    if last_tick_str:
        try:
            last_tick = datetime.fromisoformat(last_tick_str)
            gap_seconds = (datetime.now() - last_tick).total_seconds()
            if gap_seconds > _SLEEP_DETECTION_THRESHOLD_SECONDS:
                logger.info(
                    f"Wake-from-sleep detected: scheduler gap was {gap_seconds:.0f}s "
                    f"(>{_SLEEP_DETECTION_THRESHOLD_SECONDS}s). "
                    "renew_active_leases will extend any active agent leases."
                )
        except (ValueError, TypeError):
            pass
    scheduler_state["last_tick"] = datetime.now().isoformat()

    # Dispatch all due jobs (declarative — intervals defined in .octopoid/jobs.yaml)
    poll_data = run_due_jobs(scheduler_state)

    # Persist updated last_run timestamps (including last_tick set above)
    save_scheduler_state(scheduler_state)

    queue_counts: dict = (poll_data or {}).get("queue_counts") or {}
    if queue_counts:
        counts_str = ", ".join(
            f"{k}: {v}" for k, v in sorted(queue_counts.items())
        )
        logger.info(f"Scheduler tick complete ({counts_str})")
    else:
        logger.info("Scheduler tick complete")


def _check_venv_integrity() -> None:
    """Verify the orchestrator module is loaded from the correct location.

    If an agent runs `pip install -e .` inside its worktree, it hijacks the
    shared venv to load code from the wrong directory. Detect this and abort.
    """
    import octopoid as _orch
    mod_file = getattr(_orch, "__file__", None) or ""
    # Also check a submodule to catch editable installs that set __file__ on the package
    scheduler_file = str(Path(__file__).resolve())
    if "agents/" in scheduler_file and "worktree" in scheduler_file:
        logger.error(
            f"FATAL: octopoid module loaded from agent worktree: {scheduler_file}. "
            f"Fix: pip install -e . from the repo root"
        )
        sys.exit(1)


def _clear_pycache() -> None:
    """Remove all __pycache__ directories under the orchestrator package.

    This ensures the scheduler never loads stale bytecode written by other
    processes (tests, dashboard, manual imports) that ran before the source
    was updated.
    """
    orchestrator_dir = Path(__file__).parent
    for cache_dir in orchestrator_dir.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)


def main() -> None:
    """Entry point for scheduler."""
    _clear_pycache()
    _check_venv_integrity()

    parser = argparse.ArgumentParser(description="Run the orchestrator scheduler")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Set log level to DEBUG (default: INFO)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (don't wait for lock)",
    )
    args = parser.parse_args()

    if args.debug:
        logging.getLogger("octopoid").setLevel(logging.DEBUG)
        logger.debug("Scheduler starting with debug mode enabled")

    scheduler_lock_path = get_scheduler_lock_path()

    with locked_or_skip(scheduler_lock_path) as acquired:
        if not acquired:
            logger.warning("Another scheduler instance is running, exiting")
            sys.exit(0)

        logger.debug("Scheduler lock acquired")
        run_scheduler()


# Default template if file doesn't exist
if __name__ == "__main__":
    main()
