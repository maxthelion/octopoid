# Codebase Size Audit and Refactoring Purge

**Status:** Idea
**Captured:** 2026-02-16

## Raw

> what are the largest files in the project post-refactor? create a draft with that table to suggest a refactoring purge

## The Problem

After the scheduler refactor and legacy cleanup, the orchestrator is still 13,031 lines of Python. A handful of files account for most of the weight. Several of these are likely carrying dead code from earlier architectural phases (v1 task model, proposal model, file-based queues) that were never cleaned up.

## Largest Files (post-refactor)

| File | Lines | What it does | Suspicion |
|------|-------|-------------|-----------|
| `orchestrator/queue_utils.py` | 2,711 | SDK wrappers, task lifecycle, queue operations | **HIGH** — accumulated everything. Likely has dead v1 helpers, redundant wrappers around SDK calls, functions that should live elsewhere |
| `octopoid-dash.py` | 1,785 | Terminal dashboard | Medium — standalone script, but could have dead views for removed features |
| `orchestrator/scheduler.py` | 1,623 | Scheduler pipeline | Medium — just refactored, but still large. `prepare_task_directory` alone is probably 200+ lines |
| `orchestrator/git_utils.py` | 965 | Git operations (worktrees, branches, etc) | Low — mostly essential |
| `scripts/octopoid-status.py` | 897 | Status reporting script | Medium — standalone, may overlap with dashboard |
| `orchestrator/approve_orch.py` | 706 | Approval orchestration | Medium — may have v1 approval logic |
| `orchestrator/reports.py` | 652 | Reporting | Unknown |
| `orchestrator/config.py` | 632 | Config loading, agent resolution | Low — recently cleaned |
| `orchestrator/pr_utils.py` | 567 | PR operations | Medium — may have dead gatekeeper integration |
| `orchestrator/proposal_utils.py` | 488 | Proposal system | **HIGH** — the proposal model may be entirely dead. We use the task model now |
| `orchestrator/roles/base.py` | 440 | Base class for role modules | **HIGH** — only consumer is `github_issue_monitor.py`. 440 lines of base class for one subclass is excessive |

### Test files

| File | Lines | Tests for |
|------|-------|-----------|
| `tests/test_queue_utils.py` | ~1,100 | queue_utils.py |
| `tests/test_dashboard.py` | ~1,000 | dashboard |
| `tests/test_reports.py` | ~800 | reports |
| `tests/test_hooks.py` | ~750 | hooks |
| `tests/test_git_utils.py` | ~700 | git_utils |
| `tests/test_scheduler_refactor.py` | ~700 | New refactor tests |
| `tests/test_init.py` | ~450 | init command |
| `tests/test_queue_diagnostics.py` | ~400 | queue diagnostics |

## Candidates for Purge

### 1. `proposal_utils.py` (488 lines) — likely entirely dead

We switched from the proposal model to the task model. If nothing imports from `proposal_utils`, delete the whole file and its tests.

**Check:** `grep -rn "proposal_utils\|from.*proposal" orchestrator/ --include="*.py" | grep -v __pycache__`

### 2. `roles/base.py` (440 lines) — over-engineered for one consumer

`github_issue_monitor.py` is the only subclass. The base class has 440 lines of scaffolding (model selection, token counting, conversation management, etc) for a monitor that just polls GitHub issues. Options:
- Inline what github_issue_monitor actually uses and delete base.py
- Or accept it as tech debt until we rethink lightweight agents

### 3. `queue_utils.py` (2,711 lines) — needs a split

This is the god module. Everything touches it. Likely contains:
- Dead v1 functions (file-based queue operations)
- Functions that are thin wrappers around SDK calls (could be replaced by direct SDK use)
- Functions that belong in other modules (git operations mixed with queue operations)

Should be split into focused modules. But this is a big job — needs its own project.

### 4. `approve_orch.py` (706 lines) — check for dead paths

May have approval logic for the proposal model that's no longer used.

### 5. `octopoid-dash.py` + `octopoid-status.py` (2,682 lines combined)

Two overlapping tools for system visibility. Could potentially be merged or one deprecated.

### 6. `scheduler.py` (1,623 lines) — further trimming

Still large. `prepare_task_directory()` is probably 150+ lines and could be extracted. The various helper functions at the bottom may include dead code.

## Approach

This should be a **phased purge**, not a single task:

1. **Phase 1: Dead module detection** — Automated scan for modules/functions with zero callers. Quick wins: delete entirely dead files.
2. **Phase 2: queue_utils split** — Break the god module into focused modules. This is the highest-impact structural change.
3. **Phase 3: Shrink remaining large files** — Extract, inline, or delete as appropriate.

## Open Questions

- Is the proposal system entirely dead, or is it still used somewhere?
- Is `octopoid-status.py` redundant with the dashboard?
- What's the right size target? 8,000 lines for orchestrator/ would mean cutting ~40%.
- Should we set a per-file limit (e.g. 500 lines) as a project convention?
