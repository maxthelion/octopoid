# Declarative Scheduler Jobs — Implementation Design

**Status:** Draft (expanded from Draft #60)
**Captured:** 2026-02-20

## Summary

Replace the hardcoded housekeeping if-block chain in `run_scheduler()` with a declarative job system. Jobs are defined in YAML, dispatched by a generic runner, and support both programmatic (Python function) and agentic (LLM-spawned) execution — the same duality that flows already use for conditions.

## Why This Fits the Architecture

The system already has two declarative registries:

| Concept | Definition | Registry | Dispatch |
|---------|-----------|----------|----------|
| **Flow steps** | `runs:` in flow YAML | `@register_step` in `steps.py` | `execute_steps()` |
| **Flow conditions** | `conditions:` in flow YAML | `Condition` dataclass in `flow.py` | `evaluate()` / scheduler for agent/manual |

Jobs are the missing third leg — periodic work that isn't tied to a task transition. Today they're hardcoded in `scheduler.py:2352-2431` as 7 identical `is_job_due` / `try` / `record_job_run` blocks. Adding a new job means touching 4 places in a 2300-line file.

The proposed model:

| Concept | Definition | Registry | Dispatch |
|---------|-----------|----------|----------|
| **Jobs** | `.octopoid/jobs.yaml` | `@register_job` in `jobs.py` | Generic `run_due_jobs()` loop |

Jobs are **not** flow steps — they don't operate on tasks. They're system-level periodic functions. But they follow the same pattern: YAML declares intent, a registry maps names to callables, and a dispatcher executes them.

## Design

### Job Types

**`script` jobs** — call a registered Python function. The function signature is `(context: JobContext) -> None` where `JobContext` provides the SDK, poll data, and logger. This covers all existing housekeeping.

**`agent` jobs** — spawn a Claude agent with a prompt. The agent runs as a one-shot process (no worktree, no task claim). It writes `result.json` and exits. The scheduler reads the result and optionally acts on it. Agent jobs count against pool capacity — they consume the same resource (a Claude process).

### YAML Schema

```yaml
# .octopoid/jobs.yaml
jobs:
  # --- Existing housekeeping (migrated from hardcoded blocks) ---

  check_finished_agents:
    interval: 10
    type: script
    run: orchestrator.jobs:check_finished_agents
    group: local          # runs without needing poll data

  register_orchestrator:
    interval: 300
    type: script
    run: orchestrator.jobs:register_orchestrator
    group: remote         # needs poll data

  requeue_expired_leases:
    interval: 60
    type: script
    run: orchestrator.jobs:requeue_expired_leases
    group: remote

  process_hooks:
    interval: 60
    type: script
    run: orchestrator.jobs:process_hooks
    group: remote

  check_project_completion:
    interval: 60
    type: script
    run: orchestrator.jobs:check_project_completion
    group: remote

  queue_health:
    interval: 1800
    type: script
    run: orchestrator.jobs:check_queue_health
    group: remote

  agent_evaluation:
    interval: 60
    type: script
    run: orchestrator.jobs:evaluate_agents
    group: remote

  # --- New jobs (easy to add now) ---

  sweep_stale_worktrees:
    interval: 3600
    type: script
    run: orchestrator.jobs:sweep_stale_worktrees
    group: local
    conditions:
      - no_agents_running

  cleanup_merged_branches:
    interval: 3600
    type: script
    run: orchestrator.jobs:cleanup_merged_branches
    group: local

  # --- Agentic jobs ---

  review_stale_prs:
    interval: 7200
    type: agent
    prompt: |
      Review open PRs older than 48 hours. For each one, check if
      the branch still exists, if CI passed, and if it's blocked on
      review. Post a summary comment listing action items.
    model: haiku
    max_turns: 10
    conditions:
      - has_open_prs
```

### Key Schema Fields

```yaml
job_name:
  interval: int           # seconds between runs (required)
  type: script | agent    # execution mode (required)

  # For type: script
  run: module:function    # dotted path to registered job function (required for script)

  # For type: agent
  prompt: str             # prompt for the LLM agent (required for agent)
  model: str              # model to use (default: from agents.yaml defaults)
  max_turns: int          # max agentic turns (default: 10)
  allowed_tools: [str]    # tools the agent can use (default: [Read, Grep, Glob])

  # Common
  group: local | remote   # whether this job needs poll data (default: remote)
  conditions: [str]       # registered condition names — all must pass before job runs
  enabled: bool           # false to disable without deleting (default: true)
```

### Job Registry (`orchestrator/jobs.py`)

Parallels `steps.py` but with a different function signature:

```python
"""Job registry for declarative scheduler jobs.

Each job is a function: (ctx: JobContext) -> None
Jobs are referenced by name in jobs.yaml.
"""

from dataclasses import dataclass
from typing import Callable

@dataclass
class JobContext:
    """Context passed to every job function."""
    sdk: Any                    # OctopoidSDK instance
    poll_data: dict | None      # Pre-fetched poll data (None for local jobs)
    scheduler_state: dict       # Persisted scheduler state
    log: Callable[[str], None]  # Logger

JobFn = Callable[[JobContext], None]
JOB_REGISTRY: dict[str, JobFn] = {}

def register_job(name: str) -> Callable:
    """Decorator to register a job function."""
    def decorator(fn: JobFn) -> JobFn:
        JOB_REGISTRY[name] = fn
        return fn
    return decorator
```

Existing housekeeping functions move into this module largely unchanged — they just get a `@register_job` decorator and take `ctx: JobContext` instead of reading globals:

```python
@register_job("check_finished_agents")
def check_finished_agents(ctx: JobContext) -> None:
    """Check if any spawned agents have finished (local PID check)."""
    # Same logic as current check_and_update_finished_agents()
    ...

@register_job("requeue_expired_leases")
def requeue_expired_leases(ctx: JobContext) -> None:
    """Requeue tasks with expired leases."""
    # Same logic as current check_and_requeue_expired_leases()
    ...
```

### Job Conditions (`orchestrator/job_conditions.py`)

Simple boolean predicates. Not the same as flow `Condition` (which has `on_fail` routing). These are just gates.

```python
CONDITION_REGISTRY: dict[str, Callable[[JobContext], bool]] = {}

def register_condition(name: str) -> Callable:
    def decorator(fn):
        CONDITION_REGISTRY[name] = fn
        return fn
    return decorator

@register_condition("no_agents_running")
def no_agents_running(ctx: JobContext) -> bool:
    """True when no agent processes are currently running."""
    from .pool import get_running_agents
    return len(get_running_agents()) == 0

@register_condition("has_open_prs")
def has_open_prs(ctx: JobContext) -> bool:
    """True when there are open PRs to review."""
    counts = (ctx.poll_data or {}).get("queue_counts", {})
    return counts.get("provisional", 0) > 0
```

### Generic Job Runner

Replaces the if-block chain in `run_scheduler()`:

```python
def run_due_jobs(scheduler_state: dict, poll_data: dict | None) -> None:
    """Run all jobs that are due. Replaces the hardcoded if-chain."""
    jobs = load_jobs_yaml()
    ctx = JobContext(sdk=get_sdk(), poll_data=poll_data,
                     scheduler_state=scheduler_state, log=debug_log)

    # Determine if we need poll data
    needs_remote = any(
        is_job_due(scheduler_state, name, job["interval"])
        and job.get("group", "remote") == "remote"
        for name, job in jobs.items()
        if job.get("enabled", True)
    )

    if needs_remote and poll_data is None:
        ctx.poll_data = _fetch_poll_data()

    for name, job_def in jobs.items():
        if not job_def.get("enabled", True):
            continue
        if not is_job_due(scheduler_state, name, job_def["interval"]):
            continue

        # Check conditions
        conditions_pass = all(
            CONDITION_REGISTRY[c](ctx)
            for c in job_def.get("conditions", [])
            if c in CONDITION_REGISTRY
        )
        if not conditions_pass:
            debug_log(f"Job {name}: conditions not met, skipping")
            continue

        # Dispatch by type
        try:
            if job_def["type"] == "script":
                fn = JOB_REGISTRY.get(job_def["run"])
                if fn is None:
                    debug_log(f"Job {name}: function {job_def['run']} not registered")
                    continue
                fn(ctx)
            elif job_def["type"] == "agent":
                _spawn_job_agent(name, job_def, ctx)
        except Exception as e:
            debug_log(f"Job {name} failed: {e}")

        record_job_run(scheduler_state, name)
```

### Agent Job Spawning

Agent jobs reuse the existing agent spawn infrastructure. The key difference from flow-spawned agents: they don't have a task or worktree. They get a prompt and tools.

```python
def _spawn_job_agent(name: str, job_def: dict, ctx: JobContext) -> None:
    """Spawn a one-shot agent for a job."""
    from .pool import get_running_agents, get_pool_capacity

    # Check pool capacity — agent jobs share the same slots
    running = get_running_agents()
    capacity = get_pool_capacity()
    if len(running) >= capacity:
        ctx.log(f"Job {name}: pool at capacity ({len(running)}/{capacity}), deferring")
        return

    prompt = job_def["prompt"]
    model = job_def.get("model", "haiku")
    max_turns = job_def.get("max_turns", 10)
    allowed_tools = job_def.get("allowed_tools", ["Read", "Grep", "Glob"])

    # Spawn in a runtime directory (not a worktree — no task context)
    job_dir = get_runtime_dir() / "jobs" / name
    job_dir.mkdir(parents=True, exist_ok=True)

    # Use the same invoke_claude infrastructure as agents
    spawn_agent_process(
        role="job",
        prompt=prompt,
        work_dir=ctx.parent_project,  # run from project root
        result_dir=job_dir,
        model=model,
        max_turns=max_turns,
        allowed_tools=allowed_tools,
        metadata={"job_name": name},
    )
```

### Scheduler Changes

`run_scheduler()` shrinks from ~90 lines of if-blocks to:

```python
def run_scheduler():
    # ... existing preamble (pause check, lock, etc.) ...

    scheduler_state = load_scheduler_state()

    # One call replaces the entire if-block chain
    run_due_jobs(scheduler_state, poll_data=None)

    save_scheduler_state(scheduler_state)
```

The `poll_data` batching optimization is preserved — `run_due_jobs` fetches poll data once if any remote job is due, same as the current `needs_remote` check.

## Migration Path

This is a refactor, not a rewrite. The steps:

1. **Create `orchestrator/jobs.py`** with `@register_job`, `JobContext`, and `run_due_jobs()`
2. **Create `orchestrator/job_conditions.py`** with `@register_condition` and initial conditions
3. **Move existing housekeeping functions** from `scheduler.py` into `jobs.py` with `@register_job` decorators. Each function's logic stays the same; only the signature changes to accept `JobContext`.
4. **Create `.octopoid/jobs.yaml`** with the 7 existing jobs (same intervals, same grouping)
5. **Replace the if-block chain** in `run_scheduler()` with `run_due_jobs()`
6. **Delete `HOUSEKEEPING_JOB_INTERVALS`** dict — intervals now live in YAML
7. **Add agent job support** (lower priority — can be a follow-up)

Steps 1-6 are a pure refactor with no behavior change. Step 7 adds the new capability.

## What This Doesn't Change

- **Flow system** — untouched. Flows handle task transitions; jobs handle periodic work. Orthogonal concerns.
- **Agent pool** — untouched. Agent jobs just consume pool slots like any other agent.
- **Scheduler tick model** — still launchd-driven 10s ticks. Jobs just have a cleaner dispatch path.
- **State persistence** — `scheduler_state.json` format stays the same.

## Open Decisions

1. **`run:` syntax** — `module:function` (like the YAML example above) vs just `function_name` (like flow steps). The module path is more explicit but requires an import mechanism. Plain names with `@register_job` are simpler and consistent with `@register_step`. **Recommendation:** use plain names (`run: check_finished_agents`) and `@register_job("check_finished_agents")`, same as steps.

2. **Agent job results** — what happens when an agent job finishes? Script jobs are fire-and-forget. Agent jobs write `result.json`. Options:
   - Ignore the result (agent's side effects are the point)
   - Log the result summary
   - Allow jobs to define `on_result:` handlers (over-engineering for now)
   **Recommendation:** log the result, don't add handlers yet.

3. **Job-to-task creation** — should jobs be able to create tasks? (e.g. sweeper finds a problem, enqueues a fix). This is already possible via `ctx.sdk.tasks.create()` inside a job function. No special support needed. Agent jobs could also do this if given SDK access via tools. **Recommendation:** allow it implicitly, don't restrict it.

4. **YAML location** — `.octopoid/jobs.yaml` alongside `agents.yaml` and `config.yaml`? Or inside `.octopoid/flows/`? **Recommendation:** `.octopoid/jobs.yaml` — jobs aren't flows.

## Relationship to Draft #39 (Per-Job Intervals)

Draft #39 proposed activity-aware interval scaling (e.g. check leases more often when agents are active). That's orthogonal — once jobs are declarative, Draft #39 becomes a simple extension: add an `interval_active` / `interval_idle` field to the YAML schema and have `is_job_due` pick the right interval based on system state.
