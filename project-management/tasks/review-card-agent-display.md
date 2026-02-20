# Show reviewing agent and claim duration on In Review task cards

Show the reviewing agent and how long ago it was claimed on task cards in the "In Review" column. Make this consistent with the "In Progress" column.

## What needs to change

### 1. `orchestrator/reports.py` — `_format_task()`
Add `claimed_at` to the formatted task dict:
```python
"claimed_at": task.get("claimed_at"),
```

### 2. `packages/dashboard/tabs/work.py` — WorkTab.compose()
Pass `show_progress=True` and `agent_map` to the "IN REVIEW" WorkColumn:
```python
yield WorkColumn(
    "IN REVIEW",
    in_review,
    show_progress=True,
    agent_map=agent_map,
    classes="kanban-column",
    id="col-in-review",
)
```

### 3. `packages/dashboard/widgets/task_card.py` — TaskCard.compose()
Add a "claimed X ago" label below the agent name when `claimed_at` is present. Use a simple time-ago format (e.g. "5m ago", "2h 10m ago").

Add a `_time_ago(iso_str)` helper that converts an ISO timestamp to a human-readable relative time.

Show this for both In Progress and In Review cards (anywhere show_progress=True and agent is set).

## Acceptance criteria
- In Review cards show the reviewing agent name (e.g. "sanity-check-ga")
- In Review cards show time since claimed (e.g. "5m ago")
- In Progress cards also show time since claimed
- Both columns use the same StatusBadge for agent status
