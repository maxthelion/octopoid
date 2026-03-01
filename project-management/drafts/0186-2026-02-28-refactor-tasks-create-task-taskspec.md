---
**Processed:** 2026-02-28
**Mode:** human-guided
**Actions taken:**
- Enqueued as TASK-326df326 (P2, blocked by 676ad0ae — test coverage for tasks.py)
**Outstanding items:** Task blocked on test coverage landing first
---

# Refactor tasks.create_task: extract TaskSpec dataclass to eliminate 13-param signature (CCN 26 → ~4)

**Author:** architecture-analyst
**Captured:** 2026-02-28

## Issue

`create_task()` in `octopoid/tasks.py` (line 615) is the single mandatory entry point for all task creation in the system (CLAUDE.md explicitly mandates its use). It has **13 parameters** and **CCN 26** — the worst combination of parameter count and complexity in the codebase. This means:

- Every call site must use keyword args to be readable, or silently pass arguments in the wrong positional order
- The function body has 5–6 distinct logical blocks crammed together: branch resolution (server call), criteria normalization, markdown template building, SDK registration, and logger initialization
- Adding a new task attribute requires changing the signature, the body, and every call site simultaneously — there's no single place to extend

The 13 parameters are the root cause of the high CCN: each optional parameter generates at least one conditional (`if project_id`, `if blocked_by`, `if checks`, etc.), and they all live inside the same function body.

## Current Code

```python
def create_task(
    title: str,
    role: str,
    context: str,
    acceptance_criteria: list[str] | str,
    priority: str = "P1",
    branch: str | None = None,
    flow: str | None = None,
    created_by: str = "human",
    blocked_by: str | None = None,
    project_id: str | None = None,
    queue: str = "incoming",
    checks: list[str] | None = None,
    breakdown_depth: int = 0,
) -> str:
    # Branch resolution (talks to server if project_id set)
    if not branch:
        if project_id:
            try:
                sdk = get_sdk()
                project = sdk.projects.get(project_id)
                if project and project.get("branch"):
                    branch = project["branch"]
                ...
            except Exception as e: ...
        if not branch:
            branch = get_base_branch()

    # Criteria normalization
    if isinstance(acceptance_criteria, str):
        acceptance_criteria = [...]
    criteria_lines = []
    for c in acceptance_criteria:
        stripped = c.strip()
        if stripped.startswith("- [ ]") or stripped.startswith("- [x]"):
            criteria_lines.append(stripped)
        else:
            criteria_lines.append(f"- [ ] {stripped}")

    # Conditional metadata lines
    blocked_by_line = f"BLOCKED_BY: {blocked_by}\n" if blocked_by else ""
    project_line = f"PROJECT: {project_id}\n" if project_id else ""
    checks_line = f"CHECKS: {','.join(checks)}\n" if checks else ""
    breakdown_depth_line = f"BREAKDOWN_DEPTH: {breakdown_depth}\n" if breakdown_depth > 0 else ""

    # Template build + SDK registration + logger init all in same function
    content = f"""# [TASK-{task_id}] {title}\n..."""
    sdk.tasks.create(...)
    logger = get_task_logger(task_id)
    logger.log_created(...)
    return task_id
```

## Proposed Refactoring

Apply the **Data Transfer Object + extraction** pattern:

1. Introduce a `TaskSpec` dataclass that groups all 13 parameters into a single typed object
2. Extract `_resolve_branch(spec) -> str` — branch resolution logic (server call for project branch)
3. Extract `_normalize_criteria(criteria) -> list[str]` — acceptance criteria normalization
4. Extract `_build_task_content(spec, task_id, branch, criteria) -> str` — markdown template
5. Refactor `create_task(spec: TaskSpec) -> str` to orchestrate the above — drops to CCN ~4

```python
@dataclass
class TaskSpec:
    """All inputs needed to create a task."""
    title: str
    role: str
    context: str
    acceptance_criteria: list[str] | str
    priority: str = "P1"
    branch: str | None = None
    flow: str | None = None
    created_by: str = "human"
    blocked_by: str | None = None
    project_id: str | None = None
    queue: str = "incoming"
    checks: list[str] | None = None
    breakdown_depth: int = 0


def _resolve_branch(spec: TaskSpec) -> str:
    """Resolve branch: use spec.branch, fetch from project, or fall back to base."""
    if spec.branch:
        return spec.branch
    if spec.project_id:
        try:
            project = get_sdk().projects.get(spec.project_id)
            if project and project.get("branch"):
                return project["branch"]
        except Exception as e:
            print(f"Warning: Failed to fetch project {spec.project_id} for branch: {e}")
    return get_base_branch()


def _normalize_criteria(criteria: list[str] | str) -> list[str]:
    """Return criteria as a list of '- [ ] ...' checkbox lines."""
    if isinstance(criteria, str):
        criteria = [line for line in criteria.splitlines() if line.strip()]
    result = []
    for c in criteria:
        stripped = c.strip()
        result.append(stripped if stripped.startswith(("- [ ]", "- [x]")) else f"- [ ] {stripped}")
    return result


def _build_task_content(spec: TaskSpec, task_id: str, branch: str, criteria: list[str]) -> str:
    """Build the markdown content for a task file."""
    ...  # template build — pure string formatting, CCN ~1


def create_task(spec: TaskSpec) -> str:
    """Create a new task and register it on the server."""
    branch = _resolve_branch(spec)
    task_id = uuid4().hex[:8]
    criteria = _normalize_criteria(spec.acceptance_criteria)
    content = _build_task_content(spec, task_id, branch, criteria)
    _register_task_on_server(spec, task_id, branch, content)
    _init_task_logger(spec, task_id)
    return task_id
```

Call sites update from:
```python
task_id = create_task(
    title="...", role="implement", context="...",
    acceptance_criteria=[...], priority="P2",
    project_id=proj_id, checks=["ci"],
)
```
to:
```python
task_id = create_task(TaskSpec(
    title="...", role="implement", context="...",
    acceptance_criteria=[...], priority="P2",
    project_id=proj_id, checks=["ci"],
))
```
The call sites in `jobs.py:434`, `projects.py:195`, `projects.py:218` all use keyword arguments already, so migration is mechanical.

## Why This Matters

**Testability:** Each extracted helper is now independently testable with simple inputs. Currently, testing branch resolution requires mocking the full SDK and threading a project_id through 13 parameters. With `_resolve_branch(spec)`, a test only needs a `TaskSpec` with `project_id` set.

**Extensibility:** Adding a new task attribute requires only: (a) add a field to `TaskSpec`, (b) update `_build_task_content`. Today it requires touching the 13-param signature, the body, and every call site.

**Readability:** `create_task(spec)` with 5 lines of orchestration is immediately clear. The current function hides its structure behind 85 lines of interspersed logic.

**Error surface:** 13 positional parameters means a wrong argument order is silently accepted. A dataclass enforces named construction.

## Metrics

- File: `octopoid/tasks.py`
- Function: `create_task`
- Current CCN: 26 / Lines: 85 / Parameters: 13
- Estimated CCN after: `create_task` ~4, `_resolve_branch` ~5, `_normalize_criteria` ~3, `_build_task_content` ~2
