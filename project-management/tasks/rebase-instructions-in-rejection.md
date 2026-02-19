# Add explicit rebase instructions to gatekeeper rejection

ROLE: implement
PRIORITY: P1

## Problem

When the gatekeeper rejects a task for merge conflicts, the rewritten task file just says "PR #N has merge conflicts" and "Address all the issues listed above." The implementer agent that picks up the requeued task doesn't get actionable rebase instructions — no target branch, no git commands. This causes tasks to loop through reject cycles without progress (e.g. TASK-e37bc845 was rejected twice and is now stuck).

## Root cause

`rewrite_task_file()` in `orchestrator/roles/sanity_check_gatekeeper.py:740-799` uses a generic template for all rejections. The "What to Fix" section is always the same boilerplate regardless of whether the issue is merge conflicts, failing tests, or code quality.

## Approach

Modify `rewrite_task_file()` to detect merge-conflict rejections and include specific rebase instructions.

### 1. Detect conflict rejections

In `rewrite_task_file()`, check if any of the rejection details mention merge conflicts (e.g. the `check_pr` check returned "has merge conflicts").

### 2. Build rebase-specific "What to Fix" section

When the rejection is for merge conflicts, replace the generic "Address all the issues" text with explicit instructions:

```markdown
## What to Fix

This task was rejected because the PR has merge conflicts. Rebase onto the target branch:

```bash
git fetch origin
git rebase origin/<base_branch>
# Resolve any conflicts, then:
git add <resolved files>
GIT_EDITOR=true git rebase --continue
git push origin HEAD --force-with-lease
```

After rebasing, re-run tests to make sure nothing broke, then resubmit.
```

The base branch should come from `self.task.get("branch", "main")`.

### 3. Keep generic template for other rejections

Non-conflict rejections (test failures, code quality issues) should continue using the existing generic template.

## Files to modify

- `orchestrator/roles/sanity_check_gatekeeper.py` — `rewrite_task_file()` method (lines 740-799)

## Acceptance criteria

- [ ] When gatekeeper rejects for merge conflicts, the rewritten task file includes explicit `git rebase` commands with the correct base branch
- [ ] Non-conflict rejections still use the generic template
- [ ] Existing gatekeeper tests still pass
