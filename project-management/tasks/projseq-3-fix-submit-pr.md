# Fix submit-pr direct merge: use SDK, remove --no-ff, drop CHANGELOG

## Context

The `submit-pr` agent script has a `merge_to_project_branch()` path for auto-accept projects. It works but has three issues that need fixing.

## What to change

**File:** `orchestrator/agent_scripts/submit-pr`

### Fix 1: Replace `_submit_to_server_no_pr()` with SDK

The current `_submit_to_server_no_pr()` function uses raw `urllib.request` to call the server. Replace it with the SDK which handles auth and error handling consistently.

Replace the entire `_submit_to_server_no_pr()` function body with:
```python
def _submit_to_server_no_pr() -> None:
    """Submit task without PR (for auto-accept projects)."""
    if not SERVER_URL or not TASK_ID:
        return
    try:
        sdk = get_sdk()
        sdk.tasks.submit(TASK_ID, commits_count=0, turns_used=0)
        print(f"Task {TASK_ID} submitted to server via SDK")
    except Exception as e:
        print(f"Warning: Failed to submit to server: {e}", file=sys.stderr)
```

### Fix 2: Remove `--no-ff` from merge

In `merge_to_project_branch()`, change the merge command from:
```python
["git", "merge", "--no-ff", task_branch, "-m", ...]
```
To a fast-forward merge:
```python
["git", "merge", "--ff-only", task_branch]
```

If fast-forward isn't possible (branches diverged), fall back to rebase then fast-forward. This keeps the project branch history linear.

### Fix 3: Revert CHANGELOG.md

The original PR added a CHANGELOG entry that was not requested in the task. Remove the CHANGELOG.md changes — revert that file to match the base branch.

## Do NOT change

- Do not modify scheduler.py, queue_utils.py, git_utils.py, or README.md
- Do not add new files (no VERIFICATION.md or similar)
- Only touch `orchestrator/agent_scripts/submit-pr` and revert `CHANGELOG.md`

## Acceptance criteria

- [ ] `_submit_to_server_no_pr()` uses SDK instead of urllib
- [ ] `merge_to_project_branch()` uses `--ff-only` instead of `--no-ff`
- [ ] CHANGELOG.md has no changes from this feature (reverted to base branch state)
- [ ] Existing tests pass
