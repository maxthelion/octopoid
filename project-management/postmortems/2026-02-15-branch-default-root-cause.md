# Branch default mismatch — systemic root cause

**Affected:** All tasks created without explicit `branch` parameter
**Impact:** Agents get worktrees based on `main` instead of the configured main branch

## The chain of failures

1. **Config says** `repo.main_branch: feature/client-server-architecture`
2. **SDK defaults** `branch='main'` (hardcoded in client.py line 47)
3. **Server stores** `branch: 'main'` (from SDK default)
4. **Scheduler reads** `task.get("branch", get_main_branch())` — gets `'main'` (explicit value, fallback never triggers)
5. **Worktree created** from `main`, not `feature/client-server-architecture`
6. **Agent sees old code** — functions added on feature branch don't exist

## Why the config fallback doesn't work

The scheduler has correct fallback logic:
```python
base_branch = task.get("branch", get_main_branch())
```

But `task.get("branch")` returns `'main'` (explicitly stored), not `None`. The fallback to `get_main_branch()` (which reads the config) never triggers. The SDK fills in `'main'` even when the caller didn't specify a branch.

## Fix

**SDK (client.py):** Change `branch` default from `'main'` to `None`. Only include in payload when explicitly set:

```python
def create(
    self,
    id: str,
    file_path: str,
    branch: Optional[str] = None,  # was: str = 'main'
    ...
):
    data = {'id': id, 'file_path': file_path, 'queue': queue}
    if branch is not None:
        data['branch'] = branch
    ...
```

**Server (tasks.ts):** Keep `body.branch || 'main'` as-is — it handles the null case.

**Scheduler:** Already correct — `task.get("branch", get_main_branch())` will now trigger the config fallback when branch is null/unset.

## Result

- Tasks created without explicit `branch` → server stores `null` → scheduler reads config → uses `feature/client-server-architecture`
- Tasks created WITH explicit `branch` → works as before
- No breaking changes for callers who already specify branch
