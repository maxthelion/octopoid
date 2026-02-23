# Post Draft-50 Guide: What Changed and How to Adapt

This guide covers the architectural changes merged in the draft-50 branch and what existing octopoid installations need to update.

## Overview

Draft-50 was a major overhaul that completed the transition from v1.x (filesystem-centric, hardcoded state machine) to v2.0 (API-first, declarative flows, message-driven). The core theme: **the server is the source of truth for everything**.

## What Changed

### 1. Task files are gone

**Before:** `create_task()` wrote a markdown file to `.octopoid/tasks/TASK-{id}.md` and registered it on the server. Agents read the file from disk.

**After:** `create_task()` sends the full markdown content to the server as the `content` field. No file is written to `.octopoid/tasks/`. Agents receive the content in their prompt directly from the server response.

**Impact:** If you have scripts that read or write to `.octopoid/tasks/`, they will silently do nothing. Use the SDK instead:
```python
task = sdk.tasks.get(task_id)
content = task["content"]
```

### 2. Flows own all state transitions

**Before:** The scheduler had hardcoded `if role == "implementer"` / `elif role == "gatekeeper"` dispatch logic. State transitions were scattered across scheduler.py.

**After:** Declarative YAML flow definitions in `.octopoid/flows/` control all transitions. The flow engine (`result_handler.py`) reads the flow, finds the matching transition, evaluates conditions, and executes steps.

**Key files:**
- `.octopoid/flows/default.yaml` — standard flow (incoming → claimed → provisional → done)
- `.octopoid/flows/project.yaml` — multi-task project flow
- `orchestrator/flow.py` — flow parsing
- `orchestrator/result_handler.py` — flow dispatch
- `orchestrator/steps.py` — step functions (push_branch, create_pr, merge_pr, etc.)

**Default flow:**
```
incoming --[implementer agent]--> claimed
claimed  --[push_branch, run_tests, create_pr]--> provisional
provisional --[gatekeeper review, on_fail: incoming]--> done
done     --[post_review_comment, merge_pr]-->
```

See `docs/flows.md` for full documentation.

### 3. Rebase moved from submit to merge

**Before:** `rebase_on_main` ran in `before_submit` hooks (agent-side). Race condition: base branch could move between agent rebase and actual merge.

**After:** `rebase_on_base` runs in `before_merge` hooks (orchestrator-side, just before merge). The hook also uses the task's base branch instead of hardcoding `main`.

**Config change:**
```yaml
# Old
hooks:
  before_submit:
    - rebase_on_main
    - run_tests
    - create_pr

# New
hooks:
  before_submit:
    - run_tests
    - create_pr
  before_merge:
    - rebase_on_base
    - merge_pr
```

### 4. Rejection feedback via message threads (not file rewriting)

**Before:** On rejection, the task file was rewritten with the rejection notice prepended. Agents often ignored the notice and followed the original instructions.

**After:** Rejection feedback is posted as a message in a task thread (JSONL file at `.octopoid/shared/threads/TASK-{id}.jsonl`). The agent prompt includes a `$review_section` with the full conversation history. Original instructions stay intact.

### 5. Actions system replaces Python handler registry

**Before:** `orchestrator/actions.py` had a `@register_action_handler` decorator and a handler registry. Dashboard buttons called Python functions directly.

**After:** Actions are data on the server. The dashboard reads `action_data` (JSON with button definitions) and posts `action_command` messages. A message dispatcher job (`dispatch_action_messages`, runs every 30s) picks up commands and spawns lightweight Claude agents to execute them.

**Removed:** `orchestrator/actions.py` module entirely, `@register_action_handler`, `get_handler()`.

### 6. Scheduler is now job-based

**Before:** Monolithic tick function with hardcoded checks running in sequence.

**After:** Declarative jobs defined in `.octopoid/jobs.yaml`. Each job has an interval, group (local/remote), and execution type (script/agent). The scheduler dispatches due jobs and shares a single `poll()` response across all remote jobs.

**Current jobs:**
| Job | Interval | Group | Purpose |
|-----|----------|-------|---------|
| check_and_update_finished_agents | 10s | local | Process agent results |
| poll_github_issues | 900s | local | Create tasks from GitHub issues |
| _register_orchestrator | 300s | remote | Server registration |
| send_heartbeat | 60s | remote | Liveness signal |
| check_and_requeue_expired_leases | 60s | remote | Requeue stuck tasks |
| process_orchestrator_hooks | 60s | remote | Run before_merge hooks |
| check_project_completion | 60s | remote | Detect completed projects |
| agent_evaluation_loop | 60s | remote | Spawn agents for queued tasks |
| sweep_stale_resources | 1800s | remote | Clean up old worktrees/branches |
| dispatch_action_messages | 30s | remote | Handle inbox action commands |
| codebase_analyst | 86400s | agent | Daily codebase analysis |

### 7. Messages API

New server-side messaging system. SDK methods:
```python
sdk.messages.create(task_id="...", from_actor="human", type="comment", content="...")
sdk.messages.list(task_id="...", to_actor="human")
```

Message types: `comment`, `action_command`, `warning`, `worker_result`.

Used for: rejection feedback, draft actions, inbox communication, escalation warnings.

### 8. Dashboard redesign

- **Work tab:** Per-flow kanban tabs with topological state ordering
- **Inbox tab:** Master-detail layout, server messages, action buttons with free-text input
- **Drafts tab:** Split into User/Agent sub-tabs, action bar with default actions (Enqueue, Process, Archive)
- **Agents tab:** Two-tier tabs — Flow Agents (implementer/gatekeeper) and Background Agents (jobs)
- **Smart polling:** Single `poll()` call, conditional full refresh only when counts change

### 9. API key auth

The server now supports scope-scoped API keys. Keys are issued on first orchestrator registration and stored locally in `.octopoid/.api_key` (gitignored). The SDK reads this file automatically.

No manual setup needed — the scheduler captures the key from the registration response and persists it.

### 10. Circuit breaker

Tasks that fail repeatedly (default: 3 attempts) are automatically moved to `failed` queue instead of endlessly requeuing. Configurable via `agents.circuit_breaker_threshold` in config.yaml.

## Upgrading an Existing Installation

### Step 1: Pause the system

```
/pause-system
```

### Step 2: Pull latest code

```bash
git pull --recurse-submodules
```

### Step 3: Update Python SDK

```bash
pip install -e packages/python-sdk
```

### Step 4: Update config.yaml hooks

Move `rebase_on_main` to `before_merge` and rename to `rebase_on_base`:

```yaml
hooks:
  before_submit:
    - run_tests
    - create_pr
  before_merge:
    - rebase_on_base
    - merge_pr
```

### Step 5: Add jobs.yaml

If you don't have `.octopoid/jobs.yaml`, copy the one from the repo. This defines the scheduler's background jobs. Without it, the scheduler falls back to hardcoded defaults, but the jobs file gives you control over intervals and which jobs run.

### Step 6: Check your flow definitions

Verify `.octopoid/flows/default.yaml` exists and matches your desired workflow. If you had custom state machine logic, it needs to be expressed as a flow definition now.

### Step 7: Add scope to config.yaml

The scheduler requires a `scope` field. If missing, it refuses to start:

```yaml
scope: my-project-name
```

### Step 8: Remove stale env vars

If you have `OCTOPOID_API_KEY` set in your shell profile from the old `API_SECRET_KEY` system, remove it. The new auth system stores keys in `.octopoid/.api_key` automatically.

### Step 9: Restart the scheduler

Kill the old scheduler process and start a new one. The scheduler auto-clears `__pycache__` on startup.

### Step 10: Resume

```
/pause-system
```

Verify with `/queue-status` and `/agent-status`.

## Breaking Changes Summary

| What | Before | After |
|------|--------|-------|
| Task storage | `.octopoid/tasks/` files | Server `content` field |
| `create_task()` return | `Path` object | `"TASK-{id}"` string |
| State transitions | Hardcoded in scheduler | Declarative flows |
| Rebase timing | `before_submit` | `before_merge` |
| Rejection feedback | Task file rewrite | Message thread |
| Action handlers | Python `@register_action_handler` | Server-side `action_data` + message dispatcher |
| Scheduler structure | Monolithic tick | Declarative jobs |
| API key storage | `OCTOPOID_API_KEY` env var | `.octopoid/.api_key` file |
| `get_queue_dir()` | Returns path | Removed |
| `get_tasks_file_dir()` | Returns path | Removed |
| `orchestrator/actions.py` | Handler registry | Removed |
| `orchestrator/message_utils.py` | Filesystem messages | Removed (use SDK) |

## Files You Can Delete

After upgrading, these are no longer used:
- `.octopoid/tasks/` directory (task content lives on server)
- Any custom `orchestrator/actions.py` handlers
- Old `message_utils.py` references
- Pre-v2.0 test fixtures that mock filesystem queues
