# Why Octopoid Keeps Breaking and How to Fix the Abstractions

**Status:** Idea
**Captured:** 2026-02-17

## Raw

> I have been pondering why it has been so hard to get this project working end to end consistently. What are your top ideas for how we get the same results with the same flexibility, but perhaps with some better abstractions?

## Patterns of Failure

In one session today, we hit all of these independently:

1. **Guard chain silently deleted** — commit d858559 removed `guard_claim_task()` claiming it was "replaced by flows." It wasn't. 50+ consecutive implementer crashes, undetected for days.

2. **Config format fragility** — an agent changed `fleet:` to `agents:` in agents.yaml. `get_agents()` returned `[]`. Scheduler silently did nothing. No error, no warning.

3. **Two implementations, both broken** — the gatekeeper has a Python role module AND an agent directory. The scheduler spawns the Python one, which can't find provisional tasks. The agent directory (which works) is never used.

4. **Spawn routing roulette** — `get_spawn_strategy()` has three branches: `spawn_implementer` (if scripts + claimed_task), `spawn_lightweight` (if lightweight), `spawn_worktree` (fallback). Missing claimed_task → wrong branch → crash.

5. **Branch defaulting chaos** — D1 `DEFAULT 'main'` silently overwrote NULL branches. Agents worked on wrong branch. Three layers of fallback logic in different files.

6. **Silent failures** — `_submit_to_server()` catches all exceptions. Tasks stuck in `claimed` forever. Server returns 500, SDK swallows it.

7. **State scattered everywhere** — `state.json` per agent, `result.json` per task, `claimed_task.json` per agent, worktree on disk, task record on server. Five places to look to answer "what is this agent doing?"

## Root Causes

### Too many ways to do the same thing

There are multiple paths through the system that should be one:

- **Spawn paths:** `spawn_implementer` / `spawn_lightweight` / `spawn_worktree` — three functions that share 80% of their logic. The branching is based on config flags that interact in surprising ways (`spawn_mode`, `lightweight`, `claimed_task` presence).

- **Agent definitions:** Python role modules (`orchestrator/roles/*.py`) vs agent directories (`.octopoid/agents/*/`). Both define "what an agent does" in incompatible ways. The scheduler has to know which one to use.

- **State tracking:** Server task records, local state.json, PID files, result.json, worktree existence. Each was added for a reason, but together they create a distributed state problem on a single machine.

- **Config formats:** `fleet:` list vs `agents:` map vs legacy `agents:` list. Each refactor adds a new format without fully removing the old one.

### No contracts between components

The scheduler passes `task` dicts around, but nothing defines what fields they have. The server adds a `flow` column, the scheduler crashes because it doesn't send it. The gatekeeper expects `worktree_path`, the server doesn't have it. These break at runtime, not at definition time.

### Imperative spaghetti instead of declarative composition

The scheduler is a 1600-line file that handles config loading, guard evaluation, task claiming, worktree creation, prompt rendering, process spawning, result handling, and housekeeping. Adding a new agent type means understanding all of it.

## Ideas for Better Abstractions

### 1. One spawn path

Delete `spawn_lightweight`, `spawn_worktree`, and the Python role module pattern. Every agent is a Claude instance with a prompt and scripts. The agent directory IS the agent definition. One function: `spawn_agent(task, agent_config) → pid`.

The differences between agent types become config, not code:

```yaml
implementer:
  claims_from: incoming
  needs_worktree: true      # create worktree from task branch
  spawn: claude              # always Claude

gatekeeper:
  claims_from: provisional
  needs_worktree: false      # reuse implementer's worktree
  spawn: claude

github-issue-monitor:
  claims_from: none          # doesn't claim tasks
  needs_worktree: false
  spawn: claude
```

No `spawn_mode`, no `lightweight`, no Python modules. The config declares what the agent needs; a single spawn function provides it.

### 2. Server is the only source of truth for task state

Stop maintaining `state.json`, `claimed_task.json`, `result.json` as local state. The server knows:
- Which tasks exist and their queue
- Who claimed them and when
- PR numbers, commit counts, etc.

Local state reduces to just:
- **PID tracking** — "which processes are running right now" (ephemeral, not persisted across restarts)
- **Worktree paths** — convention-based (`runtime/tasks/TASK-xxx/worktree/`), not stored

The scheduler queries the server, not the filesystem. `check_and_update_finished_agents()` becomes: "for each PID I'm tracking, is it still alive? If not, read the exit code and tell the server."

### 3. Fail loudly, fail early

- **No bare `except: pass`** — if `_submit_to_server()` fails, the agent should fail visibly. Write the error to result.json so the scheduler can surface it.
- **Schema validation at boundaries** — when the SDK receives a task dict from the server, validate it has the expected fields. When the scheduler reads agents.yaml, validate the format. Fail with a clear error, not a silent empty list.
- **Guard chain assertions in tests** — a test that asserts `AGENT_GUARDS` contains exactly the expected guards in order. Deleting a guard fails the test.

### 4. Convention over configuration

Too many things are configurable that should just be conventions:

- **Agent directories** always at `.octopoid/agents/<name>/`
- **Worktrees** always at `.octopoid/runtime/tasks/<task-id>/worktree/`
- **Scripts** always at `<agent-dir>/scripts/`
- **Prompts** always at `<agent-dir>/prompt.md`
- **Task branch** always `agent/<task-id>`

No `scripts_dir` config, no `worktree_path` field, no `agent_dir` resolution chain. The conventions are the API.

### 5. Smaller scheduler, extracted concerns

The scheduler should be a thin loop:

```
for each agent blueprint:
    if should_spawn(blueprint):
        task = claim_if_needed(blueprint)
        pid = spawn(blueprint, task)
        track(pid)
```

Everything else extracted:
- **Config loading** → already in `config.py`
- **Guard evaluation** → already in guards, but keep them tested
- **Task directory preparation** → move to `task_directory.py`
- **Prompt rendering** → move to `prompt_renderer.py` (was deleted, needs to come back as simpler version)
- **Result handling** → move to `result_handler.py`
- **Worktree management** → already in `git_utils.py`

The scheduler imports and composes these; it doesn't implement them.

## Context

This came up after a day of debugging where every fix revealed another broken layer. The system has the right capabilities — agents can implement features, review PRs, create tasks — but the plumbing between components is fragile because it grew organically without strong abstractions.

The v2.0 migration (API-only) was the right architectural call, but it was done incrementally — removing `is_db_enabled()` checks, adding SDK calls — rather than rethinking the spawn/state/lifecycle model. The result is v1 plumbing carrying v2 data.

## Open Questions

- How much of this is "fix the abstractions" vs "rewrite the scheduler"? The guard chain, spawn strategies, and state management are tightly coupled.
- Should agents communicate with the server directly (via SDK in scripts) or only through the scheduler? Currently both — scripts call `_submit_to_server()` and the scheduler also calls `handle_agent_result()`.
- Is the agent directory structure flexible enough, or do we need a plugin/hook system for agent-type-specific behavior (e.g., gatekeeper claims from provisional)?
- Could the scheduler be stateless? Query the server for "what needs doing", spawn agents, done. No local state files at all.

## Possible Next Steps

- Start with #1 (one spawn path) — it's the highest-leverage change and unblocks the gatekeeper fix (draft #29)
- Then #3 (fail loudly) — catches future regressions before they accumulate
- #2 (server as truth) and #4 (conventions) can be incremental
- #5 (smaller scheduler) follows naturally from #1
