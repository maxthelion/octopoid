---
**Processed:** 2026-02-25
**Mode:** human-guided
**Actions taken:**
- Phase 1.1 (changelog): TASK-3199779f (done) — update_changelog step, agents write changes.md
- Phase 1.2 (inject terminal steps): TASK-b8c2bf6c (done) — _inject_terminal_steps() in flow.py
- Phase 1.3 (rebase at submission): TASK-f80172c6 + TASK-47766b7e (done) — rebase_on_base in claimed→provisional
- Rebase rejection message: fixed in result_handler.py (commit b9a4330) — tells agents to resolve conflicts, not re-implement
- Project changelog aggregation: re-enqueued (original TASK-b8aae0f9 failed and was purged)
- Added CLAUDE.md rules: agents must not write CHANGELOG.md directly; terminal steps are auto-injected
**Outstanding items:** Project changelog aggregation (re-enqueued). Phases 2 and 3 parked as future work in this draft.
---

# Git robustness: proactive rebase, conflict resolution, task serialization

**Status:** Complete
**Captured:** 2026-02-25
**Parent:** Draft #159 (Option D)

## Problem

Git operations are the #1 source of task failures and manual intervention. The typical failure:

1. Agent works in a worktree for 10-30 minutes
2. Another task merges to main during that time
3. At merge time, `rebase_on_base` hits conflicts
4. Task gets rejected back to incoming
5. A new agent re-implements the entire task from scratch on a fresh base
6. This costs another 10-30 minutes of LLM turns — and main may have moved again

Step 5 is the expensive waste. The original work was almost certainly fine; it just needed rebasing. Re-implementing is the wrong response to a mechanical problem.

## Current state

Git operations are spread across multiple files with two parallel code paths (flow steps vs legacy hooks):

| Operation | Where | Error handling |
|---|---|---|
| Worktree creation | `git_utils.py:create_task_worktree` | Detached HEAD, rebased to base branch |
| Rebase (flow path) | `steps.py:rebase_on_base` | Aborts rebase, raises RuntimeError → rejected to incoming |
| Rebase (legacy hooks) | `hook_manager.py:_run_rebase_on_base` | Aborts rebase, returns failed evidence → rejected to incoming |
| Push | `steps.py:push_branch` | CalledProcessError propagates |
| Merge | `steps.py:merge_pr` → `queue_utils.approve_and_merge` | RuntimeError propagates |
| Worktree cleanup | `scheduler.py:sweep_stale_resources` | Force remove, prune, delete remote branch |

**What works:** Worktree creation is solid (detached HEAD, correct base). Rebase failure handling now rejects to incoming instead of dumping to failed.

**What doesn't work:** After rejection, the worktree is abandoned and the task is re-implemented from scratch. No proactive rebasing. No conflict tracking. No serialization of conflicting tasks.

## Proposed improvements

### 1. Proactive rebase during work

**What:** The scheduler periodically rebases worktrees of in-progress tasks onto the latest base branch.

**When:** During each scheduler tick, for tasks in `claimed` queue whose worktree has fallen behind `origin/<base_branch>`.

**How:**
```
For each claimed task with a worktree:
  1. git fetch origin (in worktree)
  2. git rev-list --count HEAD..origin/<base_branch>
  3. If behind > 0:
     a. git rebase origin/<base_branch>
     b. If conflict: git rebase --abort, log it, move on (agent is still working)
     c. If success: worktree is now up to date
```

**Why:** Catches conflicts early. If a rebase fails during work, we know before the agent finishes — we can post a warning to the task thread rather than discovering at merge time.

**Risk:** Rebasing while an agent is actively editing files could cause confusion. Mitigate by only rebasing when the agent process is idle (between turns) or by checking file modification times.

**Simpler alternative:** Only rebase at task submission time (claimed → provisional), before any review gates. This avoids the mid-work risk and still catches conflicts before the expensive review stage.

### 2. Conflict resolver agent

**What:** When rebase fails, instead of re-implementing the whole task, spawn a lightweight agent whose only job is to resolve the merge conflicts in the existing worktree.

**How:**
```
On rebase failure:
  1. Don't reject to incoming
  2. Leave worktree in conflicted state (don't abort)
  3. Spawn a "conflict-resolver" agent with:
     - The conflicted worktree
     - The original task description
     - The conflict diff
  4. Agent resolves conflicts, continues rebase
  5. If resolved: push and continue to merge
  6. If unresolvable: then reject to incoming with specific feedback
```

**Why:** A conflict resolution is typically a 2-5 minute job for an LLM. A full re-implementation is 10-30 minutes. This is 5-10x cheaper.

**Complexity:** Medium. Needs a new agent type, a new task state or flow transition for "needs conflict resolution", and the scheduler needs to know how to spawn it.

**Simpler alternative:** Before spawning a dedicated agent, try mechanical resolution first. Many conflicts are trivial (whitespace, import ordering, adjacent-line edits). Run `git rebase` and check if the conflicts can be auto-resolved with a merge strategy (`-X theirs` for non-overlapping, or a custom merge driver). Only escalate to an agent for true semantic conflicts.

### 3. File-level conflict tracking

**What:** Track which files each task modifies. Use this to predict and prevent conflicts before they happen.

**How:**
- When a task's worktree has commits, record the changed file paths on the task (server-side field or metadata)
- Before claiming a new task, check if its likely-modified files overlap with any in-progress task
- If overlap detected: either delay the claim, or flag it for the human

**Why:** Prevention is better than cure. If we know two tasks will touch `scheduler.py`, running them sequentially avoids the conflict entirely.

**Complexity:** High. We don't know which files a task will modify before it starts. Could use heuristics (task title, role, past patterns) or wait until the first commit to record actuals. The value is uncertain without data on how often file-overlap actually causes conflicts.

**Simpler alternative:** Just track and log conflict frequency. Before building prevention, understand the problem: which files conflict most? How often? Are the same file pairs recurring? This data tells us whether serialization is worth the throughput cost.

### 4. Inject required terminal steps

**What:** Stop relying on each flow YAML to include `rebase_on_base` and `merge_pr`. Inject them automatically on any transition to `done`.

**How:** In `flow.py` when loading a flow, if the terminal transition (anything → done) doesn't already include `rebase_on_base` and `merge_pr` in its runs, append them.

**Why:** This is the minimal fix from draft #159 that prevents the `fast.yaml` class of bugs. Every flow that ends in done needs these steps — making them implicit removes the footgun.

**Complexity:** Low. A few lines in flow loading.

### 5. Remove conflict-prone files from agent responsibility

**What:** Stop agents writing to files that every task touches, especially `CHANGELOG.md`.

**Evidence:** CHANGELOG.md is modified in 13 of the last 50 commits — more than double any other file. Every agent appends to the top of the same file, in the same region. It's the #1 predictable source of rebase conflicts.

**How:** The agent writes its changelog entry to a task-local file (e.g. `.octopoid/runtime/tasks/<id>/changes.md`) that isn't committed to the branch. After merge, the scheduler reads that file, prepends the entry to `CHANGELOG.md`, and commits directly to main. The agent still writes the description — it's best placed to know what changed — but it never touches the shared file.

```
Agent (during work):
  Write changes.md in task runtime dir (not committed)

Scheduler (post-merge step):
  1. Read .octopoid/runtime/tasks/<id>/changes.md
  2. Prepend to CHANGELOG.md
  3. Commit and push to main
```

No conflicts possible — the file the agent writes is local and isolated, and the changelog commit happens after merge (serialized by definition).

**Other candidates:** `scheduler.py` (6 modifications in 50 commits) is the second most-modified file, but those are genuine feature changes, not boilerplate. No other file has the same "every task touches the same region" pattern as CHANGELOG.md.

**Complexity:** Low. Remove changelog instructions from agent prompts, add a post-merge step.

**Gap: Projects.** The `update_changelog` step is not in the project flow's `provisional → done` runs. Even if added, it wouldn't work: the step looks for `changes.md` in the project's task_dir, but each child task writes its own `changes.md` in its own runtime dir. Projects need an aggregation step — collect all child `changes.md` files into the project's `changes.md` before running `update_changelog`. The simple fix: add an `aggregate_child_changes` step to the project flow's terminal transition that concatenates child changes into the project's runtime dir, then `update_changelog` works as-is.

**Gap: Rebase rejection message.** When rebase fails, the rejection message says "re-implemented on a fresh base" but the worktree persists with all the agent's commits intact. The agent has no guidance to rebase rather than re-implement, and will likely redo all the work from scratch. The message should instruct the agent to rebase, or better: the scheduler should attempt the rebase itself before handing back to an agent.

## Decision

Rebase more frequently + move changelog. A shared develop branch adds complexity without proportional benefit — more frequent rebasing against main achieves the same early conflict detection with no new branch model.

## Plan

### Phase 1: Quick wins (do now)

**1. Move changelog out of agent responsibility**
- Remove changelog instructions from agent prompts (global-instructions.md, task templates)
- Agent writes a `changes.md` in task runtime dir (not committed)
- Add `update_changelog` as a post-merge step in the flow: reads `changes.md`, prepends to CHANGELOG.md, commits to main
- Eliminates the #1 predictable conflict source

**2. Inject required terminal steps**
- In `flow.py`, when loading a flow, auto-append `rebase_on_base` and `merge_pr` to any transition targeting `done` if not already present
- Prevents the fast.yaml class of bugs

**3. Rebase at submission time**
- Add `rebase_on_base` to the `claimed → provisional` transition runs (currently only on `provisional → done`)
- Catches conflicts before wasting gatekeeper turns on work that can't merge
- If rebase fails at submission: reject back to incoming immediately, don't bother with review

### Phase 2: Instrument and learn

**4. Conflict tracking**
- Log every rebase attempt: task ID, success/fail, conflicting files, time since base branch diverged
- After a few weeks, review the data to understand: how often, which files, how stale

### Phase 3: Based on data

**5. Mechanical conflict resolution** — try `-X theirs` or custom merge strategies for trivial conflicts before rejecting
**6. Conflict resolver agent** — for real semantic conflicts, spawn a cheap agent to resolve rather than re-implement
**7. File-level serialization** — if data shows same-file overlap is the driver, delay claims when conflicts are predictable

## Open questions

- What's the right format for `changes.md`? Free text, or structured (title, description, breaking changes)?
- Should `update_changelog` be a flow step or a scheduler post-merge hook?
- For rebase at submission: should it be a step in `claimed → provisional` runs, or a guard condition?
