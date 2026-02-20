#!/usr/bin/env python3
"""Main scheduler - runs on 1-minute ticks to evaluate and spawn agents."""

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path



from .config import (
    find_parent_project,
    get_agents,
    get_agents_runtime_dir,
    get_global_instructions_path,
    get_logs_dir,
    get_base_branch,
    get_orchestrator_dir,
    get_tasks_dir,
    is_system_paused,
)
from .git_utils import ensure_worktree, get_task_branch, get_worktree_path
from .hook_manager import HookManager
from .lock_utils import locked_or_skip
from .port_utils import get_port_env_vars
from . import queue_utils
from .state_utils import (
    AgentState,
    is_overdue,
    is_process_running,
    load_state,
    mark_finished,
    mark_started,
    save_state,
)
from .pool import (
    count_running_instances,
    get_active_task_ids,
    load_blueprint_pids,
    register_instance_pid,
    save_blueprint_pids,
)

# Global debug flag
DEBUG = False
_log_file: Path | None = None


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
    spawn_mode = ctx.agent_config.get("spawn_mode", "worktree")
    if spawn_mode != "scripts":
        # Not a scripts-mode agent — skip claim, let the role module claim
        return (True, "")

    claim_from = ctx.agent_config.get("claim_from", "incoming")
    type_filter = ctx.agent_config.get("type_filter")
    # When claiming from a non-incoming queue (e.g. provisional), do not filter
    # by the agent's own role — the tasks there may have a different original role.
    role_filter = ctx.role if claim_from == "incoming" else None

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
        debug_log(
            f"guard_claim_task: task {task['id']} already being processed by "
            f"another {blueprint_name} instance, skipping (not requeuing)"
        )
        return (False, f"duplicate_task: {task['id']} already being processed")

    ctx.claimed_task = task
    return (True, "")


def guard_pr_mergeable(ctx: AgentContext) -> tuple[bool, str]:
    """Check that the claimed task's PR has no merge conflicts.

    Only runs when a task has been claimed (ctx.claimed_task is set) and the
    task has a pr_number. Calls `gh pr view --json mergeable` to check the
    GitHub merge status. If the PR is CONFLICTING, the claim is released, the
    task is rejected back to incoming with a rebase request, and the guard
    returns False so the agent is not spawned.

    This prevents the gatekeeper from entering an infinite loop where it
    re-claims and re-approves a task whose PR can never be merged.

    Args:
        ctx: AgentContext containing the claimed task

    Returns:
        (should_proceed, reason_if_blocked)
    """
    import json as _json

    if not ctx.claimed_task:
        return (True, "")

    pr_number = ctx.claimed_task.get("pr_number")
    if not pr_number:
        return (True, "")

    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "mergeable"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            # gh command failed — don't block, let the agent discover the issue
            debug_log(f"guard_pr_mergeable: gh pr view failed for PR #{pr_number}: {result.stderr.strip()}")
            return (True, "")

        data = _json.loads(result.stdout)
        mergeable = data.get("mergeable", "UNKNOWN")
    except (subprocess.TimeoutExpired, _json.JSONDecodeError, Exception) as e:
        debug_log(f"guard_pr_mergeable: error checking PR #{pr_number}: {e}")
        return (True, "")

    if mergeable != "CONFLICTING":
        return (True, "")

    # PR has conflicts — release the claim and reject back to incoming
    task_id = ctx.claimed_task["id"]
    debug_log(f"guard_pr_mergeable: PR #{pr_number} for {task_id} has conflicts, releasing claim")

    try:
        sdk = queue_utils.get_sdk()
        feedback = (
            f"PR #{pr_number} has merge conflicts and cannot be merged automatically. "
            f"Please rebase the branch onto the target branch to resolve conflicts."
        )
        sdk.tasks.reject(task_id, reason=feedback, rejected_by="scheduler-guard")
    except Exception as e:
        debug_log(f"guard_pr_mergeable: failed to reject task {task_id}: {e}")

    return (False, f"pr_conflicts: PR #{pr_number} needs rebase")


# Guard chain: cheapest checks first, expensive checks last
AGENT_GUARDS = [
    guard_enabled,
    guard_pool_capacity,
    guard_interval,
    guard_backpressure,
    guard_pre_check,
    guard_claim_task,
    guard_pr_mergeable,
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
            debug_log(f"Agent {ctx.agent_name}: blocked by {guard.__name__}: {reason}")
            return False
    return True


def setup_scheduler_debug() -> None:
    """Set up debug logging for the scheduler."""
    global _log_file
    logs_dir = get_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    _log_file = logs_dir / f"scheduler-{date_str}.log"


def debug_log(message: str) -> None:
    """Write a debug message to the scheduler log."""
    if not DEBUG or not _log_file:
        return

    timestamp = datetime.now().isoformat()
    log_line = f"[{timestamp}] [SCHEDULER] {message}\n"

    try:
        with open(_log_file, "a") as f:
            f.write(log_line)
    except OSError:
        pass



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
    debug_log(f"Running pre-check for {agent_name}: {pre_check_cmd}")

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
            debug_log(f"Unknown pre_check_trigger: {trigger}, defaulting to spawn")
            has_work = True

        debug_log(f"Pre-check for {agent_name}: has_work={has_work} (stdout={result.stdout.strip()!r})")
        return has_work

    except subprocess.TimeoutExpired:
        debug_log(f"Pre-check for {agent_name} timed out, spawning anyway")
        return True
    except Exception as e:
        debug_log(f"Pre-check for {agent_name} failed: {e}, spawning anyway")
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
        debug_log(f"WARNING: {agent_name} submodule has no .git at {git_pointer}")
        return

    content = git_pointer.read_text().strip()

    # A submodule .git is a file containing "gitdir: <path>"
    if not content.startswith("gitdir:"):
        debug_log(f"WARNING: {agent_name} submodule .git is not a gitdir pointer: {content[:80]}")
        return

    gitdir = content.split("gitdir:", 1)[1].strip()

    # The gitdir should reference the worktree's modules directory, NOT
    # the main checkout's modules. A healthy worktree submodule points to
    # something like: ../../.git/worktrees/<name>/modules/orchestrator
    # A BROKEN one would point to: ../../.git/modules/orchestrator
    # (which is the main checkout's object store).
    if "worktrees" in gitdir or "worktree" in gitdir:
        debug_log(f"{agent_name} submodule .git correctly points to worktree store: {gitdir}")
    else:
        # This is the dangerous case — submodule shares the main checkout's store
        debug_log(
            f"WARNING: {agent_name} submodule .git points to MAIN checkout store: {gitdir}. "
            f"Commits may go to the wrong location! "
            f"Expected a path containing 'worktrees/' for isolated worktree storage."
        )
        print(
            f"WARNING: Agent {agent_name} submodule may share git store with main checkout. "
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

# Interval in seconds for each housekeeping / evaluation job.
# The launchd tick is 10s; jobs only run when their interval has elapsed.
HOUSEKEEPING_JOB_INTERVALS: dict[str, int] = {
    "check_and_update_finished_agents": 10,       # Local PID checks only — fast
    "_register_orchestrator": 300,                # Rarely changes
    "check_and_requeue_expired_leases": 60,       # API call, keep at 60s
    "process_orchestrator_hooks": 60,             # API call via poll
    "check_project_completion": 60,               # Project completion detection
    "_check_queue_health_throttled": 1800,        # Already self-throttled
    "agent_evaluation_loop": 60,                  # Main remote poll + claim loop
}


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

    # Pass debug mode
    if DEBUG:
        lines.append("export ORCHESTRATOR_DEBUG='1'")

    for key, value in port_vars.items():
        lines.append(f"export {key}='{value}'")

    env_path.write_text("\n".join(lines) + "\n")
    return env_path


def spawn_agent(agent_name: str, agent_id: int, role: str, agent_config: dict) -> int:
    """Spawn an agent subprocess.

    Args:
        agent_name: Name of the agent
        agent_id: Numeric ID of the agent
        role: Agent role
        agent_config: Full agent configuration

    Returns:
        Process ID of spawned agent
    """
    # Determine working directory - lightweight agents use parent project
    is_lightweight = agent_config.get("lightweight", False)
    if is_lightweight:
        cwd = find_parent_project()
        worktree_path = cwd  # For env var
    else:
        worktree_path = get_worktree_path(agent_name)
        cwd = worktree_path

    # Build environment
    env = os.environ.copy()
    env["AGENT_NAME"] = agent_name
    env["AGENT_ID"] = str(agent_id)
    env["AGENT_ROLE"] = role
    env["PARENT_PROJECT"] = str(find_parent_project())
    env["WORKTREE"] = str(worktree_path)
    from .config import get_shared_dir
    env["SHARED_DIR"] = str(get_shared_dir())
    env["ORCHESTRATOR_DIR"] = str(get_orchestrator_dir())

    # Set PYTHONPATH to include the orchestrator submodule
    # This allows `import orchestrator.orchestrator...` to work
    orchestrator_submodule = find_parent_project() / "orchestrator"
    existing_pythonpath = env.get("PYTHONPATH", "")
    if existing_pythonpath:
        env["PYTHONPATH"] = f"{orchestrator_submodule}:{existing_pythonpath}"
    else:
        env["PYTHONPATH"] = str(orchestrator_submodule)

    # Pass model override from agent config (e.g., "sonnet", "opus")
    if "model" in agent_config:
        env["AGENT_MODEL"] = agent_config["model"]

    # Pass focus for specialist agents (configured in agents.yaml)
    if "focus" in agent_config:
        env["AGENT_FOCUS"] = agent_config["focus"]

    # Pass review context if configured
    if "review_task_id" in agent_config:
        env["REVIEW_TASK_ID"] = agent_config["review_task_id"]
    if "review_check_name" in agent_config:
        env["REVIEW_CHECK_NAME"] = agent_config["review_check_name"]

    # Pass debug mode to agents
    if DEBUG:
        env["ORCHESTRATOR_DEBUG"] = "1"

    port_vars = get_port_env_vars(agent_id)
    env.update(port_vars)

    # Determine the role module to run
    role_module = f"orchestrator.roles.{role}"

    debug_log(f"Spawning agent {agent_name}: module={role_module}, cwd={cwd}, lightweight={is_lightweight}")
    debug_log(f"Agent env: AGENT_FOCUS={env.get('AGENT_FOCUS', 'N/A')}, ports={port_vars}")

    # Set up log files for agent output
    agent_dir = get_agents_runtime_dir() / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = agent_dir / "stdout.log"
    stderr_log = agent_dir / "stderr.log"

    # Open log files (truncate on each run to keep them manageable)
    stdout_file = open(stdout_log, "w")
    stderr_file = open(stderr_log, "w")

    # Spawn the role as a subprocess
    process = subprocess.Popen(
        [sys.executable, "-m", role_module],
        cwd=cwd,
        env=env,
        stdout=stdout_file,
        stderr=stderr_file,
        start_new_session=True,  # Detach from parent
    )

    # Note: We don't close the files here - the subprocess will write to them
    # and they'll be closed when the subprocess exits. The file descriptors
    # are inherited by the child process.

    debug_log(f"Agent {agent_name} spawned with PID {process.pid}, logs: {stderr_log}")
    return process.pid


def read_agent_exit_code(agent_name: str) -> int | None:
    """Read the exit code written by an agent.

    Args:
        agent_name: Name of the agent

    Returns:
        Exit code or None if not found
    """
    exit_code_path = get_agents_runtime_dir() / agent_name / "exit_code"
    if not exit_code_path.exists():
        return None

    try:
        content = exit_code_path.read_text().strip()
        exit_code = int(content)
        # Clean up the file after reading
        exit_code_path.unlink()
        return exit_code
    except (ValueError, OSError):
        return None


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
        {task_dir}/result.json   - (written by scripts, read by scheduler)
        {task_dir}/notes.md      - progress notes
    """
    from .git_utils import create_task_worktree

    task_id = task["id"]
    task_dir = get_tasks_dir() / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    # Clean stale artifacts from previous runs
    for stale_file in ['result.json', 'notes.md']:
        stale_path = task_dir / stale_file
        if stale_path.exists():
            stale_path.unlink()
            debug_log(f"Cleaned stale {stale_file} from {task_dir}")

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
    debug_log(f"Using scripts from agent directory: {scripts_src}")

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
        f"export RESULT_FILE='{task_dir / 'result.json'}'",
        f"export NOTES_FILE='{task_dir / 'notes.md'}'",
    ]
    (task_dir / "env.sh").write_text("\n".join(env_lines) + "\n")

    # Render prompt from agent directory
    agent_dir = agent_config.get("agent_dir")
    if not agent_dir or not (Path(agent_dir) / "prompt.md").exists():
        raise ValueError(f"Agent directory or prompt.md not found: {agent_dir}")

    # Use prompt template from agent directory
    prompt_template_path = Path(agent_dir) / "prompt.md"
    prompt_template = prompt_template_path.read_text()
    debug_log(f"Using prompt template from agent directory: {prompt_template_path}")

    # Load global instructions
    global_instructions = ""
    gi_path = get_global_instructions_path()
    if gi_path.exists():
        global_instructions = gi_path.read_text()

    # Load instructions.md from agent directory if available
    instructions_md_path = Path(agent_dir) / "instructions.md"
    if instructions_md_path.exists():
        instructions_content = instructions_md_path.read_text()
        # Append instructions to global instructions
        global_instructions = global_instructions + "\n\n" + instructions_content
        debug_log(f"Included instructions.md from agent directory: {instructions_md_path}")

    # Get agent hooks from task
    hooks = task.get("hooks")
    agent_hooks = None
    if hooks:
        if isinstance(hooks, str):
            agent_hooks = [
                h for h in json.loads(hooks)
                if h.get("type") == "agent"
            ]
        elif isinstance(hooks, list):
            agent_hooks = [h for h in hooks if h.get("type") == "agent"]

    # Build required_steps section
    required_steps = ""
    if agent_hooks:
        lines = ["## Required Steps Before Writing result.json", ""]
        lines.append("You must complete these steps before writing result.json:")
        for i, hook in enumerate(agent_hooks, 1):
            name = hook["name"]
            if name == "run_tests":
                lines.append(f"{i}. Run tests: `../scripts/run-tests`")
            elif name == "rebase_on_main":
                lines.append(
                    f"{i}. Rebase on main: "
                    "`git fetch origin main && git rebase origin/main`"
                )
            elif name == "create_pr":
                # Scheduler handles PR creation — skip this hook for the agent
                continue
            else:
                lines.append(f"{i}. {name}")
        required_steps = "\n".join(lines)

    # Perform template substitution
    from string import Template
    template = Template(prompt_template)
    prompt = template.safe_substitute(
        task_id=task.get("id", "unknown"),
        task_title=task.get("title", "Untitled"),
        task_content=task.get("content", ""),
        task_priority=task.get("priority", "P2"),
        task_branch=task.get("branch") or get_base_branch(),
        task_type=task.get("type", ""),
        scripts_dir="../scripts",
        global_instructions=global_instructions,
        required_steps=required_steps,
        review_section="",
        continuation_section="",
    )

    (task_dir / "prompt.md").write_text(prompt)

    debug_log(f"Prepared task directory: {task_dir}")
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
    ]

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
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

    debug_log(f"Invoked claude for task dir {task_dir} with PID {process.pid}")
    return process.pid



def read_result_json(task_dir: Path) -> dict:
    """Read and parse result.json from a task directory.

    Args:
        task_dir: Path to the task directory

    Returns:
        Parsed result dict, or an error dict if missing/invalid
    """
    import json

    result_path = task_dir / "result.json"
    if not result_path.exists():
        return {"status": "failure", "message": "No result.json produced"}

    try:
        return json.loads(result_path.read_text())
    except json.JSONDecodeError:
        return {"status": "failure", "message": "Invalid result.json"}



def handle_agent_result_via_flow(task_id: str, agent_name: str, task_dir: Path, expected_queue: str | None = None) -> None:
    """Handle agent result using the task's flow definition.

    Replaces the hardcoded if/else dispatch for agent roles. Reads the flow,
    finds the current transition, and executes steps accordingly.

    The gatekeeper result format:
      {"status": "success", "decision": "approve"/"reject", "comment": "<markdown>"}
    or on failure:
      {"status": "failure", "message": "<reason>"}

    Args:
        task_id: Task identifier
        agent_name: Name of the agent
        task_dir: Path to the task directory containing result.json
        expected_queue: Queue the agent was working from (e.g. 'provisional').
            If set and the task has moved to a different queue, the result is
            discarded as stale to prevent running wrong transition steps.
    """
    from .flow import load_flow
    from .steps import execute_steps, reject_with_feedback

    result = read_result_json(task_dir)

    debug_log(f"handle_agent_result_via_flow: task={task_id} agent={agent_name} status={result.get('status')} decision={result.get('decision')}")

    try:
        sdk = queue_utils.get_sdk()

        # Fetch current task state
        task = sdk.tasks.get(task_id)
        if not task:
            debug_log(f"Flow dispatch: task {task_id} not found on server, skipping")
            return

        current_queue = task.get("queue", "unknown")

        # When expected_queue is set, the agent claimed from that queue (e.g.
        # "provisional") and the server moved the task to "claimed".  Use the
        # pre-claim queue for transition lookup so we find the right flow
        # transition (e.g. "provisional -> done", not "claimed -> provisional").
        # Only discard as stale if the task moved to something other than the
        # expected queue or "claimed" (normal claiming behaviour).
        if expected_queue and current_queue not in (expected_queue, "claimed"):
            debug_log(
                f"Flow dispatch: task {task_id} moved from expected '{expected_queue}' "
                f"to '{current_queue}', discarding stale result from {agent_name}"
            )
            return

        lookup_queue = expected_queue if expected_queue else current_queue
        flow_name = task.get("flow", "default")

        flow = load_flow(flow_name)
        # Use child_flow transitions if this is a child task in a project
        if task.get("project_id") and flow.child_flow:
            transitions = flow.child_flow.get_transitions_from(lookup_queue)
        else:
            transitions = flow.get_transitions_from(lookup_queue)

        if not transitions:
            debug_log(f"Flow dispatch: no transition from '{current_queue}' in flow '{flow_name}' for task {task_id}")
            return

        transition = transitions[0]  # Take first matching transition

        status = result.get("status")
        decision = result.get("decision")

        if status == "failure":
            # Agent couldn't complete — find on_fail state from agent condition
            message = result.get("message", "Agent could not complete review")
            debug_log(f"Flow dispatch: agent failure for {task_id}: {message}")
            for condition in transition.conditions:
                if condition.type == "agent" and condition.on_fail:
                    debug_log(f"Flow dispatch: rejecting {task_id} back to {condition.on_fail}")
                    sdk.tasks.reject(task_id, reason=message, rejected_by=agent_name)
                    return
            # Default: reject back to incoming
            sdk.tasks.reject(task_id, reason=message, rejected_by=agent_name)
            return

        # Agent-specific decision handling (approve/reject for gatekeeper)
        if decision == "reject":
            debug_log(f"Flow dispatch: agent rejected task {task_id}")
            reject_with_feedback(task, result, task_dir)
            print(f"[{datetime.now().isoformat()}] Agent {agent_name} rejected task {task_id}")
            return

        if decision != "approve":
            debug_log(f"Flow dispatch: unknown decision '{decision}' for {task_id}, leaving in {current_queue} for human review")
            return

        # Execute the transition's runs (approve path — only reached on explicit "approve")
        if transition.runs:
            debug_log(f"Flow dispatch: executing steps {transition.runs} for task {task_id}")
            execute_steps(transition.runs, task, result, task_dir)
            print(f"[{datetime.now().isoformat()}] Agent {agent_name} completed task {task_id} (steps: {transition.runs})")
        else:
            # No runs defined — just log
            debug_log(f"Flow dispatch: no runs defined for transition from '{current_queue}', task {task_id}")

    except Exception as e:
        import traceback
        debug_log(f"Error in handle_agent_result_via_flow for {task_id}: {e}")
        debug_log(traceback.format_exc())
        try:
            sdk = queue_utils.get_sdk()
            sdk.tasks.update(task_id, queue='failed', execution_notes=f'Flow dispatch error: {e}')
        except Exception:
            debug_log(f"Failed to move {task_id} to failed queue")


def _read_or_infer_result(task_dir: Path) -> dict:
    """Read result.json from a task directory, with fallback heuristics.

    If result.json exists, parses and returns it. If it's missing or invalid,
    falls back to checking notes.md for a continuation signal, or returns an
    error result.

    Args:
        task_dir: Path to the task directory

    Returns:
        Result dict with at least an "outcome" key.
    """
    import json

    result_path = task_dir / "result.json"

    if result_path.exists():
        try:
            return json.loads(result_path.read_text())
        except json.JSONDecodeError:
            return {"outcome": "error", "reason": "Invalid result.json"}

    # No result.json — check for progress notes as a continuation signal
    notes_path = task_dir / "notes.md"
    if notes_path.exists() and notes_path.read_text().strip():
        return {"outcome": "needs_continuation"}

    return {"outcome": "error", "reason": "No result.json produced"}


def _handle_done_outcome(sdk: object, task_id: str, task: dict, result: dict, task_dir: Path) -> None:
    """Execute flow steps for a successfully-completed task."""
    from .flow import load_flow
    from .steps import execute_steps

    current_queue = task.get("queue", "unknown")
    if current_queue != "claimed":
        # Task already moved past claimed — skip to avoid double-submitting.
        debug_log(f"Task {task_id}: outcome=done but queue={current_queue}, skipping")
        return

    flow_name = task.get("flow", "default")
    flow = load_flow(flow_name)
    # Use child_flow transitions if this is a child task in a project
    if task.get("project_id") and flow.child_flow:
        transitions = flow.child_flow.get_transitions_from("claimed")
    else:
        transitions = flow.get_transitions_from("claimed")

    if transitions and transitions[0].runs:
        debug_log(f"Task {task_id}: executing flow steps {transitions[0].runs}")
        execute_steps(transitions[0].runs, task, result, task_dir)
        print(f"[{datetime.now().isoformat()}] Task {task_id} submitted via flow steps")
    else:
        # Fallback: direct submit if flow has no steps
        sdk.tasks.submit(task_id=task_id, commits_count=0, turns_used=0)
        debug_log(f"Task {task_id}: no flow steps, used direct submit")


def _handle_fail_outcome(sdk: object, task_id: str, reason: str, current_queue: str) -> None:
    """Move a failed task to the failed queue."""
    if current_queue == "claimed":
        sdk.tasks.update(task_id, queue="failed")
        debug_log(f"Task {task_id}: failed (claimed → failed): {reason}")
    else:
        debug_log(f"Task {task_id}: outcome=failed but queue={current_queue}, skipping")


def _handle_continuation_outcome(sdk: object, task_id: str, agent_name: str, current_queue: str) -> None:
    """Move a task to needs_continuation queue."""
    if current_queue == "claimed":
        sdk.tasks.update(task_id, queue="needs_continuation")
        debug_log(f"Task {task_id}: needs continuation by {agent_name}")
    else:
        debug_log(f"Task {task_id}: outcome=needs_continuation but queue={current_queue}, skipping")


def handle_agent_result(task_id: str, agent_name: str, task_dir: Path) -> None:
    """Handle the result of a script-based agent run.

    Reads result.json and transitions the task using flow steps:
    1. Read result.json to determine outcome
    2. Fetch current task state from server
    3. For "done" outcomes in "claimed" queue: execute the flow's claimed→provisional steps
    4. For "failed"/"error": move to failed queue
    5. For "needs_continuation": move to needs_continuation queue

    The scheduler owns the full push/PR/submit lifecycle — agents just commit
    code and write result.json.

    Args:
        task_id: Task identifier
        agent_name: Name of the agent
        task_dir: Path to the task directory
    """
    result = _read_or_infer_result(task_dir)
    outcome = result.get("outcome", "error")
    debug_log(f"Task {task_id} result: {outcome}")

    try:
        sdk = queue_utils.get_sdk()

        task = sdk.tasks.get(task_id)
        if not task:
            debug_log(f"Task {task_id}: not found on server, skipping result handling")
            return

        current_queue = task.get("queue", "unknown")
        debug_log(f"Task {task_id}: current queue = {current_queue}, outcome = {outcome}")

        if outcome in ("done", "submitted"):
            _handle_done_outcome(sdk, task_id, task, result, task_dir)
        elif outcome in ("failed", "error"):
            _handle_fail_outcome(sdk, task_id, result.get("reason", "Agent reported failure"), current_queue)
        elif outcome == "needs_continuation":
            _handle_continuation_outcome(sdk, task_id, agent_name, current_queue)
        else:
            _handle_fail_outcome(sdk, task_id, f"Unknown outcome: {outcome}", current_queue)

    except Exception as e:
        debug_log(f"Error handling result for {task_id}: {e}")
        # Don't try to fail the task here — let lease monitor handle recovery


def process_orchestrator_hooks(provisional_tasks: list | None = None) -> None:
    """Run orchestrator-side hooks on provisional tasks.

    For each provisional task that has pending orchestrator hooks (e.g. merge_pr):
    1. Get pending orchestrator hooks
    2. Run each one via HookManager
    3. Record evidence
    4. If all hooks pass, accept the task

    Args:
        provisional_tasks: Pre-fetched list of provisional tasks from the poll endpoint.
            If provided, skips the sdk.tasks.list(queue="provisional") call.
            If None, fetches from the API.
    """
    try:
        sdk = queue_utils.get_sdk()
        hook_manager = HookManager(sdk)

        # Use pre-fetched list if provided, otherwise fetch from API
        if provisional_tasks is not None:
            provisional = provisional_tasks
        else:
            provisional = sdk.tasks.list(queue="provisional")
        if not provisional:
            return

        for task in provisional:
            task_id = task.get("id", "")
            pending = hook_manager.get_pending_hooks(task, hook_type="orchestrator")
            if not pending:
                continue

            debug_log(f"Task {task_id}: {len(pending)} pending orchestrator hooks")

            for hook in pending:
                evidence = hook_manager.run_orchestrator_hook(task, hook)
                hook_manager.record_evidence(task_id, hook["name"], evidence)
                debug_log(f"  Hook {hook['name']}: {evidence.status} - {evidence.message}")

                if evidence.status == "failed":
                    debug_log(f"  Orchestrator hook {hook['name']} failed for {task_id}")
                    break

            # Re-fetch task to get updated hooks
            updated_task = sdk.tasks.get(task_id)
            if updated_task:
                can_accept, still_pending = hook_manager.can_transition(updated_task, "before_merge")
                if can_accept:
                    debug_log(f"All orchestrator hooks passed for {task_id}, accepting")
                    sdk.tasks.accept(task_id=task_id, accepted_by="scheduler-hooks")
                    print(f"[{datetime.now().isoformat()}] Accepted task {task_id} (all hooks passed)")

    except Exception as e:
        debug_log(f"Error processing orchestrator hooks: {e}")

def check_and_update_finished_agents() -> None:
    """Check for agents that have finished and update their state.

    Iterates blueprints via running_pids.json. For each dead PID, processes
    the agent result and removes the PID from pool tracking.
    """
    agents_dir = get_agents_runtime_dir()
    if not agents_dir.exists():
        return

    # Pre-fetch agent configs to look up claim_from per blueprint
    try:
        agents_list = get_agents()
        blueprint_configs: dict[str, dict] = {
            a.get("blueprint_name", a.get("name", "")): a
            for a in agents_list
        }
    except Exception:
        blueprint_configs = {}

    for agent_dir in agents_dir.iterdir():
        if not agent_dir.is_dir():
            continue

        blueprint_name = agent_dir.name
        pids_path = agent_dir / "running_pids.json"
        if not pids_path.exists():
            continue

        pids = load_blueprint_pids(blueprint_name)
        if not pids:
            continue

        dead_pids = {
            pid: info
            for pid, info in pids.items()
            if not is_process_running(pid)
        }
        if not dead_pids:
            continue

        blueprint_config = blueprint_configs.get(blueprint_name, {})
        claim_from = blueprint_config.get("claim_from", "incoming")

        for pid, info in dead_pids.items():
            instance_name = info.get("instance_name", blueprint_name)
            task_id = info.get("task_id", "")
            debug_log(f"Instance {instance_name} (PID {pid}) has finished")

            if task_id:
                task_dir = get_tasks_dir() / task_id
                if task_dir.exists():
                    if claim_from != "incoming":
                        # Review agents (claim from provisional, etc.) use flow dispatch
                        handle_agent_result_via_flow(task_id, instance_name, task_dir, expected_queue=claim_from)
                    else:
                        # Implementers (claim from incoming) use outcome dispatch
                        handle_agent_result(task_id, instance_name, task_dir)

            del pids[pid]
            print(f"[{datetime.now().isoformat()}] Instance {instance_name} (PID {pid}) finished")

        save_blueprint_pids(blueprint_name, pids)


# =============================================================================
# Queue Health Detection (for queue-manager agent)
# =============================================================================

# Track last queue health check time (global state)
_last_queue_health_check: datetime | None = None
QUEUE_HEALTH_CHECK_INTERVAL_SECONDS = 1800  # 30 minutes


def _check_queue_health_throttled() -> None:
    """Check queue health with throttling to avoid running too frequently."""
    global _last_queue_health_check

    now = datetime.now()

    # Check if enough time has passed since last check
    if _last_queue_health_check is not None:
        elapsed = (now - _last_queue_health_check).total_seconds()
        if elapsed < QUEUE_HEALTH_CHECK_INTERVAL_SECONDS:
            return  # Not time yet

    # Update last check time
    _last_queue_health_check = now

    # Run the actual check
    check_queue_health()


def check_queue_health() -> None:
    """Check queue health and invoke queue-manager agent if issues found.

    Runs the diagnostic script and spawns queue-manager agent if any issues
    are detected. This is called periodically from the scheduler (every 30 minutes).
    """
    # Path to diagnostic script
    parent_project = find_parent_project()
    script_path = parent_project / ".octopoid" / "scripts" / "diagnose_queue_health.py"

    if not script_path.exists():
        debug_log("Queue health diagnostic script not found, skipping")
        return

    # Run diagnostic script with JSON output
    try:
        result = subprocess.run(
            [sys.executable, str(script_path), "--json"],
            cwd=parent_project,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            # No issues found
            debug_log("Queue health check: no issues found")
            return

        # Parse diagnostic output
        import json
        try:
            diagnostic_data = json.loads(result.stdout)
        except json.JSONDecodeError:
            debug_log(f"Failed to parse diagnostic output: {result.stdout[:200]}")
            return

        # Count issues
        mismatches = len(diagnostic_data.get("file_db_mismatches", []))
        orphans = len(diagnostic_data.get("orphan_files", []))
        zombies = len(diagnostic_data.get("zombie_claims", []))

        total_issues = mismatches + orphans + zombies

        if total_issues == 0:
            debug_log("Queue health check: no issues found")
            return

        # Issues found - log summary
        print(f"[{datetime.now().isoformat()}] Queue health issues detected:")
        print(f"  File-DB mismatches: {mismatches}")
        print(f"  Orphan files: {orphans}")
        print(f"  Zombie claims: {zombies}")
        debug_log(f"Queue health issues: {mismatches} mismatches, {orphans} orphans, {zombies} zombies")

        # Check if queue-manager agent is configured and ready to run
        agents = get_agents()
        queue_manager = next((a for a in agents if a.get("role") == "queue_manager"), None)

        if not queue_manager:
            debug_log("No queue-manager agent configured")
            return

        if queue_manager.get("paused", False):
            debug_log("Queue-manager agent is paused, not invoking")
            print(f"  (queue-manager agent is paused - issues not auto-reported)")
            return

        # Trigger queue-manager agent by setting environment variable
        # The agent's prompt will check this variable to know why it was triggered
        agent_name = queue_manager.get("name", "queue-manager")
        print(f"  Triggering {agent_name} to diagnose and report issues")
        debug_log(f"Triggering {agent_name} with {total_issues} issues")

        # Write diagnostic data to a temp file for the agent to read
        from .config import get_notes_dir as _get_notes_dir
        notes_dir = _get_notes_dir()
        notes_dir.mkdir(parents=True, exist_ok=True)
        diagnostic_file = notes_dir / f"queue-health-diagnostic-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        diagnostic_file.write_text(json.dumps(diagnostic_data, indent=2))

        debug_log(f"Wrote diagnostic data to {diagnostic_file}")

        # The queue-manager agent will read this file and generate a report
        # For now, we just log that issues were found. In a future phase, we
        # could automatically spawn the agent here.

    except subprocess.TimeoutExpired:
        debug_log("Queue health diagnostic timed out")
    except Exception as e:
        debug_log(f"Queue health check failed: {e}")


def check_project_completion() -> None:
    """Check active projects and create PRs when all child tasks are done.

    For each active project where every child task is in the 'done' queue:
    1. Create a PR from the project's shared branch to the base branch
    2. Update the project status to 'review'

    Runs as a housekeeping job every 60 seconds. Skips projects that are
    already in 'review' or 'completed' status (by only listing 'active' ones).
    """
    try:
        sdk = queue_utils.get_sdk()
        projects = sdk.projects.list(status="active")

        if not projects:
            return

        for project in projects:
            project_id = project.get("id", "")
            project_status = project.get("status", "")

            # Extra safety check — skip non-active projects if list returns stale data
            if project_status in ("review", "completed", "done"):
                debug_log(f"check_project_completion: skipping {project_id} (status={project_status})")
                continue

            tasks = sdk.projects.get_tasks(project_id)

            if not tasks:
                continue

            all_done = all(t.get("queue") == "done" for t in tasks)
            if not all_done:
                continue

            # All children done — create PR on the shared branch
            project_branch = project.get("branch")
            if not project_branch:
                debug_log(f"check_project_completion: project {project_id} has no branch, skipping")
                continue

            base_branch = get_base_branch()
            parent_project = find_parent_project()

            # Check if PR already exists for this branch
            pr_check = subprocess.run(
                [
                    "gh", "pr", "view", project_branch,
                    "--json", "url,number",
                    "-q", '.url + " " + (.number|tostring)',
                ],
                cwd=parent_project,
                capture_output=True,
                text=True,
                timeout=30,
            )

            pr_url: str | None = None
            pr_number: int | None = None

            if pr_check.returncode == 0 and pr_check.stdout.strip():
                # PR already exists
                parts = pr_check.stdout.strip().rsplit(" ", 1)
                pr_url = parts[0]
                try:
                    pr_number = int(parts[1]) if len(parts) > 1 else None
                except ValueError:
                    pass
                debug_log(f"check_project_completion: PR already exists for {project_id}: {pr_url}")
            else:
                # Create new PR from the project branch
                project_title = project.get("title", project_id)
                pr_body = (
                    f"## Project: {project_title}\n\n"
                    f"All child tasks for project `{project_id}` are complete. "
                    f"This PR merges the shared project branch into `{base_branch}`."
                )
                pr_create = subprocess.run(
                    [
                        "gh", "pr", "create",
                        "--base", base_branch,
                        "--head", project_branch,
                        "--title", f"[{project_id}] {project_title}",
                        "--body", pr_body,
                    ],
                    cwd=parent_project,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

                if pr_create.returncode != 0:
                    # PR may already exist (race condition or "already exists" error)
                    if "already exists" in (pr_create.stderr or ""):
                        retry = subprocess.run(
                            [
                                "gh", "pr", "view", project_branch,
                                "--json", "url,number",
                                "-q", '.url + " " + (.number|tostring)',
                            ],
                            cwd=parent_project,
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                        if retry.returncode == 0 and retry.stdout.strip():
                            parts = retry.stdout.strip().rsplit(" ", 1)
                            pr_url = parts[0]
                            try:
                                pr_number = int(parts[1]) if len(parts) > 1 else None
                            except ValueError:
                                pass
                        else:
                            debug_log(
                                f"check_project_completion: PR creation failed for {project_id}: "
                                f"{pr_create.stderr.strip()}"
                            )
                            print(
                                f"[{datetime.now().isoformat()}] Failed to create project PR "
                                f"for {project_id}: {pr_create.stderr.strip()}"
                            )
                            continue
                    else:
                        debug_log(
                            f"check_project_completion: PR creation failed for {project_id}: "
                            f"{pr_create.stderr.strip()}"
                        )
                        print(
                            f"[{datetime.now().isoformat()}] Failed to create project PR "
                            f"for {project_id}: {pr_create.stderr.strip()}"
                        )
                        continue
                else:
                    pr_url = pr_create.stdout.strip()
                    if pr_url:
                        try:
                            pr_number = int(pr_url.rstrip("/").rsplit("/", 1)[-1])
                        except (ValueError, IndexError):
                            pass
                    print(
                        f"[{datetime.now().isoformat()}] Created project PR for {project_id}: {pr_url}"
                    )
                    debug_log(f"check_project_completion: created PR {pr_url} for {project_id}")

            # Update project: store PR info and move to review
            update_kwargs: dict = {"status": "review"}
            if pr_url:
                update_kwargs["pr_url"] = pr_url
            if pr_number is not None:
                update_kwargs["pr_number"] = pr_number

            sdk.projects.update(project_id, **update_kwargs)
            print(
                f"[{datetime.now().isoformat()}] Project {project_id} moved to review "
                f"(PR: {pr_url})"
            )
            debug_log(f"check_project_completion: project {project_id} -> review")

    except Exception as e:
        debug_log(f"check_project_completion failed: {e}")


def check_and_requeue_expired_leases() -> None:
    """Requeue tasks whose lease has expired (orchestrator-side fallback)."""
    try:
        sdk = queue_utils.get_sdk()
        claimed_tasks = sdk.tasks.list(queue="claimed")
        now = datetime.now(timezone.utc)

        for task in claimed_tasks or []:
            lease_expires = task.get("lease_expires_at")
            if not lease_expires:
                continue

            try:
                expires_at = datetime.fromisoformat(lease_expires.replace('Z', '+00:00'))
                if expires_at < now:
                    task_id = task["id"]
                    sdk.tasks.update(task_id, queue="incoming", claimed_by=None, lease_expires_at=None)
                    debug_log(f"Requeued expired lease: {task_id} (expired {lease_expires})")
                    print(f"[{datetime.now().isoformat()}] Requeued expired lease: {task_id}")
            except (ValueError, TypeError):
                pass
    except Exception as e:
        debug_log(f"Lease expiry check failed: {e}")


def _register_orchestrator(orchestrator_registered: bool = False) -> None:
    """Register this orchestrator with the API server (idempotent).

    Skips the POST if the poll response already reports orchestrator_registered: true.
    Only sends the registration request on the first tick or when the server
    reports the orchestrator is not registered.

    Args:
        orchestrator_registered: Whether the poll endpoint reported this
            orchestrator as already registered. If True, skip the POST.
    """
    if orchestrator_registered:
        debug_log("Orchestrator already registered (poll confirmed), skipping registration POST")
        return
    try:
        from .queue_utils import get_sdk, get_orchestrator_id
        sdk = get_sdk()
        orch_id = get_orchestrator_id()
        parts = orch_id.split("-", 1)
        cluster = parts[0] if len(parts) > 1 else "default"
        machine_id = parts[1] if len(parts) > 1 else orch_id
        sdk._request("POST", "/api/v1/orchestrators/register", json={
            "id": orch_id,
            "cluster": cluster,
            "machine_id": machine_id,
            "repo_url": "",
            "version": "2.0.0",
            "max_agents": 3,
        })
        debug_log(f"Registered orchestrator: {orch_id}")
    except Exception as e:
        debug_log(f"Orchestrator registration failed (non-fatal): {e}")

    # Sync flow definitions to server so it can validate queue names at runtime.
    # Non-fatal: errors are logged but never block registration.
    try:
        from .flow import list_flows, load_flow
        from .config import get_orchestrator_dir
        flows_dir = get_orchestrator_dir() / "flows"
        if flows_dir.exists():
            from .queue_utils import get_sdk as _get_sdk
            _sdk = _get_sdk()
            for flow_name in list_flows():
                try:
                    flow = load_flow(flow_name)
                    states = sorted(flow.get_all_states())
                    transitions = [
                        {"from": t.from_state, "to": t.to_state}
                        for t in flow.transitions
                    ]
                    _sdk._request("PUT", f"/api/v1/flows/{flow_name}", json={
                        "states": states,
                        "transitions": transitions,
                    })
                    debug_log(f"Synced flow '{flow_name}' to server")
                except Exception as flow_err:
                    debug_log(f"Flow sync failed for '{flow_name}' (non-fatal): {flow_err}")
    except Exception as e:
        debug_log(f"Flow sync failed (non-fatal): {e}")


# =============================================================================
# Housekeeping Jobs
# =============================================================================
#
# The following functions were removed during the scheduler refactor:
#
# - process_auto_accept_tasks(): Dead code — auto_accept feature not implemented
#   (function body was just `return` in pre-refactor code)
#
# - assign_qa_checks(): Dead code — gatekeeper QA system not implemented
#   (function body was just `return` in pre-refactor code)
#
# - process_gatekeeper_reviews(): Dead code — gatekeeper review system not implemented
#   (function body was just `return` in pre-refactor code)
#
# - dispatch_gatekeeper_agents(): Dead code — gatekeeper agent dispatch not implemented
#   (function body was just `return` in pre-refactor code)
#
# - check_stale_branches(): Dead code — branch staleness monitoring not implemented
#   (function body was just `return` in pre-refactor code; helper functions existed
#   but were never called)
#
# - check_branch_freshness(): Dead code — branch freshness checks not implemented
#   (function body was just `return` in pre-refactor code; rebase logic was stubbed)
#
# Note: process_orchestrator_hooks() is still active and listed below.

HOUSEKEEPING_JOBS = [
    _register_orchestrator,
    check_and_requeue_expired_leases,
    check_and_update_finished_agents,
    _check_queue_health_throttled,
    process_orchestrator_hooks,
    check_project_completion,
]


def run_housekeeping() -> None:
    """Run all housekeeping jobs. Each is independent and fault-isolated."""
    for job in HOUSEKEEPING_JOBS:
        try:
            job()
        except Exception as e:
            debug_log(f"Housekeeping job {job.__name__} failed: {e}")


# =============================================================================
# Spawn Strategies
# =============================================================================

def _requeue_task(task_id: str) -> None:
    """Requeue a claimed task back to incoming after spawn failure."""
    try:
        from .queue_utils import get_sdk
        sdk = get_sdk()
        sdk.tasks.update(task_id, queue="incoming", claimed_by=None)
        debug_log(f"Requeued task {task_id} back to incoming")
    except Exception as e:
        debug_log(f"Failed to requeue task {task_id}: {e}")


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
        debug_log(f"Submodule initialized for {agent_name}")
    except Exception as e:
        debug_log(f"Submodule init failed for {agent_name}: {e}")


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


def spawn_lightweight(ctx: AgentContext) -> int:
    """Spawn a lightweight agent (no worktree, runs in parent project)."""
    blueprint_name = ctx.agent_config.get("blueprint_name", ctx.agent_name)
    instance_name = _next_instance_name(blueprint_name)

    write_agent_env(ctx.agent_name, ctx.agent_config.get("id", 0), ctx.role, ctx.agent_config)
    pid = spawn_agent(ctx.agent_name, ctx.agent_config.get("id", 0), ctx.role, ctx.agent_config)

    task_id = ctx.claimed_task["id"] if ctx.claimed_task else ""
    register_instance_pid(blueprint_name, pid, task_id, instance_name)

    new_state = mark_started(ctx.state, pid)
    save_state(new_state, ctx.state_path)
    return pid


def spawn_worktree(ctx: AgentContext) -> int:
    """Spawn an agent with a worktree (general case for non-lightweight, non-implementer)."""
    # Resolve base branch for the worktree
    base_branch = ctx.agent_config.get("base_branch", get_base_branch())

    if ctx.claimed_task:
        # Use the claimed task's branch for the worktree
        task_branch = ctx.claimed_task.get("branch")
        if task_branch and task_branch != "main":
            debug_log(f"Using claimed task branch for {ctx.agent_name}: {task_branch}")
            base_branch = task_branch
    else:
        # For non-claimable agents, peek at queue for branch hint
        task_branch = peek_task_branch(ctx.role)
        if task_branch:
            debug_log(f"Peeked task branch for {ctx.agent_name}: {task_branch}")
            base_branch = task_branch

    debug_log(f"Ensuring worktree for {ctx.agent_name} on branch {base_branch}")
    ensure_worktree(ctx.agent_name, base_branch)

    # Initialize submodule for orchestrator_impl agents
    if ctx.role == "orchestrator_impl":
        _init_submodule(ctx.agent_name)

    # Write env file
    debug_log(f"Writing env file for {ctx.agent_name}")
    write_agent_env(ctx.agent_name, ctx.agent_config.get("id", 0), ctx.role, ctx.agent_config)

    # Spawn agent
    pid = spawn_agent(ctx.agent_name, ctx.agent_config.get("id", 0), ctx.role, ctx.agent_config)

    blueprint_name = ctx.agent_config.get("blueprint_name", ctx.agent_name)
    instance_name = _next_instance_name(blueprint_name)
    task_id = ctx.claimed_task["id"] if ctx.claimed_task else ""
    register_instance_pid(blueprint_name, pid, task_id, instance_name)

    # Update JSON state
    new_state = mark_started(ctx.state, pid)
    save_state(new_state, ctx.state_path)
    return pid


def get_spawn_strategy(ctx: AgentContext) -> Callable:
    """Select spawn strategy based on agent config."""
    spawn_mode = ctx.agent_config.get("spawn_mode", "worktree")
    is_lightweight = ctx.agent_config.get("lightweight", False)

    if spawn_mode == "scripts" and ctx.claimed_task:
        return spawn_implementer
    if is_lightweight:
        return spawn_lightweight
    return spawn_worktree


def _fetch_poll_data() -> dict | None:
    """Fetch combined scheduler state from the poll endpoint.

    Returns the poll response dict, or None if the call failed.
    Logs a debug warning on failure so callers can fall back gracefully.
    """
    try:
        orch_id = queue_utils.get_orchestrator_id()
        sdk = queue_utils.get_sdk()
        poll_data = sdk.poll(orch_id)
        debug_log(f"Poll response: queue_counts={poll_data.get('queue_counts')}, "
                  f"provisional_tasks={len(poll_data.get('provisional_tasks') or [])}, "
                  f"orchestrator_registered={poll_data.get('orchestrator_registered')}")
        return poll_data
    except Exception as e:
        debug_log(f"Poll endpoint unavailable, falling back to individual API calls: {e}")
        return None


def _run_agent_evaluation_loop(queue_counts: dict | None) -> None:
    """Evaluate and spawn agents for one tick.

    Args:
        queue_counts: Pre-fetched queue counts from poll (or None to use individual calls).
    """
    try:
        agents = get_agents()
        debug_log(f"Loaded {len(agents)} agents from config")
    except FileNotFoundError as e:
        print(f"Error: {e}")
        debug_log(f"Failed to load agents config: {e}")
        return

    if not agents:
        debug_log("No agents configured")
        return

    for agent_config in agents:
        agent_name = agent_config.get("name")
        role = agent_config.get("role")
        if not agent_name or not role:
            print(f"Skipping invalid agent config: {agent_config}")
            debug_log(f"Invalid agent config: {agent_config}")
            continue

        debug_log(f"Evaluating agent {agent_name}: role={role}")

        # Acquire agent lock
        agent_lock_path = get_agent_lock_path(agent_name)
        with locked_or_skip(agent_lock_path) as acquired:
            if not acquired:
                print(f"Agent {agent_name} is locked (another instance running?)")
                debug_log(f"Agent {agent_name} lock not acquired")
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
            print(f"[{datetime.now().isoformat()}] Starting agent {agent_name} (role: {role})")
            debug_log(f"Starting agent {agent_name} (role: {role})")

            strategy = get_spawn_strategy(ctx)
            try:
                pid = strategy(ctx)
                print(f"Agent {agent_name} started with PID {pid}")
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] Spawn failed for {agent_name}: {e}")
                debug_log(f"Spawn failed for {agent_name}: {e}")
                if ctx.claimed_task:
                    _requeue_task(ctx.claimed_task["id"])


def run_scheduler() -> None:
    """Main scheduler loop - evaluate and spawn agents.

    Runs with per-job intervals so individual jobs can run at different rates:
    - check_and_update_finished_agents: every 10s (local PID checks, no API)
    - _register_orchestrator: every 300s (uses poll to skip if already registered)
    - check_and_requeue_expired_leases: every 60s
    - process_orchestrator_hooks: every 60s (uses poll provisional_tasks list)
    - check_project_completion: every 60s (detect completed projects, create PRs)
    - _check_queue_health_throttled: every 1800s
    - agent_evaluation_loop: every 60s (uses poll queue_counts for backpressure)

    One poll() call per 60s tick replaces ~14 individual API calls.
    """
    print(f"[{datetime.now().isoformat()}] Scheduler starting")
    debug_log("Scheduler tick starting")

    # Check global pause flag
    if is_system_paused():
        print("System is paused (rm .octopoid/PAUSE or set 'paused: false' in agents.yaml)")
        debug_log("System is paused globally")
        return

    # Load per-job scheduler state (persists last_run across launchd invocations)
    scheduler_state = load_scheduler_state()

    # --- Fast local job (10s): check if any spawned agents have finished ---
    if is_job_due(scheduler_state, "check_and_update_finished_agents",
                  HOUSEKEEPING_JOB_INTERVALS["check_and_update_finished_agents"]):
        try:
            check_and_update_finished_agents()
        except Exception as e:
            debug_log(f"check_and_update_finished_agents failed: {e}")
        record_job_run(scheduler_state, "check_and_update_finished_agents")

    # --- Determine which remote jobs are due ---
    needs_register = is_job_due(scheduler_state, "_register_orchestrator",
                                HOUSEKEEPING_JOB_INTERVALS["_register_orchestrator"])
    needs_requeue = is_job_due(scheduler_state, "check_and_requeue_expired_leases",
                               HOUSEKEEPING_JOB_INTERVALS["check_and_requeue_expired_leases"])
    needs_hooks = is_job_due(scheduler_state, "process_orchestrator_hooks",
                             HOUSEKEEPING_JOB_INTERVALS["process_orchestrator_hooks"])
    needs_project_completion = is_job_due(scheduler_state, "check_project_completion",
                                          HOUSEKEEPING_JOB_INTERVALS["check_project_completion"])
    needs_health = is_job_due(scheduler_state, "_check_queue_health_throttled",
                              HOUSEKEEPING_JOB_INTERVALS["_check_queue_health_throttled"])
    needs_agents = is_job_due(scheduler_state, "agent_evaluation_loop",
                              HOUSEKEEPING_JOB_INTERVALS["agent_evaluation_loop"])

    needs_remote = (
        needs_register or needs_requeue or needs_hooks or needs_project_completion
        or needs_health or needs_agents
    )

    # --- Fetch poll data once for all remote jobs ---
    poll_data: dict | None = None
    if needs_remote:
        poll_data = _fetch_poll_data()

    # --- Register orchestrator (300s) ---
    if needs_register:
        orchestrator_registered = (poll_data or {}).get("orchestrator_registered", False)
        try:
            _register_orchestrator(orchestrator_registered=orchestrator_registered)
        except Exception as e:
            debug_log(f"_register_orchestrator failed: {e}")
        record_job_run(scheduler_state, "_register_orchestrator")

    # --- Requeue expired leases (60s) ---
    if needs_requeue:
        try:
            check_and_requeue_expired_leases()
        except Exception as e:
            debug_log(f"check_and_requeue_expired_leases failed: {e}")
        record_job_run(scheduler_state, "check_and_requeue_expired_leases")

    # --- Process orchestrator hooks on provisional tasks (60s) ---
    if needs_hooks:
        provisional_tasks = (poll_data or {}).get("provisional_tasks")
        try:
            process_orchestrator_hooks(provisional_tasks=provisional_tasks)
        except Exception as e:
            debug_log(f"process_orchestrator_hooks failed: {e}")
        record_job_run(scheduler_state, "process_orchestrator_hooks")

    # --- Project completion check (60s) ---
    if needs_project_completion:
        try:
            check_project_completion()
        except Exception as e:
            debug_log(f"check_project_completion failed: {e}")
        record_job_run(scheduler_state, "check_project_completion")

    # --- Queue health check (1800s, already self-throttled) ---
    if needs_health:
        try:
            _check_queue_health_throttled()
        except Exception as e:
            debug_log(f"_check_queue_health_throttled failed: {e}")
        record_job_run(scheduler_state, "_check_queue_health_throttled")

    # --- Agent evaluation loop (60s): use poll queue_counts for backpressure ---
    if needs_agents:
        queue_counts = (poll_data or {}).get("queue_counts")
        _run_agent_evaluation_loop(queue_counts=queue_counts)
        record_job_run(scheduler_state, "agent_evaluation_loop")

    # Persist updated last_run timestamps
    save_scheduler_state(scheduler_state)

    print(f"[{datetime.now().isoformat()}] Scheduler tick complete")
    debug_log("Scheduler tick complete")


def _check_venv_integrity() -> None:
    """Verify the orchestrator module is loaded from the correct location.

    If an agent runs `pip install -e .` inside its worktree, it hijacks the
    shared venv to load code from the wrong directory. Detect this and abort.
    """
    import orchestrator as _orch
    mod_file = getattr(_orch, "__file__", None) or ""
    # Also check a submodule to catch editable installs that set __file__ on the package
    scheduler_file = str(Path(__file__).resolve())
    if "agents/" in scheduler_file and "worktree" in scheduler_file:
        print(
            f"FATAL: orchestrator module loaded from agent worktree: {scheduler_file}\n"
            f"Fix: cd orchestrator && pip install -e .",
            file=sys.stderr,
        )
        sys.exit(1)


def main() -> None:
    """Entry point for scheduler."""
    global DEBUG

    _check_venv_integrity()

    parser = argparse.ArgumentParser(description="Run the orchestrator scheduler")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging to .octopoid/runtime/logs/",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (don't wait for lock)",
    )
    args = parser.parse_args()

    DEBUG = args.debug
    if DEBUG:
        setup_scheduler_debug()
        debug_log("Scheduler starting with debug mode enabled")
        print("Debug mode enabled - logs in .octopoid/runtime/logs/")

    scheduler_lock_path = get_scheduler_lock_path()

    with locked_or_skip(scheduler_lock_path) as acquired:
        if not acquired:
            print("Another scheduler instance is running, exiting")
            debug_log("Scheduler lock not acquired - another instance running")
            sys.exit(0)

        debug_log("Scheduler lock acquired")
        run_scheduler()


# Default template if file doesn't exist
if __name__ == "__main__":
    main()
