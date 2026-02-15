# Fix check_project_completion to use API instead of local files

## Context

`check_project_completion()` in `orchestrator/scheduler.py` has two problems:

1. It updates project status via `queue_utils._write_project_file(project)` which writes to a local YAML file. We're API-only in v2.0 — it should use the SDK to update the server.

2. It fetches ALL tasks via `sdk.tasks.list()` then filters in Python for the project's tasks. This is wasteful and won't scale. Use the project tasks endpoint instead.

## What to change

**File:** `orchestrator/scheduler.py` — `check_project_completion()` function (around line 1486)

### Fix 1: Use API to update project status

Replace:
```python
project["status"] = "review"
queue_utils._write_project_file(project)
```

With:
```python
sdk = queue_utils.get_sdk()
sdk._request("PATCH", f"/api/v1/projects/{project_id}", json={"status": "review"})
```

### Fix 2: Use project tasks endpoint

Replace:
```python
sdk = queue_utils.get_sdk()
all_tasks = sdk.tasks.list()
project_tasks = [t for t in all_tasks if t.get("project_id") == project_id]
```

With:
```python
sdk = queue_utils.get_sdk()
project_tasks = sdk._request("GET", f"/api/v1/projects/{project_id}/tasks")
```

### Fix 3: Use SDK for project listing too

Replace:
```python
projects = queue_utils.list_projects(status="active")
```

With:
```python
sdk = queue_utils.get_sdk()
projects = sdk._request("GET", "/api/v1/projects", params={"status": "active"})
```

## Do NOT change

- Do not modify any other functions in scheduler.py
- Do not modify README, CHANGELOG, or any other files
- Do not add new functions — just fix the existing one

## Acceptance criteria

- [ ] `check_project_completion()` uses `sdk._request("GET", f"/api/v1/projects/{project_id}/tasks")` to get project tasks
- [ ] `check_project_completion()` uses `sdk._request("PATCH", ...)` to update project status (not `_write_project_file`)
- [ ] `check_project_completion()` uses SDK to list projects (not `queue_utils.list_projects`)
- [ ] Existing tests pass
