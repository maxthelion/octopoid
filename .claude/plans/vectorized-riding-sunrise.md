# Plan: Agent Notes System

## Summary

Add a notes system so implementer agents preserve learnings across attempts. Notes are stored in `.octopoid/runtime/shared/notes/TASK-{id}.md` and cleaned up when the task is accepted to done.

## Changes

### 1. `orchestrator/config.py` — Add notes dir helper

Add `get_notes_dir()` returning `.octopoid/runtime/shared/notes/`, creating it if needed:

```python
def get_notes_dir() -> Path:
    notes_dir = get_orchestrator_dir() / "shared" / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    return notes_dir
```

### 2. `orchestrator/queue_utils.py` — Notes read/write/cleanup functions

Add three functions:

```python
def get_task_notes(task_id: str) -> str | None:
    """Read notes for a task. Returns content or None."""

def save_task_notes(task_id: str, agent_name: str, stdout: str,
                    commits: int, turns: int) -> None:
    """Append a run summary to the notes file.
    Saves last ~3000 chars of stdout plus metadata header."""

def cleanup_task_notes(task_id: str) -> bool:
    """Delete notes file for a task. Returns True if file existed."""
```

`save_task_notes` appends each attempt as a section:
```markdown
## Attempt 2 — impl-agent-1 (2026-02-05T23:15:00)
Turns: 100 | Commits: 0

[last ~3000 chars of stdout]
```

### 3. `orchestrator/roles/implementer.py` — Inject & save notes

**Before invoking Claude** (after prompt building, ~line 81):
- Call `get_task_notes(task_id)`
- If notes exist, append a `## Previous Agent Notes` section to the prompt

**After Claude finishes** (~line 105, after commit counting):
- Call `save_task_notes(task_id, self.agent_name, stdout, commits_made, turns_used)`
- This runs on both success and failure paths, so notes are always saved

### 4. `orchestrator/queue_utils.py` — Cleanup on accept

In `accept_completion()` (~line 443, after `os.rename`):
- Call `cleanup_task_notes(task_id)` to delete notes when task is done

Also in `complete_task()` (~line 355, after `os.rename`):
- Call `cleanup_task_notes(task_id)` (for direct completion path)

### 5. Tests — `tests/test_agent_notes.py`

- `test_save_and_read_notes` — save notes, read them back
- `test_notes_append_multiple_attempts` — verify attempts accumulate
- `test_cleanup_deletes_notes` — verify cleanup removes file
- `test_cleanup_missing_file_noop` — cleanup on non-existent file returns False
- `test_accept_completion_cleans_notes` — integration: accept triggers cleanup
- `test_notes_truncate_long_stdout` — verify stdout is truncated to ~3000 chars

## Key Design Decisions

- **Last ~3000 chars of stdout**: Most useful info (summary, final state) is at the end. Full stdout is too large (can be 100KB+).
- **Append, don't overwrite**: Each attempt adds a section, building up knowledge across retries.
- **Cleanup on done, not provisional**: If a task gets rejected from provisional back to incoming, notes are still available for the retry.
- **Notes survive recycling**: If a task is recycled to re-breakdown, the notes from failed implementation attempts stay available. The breakdown agent could optionally read them too.

## Files Modified

1. `orchestrator/config.py` — add `get_notes_dir()`
2. `orchestrator/queue_utils.py` — add 3 notes functions + cleanup calls in `accept_completion()` and `complete_task()`
3. `orchestrator/roles/implementer.py` — inject notes into prompt, save notes after run
4. `tests/test_agent_notes.py` — new test file

## Verification

```bash
cd orchestrator && ./venv/bin/python -m pytest tests/ -v
```

Then `pip install -e .` and restart scheduler.
