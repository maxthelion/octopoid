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
    get_scope,
    get_tasks_dir,
    get_tasks_file_dir,
    is_system_paused,
)
from .git_utils import ensure_worktree, get_task_branch, get_worktree_path, run_git
from .hook_manager import HookManager
from .lock_utils import locked_or_skip
from .port_utils import get_port_env_vars
from . import queue_utils
from .state_utils import (
    AgentState,
    is_overdue,
    is_process_running,
    load_state,
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


def guard_task_description_nonempty(ctx: AgentContext) -> tuple[bool, str]:
    """Guard against spawning agents for tasks with empty or missing descriptions.

    Only active for scripts-mode agents with a claimed task. Checks that the
    task's content (read from the .octopoid/tasks/ file) is non-empty. If the
    file is missing or empty, the task is moved to the failed queue and no
    agent is spawned.

    Args:
        ctx: AgentContext containing the claimed task

    Returns:
        (should_proceed, reason_if_blocked)
    """
    if not ctx.claimed_task:
        return (True, "")

    spawn_mode = ctx.agent_config.get("spawn_mode", "worktree")
    if spawn_mode != "scripts":
        return (True, "")

    content = ctx.claimed_task.get("content", "")
    if content and content.strip():
        return (True, "")

    # Content is empty — determine why and build a clear reason
    task_id = ctx.claimed_task.get("id", "unknown")
    file_path_str = ctx.claimed_task.get("file_path", "")

    tasks_file_dir = get_tasks_file_dir()
    expected_path = tasks_file_dir / f"TASK-{task_id}.md"

    if file_path_str:
        fp = Path(file_path_str)
        if fp.is_absolute() and fp.exists():
            reason = f"Task description is empty — file at {file_path_str} exists but has no content"
        else:
            reason = f"Task description is empty — no file at {expected_path}"
    else:
        reason = f"Task description is empty — no file at {expected_path}"

    debug_log(f"guard_task_description_nonempty: {reason}")

    try:
        sdk = queue_utils.get_sdk()
        fail_target = _get_fail_target_from_flow(ctx.claimed_task, "claimed")
        sdk.tasks.update(task_id, queue=fail_target, claimed_by=None)
        debug_log(f"Moved task {task_id} to {fail_target}: {reason}")
    except Exception as e:
        debug_log(f"guard_task_description_nonempty: failed to update task {task_id}: {e}")

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

    # Load task message thread for review_section
    review_section = ""
    task_id_for_thread = task.get("id", "")
    if task_id_for_thread:
        try:
            from .task_thread import get_thread, format_thread_for_prompt
            thread_messages = get_thread(task_id_for_thread)
            review_section = format_thread_for_prompt(thread_messages)
        except Exception as e:
            debug_log(f"Failed to load task thread for {task_id_for_thread}: {e}")

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
        review_section=review_section,
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



def handle_agent_result_via_flow(task_id: str, agent_name: str, task_dir: Path, expected_queue: str | None = None) -> bool:
    """Handle agent result using the task's flow definition.

    Replaces the hardcoded if/else dispatch for agent roles. Reads the flow,
    finds the current transition, and executes steps accordingly.

    The gatekeeper result format:
      {"status": "success", "decision": "approve"/"reject", "comment": "<markdown>"}
    or on failure:
      {"status": "failure", "message": "<reason>"}

    Returns:
        True if the task was transitioned or is gone (PID safe to remove).
        False if the task was not transitioned and the PID should be kept for retry.

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
            return True  # Nothing to track — PID safe to remove

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
            return True  # Task already moved on — PID safe to remove

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
            return True  # No transition defined — nothing to retry, PID safe to remove

        transition = transitions[0]  # Take first matching transition

        status = result.get("status")
        decision = result.get("decision")

        if status == "failure":
            # Agent couldn't complete — find on_fail state from agent condition
            message = result.get("message", "Agent could not complete review")
            debug_log(f"Flow dispatch: agent failure for {task_id}: {message}")
            sdk.tasks.update(task_id, last_error=message)
            for condition in transition.conditions:
                if condition.type == "agent" and condition.on_fail:
                    debug_log(f"Flow dispatch: rejecting {task_id} back to {condition.on_fail}")
                    sdk.tasks.reject(task_id, reason=message, rejected_by=agent_name)
                    return True  # Task transitioned — PID safe to remove
            # Default: reject back to incoming
            sdk.tasks.reject(task_id, reason=message, rejected_by=agent_name)
            return True  # Task transitioned — PID safe to remove

        # Agent-specific decision handling (approve/reject for gatekeeper)
        if decision == "reject":
            debug_log(f"Flow dispatch: agent rejected task {task_id}")
            reject_with_feedback(task, result, task_dir)
            print(f"[{datetime.now().isoformat()}] Agent {agent_name} rejected task {task_id}")
            return True  # Task transitioned — PID safe to remove

        if decision != "approve":
            debug_log(f"Flow dispatch: unknown decision '{decision}' for {task_id}, leaving in {current_queue} for human review")
            return True  # Cannot act — human review needed, retrying won't help

        # Execute the transition's runs (approve path — only reached on explicit "approve")
        if transition.runs:
            debug_log(f"Flow dispatch: executing steps {transition.runs} for task {task_id}")
            execute_steps(transition.runs, task, result, task_dir)
            print(f"[{datetime.now().isoformat()}] Agent {agent_name} completed task {task_id} (steps: {transition.runs})")
        else:
            # No runs defined — just log
            debug_log(f"Flow dispatch: no runs defined for transition from '{current_queue}', task {task_id}")

        return True  # Steps executed (or no steps needed) — PID safe to remove

    except Exception as e:
        import traceback
        debug_log(f"Error in handle_agent_result_via_flow for {task_id}: {e}")
        debug_log(traceback.format_exc())
        try:
            sdk = queue_utils.get_sdk()
            # Intentionally hardcoded: this is the emergency fallback that fires when
            # the flow system itself crashes (load_flow, execute_steps, etc.).  We
            # cannot consult the flow to find the target because the flow machinery is
            # what just failed.  "failed" is the only safe terminal state here.
            sdk.tasks.update(task_id, queue='failed', execution_notes=f'Flow dispatch error: {e}', last_error=str(e))
        except Exception:
            debug_log(f"Failed to move {task_id} to failed queue")
        return True  # Task moved to terminal state (or already gone) — PID safe to remove


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


def _perform_transition(sdk: object, task_id: str, to_state: str) -> None:
    """Perform the actual API call to transition a task to the given state.

    The mapping is:
    - "provisional" → sdk.tasks.submit()  (standard submit after implementation)
    - "done"        → sdk.tasks.accept()  (direct accept, e.g. child tasks)
    - anything else → sdk.tasks.update(queue=to_state)  (custom queues)
    """
    if to_state == "provisional":
        sdk.tasks.submit(task_id=task_id, commits_count=0, turns_used=0)
    elif to_state == "done":
        sdk.tasks.accept(task_id=task_id, accepted_by="flow-engine")
    else:
        sdk.tasks.update(task_id, queue=to_state)
    debug_log(f"Task {task_id}: engine performed transition to {to_state}")


def _get_fail_target_from_flow(task: dict, current_queue: str) -> str:
    """Consult the flow definition for the target queue when a task fails.

    Loads the task's flow and checks for on_fail targets on conditions in the
    transition from current_queue. Returns the first on_fail state found, or
    "failed" if the flow defines no failure path for this transition.

    Falls back to "failed" gracefully if the flow cannot be loaded (e.g. in
    test environments or when the flow file is missing).

    Args:
        task: Task dict containing at least a "flow" key (defaults to "default")
        current_queue: The queue the task is currently in

    Returns:
        Target queue name for failed outcomes (e.g. "failed", "incoming")
    """
    from .flow import load_flow

    try:
        flow_name = task.get("flow", "default")
        flow = load_flow(flow_name)
        transitions = flow.get_transitions_from(current_queue)
        if transitions:
            for condition in transitions[0].conditions:
                if condition.on_fail:
                    return condition.on_fail
    except Exception:
        pass  # Fall through to hardcoded default

    return "failed"


def _get_continuation_target_from_flow(task: dict, current_queue: str) -> str:
    """Consult the flow definition for the target queue when a task needs continuation.

    Currently the flow YAML has no dedicated continuation concept, so this
    always returns "needs_continuation". The function exists so the scheduler
    consults the flow for continuation routing — when flows gain a continuation
    path, this function will pick it up automatically.

    Falls back to "needs_continuation" gracefully if the flow cannot be loaded.

    Args:
        task: Task dict containing at least a "flow" key (defaults to "default")
        current_queue: The queue the task is currently in

    Returns:
        Target queue name for continuation outcomes (e.g. "needs_continuation")
    """
    # No continuation concept in flows yet — always use the standard queue.
    # When flows gain on_continuation support, look it up here.
    return "needs_continuation"


def _increment_step_failure_count(task_dir: Path) -> int:
    """Increment and return the consecutive step-failure count for a task."""
    counter_file = task_dir / "step_failure_count"
    count = 0
    if counter_file.exists():
        try:
            count = int(counter_file.read_text().strip())
        except (ValueError, OSError):
            count = 0
    count += 1
    try:
        counter_file.write_text(str(count))
    except OSError:
        pass
    return count


def _reset_step_failure_count(task_dir: Path) -> None:
    """Reset the step-failure counter for a task."""
    counter_file = task_dir / "step_failure_count"
    try:
        counter_file.unlink(missing_ok=True)
    except OSError:
        pass


def _handle_done_outcome(sdk: object, task_id: str, task: dict, result: dict, task_dir: Path) -> bool:
    """Execute flow steps for a successfully-completed task, then perform the transition.

    The engine owns the transition:
    1. Look up the flow transition from "claimed"
    2. Run pre-transition steps (push_branch, run_tests, create_pr, etc.)
    3. Engine calls the right API method based on the transition's to_state

    Returns:
        True if the task was transitioned (PID safe to remove).
        False if the task was not transitioned and the PID should be kept for retry.
    """
    from .flow import load_flow
    from .steps import execute_steps

    current_queue = task.get("queue", "unknown")
    if current_queue != "claimed":
        # Task is not in "claimed" — do not transition. Return False so the
        # caller keeps the PID and retries next tick. This prevents orphaning
        # a task that is still claimed but was observed in a transient state.
        debug_log(f"Task {task_id}: outcome=done but queue={current_queue}, skipping")
        return False

    flow_name = task.get("flow", "default")
    flow = load_flow(flow_name)
    # Use child_flow transitions if this is a child task in a project
    if task.get("project_id") and flow.child_flow:
        transitions = flow.child_flow.get_transitions_from("claimed")
    else:
        transitions = flow.get_transitions_from("claimed")

    if not transitions:
        # Fallback: direct submit if no transition defined in flow
        sdk.tasks.submit(task_id=task_id, commits_count=0, turns_used=0)
        debug_log(f"Task {task_id}: no flow transition from claimed, used direct submit")
        return True

    transition = transitions[0]

    # Execute pre-transition steps (side effects before state change)
    if transition.runs:
        debug_log(f"Task {task_id}: executing flow steps {transition.runs}")
        execute_steps(transition.runs, task, result, task_dir)

    # Engine performs the transition — the step list no longer needs a "move" step
    _perform_transition(sdk, task_id, transition.to_state)
    print(f"[{datetime.now().isoformat()}] Task {task_id} transitioned to {transition.to_state} via flow")
    return True


def _handle_fail_outcome(sdk: object, task_id: str, task: dict, reason: str, current_queue: str) -> bool:
    """Move a failed task to the appropriate queue, consulting the flow for the target.

    Loads the task's flow to find any on_fail target defined on conditions for
    the current transition. Falls back to "failed" if the flow defines no
    failure path.

    Returns:
        True if the task was transitioned (PID safe to remove).
        False if the task was not transitioned and the PID should be kept for retry.
    """
    if current_queue == "claimed":
        fail_target = _get_fail_target_from_flow(task, current_queue)
        sdk.tasks.update(task_id, queue=fail_target, last_error=reason)
        debug_log(f"Task {task_id}: failed (claimed → {fail_target}): {reason}")
        return True
    else:
        debug_log(f"Task {task_id}: outcome=failed but queue={current_queue}, skipping")
        return False


def _handle_continuation_outcome(sdk: object, task_id: str, task: dict, agent_name: str, current_queue: str) -> bool:
    """Move a task to the continuation queue, consulting the flow for the target.

    Loads the task's flow to find any continuation routing defined there.
    Currently flows have no dedicated continuation concept, so this always
    falls back to "needs_continuation". When flows gain on_continuation
    support, _get_continuation_target_from_flow will return that target.

    Returns:
        True if the task was transitioned (PID safe to remove).
        False if the task was not transitioned and the PID should be kept for retry.
    """
    if current_queue == "claimed":
        continuation_target = _get_continuation_target_from_flow(task, current_queue)
        sdk.tasks.update(task_id, queue=continuation_target)
        debug_log(f"Task {task_id}: needs continuation by {agent_name} (→ {continuation_target})")
        return True
    else:
        debug_log(f"Task {task_id}: outcome=needs_continuation but queue={current_queue}, skipping")
        return False


def handle_agent_result(task_id: str, agent_name: str, task_dir: Path) -> bool:
    """Handle the result of a script-based agent run.

    Reads result.json and transitions the task using flow steps:
    1. Read result.json to determine outcome
    2. Fetch current task state from server
    3. For "done" outcomes in "claimed" queue: execute the flow's steps, then the engine
       performs the transition to the target queue (submit, accept, or update)
    4. For "failed"/"error": move to failed queue
    5. For "needs_continuation": move to needs_continuation queue

    Raises on step failure so the caller (check_and_update_finished_agents) knows
    NOT to delete the PID — the next tick will retry. After 3 consecutive failures
    for the same task, the task is moved to failed and the function returns True
    (so the PID is removed).

    Returns:
        True if the task was transitioned or is gone (PID safe to remove).
        False if the task was not transitioned and the PID should be kept for retry.
        Raises on transient step failure (caller keeps PID for retry).

    Args:
        task_id: Task identifier
        agent_name: Name of the agent
        task_dir: Path to the task directory
    """
    result = _read_or_infer_result(task_dir)
    outcome = result.get("outcome", "error")
    debug_log(f"Task {task_id} result: {outcome}")

    sdk = queue_utils.get_sdk()

    task = sdk.tasks.get(task_id)
    if not task:
        debug_log(f"Task {task_id}: not found on server, skipping result handling")
        return True  # Nothing to track — PID safe to remove

    current_queue = task.get("queue", "unknown")
    debug_log(f"Task {task_id}: current queue = {current_queue}, outcome = {outcome}")

    try:
        if outcome in ("done", "submitted"):
            return _handle_done_outcome(sdk, task_id, task, result, task_dir)
        elif outcome in ("failed", "error"):
            return _handle_fail_outcome(sdk, task_id, task, result.get("reason", "Agent reported failure"), current_queue)
        elif outcome == "needs_continuation":
            return _handle_continuation_outcome(sdk, task_id, task, agent_name, current_queue)
        else:
            return _handle_fail_outcome(sdk, task_id, task, f"Unknown outcome: {outcome}", current_queue)
    except Exception as e:
        import traceback
        failure_count = _increment_step_failure_count(task_dir)
        print(
            f"[{datetime.now().isoformat()}] ERROR: step failure for task {task_id} "
            f"(attempt {failure_count}/3): {e}"
        )
        debug_log(f"handle_agent_result: step failure #{failure_count} for {task_id}:\n{traceback.format_exc()}")

        if failure_count >= 3:
            # Too many consecutive failures — give up and move to failed
            print(
                f"[{datetime.now().isoformat()}] Task {task_id}: {failure_count} consecutive "
                f"step failures, moving to failed"
            )
            try:
                sdk.tasks.update(
                    task_id,
                    queue="failed",
                    execution_notes=f"Step failure after {failure_count} attempts: {e}",
                    last_error=str(e),
                )
            except Exception as update_err:
                debug_log(f"handle_agent_result: failed to update {task_id} to failed: {update_err}")
            _reset_step_failure_count(task_dir)
            return True  # Task moved to terminal state — PID safe to remove

        raise  # Re-raise so caller leaves PID in tracking for retry


def _has_flow_blocking_conditions(task: dict) -> bool:
    """Check if the task's flow has agent/manual conditions on the current transition.

    Returns True if the flow requires an agent or human to explicitly approve
    before the task can transition from its current queue. In that case,
    process_orchestrator_hooks must not auto-accept the task — the gatekeeper
    (or a human) must run first via handle_agent_result_via_flow.

    Returns False if:
    - The flow has no conditions (or only script conditions) on the transition
    - The flow cannot be loaded (fail open so legacy tasks are unaffected)
    """
    from .flow import load_flow

    try:
        flow_name = task.get("flow", "default")
        current_queue = task.get("queue", "provisional")

        flow = load_flow(flow_name)
        transitions = flow.get_transitions_from(current_queue)

        if not transitions:
            return False

        transition = transitions[0]
        return any(
            c.type in ("agent", "manual") and not c.skip
            for c in transition.conditions
        )
    except Exception:
        # If flow can't be loaded, don't block legacy tasks
        return False


def process_orchestrator_hooks(provisional_tasks: list | None = None) -> None:
    """Run orchestrator-side hooks on provisional tasks.

    For each provisional task that has pending orchestrator hooks (e.g. merge_pr):
    1. Get pending orchestrator hooks
    2. Run each one via HookManager
    3. Record evidence
    4. If all hooks pass, accept the task

    Skips tasks whose flow has agent/manual conditions on the next transition —
    those require a gatekeeper or human to explicitly approve via
    handle_agent_result_via_flow. Auto-accepting such tasks bypasses the flow
    conditions and is the root cause of GH-143.

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

            # Skip tasks whose flow has blocking conditions (agent/manual approval).
            # These must go through the gatekeeper / human approval path, not here.
            if _has_flow_blocking_conditions(task):
                debug_log(
                    f"Task {task_id}: flow has agent/manual conditions, "
                    "skipping orchestrator hook auto-accept"
                )
                continue

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
                    try:
                        if claim_from != "incoming":
                            # Review agents (claim from provisional, etc.) use flow dispatch
                            transitioned = handle_agent_result_via_flow(task_id, instance_name, task_dir, expected_queue=claim_from)
                        else:
                            # Implementers (claim from incoming) use outcome dispatch
                            transitioned = handle_agent_result(task_id, instance_name, task_dir)
                        # Only remove PID when the handler confirmed a state transition
                        # (or the task is gone). If transitioned=False, the task was not
                        # moved — keep the PID so the next tick retries.
                        if transitioned:
                            del pids[pid]
                            print(f"[{datetime.now().isoformat()}] Instance {instance_name} (PID {pid}) finished")
                        else:
                            debug_log(
                                f"Instance {instance_name} (PID {pid}): handler returned False "
                                f"(task not transitioned), keeping PID for retry"
                            )
                    except Exception as e:
                        print(
                            f"[{datetime.now().isoformat()}] Instance {instance_name} (PID {pid}) "
                            f"result handling failed, will retry next tick: {e}"
                        )
                        # PID intentionally left in tracking for retry
                else:
                    # Task dir missing — clean up the PID
                    del pids[pid]
                    print(f"[{datetime.now().isoformat()}] Instance {instance_name} (PID {pid}) finished (no task dir)")
            else:
                # No task ID — clean up the PID
                del pids[pid]
                print(f"[{datetime.now().isoformat()}] Instance {instance_name} (PID {pid}) finished (no task id)")

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


def _evaluate_project_script_condition(condition: object, project_dir: Path, project_id: str) -> bool:
    """Evaluate a script-type condition for a project.

    Runs the condition's script in the project directory.
    Returns True if the script exits with code 0 (passes), False otherwise.

    The special script name 'run-tests' is mapped to auto-detected test runner
    commands (pytest, npm test, make test) based on project files present.
    """
    script_name = getattr(condition, "script", None)
    if not script_name:
        debug_log(f"Project {project_id}: condition '{condition.name}' has no script, passing by default")
        return True

    if script_name == "run-tests":
        # Auto-detect test runner based on project files
        test_cmd: list[str] | None = None
        if (project_dir / "pytest.ini").exists() or (project_dir / "pyproject.toml").exists():
            test_cmd = ["python", "-m", "pytest", "--tb=short", "-q"]
        elif (project_dir / "package.json").exists():
            test_cmd = ["npm", "test"]
        elif (project_dir / "Makefile").exists():
            test_cmd = ["make", "test"]

        if test_cmd is None:
            debug_log(f"Project {project_id}: no test runner detected, condition '{condition.name}' passes")
            return True
        cmd = test_cmd
    else:
        cmd = [script_name]

    try:
        proc = subprocess.run(
            cmd,
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode == 0:
            debug_log(f"Project {project_id}: condition '{condition.name}' passed")
            return True
        else:
            output = (proc.stdout + "\n" + proc.stderr)[-1000:]
            debug_log(
                f"Project {project_id}: condition '{condition.name}' failed "
                f"(exit {proc.returncode}):\n{output}"
            )
            print(
                f"[{datetime.now().isoformat()}] Project {project_id}: "
                f"condition '{condition.name}' failed (exit {proc.returncode})"
            )
            return False
    except subprocess.TimeoutExpired:
        debug_log(f"Project {project_id}: condition '{condition.name}' timed out")
        print(f"[{datetime.now().isoformat()}] Project {project_id}: condition '{condition.name}' timed out")
        return False
    except Exception as e:
        debug_log(f"Project {project_id}: condition '{condition.name}' error: {e}")
        return False


def _execute_project_flow_transition(sdk: object, project: dict, from_state: str) -> bool:
    """Execute a project flow transition from the given state via the flow engine.

    Loads the project's flow, finds the transition from from_state, evaluates
    script conditions, executes the transition's step list, then updates the
    project status to the transition's target state.

    Returns True if the transition was executed, False if skipped (no transition
    defined or a condition failed).
    """
    from .flow import load_flow
    from .steps import execute_steps

    project_id = project["id"]
    flow_name = project.get("flow", "project")

    try:
        flow = load_flow(flow_name)
    except FileNotFoundError:
        if flow_name != "project":
            debug_log(
                f"Project {project_id}: flow '{flow_name}' not found, falling back to 'project'"
            )
            try:
                flow = load_flow("project")
            except FileNotFoundError:
                debug_log(f"Project {project_id}: 'project' flow not found, skipping")
                return False
        else:
            debug_log(f"Project {project_id}: 'project' flow not found, skipping")
            return False

    transitions = flow.get_transitions_from(from_state)
    if not transitions:
        debug_log(
            f"Project {project_id}: no transition from '{from_state}' in flow '{flow_name}'"
        )
        return False

    transition = transitions[0]
    parent_project_dir = find_parent_project()

    # Evaluate conditions — script conditions run synchronously; manual conditions
    # are skipped here (they require explicit human action via approve_project_via_flow)
    for condition in transition.conditions:
        if condition.type == "script":
            if not _evaluate_project_script_condition(condition, parent_project_dir, project_id):
                debug_log(
                    f"Project {project_id}: condition '{condition.name}' failed, "
                    f"not transitioning to '{transition.to_state}'"
                )
                return False
        elif condition.type == "manual":
            # Manual conditions block automatic transitions — they require an explicit
            # human approval call (approve_project_via_flow). Skip silently here.
            debug_log(
                f"Project {project_id}: transition '{from_state} -> {transition.to_state}' "
                f"requires manual condition '{condition.name}' — skipping automatic transition"
            )
            return False
        # Agent conditions on project flows are not currently supported

    # Execute pre-transition steps
    if transition.runs:
        debug_log(f"Project {project_id}: executing steps {transition.runs}")
        execute_steps(transition.runs, project, {}, parent_project_dir)

    # Re-fetch project to pick up PR metadata stored by steps (e.g. create_project_pr)
    updated_project = sdk.projects.get(project_id) or project
    pr_url = updated_project.get("pr_url")

    # Perform the transition
    sdk.projects.update(project_id, status=transition.to_state)
    print(
        f"[{datetime.now().isoformat()}] Project {project_id} moved to '{transition.to_state}' "
        f"via flow (PR: {pr_url})"
    )
    debug_log(f"Project {project_id}: transitioned '{from_state}' -> '{transition.to_state}'")
    return True


def check_project_completion() -> None:
    """Check active projects and run the children_complete -> provisional flow transition.

    For each active project where every child task is in the 'done' queue:
    1. Load the project's flow (defaults to 'project')
    2. Find the 'children_complete -> provisional' transition
    3. Evaluate flow conditions (e.g. all_tests_pass) before transitioning
    4. Execute transition steps (e.g. create_project_pr) via the flow engine
    5. Update project status to the transition's target state

    Runs as a housekeeping job every 60 seconds. Skips projects that are
    already past 'active' status.
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
            if project_status in ("review", "provisional", "completed", "done"):
                debug_log(f"check_project_completion: skipping {project_id} (status={project_status})")
                continue

            tasks = sdk.projects.get_tasks(project_id)

            if not tasks:
                continue

            all_done = all(t.get("queue") == "done" for t in tasks)
            if not all_done:
                continue

            # All children done — project has no branch check kept for safety
            if not project.get("branch"):
                debug_log(f"check_project_completion: project {project_id} has no branch, skipping")
                continue

            debug_log(f"check_project_completion: all children done for {project_id}, running flow transition")
            _execute_project_flow_transition(sdk, project, "children_complete")

    except Exception as e:
        debug_log(f"check_project_completion failed: {e}")


def check_and_requeue_expired_leases() -> None:
    """Requeue tasks whose lease has expired (orchestrator-side fallback).

    Handles two cases:
    - Tasks in 'claimed' queue (claimed from 'incoming' via the implementer):
      returned to 'incoming'.
    - Tasks in 'provisional' queue with an active claim (claimed in-place by the
      gatekeeper via claim_for_review): claim fields are cleared, task stays in
      'provisional'.
    """
    try:
        sdk = queue_utils.get_sdk()
        now = datetime.now(timezone.utc)

        # Map: queue_name -> target queue after expiry
        # 'claimed' tasks came from 'incoming'; 'provisional' tasks stay in 'provisional'.
        queues_to_check = {
            "claimed": "incoming",
            "provisional": "provisional",
        }

        for queue_name, target_queue in queues_to_check.items():
            tasks = sdk.tasks.list(queue=queue_name)
            for task in tasks or []:
                # For provisional queue, only process tasks actively claimed
                # (claimed_by set) — unclaimed provisional tasks need no action.
                if queue_name == "provisional" and not task.get("claimed_by"):
                    continue

                lease_expires = task.get("lease_expires_at")
                if not lease_expires:
                    continue

                try:
                    expires_at = datetime.fromisoformat(lease_expires.replace('Z', '+00:00'))
                    if expires_at < now:
                        task_id = task["id"]
                        current_requeue_count = task.get("requeue_count") or 0
                        sdk.tasks.update(
                            task_id,
                            queue=target_queue,
                            claimed_by=None,
                            lease_expires_at=None,
                            requeue_count=current_requeue_count + 1,
                            last_error="Lease expired",
                        )
                        debug_log(f"Requeued expired lease: {task_id} → {target_queue} (expired {lease_expires})")
                        print(f"[{datetime.now().isoformat()}] Requeued expired lease: {task_id} → {target_queue}")
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
        from .config import _load_project_config as load_config
        sdk = get_sdk()
        orch_id = get_orchestrator_id()
        parts = orch_id.split("-", 1)
        cluster = parts[0] if len(parts) > 1 else "default"
        machine_id = parts[1] if len(parts) > 1 else orch_id
        config = load_config()
        repo_url = config.get("repo", {}).get("url", "")
        sdk._request("POST", "/api/v1/orchestrators/register", json={
            "id": orch_id,
            "cluster": cluster,
            "machine_id": machine_id,
            "repo_url": repo_url,
            "version": "2.0.0",
            "max_agents": config.get("agents", {}).get("max_concurrent", 3),
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


def send_heartbeat() -> None:
    """Send a heartbeat to the API server to update last_heartbeat.

    POSTs to /api/v1/orchestrators/{orchestrator_id}/heartbeat.
    Failures are non-fatal: errors are logged but never crash the scheduler.
    """
    try:
        from .queue_utils import get_sdk, get_orchestrator_id
        sdk = get_sdk()
        orch_id = get_orchestrator_id()
        sdk._request("POST", f"/api/v1/orchestrators/{orch_id}/heartbeat")
        debug_log(f"Heartbeat sent for orchestrator: {orch_id}")
    except Exception as e:
        debug_log(f"Heartbeat failed (non-fatal): {e}")


def sweep_stale_resources() -> None:
    """Archive logs and delete worktrees for old done/failed tasks.

    For each task in the 'done' or 'failed' queue that has been there
    for more than 1 hour:
    - Archives stdout.log, stderr.log, result.json, prompt.md to
      .octopoid/runtime/logs/<task-id>/
    - Deletes the worktree at .octopoid/runtime/tasks/<task-id>/worktree
    - Deletes the remote branch agent/<task-id> for done tasks only
    - Runs git worktree prune after deletions

    Idempotent: safe to run multiple times.
    Failed individual cleanups are logged but do not abort the sweep.
    """
    import shutil

    GRACE_PERIOD_SECONDS = 3600  # 1 hour

    try:
        sdk = queue_utils.get_sdk()
        done_tasks = sdk.tasks.list(queue="done") or []
        failed_tasks = sdk.tasks.list(queue="failed") or []
    except Exception as e:
        debug_log(f"sweep_stale_resources: failed to fetch tasks: {e}")
        return

    try:
        parent_repo = find_parent_project()
    except Exception as e:
        debug_log(f"sweep_stale_resources: could not find parent repo: {e}")
        return

    tasks_dir = get_tasks_dir()
    logs_dir = get_logs_dir()
    now = datetime.now(timezone.utc)
    pruned_any = False

    for task in done_tasks + failed_tasks:
        task_id = task.get("id")
        queue = task.get("queue", "")
        if not task_id:
            continue

        # Check age: skip if within grace period
        ts_str = task.get("updated_at") or task.get("completed_at")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            elapsed = (now - ts).total_seconds()
        except (ValueError, TypeError) as e:
            debug_log(f"sweep_stale_resources: could not parse timestamp {ts_str!r} for task {task_id}: {e}")
            continue

        if elapsed < GRACE_PERIOD_SECONDS:
            continue

        task_dir = tasks_dir / task_id
        worktree_path = task_dir / "worktree"

        # Archive logs and delete worktree if it exists
        if worktree_path.exists():
            # Archive log files before deleting
            try:
                archive_dir = logs_dir / task_id
                archive_dir.mkdir(parents=True, exist_ok=True)
                for filename in ("stdout.log", "stderr.log", "result.json", "prompt.md"):
                    src = task_dir / filename
                    if src.exists():
                        shutil.copy2(src, archive_dir / filename)
            except Exception as e:
                debug_log(f"sweep_stale_resources: failed to archive logs for {task_id}: {e}")

            # Remove worktree from git tracking and filesystem
            try:
                run_git(
                    ["worktree", "remove", "--force", str(worktree_path)],
                    cwd=parent_repo,
                    check=False,
                )
                if worktree_path.exists():
                    shutil.rmtree(worktree_path)
                pruned_any = True
                debug_log(f"sweep_stale_resources: deleted worktree for {task_id} ({queue})")
                print(f"[{datetime.now().isoformat()}] Swept worktree for task {task_id} ({queue})")
            except Exception as e:
                debug_log(f"sweep_stale_resources: failed to delete worktree for {task_id}: {e}")

        # Delete remote branch for done (merged) tasks only — not failed
        if queue == "done":
            branch = f"agent/{task_id}"
            try:
                result = run_git(
                    ["push", "origin", "--delete", branch],
                    cwd=parent_repo,
                    check=False,
                )
                if result.returncode == 0:
                    debug_log(f"sweep_stale_resources: deleted remote branch {branch}")
                    print(f"[{datetime.now().isoformat()}] Deleted remote branch {branch}")
                else:
                    # Already gone or no permissions — non-fatal
                    debug_log(
                        f"sweep_stale_resources: remote branch {branch} deletion skipped: "
                        f"{result.stderr.strip()}"
                    )
            except Exception as e:
                debug_log(f"sweep_stale_resources: failed to delete remote branch {branch}: {e}")

    # Run git worktree prune once after all deletions
    if pruned_any:
        try:
            run_git(["worktree", "prune"], cwd=parent_repo, check=False)
            debug_log("sweep_stale_resources: ran git worktree prune")
        except Exception as e:
            debug_log(f"sweep_stale_resources: git worktree prune failed: {e}")


HOUSEKEEPING_JOBS = [
    _register_orchestrator,
    check_and_requeue_expired_leases,
    check_and_update_finished_agents,
    _check_queue_health_throttled,
    process_orchestrator_hooks,
    check_project_completion,
    sweep_stale_resources,
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

def _requeue_task(task_id: str, source_queue: str = "incoming", task: dict | None = None) -> None:
    """Requeue a task back to its source queue after spawn failure.

    Args:
        task_id: Task to requeue.
        source_queue: Queue the task should return to. For tasks claimed from
            'incoming' (implementer), this is 'incoming'. For tasks claimed
            in-place from 'provisional' (gatekeeper), this is 'provisional'.
        task: Full task dict (if available) to read current requeue_count from.
    """
    try:
        from .queue_utils import get_sdk
        sdk = get_sdk()
        current_requeue_count = (task or {}).get("requeue_count") or 0
        sdk.tasks.update(
            task_id,
            queue=source_queue,
            claimed_by=None,
            lease_expires_at=None,
            requeue_count=current_requeue_count + 1,
            last_error="Spawn failed",
        )
        debug_log(f"Requeued task {task_id} back to {source_queue}")
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


def get_spawn_strategy(ctx: AgentContext) -> Callable:
    """Select spawn strategy: always uses spawn_implementer (scripts mode only)."""
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
        role = agent_config.get("role") or agent_config.get("type")
        if not agent_name or not role:
            missing = []
            if not agent_name:
                missing.append("name")
            if not role:
                missing.append("role")
            print(f"Skipping agent config (missing {', '.join(missing)}): {agent_config}")
            debug_log(f"Invalid agent config (missing {', '.join(missing)}): {agent_config}")
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
                    source_queue = ctx.agent_config.get("claim_from", "incoming")
                    _requeue_task(ctx.claimed_task["id"], source_queue=source_queue, task=ctx.claimed_task)


def run_scheduler() -> None:
    """Main scheduler loop - evaluate and spawn agents.

    Job intervals and grouping are defined declaratively in .octopoid/jobs.yaml.
    run_due_jobs() handles the poll-batching optimisation: it fetches poll data
    once if any remote job is due, avoiding ~14 individual API calls per tick.
    """
    from .jobs import run_due_jobs

    print(f"[{datetime.now().isoformat()}] Scheduler starting")
    debug_log("Scheduler tick starting")

    # Fail loudly if scope is not configured — prevents cross-project task claiming
    scope = get_scope()
    if not scope:
        print(
            "FATAL: 'scope' is not set in .octopoid/config.yaml. "
            "Add 'scope: <project-name>' to prevent cross-project task claiming.",
            file=sys.stderr,
        )
        sys.exit(1)
    debug_log(f"Scope: {scope}")

    # Check global pause flag
    if is_system_paused():
        print("System is paused (rm .octopoid/PAUSE or set 'paused: false' in agents.yaml)")
        debug_log("System is paused globally")
        return

    # Load per-job scheduler state (persists last_run across launchd invocations)
    scheduler_state = load_scheduler_state()

    # Dispatch all due jobs (declarative — intervals defined in .octopoid/jobs.yaml)
    poll_data = run_due_jobs(scheduler_state)

    # Persist updated last_run timestamps
    save_scheduler_state(scheduler_state)

    queue_counts: dict = (poll_data or {}).get("queue_counts") or {}
    if queue_counts:
        counts_str = ", ".join(
            f"{k}: {v}" for k, v in sorted(queue_counts.items())
        )
        print(f"[{datetime.now().isoformat()}] Scheduler tick complete ({counts_str})")
    else:
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
