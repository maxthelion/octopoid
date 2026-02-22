"""Declarative job system for scheduler housekeeping.

Jobs are defined in .octopoid/jobs.yaml and dispatched by run_due_jobs().
Each job function is registered with @register_job and accepts a JobContext.

Execution types:
  script — calls a registered Python function
  agent  — spawns a one-shot Claude agent using existing pool infrastructure
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml

from .config import (
    find_parent_project,
    get_agents,
    get_agents_runtime_dir,
    get_logs_dir,
    get_orchestrator_dir,
    get_tasks_dir,
)
from .git_utils import run_git
from .hook_manager import HookManager
from . import queue_utils
from .pool import count_running_instances, load_blueprint_pids, save_blueprint_pids
from .state_utils import is_process_running


# ---------------------------------------------------------------------------
# Core infrastructure
# ---------------------------------------------------------------------------


@dataclass
class JobContext:
    """Context passed to each job function.

    Attributes:
        scheduler_state: The current scheduler state dict (for is_job_due etc.).
        poll_data: Combined poll response from the server, or None if the job is
                   local (no API call) or poll failed. Job functions extract
                   relevant fields (e.g. poll_data.get("queue_counts")).
    """

    scheduler_state: dict
    poll_data: dict | None = None


# Registry mapping job name → callable that accepts JobContext
JOB_REGISTRY: dict[str, Callable[[JobContext], None]] = {}


def register_job(func: Callable[[JobContext], None]) -> Callable[[JobContext], None]:
    """Decorator — register a function in JOB_REGISTRY under its __name__."""
    JOB_REGISTRY[func.__name__] = func
    return func


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def get_jobs_yaml_path() -> Path:
    """Return the path to .octopoid/jobs.yaml."""
    return get_orchestrator_dir() / "jobs.yaml"


def load_jobs_yaml() -> list[dict]:
    """Load job definitions from .octopoid/jobs.yaml.

    Returns an empty list if the file does not exist or is empty.
    """
    path = get_jobs_yaml_path()
    if not path.exists():
        return []
    with open(path) as f:
        data = yaml.safe_load(f)
    return data.get("jobs", []) if data else []


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------


def run_due_jobs(scheduler_state: dict) -> dict | None:
    """Dispatch all due jobs for one scheduler tick.

    Preserves the poll-batching optimisation: a single poll() call fetches
    shared state for all remote jobs that are due in the same tick, instead
    of each job making its own API call.

    Job groups:
      local  — run immediately, no poll data needed (e.g. PID checks)
      remote — run after poll fetch; ctx.poll_data is populated

    Args:
        scheduler_state: Mutable dict loaded from scheduler_state.json.
                         is_job_due / record_job_run operate on this dict.
                         Caller is responsible for saving it afterwards.

    Returns:
        The poll_data dict fetched during this tick (contains queue_counts etc.),
        or None if no remote jobs were due or the poll call failed.
    """
    from .scheduler import is_job_due, record_job_run, _fetch_poll_data

    jobs = load_jobs_yaml()
    _debug_log(f"Loaded {len(jobs)} job definitions from YAML")

    # Classify which jobs are due by group
    due_local: list[dict] = []
    due_remote: list[dict] = []
    skipped: list[str] = []

    for job_def in jobs:
        name = job_def.get("name", "")
        interval = job_def.get("interval", 60)
        if is_job_due(scheduler_state, name, interval):
            group = job_def.get("group", "remote")
            if group == "local":
                due_local.append(job_def)
            else:
                due_remote.append(job_def)
        else:
            skipped.append(name)

    _debug_log(
        f"Due: {len(due_local)} local, {len(due_remote)} remote. "
        f"Skipped (not due): {', '.join(skipped) if skipped else 'none'}"
    )

    # Run local jobs first — no API calls needed
    for job_def in due_local:
        name = job_def["name"]
        ctx = JobContext(scheduler_state=scheduler_state, poll_data=None)
        _run_job(job_def, ctx)
        record_job_run(scheduler_state, name)

    # Fetch poll data once for all remote jobs
    poll_data: dict | None = None
    if due_remote:
        poll_data = _fetch_poll_data()

    # Run remote jobs with shared poll data
    for job_def in due_remote:
        name = job_def["name"]
        ctx = JobContext(scheduler_state=scheduler_state, poll_data=poll_data)
        _run_job(job_def, ctx)
        record_job_run(scheduler_state, name)

    return poll_data


def _run_job(job_def: dict, ctx: JobContext) -> None:
    """Dispatch a single job with error isolation."""
    name = job_def.get("name", "unknown")
    job_type = job_def.get("type", "script")

    _debug_log(f"Running job: {name} (type={job_type})")

    try:
        if job_type == "script":
            func = JOB_REGISTRY.get(name)
            if func is None:
                _debug_log(f"No job function registered for: {name}")
                return
            func(ctx)
            _debug_log(f"Job {name} completed OK")
        elif job_type == "agent":
            _run_agent_job(job_def, ctx)
            _debug_log(f"Agent job {name} completed OK")
        else:
            _debug_log(f"Unknown job type '{job_type}' for job '{name}'")
    except Exception as e:
        _debug_log(f"{name} FAILED: {e}")


def _run_agent_job(job_def: dict, ctx: JobContext) -> None:
    """Spawn a one-shot Claude agent for a job with type: agent.

    Counts against pool capacity. Uses the lightweight spawn strategy so no
    worktree is required. The agent receives its task via agent_config fields
    defined in the job YAML.
    """
    from .scheduler import (
        AgentContext,
        get_agent_state_path,
        get_spawn_strategy,
        load_state,
    )
    from .state_utils import AgentState

    name = job_def.get("name", "")
    blueprint = job_def.get("blueprint", name)
    max_instances = job_def.get("max_instances", 1)
    agent_config = dict(job_def.get("agent_config", {}))

    # Check pool capacity before spawning
    running = count_running_instances(blueprint)
    if running >= max_instances:
        _debug_log(f"Agent job '{name}' at capacity ({running}/{max_instances}), skipping")
        return

    _debug_log(f"Spawning agent job '{name}'")

    agent_config.setdefault("name", name)
    agent_config.setdefault("blueprint_name", blueprint)
    agent_config.setdefault("lightweight", True)
    agent_config.setdefault("max_instances", max_instances)

    state_path = get_agent_state_path(name)
    state = load_state(state_path)

    agent_ctx = AgentContext(
        agent_config=agent_config,
        agent_name=name,
        role=agent_config.get("role", "implement"),
        interval=job_def.get("interval", 60),
        state=state,
        state_path=state_path,
    )

    strategy = get_spawn_strategy(agent_ctx)
    try:
        pid = strategy(agent_ctx)
        _debug_log(f"Agent job '{name}' started with PID {pid}")
    except Exception as e:
        _debug_log(f"Agent job '{name}' spawn failed: {e}")
        raise  # Propagate so _run_job() logs failure instead of "completed OK"


def _debug_log(msg: str) -> None:
    """Proxy to scheduler.debug_log, using lazy import to avoid circular imports."""
    from . import scheduler
    scheduler.debug_log(msg)


# ---------------------------------------------------------------------------
# Job handler functions
#
# Each function below is registered in JOB_REGISTRY via @register_job.
# The implementation lives in scheduler.py (for backward compatibility with
# tests that import or patch these functions directly).  The handler here
# extracts whatever it needs from ctx and forwards to the implementation.
# ---------------------------------------------------------------------------


@register_job
def check_and_update_finished_agents(ctx: JobContext) -> None:
    """Check for finished agents and process their results.

    Delegates to the scheduler implementation which handles PID tracking,
    result dispatch, and flow transitions.
    """
    from .scheduler import check_and_update_finished_agents as _impl
    _impl()


@register_job
def _register_orchestrator(ctx: JobContext) -> None:
    """Register this orchestrator with the API server (idempotent).

    Passes orchestrator_registered from poll_data so the implementation
    can skip the POST when the server already knows about this orchestrator.
    """
    from .scheduler import _register_orchestrator as _impl
    orchestrator_registered = (ctx.poll_data or {}).get("orchestrator_registered", False)
    _impl(orchestrator_registered=orchestrator_registered)


@register_job
def check_and_requeue_expired_leases(ctx: JobContext) -> None:
    """Requeue tasks whose claim lease has expired."""
    from .scheduler import check_and_requeue_expired_leases as _impl
    _impl()


@register_job
def process_orchestrator_hooks(ctx: JobContext) -> None:
    """Run orchestrator-side hooks on provisional tasks.

    Passes pre-fetched provisional_tasks from poll_data so the implementation
    can skip the sdk.tasks.list() call when poll data is available.
    """
    from .scheduler import process_orchestrator_hooks as _impl
    provisional_tasks = (ctx.poll_data or {}).get("provisional_tasks")
    _impl(provisional_tasks=provisional_tasks)


@register_job
def check_project_completion(ctx: JobContext) -> None:
    """Check active projects and run the children_complete flow transition."""
    from .scheduler import check_project_completion as _impl
    _impl()


@register_job
def _check_queue_health_throttled(ctx: JobContext) -> None:
    """Check queue health (interval managed by declarative scheduler)."""
    from .scheduler import _check_queue_health_throttled as _impl
    _impl()


@register_job
def agent_evaluation_loop(ctx: JobContext) -> None:
    """Main agent evaluation and spawning loop.

    Passes pre-fetched queue_counts from poll_data so the evaluation loop
    can avoid per-agent API calls.
    """
    from .scheduler import _run_agent_evaluation_loop
    queue_counts = (ctx.poll_data or {}).get("queue_counts")
    _run_agent_evaluation_loop(queue_counts=queue_counts)


@register_job
def sweep_stale_resources(ctx: JobContext) -> None:
    """Archive logs and clean up stale worktrees and remote branches."""
    from .scheduler import sweep_stale_resources as _impl
    _impl()


@register_job
def send_heartbeat(ctx: JobContext) -> None:
    """Send a heartbeat to the API server to update last_heartbeat."""
    from .scheduler import send_heartbeat as _impl
    _impl()


# ---------------------------------------------------------------------------
# GitHub issue poller
# ---------------------------------------------------------------------------


def _load_github_issues_state(state_file: Path) -> dict:
    """Load processed-issues state from disk."""
    if not state_file.exists():
        return {"processed_issues": []}
    try:
        with open(state_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        _debug_log(f"poll_github_issues: could not load state ({e}), starting fresh")
        return {"processed_issues": []}


def _save_github_issues_state(state_file: Path, state: dict) -> None:
    """Persist processed-issues state to disk."""
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)
    except OSError as e:
        _debug_log(f"poll_github_issues: could not save state: {e}")


def _fetch_github_issues(cwd: Path) -> list[dict]:
    """Fetch open GitHub issues via the gh CLI."""
    try:
        result = subprocess.run(
            [
                "gh", "issue", "list",
                "--state", "open",
                "--json", "number,title,url,body,labels",
                "--limit", "100",
            ],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            _debug_log(f"poll_github_issues: gh issue list failed: {result.stderr.strip()}")
            return []
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        _debug_log("poll_github_issues: gh issue list timed out")
        return []
    except (json.JSONDecodeError, FileNotFoundError, Exception) as e:
        _debug_log(f"poll_github_issues: error fetching issues: {e}")
        return []


def _create_task_from_github_issue(issue: dict) -> str | None:
    """Create a task from a GitHub issue using create_task().

    Returns the task_id string on success, or None on failure.
    """
    from .tasks import create_task

    issue_number = issue["number"]
    title = issue["title"]
    url = issue["url"]
    body = issue.get("body") or ""
    labels = [label["name"] for label in issue.get("labels", [])]

    # Map labels → priority
    priority = "P1"
    if any(label in ("urgent", "critical", "P0") for label in labels):
        priority = "P0"
    elif any(label in ("low-priority", "P2") for label in labels):
        priority = "P2"

    # All issue-originated tasks use the implement role
    role = "implement"

    context_parts = [
        f"**GitHub Issue:** [{issue_number}]({url})",
        "",
        "**Description:**",
        body if body else "(No description provided)",
    ]
    if labels:
        context_parts.extend(["", "**Labels:** " + ", ".join(labels)])
    context = "\n".join(context_parts)

    acceptance_criteria = [
        f"Resolve GitHub issue #{issue_number}",
        "All tests pass",
        "Code follows project conventions",
    ]

    try:
        task_path = create_task(
            title=f"[GH-{issue_number}] {title}",
            role=role,
            context=context,
            acceptance_criteria=acceptance_criteria,
            priority=priority,
            created_by="poll_github_issues",
        )
        # Derive the task_id from the filename (TASK-<id>.md)
        task_id = task_path.stem.removeprefix("TASK-")
        _debug_log(f"poll_github_issues: created task {task_id} for issue #{issue_number}")
        return task_id
    except Exception as e:
        _debug_log(f"poll_github_issues: failed to create task for issue #{issue_number}: {e}")
        return None


def _comment_on_github_issue(issue_number: int, task_id: str, cwd: Path) -> None:
    """Post a comment on a GitHub issue noting that a task was created."""
    comment = (
        f"Octopoid has automatically created task `{task_id}` for this issue.\n\n"
        f"The task is now in the queue and will be picked up by an available agent."
    )
    try:
        subprocess.run(
            ["gh", "issue", "comment", str(issue_number), "--body", comment],
            cwd=cwd,
            capture_output=True,
            timeout=15,
        )
    except Exception as e:
        _debug_log(f"poll_github_issues: could not comment on issue #{issue_number}: {e}")


def _forward_github_issue_to_server(issue: dict, cwd: Path) -> bool:
    """Forward a server-labelled issue to maxthelion/octopoid-server.

    Creates a new issue on the server repo with a back-link, then comments
    on the original issue.
    """
    issue_number = issue["number"]
    title = issue["title"]
    url = issue["url"]
    body = (issue.get("body") or "").strip()

    cross_body = f"Forwarded from octopoid issue [{issue_number}]({url}).\n\n---\n\n{body}"

    try:
        result = subprocess.run(
            [
                "gh", "issue", "create",
                "--repo", "maxthelion/octopoid-server",
                "--title", title,
                "--body", cross_body,
            ],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            _debug_log(
                f"poll_github_issues: failed to forward issue #{issue_number}: "
                f"{result.stderr.strip()}"
            )
            return False

        server_issue_url = result.stdout.strip()
        _debug_log(
            f"poll_github_issues: forwarded issue #{issue_number} → {server_issue_url}"
        )

        # Comment on the original issue
        comment = f"This issue has been forwarded to the server repo: {server_issue_url}"
        subprocess.run(
            ["gh", "issue", "comment", str(issue_number), "--body", comment],
            cwd=cwd,
            capture_output=True,
            timeout=15,
        )
        return True

    except Exception as e:
        _debug_log(f"poll_github_issues: error forwarding issue #{issue_number}: {e}")
        return False


@register_job
def process_actions(ctx: JobContext) -> None:
    """Poll for execute_requested actions and dispatch to registered handlers.

    This is a pure dispatcher — no business logic lives here.  Each action_type
    maps to a handler registered in orchestrator/actions.py via
    @register_action_handler.

    Outcome for each action:
      - Handler returns → sdk.actions.complete(action_id, result)
      - Handler raises  → sdk.actions.fail(action_id, error_message)
      - No handler found → sdk.actions.fail(action_id, "unknown action_type: <type>")
    """
    from .sdk import get_sdk
    from .actions import get_handler

    sdk = get_sdk()

    try:
        actions = sdk.actions.list(status="execute_requested")
    except Exception as e:
        _debug_log(f"process_actions: failed to fetch actions: {e}")
        return

    if not actions:
        return

    _debug_log(f"process_actions: {len(actions)} action(s) to dispatch")

    for action in actions:
        action_id: str = action.get("id", "")
        action_type: str = action.get("action_type", "")

        handler = get_handler(action_type)
        if handler is None:
            error_msg = f"unknown action_type: {action_type}"
            _debug_log(f"process_actions: {error_msg}")
            try:
                sdk.actions.fail(action_id, {"error": error_msg})
            except Exception as e:
                _debug_log(f"process_actions: failed to mark action {action_id} as failed: {e}")
            continue

        try:
            result = handler(action, sdk)
            try:
                sdk.actions.complete(action_id, result)
            except Exception as e:
                _debug_log(f"process_actions: failed to mark action {action_id} complete: {e}")
        except Exception as handler_err:
            error_msg = str(handler_err)
            _debug_log(f"process_actions: {action_type} (id={action_id}) failed: {error_msg}")
            try:
                sdk.actions.fail(action_id, {"error": error_msg})
            except Exception as e:
                _debug_log(f"process_actions: failed to mark action {action_id} as failed: {e}")


@register_job
def poll_github_issues(ctx: JobContext) -> None:
    """Poll GitHub issues and create tasks for new ones.

    Rate limit budget: 1 gh issue list call per run (interval: 900s = 4 calls/hour).
    Issues labelled 'server' are forwarded to maxthelion/octopoid-server instead.
    Processed issue numbers are persisted in .octopoid/runtime/github_issues_state.json.
    """
    from .config import get_orchestrator_dir, find_parent_project

    state_file = get_orchestrator_dir() / "runtime" / "github_issues_state.json"
    parent_project = find_parent_project()

    state = _load_github_issues_state(state_file)
    processed_issues: set[int] = set(state.get("processed_issues", []))

    issues = _fetch_github_issues(parent_project)
    if not issues:
        return

    _debug_log(f"poll_github_issues: {len(issues)} open issue(s) fetched")

    new_count = 0
    forwarded_count = 0

    for issue in issues:
        issue_number = issue["number"]
        if issue_number in processed_issues:
            continue

        labels = [label["name"] for label in issue.get("labels", [])]

        if "server" in labels:
            _debug_log(
                f"poll_github_issues: issue #{issue_number} has 'server' label, forwarding"
            )
            if _forward_github_issue_to_server(issue, parent_project):
                processed_issues.add(issue_number)
                forwarded_count += 1
        else:
            task_id = _create_task_from_github_issue(issue)
            if task_id:
                _comment_on_github_issue(issue_number, task_id, parent_project)
                processed_issues.add(issue_number)
                new_count += 1

    state["processed_issues"] = sorted(processed_issues)
    _save_github_issues_state(state_file, state)

    if new_count or forwarded_count:
        _debug_log(
            f"poll_github_issues: created {new_count} task(s), "
            f"forwarded {forwarded_count} issue(s)"
        )
