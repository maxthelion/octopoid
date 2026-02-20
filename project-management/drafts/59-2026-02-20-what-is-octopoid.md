# What Is Octopoid — System Architecture and Design Philosophy

**Status:** Active
**Captured:** 2026-02-20

## The One-Liner

Octopoid is a distributed orchestration system that coordinates multiple Claude Code agents to perform software development tasks autonomously. It's CI/CD, but for development itself.

## The Problem

You want multiple AI agents working on your codebase in parallel — implementing features, writing tests, reviewing code. But agents are unreliable. They crash, they go off-task, they produce bad code. You can't just launch 5 Claude sessions and hope for the best. You need:

- **Coordination** — no two agents claiming the same task
- **Isolation** — each agent works in its own sandbox so they don't step on each other
- **Quality control** — automated review before anything touches your branch
- **Recovery** — when an agent crashes, its task goes back to the queue
- **Human oversight** — you can see what's happening and intervene

## The Architecture

Four layers, each with a clear responsibility:

```
┌──────────────────────────────────────┐
│  Agents (Claude Code instances)      │  Pure functions: read task, do work,
│  - Implementer                       │  write result.json. No side effects.
│  - Gatekeeper                        │
└──────────────┬───────────────────────┘
               │ result.json
┌──────────────┴───────────────────────┐
│  Scheduler (Python, launchd daemon)  │  Single supervisor. Reads results,
│  - Guard chain → claim → spawn       │  pushes branches, creates PRs,
│  - Steps: push_branch, create_pr     │  transitions state. Runs every 10s.
│  - Pool management, PID tracking     │
└──────────────┬───────────────────────┘
               │ SDK (HTTP)
┌──────────────┴───────────────────────┐
│  Server (Cloudflare Workers + D1)    │  Source of truth. Atomic claims,
│  - REST API for tasks/projects       │  lease-based locking, state machine
│  - Lease monitor (cron)              │  validation, optimistic concurrency.
│  - State machine with guards         │
└──────────────────────────────────────┘
               │
┌──────────────┴───────────────────────┐
│  Dashboard (Textual TUI)             │  Real-time monitoring. Task kanban,
│  - Polls every 5s                    │  agent status, draft browser,
│  - Work / Inbox / Agents / Tasks     │  detail views.
└──────────────────────────────────────┘
```

## The Core Insight: Agents as Pure Functions

This is the architectural decision everything else follows from.

An agent receives a git worktree and a rendered prompt. It does work — edits files, makes commits, runs tests. When it's done, it writes a single file:

```json
{"outcome": "done"}
```

That's it. The agent **never** pushes branches, creates PRs, updates task state, or calls the server API. The implementer prompt explicitly says:

> Do NOT create PRs, push branches, or call any scripts to submit your work. The orchestrator handles all of that automatically after you write result.json.

Why? Because agents are unreliable. If an agent pushes a branch and then crashes before recording the PR number, you have orphaned state. If an agent calls the API to transition a task and the API is slow, the agent might time out and leave the task in a half-transitioned state. By making agents pure functions with filesystem-only output, the scheduler becomes the single point of control. All state transitions are visible in one place (the flow YAML), and the system can recover from any agent failure by simply checking whether `result.json` exists.

## The Task Lifecycle

Defined in 18 lines of YAML (`.octopoid/flows/default.yaml`):

```
incoming ──→ claimed ──→ provisional ──→ done
    ↑                        │
    └────────────────────────┘
              (reject)
```

1. **Incoming** — task is in the queue, waiting to be claimed
2. **Claimed** — an agent has picked it up and is working on it (with a 5-minute lease)
3. **Provisional** — agent finished, branch pushed, PR created, awaiting review
4. **Done** — gatekeeper approved, PR merged

The rejection path (`provisional → incoming`) is where the gatekeeper sends tasks that don't meet acceptance criteria. The task file gets rewritten with feedback, and a new agent picks it up.

Additional states: `blocked` (waiting on dependencies), `failed` (unrecoverable error), `needs_continuation` (agent ran out of turns).

## Key Patterns

### Declarative Flows

The flow YAML defines transitions, conditions, and steps:

```yaml
transitions:
  "incoming -> claimed":
    agent: implementer
  "claimed -> provisional":
    runs: [push_branch, run_tests, create_pr]
  "provisional -> done":
    conditions:
      - name: gatekeeper_review
        type: agent
        agent: gatekeeper
        on_fail: incoming
    runs: [post_review_comment, merge_pr]
```

Steps are Python functions registered via `@register_step("name")`. Adding a new step (say, `run_linter`) means writing a function and adding its name to the YAML. No scheduler code changes.

Conditions can be `script` (runs a shell script, gate on exit code), `agent` (spawns a review agent), or `manual` (requires human approval). Each has an `on_fail` target state.

The flow engine validates itself at startup — unreachable states, invalid agent references, and missing `on_fail` targets are caught before any task runs.

### Claim/Lease Distributed Locking

Multiple orchestrators on different machines can safely compete for tasks:

1. Server finds highest-priority unclaimed task
2. Optimistic lock: `UPDATE tasks SET queue='claimed' WHERE id=? AND version=?`
3. Lease set: `lease_expires_at = now + 5 minutes`
4. If the orchestrator crashes, a cron job detects the expired lease and returns the task to `incoming`

The SDK's `poll()` endpoint batches what would be ~14 individual API calls (queue counts, provisional tasks, registration status) into a single request, reducing per-tick latency.

### Git Worktree Isolation

Every task gets its own git worktree at `.octopoid/runtime/tasks/<task-id>/worktree/`. The strict rule: **worktrees are always on detached HEAD.** Named branches are created only at push time by the `push_branch` step.

Why detached HEAD? Because git refuses to checkout a branch that's already checked out in another worktree. With 5 parallel agents, you'd constantly hit this. By staying on detached HEAD, the system supports unlimited parallel agents without git conflicts.

Worktrees are reused on retry — if a task is rejected and re-claimed, the agent picks up where the previous agent left off (same worktree, same commits).

### The Guard Chain

Before spawning an agent, the scheduler runs a filter pipeline, ordered cheapest-first:

```
1. guard_enabled          (config check — is agent paused?)
2. guard_pool_capacity    (file read — any slots available?)
3. guard_interval         (file read — enough time since last spawn?)
4. guard_backpressure     (cached data — any tasks in the queue?)
5. guard_pre_check        (subprocess — custom script passes?)
6. guard_claim_task       (API call — atomically claim a task)
7. guard_task_description (file read — task file has content?)
8. guard_pr_mergeable     (gh CLI — PR not conflicting?)
```

If any guard fails, the rest are skipped. A paused agent never touches the network. An agent at capacity never claims a task. The API call (the most expensive, state-mutating operation) happens only after all cheap checks pass.

### The Gatekeeper: Two-Phase Review

The gatekeeper is an agent that reviews other agents' work:

**Phase 1 — Scripted checks** (deterministic, fast):
- Run tests (auto-reject on failure)
- Check for debug code (advisory)
- Check scope drift (advisory)
- Verify PR status (auto-reject on conflicts)

**Phase 2 — LLM review** (only if phase 1 passes):
- Claude reviews the diff against the task's acceptance criteria
- Outputs `DECISION: APPROVE` or `DECISION: REJECT`
- Rejection includes detailed feedback

On rejection, the gatekeeper **rewrites the entire task file** — not just adds a comment. This is a hard-won lesson: agents read the task file, not PR comments. If you add a "REJECTION NOTICE" above the original instructions, the agent follows the original instructions and ignores the notice.

### The Scheduler as a Daemon

The scheduler is designed as a **single-tick-per-invocation** process. It doesn't run a loop — launchd fires it every 10 seconds. This is a Unix-philosophy choice:

- The scheduler can crash without leaving a zombie loop
- File-based locking prevents overlapping ticks
- No long-running process to monitor
- Each "job" (registration, health check, agent evaluation) has its own interval tracked via `scheduler_state.json`

### Projects: Coordinated Multi-Task Work

Projects break large initiatives into child tasks that share a git branch:

1. Project creates N child tasks with `project_id`
2. Children use a `child_flow` — no individual PRs, they commit directly to the shared branch
3. When all children reach `done`, the scheduler detects completion
4. Script conditions run (e.g., full test suite passes)
5. A single project PR is created
6. Human approves via `manual` condition

This means a 10-task project produces 1 PR, not 10.

## What's Special About This Approach

**The agent boundary is the key decision.** By making agents pure functions that only interact via the filesystem, every other problem becomes tractable:

- **Recovery** is simple — check if result.json exists, requeue if not
- **Testing** is simple — give an agent a worktree, check what it writes (mock the agent, not the infrastructure)
- **Observability** is simple — the flow YAML is the complete specification of what happens
- **Extension** is simple — add a step function, reference it in YAML
- **Distribution** is simple — the server handles coordination, orchestrators are stateless clients

The system deliberately avoids giving agents any control over their own lifecycle. An agent cannot requeue itself, cannot create follow-up tasks, cannot merge its own PR. All of that is the scheduler's job, governed by the flow definition. This makes the system predictable — you can read the YAML and know exactly what will happen.
