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


def run_due_jobs(scheduler_state: dict) -> None:
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
    """
    from .scheduler import is_job_due, record_job_run, _fetch_poll_data

    jobs = load_jobs_yaml()

    # Classify which jobs are due by group
    due_local: list[dict] = []
    due_remote: list[dict] = []

    for job_def in jobs:
        name = job_def.get("name", "")
        interval = job_def.get("interval", 60)
        if is_job_due(scheduler_state, name, interval):
            group = job_def.get("group", "remote")
            if group == "local":
                due_local.append(job_def)
            else:
                due_remote.append(job_def)

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


def _run_job(job_def: dict, ctx: JobContext) -> None:
    """Dispatch a single job with error isolation."""
    name = job_def.get("name", "unknown")
    job_type = job_def.get("type", "script")

    try:
        if job_type == "script":
            func = JOB_REGISTRY.get(name)
            if func is None:
                _debug_log(f"No job function registered for: {name}")
                return
            func(ctx)
        elif job_type == "agent":
            _run_agent_job(job_def, ctx)
        else:
            _debug_log(f"Unknown job type '{job_type}' for job '{name}'")
    except Exception as e:
        _debug_log(f"{name} failed: {e}")


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
