# Octopoid Architecture Reference

**Status:** Living Document
**Date:** 2026-02-08

This document describes the architecture of Octopoid, the automated orchestration system for the Boxen project. It is grounded in the actual code as of the date above.

---

## 1. System Topology

### Components

Octopoid comprises the following runtime components:

| Component | Description | Location |
|-----------|-------------|----------|
| **Scheduler** | Single-process loop that evaluates and spawns agents on a cron-like tick | `orchestrator/orchestrator/scheduler.py` |
| **Agent subprocesses** | Claude Code CLI sessions (or lightweight Python scripts) spawned by the scheduler | `orchestrator/orchestrator/roles/*.py` |
| **SQLite database** | Source of truth for task queue state, agent records, and history | `.orchestrator/state.db` (WAL mode, schema v7) |
| **Queue directory tree** | Markdown task files mirroring DB state for human readability | `.orchestrator/shared/queue/{incoming,claimed,provisional,done,...}/` |
| **Git worktrees** | Per-agent git worktrees for isolated code changes | `.orchestrator/agents/{name}/worktree/` |
| **Review worktree** | Permanent shared worktree for human review and automated checks | `.orchestrator/agents/review-worktree/` |
| **Agent state files** | JSON state files tracking agent running/finished status | `.orchestrator/agents/{name}/state.json` |

### The Submodule Relationship

The orchestrator's *code* lives in the `orchestrator/` git submodule (a separate git repository). The *runtime configuration* lives in `.orchestrator/` in the main Boxen repo.

```
boxen/                          (main repo)
├── orchestrator/               (git submodule — orchestrator Python code)
│   ├── orchestrator/
│   │   ├── scheduler.py
│   │   ├── db.py
│   │   ├── queue_utils.py
│   │   ├── config.py
│   │   ├── backpressure.py
│   │   ├── roles/
│   │   │   ├── base.py
│   │   │   ├── implementer.py
│   │   │   ├── orchestrator_impl.py
│   │   │   ├── breakdown.py
│   │   │   ├── recycler.py
│   │   │   ├── rebaser.py
│   │   │   ├── pre_check.py
│   │   │   ├── check_runner.py
│   │   │   ├── gatekeeper.py
│   │   │   ├── proposer.py
│   │   │   └── ...
│   │   └── ...
│   ├── tests/
│   └── venv/
├── .orchestrator/              (runtime config, not in submodule)
│   ├── agents.yaml             (agent definitions)
│   ├── state.db                (SQLite database)
│   ├── venv/                   (Python virtualenv)
│   ├── agents/                 (per-agent runtime dirs)
│   │   ├── impl-agent-1/
│   │   │   ├── worktree/       (git worktree)
│   │   │   ├── state.json
│   │   │   ├── stdout.log
│   │   │   └── stderr.log
│   │   ├── review-worktree/    (shared review worktree)
│   │   └── ...
│   ├── shared/
│   │   ├── queue/              (task markdown files)
│   │   ├── breakdowns/         (breakdown output files)
│   │   ├── notes/              (agent learning notes per task)
│   │   └── projects/           (project YAML files)
│   ├── prompts/                (domain-specific prompts)
│   ├── logs/                   (scheduler and agent debug logs)
│   └── scripts/                (operational scripts)
```

**Critical implication:** The `orchestrator/` submodule and the main repo have **separate git object stores**. A commit in one is invisible from the other. This is the root cause of many debugging pitfalls when reviewing orchestrator_impl agent work.

### Scheduler Poll Loop

The scheduler runs as a single invocation per tick (driven externally by cron or launchd):

```
main() → run_scheduler()
  1. Check global pause flag (agents.yaml: `paused`)
  2. check_and_update_finished_agents() — detect dead processes, update state
  3. process_auto_accept_tasks() — move provisional tasks with auto_accept to done
  4. assign_qa_checks() — auto-add gk-qa check to app tasks with staging_url
  5. process_gatekeeper_reviews() — initialize/check gatekeeper review cycles
  6. check_stale_branches() — mark branches >5 commits behind for rebase
  7. For each agent in agents.yaml:
     a. Skip if paused
     b. Acquire agent lock (file-based, non-blocking)
     c. Check if still running (PID check)
     d. Check if overdue (interval_seconds elapsed since last run)
     e. check_backpressure_for_role() — role-specific capacity checks
     f. run_pre_check() — optional shell command to test for work
     g. Create/update worktree (ensure_worktree, peek_task_branch for branch)
     h. For orchestrator_impl: init submodule, verify isolation
     i. setup_agent_commands(), generate_agent_instructions()
     j. spawn_agent() — subprocess.Popen with detached session
     k. Update state.json and DB
```

Agents are spawned as `python -m orchestrator.roles.{role}` subprocesses. They run to completion and exit. The scheduler detects finished agents on the next tick via PID checks and exit code files.

---

## 2. Task Lifecycle

### High-Level Flow

```
             ┌─────────┐
  Human ───→ │ QUEUED  │ ◄─── Breakdown agent
             └────┬────┘
                  │ scheduler claims for matching agent
                  ▼
           ┌────────────┐
           │ IN PROGRESS │  LLM agent works (implementer / orch-impl)
           └──────┬─────┘
                  │ agent calls submit_for_review()
                  ▼
           ┌────────────┐
           │   CHECKS   │  Automated: pre-check + check_runner + gatekeepers
           └──────┬─────┘
                  │ all checks pass
                  ▼
           ┌────────────┐
           │  IN REVIEW  │  Human inspects
           └──────┬─────┘
                  │ human approves
                  ▼
             ┌────────┐
             │  DONE  │
             └────────┘
```

Each stage has specific rejection/recycling paths described below.

### DB Schema (tasks table)

```sql
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL UNIQUE,
    queue TEXT NOT NULL DEFAULT 'incoming',
    priority TEXT DEFAULT 'P2',         -- P0, P1, P2
    complexity TEXT,
    role TEXT,                          -- implement, orchestrator_impl, breakdown, test, etc.
    branch TEXT DEFAULT 'main',
    blocked_by TEXT,                    -- comma-separated task IDs
    claimed_by TEXT,                    -- agent name
    claimed_at DATETIME,
    commits_count INTEGER DEFAULT 0,
    turns_used INTEGER,
    attempt_count INTEGER DEFAULT 0,    -- pre-check rejections
    has_plan BOOLEAN DEFAULT FALSE,
    plan_id TEXT,
    project_id TEXT,                    -- FK to projects
    auto_accept BOOLEAN DEFAULT FALSE,
    rejection_count INTEGER DEFAULT 0,  -- gatekeeper/review rejections
    pr_number INTEGER,
    pr_url TEXT,
    checks TEXT,                        -- comma-separated check names
    check_results TEXT,                 -- JSON: {check_name: {status, summary, timestamp}}
    needs_rebase BOOLEAN DEFAULT FALSE,
    created_at DATETIME,
    updated_at DATETIME
);
```

### Queue States

The `queue` column holds one of these values:

| Queue | Description |
|-------|-------------|
| `incoming` | Ready to be claimed by an agent |
| `breakdown` | Awaiting decomposition by a breakdown agent |
| `claimed` | Locked by an agent (claimed_by set) |
| `provisional` | Submitted by agent, awaiting pre-check / review |
| `done` | Accepted and complete |
| `failed` | Agent reported failure |
| `recycled` | Burned out, sent to re-breakdown |
| `escalated` | Exceeded max rejections or max attempts, needs human |
| `rejected` | Rejected by agent (task invalid, duplicate, etc.) |
| `needs_continuation` | Agent timed out with partial work |

### Happy Path

```
incoming → claimed → provisional → done
```

1. **incoming**: Task created via `create_task()`. File written to `queue/incoming/TASK-{id}.md`, DB row inserted.
2. **claimed**: Agent calls `claim_task(role_filter=..., agent_name=...)`. Atomically moves queue to `claimed`, sets `claimed_by` and `claimed_at`. Blocked tasks (non-null `blocked_by`) are skipped. Previously-rejected tasks are prioritized.
3. **provisional**: Agent calls `submit_completion(task_path, commits_count, turns_used)`. File moves to `queue/provisional/`.
4. **done**: Pre-check/recycler/auto-accept/gatekeeper/human calls `accept_completion()`. Moves to `queue/done/`, clears `claimed_by`, calls `_unblock_dependent_tasks()`.

### Rejection Paths

**Pre-check rejection** (no commits):
```
provisional → incoming  (attempt_count incremented)
```
Function: `reject_completion()`. Resets `claimed_by`, `commits_count`, `turns_used` to zero.

**Gatekeeper/review rejection** (code quality):
```
provisional → incoming  (rejection_count incremented, branch preserved)
```
Function: `review_reject_completion()`. Branch is NOT reset so the agent can push fixes.

**Burnout recycling** (0 commits, 80+ turns):
```
provisional → recycled  (new breakdown task created)
```
Function: `recycle_to_breakdown()`. Original task moves to `recycled` queue. A new `breakdown` task is created with the original task content as context.

**Escalation** (max rejections or max attempts):
```
provisional → escalated
```
After `max_rejections` (default 3) gatekeeper rejections, or `max_attempts_before_planning` (default 3) pre-check failures.

### Side Effects on Queue Transitions

All queue transitions go through `update_task_queue()` which guarantees:
- Moving to `done` always calls `_unblock_dependent_tasks()` to remove the completed task from other tasks' `blocked_by` lists
- Moving to `done` always clears `claimed_by`
- A `task_history` row is inserted for every transition

### Task Dependencies

Dependencies are stored as comma-separated task IDs in `blocked_by`. When a task moves to `done`, `_unblock_dependent_tasks()` scans all tasks and removes the completed ID from their `blocked_by`. When all blockers are removed, the task becomes claimable.

The recycler also runs `reconcile_stale_blockers()` on each tick to catch any dependency that was missed.

---

## 3. Agent Roles

### Active Roles

#### `implementer` (class: `ImplementerRole`)
- **File:** `orchestrator/orchestrator/roles/implementer.py`
- **Claims from:** `incoming` queue, `role_filter='implement'`
- **Worktree:** Yes (full git worktree with `node_modules`)
- **Claude invocation:** Yes, `max_turns=100` (fresh), `max_turns=50` (continuation)
- **Flow:**
  1. Check for continuation work (task markers, `needs_continuation` queue, WIP branches)
  2. If no continuation: `claim_task(role_filter='implement')`
  3. Create feature branch `agent/{task_id}` from base branch
  4. Invoke Claude with Read/Write/Edit/Glob/Grep/Bash/Skill tools
  5. Claude implements, commits, tests
  6. Count commits via `get_commit_count(worktree, since_ref=head_before)`
  7. Create PR via `gh pr create`
  8. `submit_completion()` to provisional queue
- **Agents:** `impl-agent-1` (active), `impl-agent-2` (paused)

#### `orchestrator_impl` (class: `OrchestratorImplRole`)
- **File:** `orchestrator/orchestrator/roles/orchestrator_impl.py`
- **Claims from:** `incoming` queue, `role_filter='orchestrator_impl'`
- **Worktree:** Yes (main branch, with submodule initialized)
- **Claude invocation:** Yes, `max_turns=200`
- **Flow:**
  1. `claim_task(role_filter='orchestrator_impl')`
  2. Create feature branch in main repo (tracking only)
  3. Create submodule branch `orch/{task_id}` in `worktree/orchestrator/`
  4. Snapshot submodule HEAD before implementation
  5. Invoke Claude with explicit submodule paths in prompt
  6. Count commits from SUBMODULE (not main repo)
  7. **No PR.** Instead, attempt self-merge via `_try_merge_to_main()`:
     - **Submodule path** (`orch/{task_id}`):
       - Rebase onto main, run `pytest tests/ -v`
       - Fast-forward merge to main in agent worktree submodule
       - Fetch into main checkout's submodule, ff-merge, push to origin
       - Update submodule ref in main repo, commit, push
     - **Main repo path** (`tooling/{task_id}`) — push-to-origin pattern:
       - Fetch `origin/main`, rebase onto it (all in agent worktree)
       - Push rebased branch to origin, then `git push origin tooling/{task_id}:main`
       - If ff push fails, rebase and retry once
       - Send notification to human inbox: "run `git pull`"
       - Human working tree is never touched
  8. If self-merge succeeds: `accept_completion(accepted_by='self-merge')`
  9. If self-merge fails: `submit_completion()` for manual review
- **Agent:** `orch-impl-1` (active)

#### `breakdown` (class: `BreakdownRole`)
- **File:** `orchestrator/orchestrator/roles/breakdown.py`
- **Claims from:** `breakdown` queue, `role_filter='breakdown'`
- **Worktree:** Yes (for codebase exploration)
- **Claude invocation:** Yes, two phases:
  - Phase 1 (exploration): `max_turns=50`, tools: Read/Glob/Grep/Bash/Task
  - Phase 2 (decomposition): `max_turns=10`, no tools (structured JSON output only)
- **Flow:**
  1. `claim_task(role_filter='breakdown', from_queue='breakdown')`
  2. Explore codebase to understand patterns, testing, files, integration points
  3. Generate structured JSON subtasks with dependencies
  4. Write breakdown file to `.orchestrator/shared/breakdowns/`
  5. Human reviews and approves via `/approve-breakdown`
  6. `approve_breakdown()` creates individual tasks with dependency wiring
- **Agent:** `breakdown-agent` (active)

#### `recycler` (class: `RecyclerRole`)
- **File:** `orchestrator/orchestrator/roles/recycler.py`
- **Claims from:** N/A (polls provisional queue directly)
- **Lightweight:** Yes (no worktree, no Claude invocation)
- **Flow:**
  1. List all provisional tasks
  2. For each: check `is_burned_out(commits_count, turns_used)`
  3. If burned out: `recycle_to_breakdown(task_path)` or accept at depth cap
  4. Run `reconcile_stale_blockers()` to clear stuck dependencies
- **Agent:** `recycler` (active)

#### `pre_check` (class: `PreCheckRole`)
- **File:** `orchestrator/orchestrator/roles/pre_check.py`
- **Claims from:** N/A (polls provisional queue directly)
- **Lightweight:** Yes (no worktree, no Claude invocation)
- **Flow:**
  1. List all provisional tasks
  2. For each: accept if has commits, reject if no commits
  3. Burned out tasks: recycle to breakdown
  4. Too many attempts: recycle or escalate to planning
  5. Reset stuck claimed tasks (older than `claim_timeout_minutes`, default 60)
  6. Check for unblocked tasks
- **Note:** Currently the recycler handles most of this. Pre-check is a separate role that can run independently.

#### `rebaser` (class: `RebaserRole`)
- **File:** `orchestrator/orchestrator/roles/rebaser.py`
- **Claims from:** N/A (processes tasks with `needs_rebase=TRUE`)
- **Lightweight:** Yes (no Claude invocation; uses review worktree for git operations)
- **Flow:**
  1. `db.get_tasks_needing_rebase()`
  2. Skip `orchestrator_impl` tasks (v1 limitation)
  3. Find task branch via `git branch -r --list origin/*{task_id}*`
  4. In review worktree: checkout branch, rebase onto `origin/main`
  5. Run `npm run test:run`
  6. If pass: `git push --force-with-lease`, `db.clear_rebase_flag()`
  7. If fail: add note to task, leave `needs_rebase` set for human
- **Trigger:** Scheduler's `check_stale_branches()` marks tasks with `needs_rebase=TRUE` when their branch is 5+ commits behind `origin/main`

#### `check_runner` (class: `CheckRunnerRole`)
- **File:** `orchestrator/orchestrator/roles/check_runner.py`
- **Claims from:** N/A (polls provisional queue for tasks with pending checks)
- **Lightweight:** Yes (no Claude invocation)
- **Supported checks:** `pytest-submodule`, `gk-testing-octopoid`
- **Flow for `gk-testing-octopoid`:**
  1. Find agent worktree and submodule commits
  2. Set up review worktree submodule with clean `origin/main`
  3. Cherry-pick agent commits onto current main (with conflict detection)
  4. Run `pytest tests/ -v --tb=short`
  5. `db.record_check_result(task_id, check_name, 'pass'|'fail', summary)`
  6. On all checks complete: reject tasks with any failed check via `review_reject_task()`
- **Agent:** `gk-testing-octopoid` (active), others (paused)

#### `proposer` (class: `ProposerRole`)
- **File:** `orchestrator/orchestrator/roles/proposer.py`
- **Claude invocation:** Yes
- **Focus areas:** Each proposer has a `focus` field: `inbox_triage`, `backlog_grooming`, `test_quality`, `code_structure`, `project_plans`
- **Git lifecycle:** After Claude finishes, the role's `_commit_and_push()` method checks for uncommitted changes in the worktree. If changes exist, it creates a `tooling/<agent-name>-<timestamp>` branch, commits, and pushes to origin. This runs regardless of whether Claude succeeded or failed (to preserve partial work).
- **Agents:** `inbox-poller` (active, focus: `inbox_triage`); others paused

### Disabled Roles

| Role | Class | Status | Purpose |
|------|-------|--------|---------|
| `gatekeeper` | `GatekeeperRole` | gk-qa active (auto-dispatched for app tasks with staging_url); gk-architecture and gk-testing paused; global system disabled (`gatekeeper.enabled: false`) | LLM-based review of branch diffs; records pass/fail per focus area (architecture, testing, qa) |
| `gatekeeper_coordinator` | N/A | Not in agents.yaml | Orchestrates gatekeeper review cycles, aggregates results |
| `curator` | `CuratorRole` | Paused | Evaluates proposals, scores them, approves/rejects |
| `reviewer` | `ReviewerRole` | Paused | Code review on PRs |
| `pr_coordinator` | N/A | Paused | Watches for open PRs, creates review tasks |

---

## 4. Two Task Models

### App Tasks (role = `implement`)

```
create_task(role='implement', branch='main')
  → agent claims, creates feature branch agent/{task_id}
  → agent commits to feature branch
  → agent creates PR via `gh pr create`
  → submit_completion() → provisional
  → pre-check accepts (has commits) → done
  → human merges PR via `gh pr merge`
```

**Key characteristics:**
- Feature branch in the main Boxen repo
- PR-based workflow
- Tests: `npm run test:run` (vitest)
- Merge: human runs `gh pr merge` or `approve_and_merge()` which calls `gh pr merge --merge --delete-branch`

### Orchestrator Tasks (role = `orchestrator_impl`)

```
create_task(role='orchestrator_impl', branch='main')
  → agent claims, creates orch/{task_id} in submodule + tooling/{task_id} in main repo
  → agent commits to submodule and/or main repo tooling branch
  → NO PR created
  → determine target branch:
      if task has project_id → use project.branch (e.g. "project/my-feature")
      else → use "main"
  → self-merge attempt (submodule):
      ensure target branch exists → rebase onto target → pytest → ff-merge → push
      if target is main: update submodule ref in main repo
      if target is project branch: skip submodule ref update (deferred to project completion)
  → self-merge attempt (main repo, push-to-origin):
      fetch origin/{target} → rebase → push branch → push tooling/{task_id}:{target}
      → retry once if ff push fails → notify human to git pull
  → if self-merge succeeds: accept_completion(accepted_by='self-merge')
  → if self-merge fails: submit_completion() → provisional → manual review
  → on accept_completion: if task has project_id and all project tasks done →
      merge_project_to_main() merges project branch to main in submodule,
      updates submodule ref, sets project status to "complete"
```

**Key characteristics:**
- Boxen worktree is on `main`; all work in `worktree/orchestrator/` submodule
- Submodule feature branch: `orch/{task_id}`
- Main repo tooling branch: `tooling/{task_id}` (push-to-origin, never touches human checkout)
- Tests: `pytest tests/ -v` (in submodule, using `.orchestrator/venv/`)
- No PR in main repo
- Self-merge on success; `approve_orchestrator_task.py` for manual approval
- Default check: `gk-testing-octopoid` (auto-added by `create_task()` for `orchestrator_impl` role)
- **Project-aware:** tasks with `project_id` merge to project branch, not main

**Fallback approval script:** `.orchestrator/scripts/approve_orchestrator_task.py <task-id>`
- Auto-detects agent branch
- Cherry-picks to main in submodule
- Pushes submodule
- Updates submodule ref in main repo
- Calls `accept_completion()` in DB

---

## 5. Review Pipeline

### Pipeline Stages

```
Agent submits                                    Human/system
    │                                                │
    ▼                                                │
[provisional queue]                                  │
    │                                                │
    ├── auto_accept=true? ─────→ [done] ◄────────────┤
    │                                                │
    ├── recycler: burned out? ──→ [recycled] → breakdown queue
    │                                                │
    ├── pre-check: has commits? ─→ pass ─────────────┤
    │   └── no commits ─────────→ [incoming] (retry) │
    │                                                │
    ├── check_runner: run checks ─→ pass/fail        │
    │   └── all checks passed? ──→ awaits human      │
    │   └── any failed? ────────→ [incoming] (retry) │
    │                                                │
    ├── gatekeeper (disabled): LLM review            │
    │   └── all passed? ────────→ [done]             │
    │   └── any failed? ────────→ [incoming] (retry) │
    │                                                │
    └── human review via PR ─────→ merge → [done] ◄──┘
```

### Pre-Check

Runs as part of the scheduler tick or as a standalone lightweight agent. Checks:
- `commits_count > 0` (configurable via `pre_check.require_commits`)
- Burned out: `is_burned_out()` → recycle
- Max attempts exceeded → recycle or escalate

### Auto-Accept

Tasks or projects with `auto_accept=true` skip the entire review pipeline. The scheduler's `process_auto_accept_tasks()` moves them directly from provisional to done.

### Automated Checks (check_runner)

Tasks can have a `checks` column (comma-separated list of check names). The check runner processes provisional tasks with pending checks:
- `pytest-submodule`: cherry-pick agent commits, run pytest
- `gk-testing-octopoid`: rebase + pytest with divergence detection

Results stored in `check_results` JSON column: `{check_name: {status: 'pass'|'fail', summary: str, timestamp: str}}`.

### QA Gatekeeper (gk-qa)

`assign_qa_checks()` in the scheduler auto-assigns the `gk-qa` check to provisional app tasks that have a `staging_url` (Cloudflare Pages preview). The staging URL is populated by `_store_staging_url()` in reports.py, which scrapes Cloudflare bot comments on the PR.

Flow:
1. Task reaches provisional with commits → scheduler assigns gk-qa check (if staging_url present)
2. `dispatch_gatekeeper_agents()` spawns the gk-qa agent for the pending check
3. Agent evaluates the staging URL against acceptance criteria
4. Agent records pass/fail via `/record-check` skill
5. `process_gatekeeper_reviews()` handles the result (leave for human review or reject)

Tasks without a staging_url are silently skipped and retried on subsequent ticks.

### Gatekeeper Review (disabled)

When `gatekeeper.enabled: true` in agents.yaml:
1. Scheduler initializes review tracking for provisional tasks with commits
2. Gatekeeper agents (architecture, testing, qa) review the branch diff
3. Each records pass/fail via `/record-check` skill
4. When all reviews complete: all pass → accept; any fail → reject with feedback

### Self-Merge (orchestrator_impl only)

The `_try_merge_to_main()` method in `OrchestratorImplRole` determines the
merge target branch from the task's project (if any) and performs:

1. Determine target branch: project branch if task has `project_id`, else `main`
2. If target is not `main`: ensure branch exists locally (check local, then origin, then create from base)
3. Rebase `orch/{task_id}` onto target branch in the agent's worktree submodule
4. Run `pytest` via the orchestrator venv
5. Fast-forward merge to target branch in agent's worktree submodule
6. Fetch into main checkout's submodule, ff-merge to target branch
7. Push submodule target branch to origin
8. **If target is `main`:** update submodule ref in main repo, commit, push
9. **If target is a project branch:** skip submodule ref update (deferred to project completion)

If any step fails, falls back to `submit_completion()` for manual review.

### Project Completion Merge

When `accept_completion()` detects that all tasks in a project are done
(via `check_project_completion()`), it calls `merge_project_to_main()`:

1. Look up project branch from DB
2. In the main checkout submodule: fetch, checkout project branch, merge to main (ff-only, fallback to regular merge)
3. Push submodule main to origin
4. Update submodule ref in main repo, commit, push
5. Set project status to `"complete"` in DB

This ensures the submodule ref on `main` is only updated once — when the entire
project is finished — rather than after each individual task.

---

## 6. Burnout and Recycling

### Detection

A task is "burned out" when:
```python
def is_burned_out(commits_count, turns_used):
    return commits_count == 0 and (turns_used or 0) >= BURNED_OUT_TURN_THRESHOLD
```

`BURNED_OUT_TURN_THRESHOLD = 80` (defined in `queue_utils.py`).

This applies to ALL roles including `orchestrator_impl`. The orchestrator_impl role correctly counts submodule commits and reports them via `submit_completion()`.

### Recycling Flow

`recycle_to_breakdown(task_path, reason='too_large')`:

1. Read original task content
2. Check depth cap: if `RE_BREAKDOWN_DEPTH >= 1`, return `None` (escalate to human instead)
3. Gather project context (completed siblings, branch info)
4. Create new breakdown task with rich context (original task content, project state, completed sibling list)
5. Move original task to `recycled` queue
6. Dependencies are NOT rewired at recycle time (to avoid race conditions). They are rewired at breakdown approval time in `approve_breakdown()`.

### Depth Cap

Maximum re-breakdown depth: 1 level. If a re-breakdown task itself burns out, it is accepted for human review rather than recursively re-broken-down.

### Dependency Rewiring

When a breakdown is approved via `approve_breakdown()`:
1. Parse subtasks from the breakdown file
2. Create tasks with internal dependencies
3. Identify "leaf" subtasks (not depended on by any sibling)
4. For re-breakdowns: call `_rewire_dependencies(original_task_id, leaf_ids)` to point external dependents at the leaf subtasks instead of the original recycled task

This ensures external tasks stay blocked until the actual replacement work is done.

### Rejection Counters Summary

There are **three distinct rejection counters** on each task:

| Counter | Incremented by | Meaning |
|---------|---------------|---------|
| `attempt_count` | `reject_completion()` (pre-check) | Agent submitted with 0 commits |
| `rejection_count` | `review_reject_task()` (check_runner / human) | Code didn't pass checks or review |
| (burn-out) | `is_burned_out()` heuristic | 0 commits + 80+ turns = task too big |

### Dashboard Column Mapping

The TUI dashboard (`octopoid-dash.py`) maps DB state to columns:

| Column | DB state |
|--------|----------|
| QUEUED | `incoming` (unblocked) |
| IN PROGRESS | `claimed` |
| CHECKS | `provisional` with pending checks |
| IN REVIEW | `provisional` with all checks passed (or no checks) |

---

## 7. Git Topology

### Repositories

```
boxen (main repo)
├── .git/
│   ├── modules/orchestrator/     (submodule object store for main checkout)
│   └── worktrees/
│       ├── impl-agent-1-worktree/
│       │   └── modules/orchestrator/  (submodule object store for this worktree)
│       ├── orch-impl-1-worktree/
│       │   └── modules/orchestrator/  (submodule object store for this worktree)
│       └── ...
└── orchestrator/                  (submodule checkout, on branch main)
```

Each git worktree gets its OWN submodule object store under `.git/worktrees/{name}/modules/orchestrator/`. The scheduler's `_verify_submodule_isolation()` checks that a worktree's submodule `.git` pointer references its own store (contains "worktrees" in the path) rather than the main checkout's store.

### Worktrees

| Worktree | Location | Purpose |
|----------|----------|---------|
| Per-agent | `.orchestrator/agents/{name}/worktree/` | Isolated working copy for agent code changes |
| Review | `.orchestrator/agents/review-worktree/` | Shared worktree for human review and automated checks (has own `node_modules`) |

Worktrees are created/updated by `ensure_worktree()` in `git_utils.py`. Both new and existing worktrees are always based on `origin/{base_branch}` (after a fetch), not the local branch. This prevents stale state when the human's local main is behind origin. Similarly, `create_feature_branch()` detaches to `origin/{base_branch}` before creating the agent branch. The scheduler peeks at the next task's branch via `peek_task_branch()` to create the worktree on the right base branch, saving the agent from wasting turns on `git checkout`.

### Branch Naming

| Pattern | Used by | Example |
|---------|---------|---------|
| `agent/{task_id}` | Implementer agents | `agent/a49f5ecd` |
| `orch/{task_id}` | Orchestrator_impl agents (in submodule) | `orch/c338e19e` |
| `feature/{project_id}` | Project feature branches | `feature/proj-abc12345` |

### Three Separate Git Contexts (orchestrator tasks)

When reviewing orchestrator_impl work, commits can exist in three places with separate object stores:

1. **Agent worktree submodule:** `.orchestrator/agents/{agent}/worktree/orchestrator/` (branch `orch/{task-id}`)
2. **Main checkout submodule:** `orchestrator/` (branch `main`)
3. **Remote:** `origin/main` or `origin/orch/{task-id}` in the submodule

A commit visible in one location is invisible from the others via `git log` or `git cat-file`. "I can't find the commit" does not mean "the commit doesn't exist."

---

## 8. Configuration

### agents.yaml

Located at `.orchestrator/agents.yaml`. Top-level keys:

```yaml
agents:              # List of agent definitions
database:
  enabled: true      # SQLite mode (required for most features)
  path: state.db     # Relative to .orchestrator/
model: proposal      # Orchestrator model type (task or proposal)
queue_limits:
  max_incoming: 20   # Max tasks in incoming queue
  max_claimed: 5     # Max concurrently claimed tasks
  max_open_prs: 10   # Max open GitHub PRs
gatekeeper:
  enabled: false     # Gatekeeper review system
  max_rejections: 3  # Max review rejections before escalation
  required_checks:
  - architecture
  - testing
  - qa
  skip_if_auto_accept: true
proposal_limits:     # Per-proposer limits
voice_weights:       # Weight multipliers for proposer types
paused: false        # Global pause flag
```

### Agent Definition

```yaml
- name: impl-agent-1
  role: implementer
  interval_seconds: 30         # Minimum time between spawns
  paused: false                # Per-agent pause
  lightweight: false           # If true, no worktree needed (default false)
  pre_check: "ls -A ..."      # Shell command to check for work (optional)
  pre_check_trigger: non_empty # Trigger type: non_empty, exit_zero, exit_nonzero
  focus: inbox_triage          # For proposers/gatekeepers (optional)
  base_branch: main            # Worktree base branch (optional, default main)
  target_roles:                # For gatekeepers: which task roles to review
  - orchestrator_impl
```

### Backpressure Rules

Defined in `orchestrator/orchestrator/backpressure.py`. Each role maps to a check function:

| Role | Check function | Conditions |
|------|----------------|------------|
| `implementer` | `check_implementer_backpressure` | Has incoming tasks AND claimed < `max_claimed` AND open PRs < `max_open_prs` |
| `orchestrator_impl` | `check_implementer_backpressure` | Same as implementer |
| `breakdown` | `check_breakdown_backpressure` | Has tasks in breakdown queue |
| `recycler` | `check_recycler_backpressure` | Has tasks in provisional queue |
| `gatekeeper` | `check_gatekeeper_backpressure` | Has active reviews with pending checks |
| `check_runner` | `check_check_runner_backpressure` | Has provisional tasks with pending automated checks |

If backpressure blocks, the scheduler records `blocked_reason` and `blocked_at` in the agent's state file and skips spawning.

### Turn Limits by Role

| Role | Max turns | Notes |
|------|-----------|-------|
| `implementer` (fresh) | 100 | `max_turns=100` in `invoke_claude()` |
| `implementer` (continuation) | 50 | `max_turns=50` |
| `orchestrator_impl` | 200 | `max_turns=200` |
| `breakdown` (exploration) | 50 | Phase 1 |
| `breakdown` (decomposition) | 10 | Phase 2, no tools |
| `gatekeeper` | 15 | Review only |

### Pre-Check Configuration

```python
DEFAULT_PRE_CHECK_CONFIG = {
    "require_commits": True,
    "max_attempts_before_planning": 3,
    "claim_timeout_minutes": 60,
}
```

---

## 9. Escalation

### Failure Points and Responses

| Stage | Failure | Response | Function |
|-------|---------|----------|----------|
| **Claiming** | No tasks available | Agent exits cleanly (exit 0) | `claim_task()` returns `None` |
| **Claiming** | Task blocked by dependencies | Skipped, next task tried | `blocked_by IS NOT NULL` check in `claim_task()` |
| **Implementation** | Claude exits non-zero | Task failed or marked for continuation | `fail_task()` or `mark_needs_continuation()` |
| **Implementation** | No changes made | Task failed | `fail_task(task_path, 'Claude completed without making any changes')` |
| **Implementation** | PR creation fails | Mark for continuation | `mark_needs_continuation(reason='pr_creation_failed')` |
| **Pre-check** | No commits (1st attempt) | Rejected back to incoming | `reject_completion(reason='no_commits')`, `attempt_count` incremented |
| **Pre-check** | No commits (burned out) | Recycled to breakdown queue | `recycle_to_breakdown()` if `is_burned_out()` |
| **Pre-check** | No commits (max attempts) | Recycled or escalated to planning | `recycle_to_breakdown()` or `escalate_to_planning()` |
| **Automated check** | Check fails | Rejected with feedback (inserted at top of task file, before ## Context) | `review_reject_task()`, `rejection_count` incremented, previous rejection replaced |
| **Automated check** | Check fails (max rejections) | Escalated to human | `review_reject_task()` with `max_rejections=3` → escalated queue |
| **Self-merge** (orch) | Rebase conflict | Falls back to provisional | `submit_completion()` |
| **Self-merge** (orch) | pytest fails | Falls back to provisional | `submit_completion()` |
| **Self-merge** (orch) | Push fails | Local merge kept, human can push | Non-fatal warning |
| **Rebase** | Conflict or test failure | Note added to task, left for human | `needs_rebase` stays set |
| **Recycling** | Depth cap (depth >= 1) | Accepted for human review | `accept_completion()` |
| **Claimed timeout** | Task claimed > 60 minutes | Reset to incoming | `reset_stuck_claimed()` |

### Current Escalation Destinations

- **`failed` queue:** Agent-reported failures. Human can retry with `/retry-failed`.
- **`recycled` queue:** Burned-out tasks. New breakdown task created automatically.
- **`escalated` queue:** Max rejections or max attempts exceeded. Requires human attention.
- **Human inbox:** Messages sent via `message_utils.warning()` for critical escalations.
- **Task notes:** Rebaser and other agents add notes to `.orchestrator/shared/notes/TASK-{id}.md` when they encounter issues.

### Monitoring

The status script (`.orchestrator/venv/bin/python .orchestrator/scripts/status.py`) provides a single-page overview of:
- Scheduler health (last run, process status)
- Queue state (counts per queue)
- Agent status (running, paused, blocked, last exit)
- Worktree branches, commits, diffs
- Agent notes
- Breakdowns pending review
- Projects
- Open PRs
- Messages
