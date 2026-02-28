# SDK claim() should return explicit result, not None

**Captured:** 2026-02-25

## Raw

> It seems like returning None is less than ideal. Shouldn't it be fixed?

## Problem

`TasksAPI.claim()` silently swallows 404 and 429 HTTP errors and returns `None`:

```python
try:
    return self.client._request('POST', '/api/v1/tasks/claim', json=data)
except requests.HTTPError as e:
    if e.response.status_code in (404, 429):
        return None
    raise
```

The caller has to check `if task is None` and guess why — was the queue empty (404)? Was the max_claimed limit hit (429)? Was there a network error that happened to be swallowed? The `None` return type conflates multiple distinct outcomes into one signal.

## Why this matters

- The scheduler's main loop calls `claim()` thousands of times per day. Misinterpreting the result leads to wrong spawn decisions.
- A refactor that accidentally changes which status codes are swallowed would crash the scheduler in idle state (the normal case).
- 404 (empty queue) and 429 (at capacity) require different responses — one means "wait for work", the other means "wait for agents to finish". Currently both look the same to the caller.

## Possible approaches

**A) Return a typed result object:**
```python
@dataclass
class ClaimResult:
    task: dict | None
    reason: str  # "claimed", "empty_queue", "at_capacity"
```

**B) Raise specific exceptions:**
```python
class QueueEmpty(Exception): pass
class AtCapacity(Exception): pass
# claim() raises these instead of returning None
```

**C) Return (task, status) tuple:**
```python
task, status = sdk.tasks.claim(...)
# status is "claimed", "empty", "throttled"
```

## Context

Came up while processing draft #92 (SDK claim integration tests). The draft documented the None-on-404/429 contract as load-bearing, which prompted the question: shouldn't this be more explicit?

## Invariants

- `claim-returns-typed-result`: `tasks.claim()` returns an explicit typed result that distinguishes between: task claimed successfully, queue empty (no work available), and at-capacity (max_claimed limit reached). Callers can determine the specific reason without interpreting `None` return values.

## Open Questions

- Which approach (A/B/C) fits best with the existing SDK style?
- Are there other SDK methods with similar silent error swallowing?
- Should this be fixed before or after the claim() integration tests land?
