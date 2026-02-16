# Plan: Completing the Scheduler Refactor Project

## Current State

REFACTOR-01 through REFACTOR-10 are done. REFACTOR-11 and REFACTOR-12 are in flight.
All commits live on `origin/agent/REFACTOR-01-de692452`, based on `feature/client-server-architecture`.

## Phase 1: Finish REFACTOR-12

The monitor script handles push/detach/approve for each task as it completes. Once REFACTOR-12 finishes:

1. Let the monitor process it (push, detach, approve)
2. Verify the full commit chain on `origin/agent/REFACTOR-01-de692452`
3. Check if `approve_and_merge` created a PR or if we need to do it manually

**Expected:** The project completion flow should create a PR from `agent/REFACTOR-01-de692452` → `feature/client-server-architecture`. If it doesn't (likely, given what we've seen), we create it manually.

## Phase 2: Review the branch against drafts 9 and 10

Before merging, do a thorough review of what was actually built vs what was intended.

### Draft 10 (Scheduler Refactor) — check for:
- [ ] `AgentContext` dataclass extracted and used throughout
- [ ] Guard functions extracted as standalone functions with `(proceed, reason)` return
- [ ] `AGENT_GUARDS` list exists and is used in `evaluate_agent()`
- [ ] `HOUSEKEEPING_JOBS` list exists with fault-isolated `run_housekeeping()`
- [ ] Spawn strategies extracted (`spawn_implementer`, `spawn_lightweight`, `spawn_worktree`)
- [ ] `get_spawn_strategy()` dispatch function
- [ ] `run_scheduler()` reduced to ~30 lines (housekeeping → evaluate → spawn)
- [ ] No behaviour changes — same guards, same logic, just restructured

### Draft 9 (Agent Directories) — check for:
- [ ] `packages/client/agents/implementer/` directory with `agent.yaml`, `prompt.md`, `instructions.md`, `scripts/`
- [ ] `packages/client/agents/gatekeeper/` directory (same structure)
- [ ] `.octopoid/agents.yaml` simplified to fleet config format (`type:` references, not inline config)
- [ ] `octopoid init` scaffolds agent directories from templates
- [ ] Spawn strategies read from agent directory config, not hardcoded role names
- [ ] Scripts moved from `orchestrator/agent_scripts/` to agent directories

### Review method:
```bash
# Full diff of the refactor branch vs its base
git diff feature/client-server-architecture...agent/REFACTOR-01-de692452

# File-level summary
git diff --stat feature/client-server-architecture...agent/REFACTOR-01-de692452

# Commit log
git log --oneline feature/client-server-architecture..agent/REFACTOR-01-de692452
```

Read each changed file. Compare against the draft spec. Flag anything missing, wrong, or that diverges from the plan without good reason.

## Phase 3: Create PR and merge

1. Create PR: `agent/REFACTOR-01-de692452` → `feature/client-server-architecture`
2. Include review findings in PR description
3. Note any deviations from drafts and whether they're acceptable
4. Merge (no delete branch)

## Phase 4: Post-mortem review

Read `project-management/drafts/13-2026-02-15-project-branch-lessons.md` and turn the lessons into actionable fixes. Key issues:

1. **Project tasks don't push shared branch to origin** — the root cause of every "failed with 0 commits". Needs a code fix in the task completion flow.
2. **`get_project()` reads local YAML not API** — the TODO that caused project branch to be invisible. Needs SDK `projects.get()` method.
3. **Completed worktrees block shared branch** — implement detach-HEAD approach in `create_task_worktree`.
4. **GH issue monitor dedup broken** — separate issue, tracked in draft 14.
5. **No automated push/detach/approve for project tasks** — the monitor script shouldn't have been needed. The scheduler should handle this.

For each: decide whether to fix now or create a task.

## Phase 5: Clean up

- Kill the monitor script
- Reset orchestrator interval back to 60s (or whatever we decide is right)
- Clean up stale worktrees from the refactor tasks
- Update MEMORY.md with what we learned
- Re-enable GH issue monitor once dedup is fixed (or leave disabled)
