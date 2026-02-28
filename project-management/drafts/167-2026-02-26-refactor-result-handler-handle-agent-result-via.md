# Refactor result_handler.handle_agent_result_via_flow: extract 4 responsibility helpers (CCN 25 → ~5)

**Author:** architecture-analyst
**Captured:** 2026-02-26

## Issue

`handle_agent_result_via_flow` in `octopoid/result_handler.py` has a cyclomatic complexity of 25 across 171 lines. It is the primary dispatch function called on **every agent completion** (via `check_and_update_finished_agents` in `scheduler.py`), yet it mixes five distinct concerns in a single function:

1. **Staleness detection** — checks if the task moved to a different queue since the agent claimed it
2. **Flow loading and transition resolution** — fetches the task, loads its flow, and resolves the applicable transition
3. **Agent failure handling** — handles `status == "failure"` by finding `on_fail` from the transition and rejecting
4. **Gatekeeper decision dispatch** — routes on `decision: approve/reject/unknown`
5. **Step execution with rebase/merge recovery** — runs steps, catches `RuntimeError`, classifies as recoverable vs non-recoverable, posts a rejection message, and requeues

This makes the function hard to unit-test (each path requires setting up the full task + flow + SDK context), hard to reason about in code review, and costly to extend (adding a new decision type means navigating all five concerns simultaneously).

## Current Code

```python
# The 5 concerns interleaved in one 171-line function:

def handle_agent_result_via_flow(task_id, agent_name, task_dir, expected_queue=None) -> bool:
    result = read_result_json(task_dir)
    sdk = queue_utils.get_sdk()
    task = sdk.tasks.get(task_id)

    # 1. Staleness detection
    if expected_queue and current_queue not in (expected_queue, "claimed"):
        return True

    # 2. Flow/transition resolution
    flow = load_flow(flow_name)
    transitions = flow.get_transitions_from(lookup_queue)
    transition = transitions[0]

    # 3. Agent failure
    if status == "failure":
        for condition in transition.conditions:
            if condition.type == "agent" and condition.on_fail:
                sdk.tasks.reject(...)
                return True
        sdk.tasks.reject(...)
        return True

    # 4. Decision dispatch
    if decision == "reject":
        reject_with_feedback(task, result, task_dir)
        return True
    if decision != "approve":
        return True

    # 5. Step execution + rebase/merge recovery (50+ lines nested inside here)
    if transition.runs:
        try:
            execute_steps(...)
        except RuntimeError as step_err:
            is_merge_fail = any(kw in err_msg for kw in (...))
            if not is_merge_fail:
                raise
            # ... post message, find on_fail, reject, return True
```

## Proposed Refactoring

Apply the **Decomposition into single-responsibility helpers** pattern (a form of Function Extraction / Command). Extract each concern into a private helper, leaving the top-level function as a thin coordinator:

```python
def handle_agent_result_via_flow(task_id, agent_name, task_dir, expected_queue=None) -> bool:
    """Top-level coordinator — delegates to single-responsibility helpers."""
    result = read_result_json(task_dir)
    sdk = queue_utils.get_sdk()

    # 1 + 2: Guard + resolve — returns (task, transition) or (None, None)
    task, transition, lookup_queue = _resolve_task_and_transition(
        sdk, task_id, expected_queue
    )
    if task is None:
        return True  # Stale or no transition — PID safe to remove

    # 3 + 4 + 5: Dispatch on result
    return _dispatch_result(
        task_id, agent_name, task, transition, result, task_dir, sdk
    )


def _resolve_task_and_transition(sdk, task_id, expected_queue):
    """Fetch task, check staleness, load flow, resolve transition.

    Returns (task, transition, lookup_queue) or (None, None, None) if the
    result should be discarded (task gone, queue mismatch, no transition).
    """
    ...  # ~30 lines, CCN ~6


def _dispatch_result(task_id, agent_name, task, transition, result, task_dir, sdk) -> bool:
    """Route to the appropriate handler based on result status/decision.

    CCN ~4: one branch per outcome (failure, reject, approve, unknown).
    """
    status = result.get("status")
    decision = result.get("decision")

    if status == "failure":
        return _handle_agent_failure(task_id, result, transition, sdk, agent_name)
    if decision == "reject":
        return _handle_gatekeeper_reject(task, result, task_dir, agent_name)
    if decision != "approve":
        return True  # Unknown decision — leave for human review
    return _handle_approve_and_run_steps(task_id, agent_name, task, transition, result, task_dir, sdk)


def _handle_agent_failure(task_id, result, transition, sdk, agent_name) -> bool:
    """Handle status='failure': find on_fail queue and reject back."""
    ...  # ~15 lines, CCN ~3


def _handle_gatekeeper_reject(task, result, task_dir, agent_name) -> bool:
    """Handle decision='reject': post feedback and reject task."""
    ...  # ~5 lines, CCN ~1


def _handle_approve_and_run_steps(task_id, agent_name, task, transition, result, task_dir, sdk) -> bool:
    """Execute transition steps; recover from rebase/merge failures."""
    ...  # ~45 lines, CCN ~6 (recoverable vs non-recoverable exception split)
```

## Why This Matters

- **Testability**: Each helper can be tested in isolation with mocked SDK and minimal fixtures. Currently, testing the rebase failure path requires constructing a full RuntimeError inside a mock of `execute_steps` alongside a loaded flow object.
- **Readability**: The top-level function becomes a 10-line coordinator — a new engineer can understand the dispatch logic without reading 170 lines.
- **Extensibility**: Adding a new outcome (e.g. `decision: escalate`) requires editing only `_dispatch_result`, not navigating through unrelated error recovery code.
- **Maintainability**: The rebase/merge recovery logic (currently buried inside a 50-line `if transition.runs:` block) becomes its own named function with a clear contract.

## Metrics

- **File:** `octopoid/result_handler.py`
- **Function:** `handle_agent_result_via_flow` (lines 330–500)
- **Current CCN:** 25
- **Current NLOC:** 106 (171 total lines including blank/comments)
- **Current parameter count:** 4
- **Estimated CCN after:** ~5 per helper function (top-level coordinator ≤4)
- **Call site:** `scheduler.py:check_and_update_finished_agents` — called on every agent completion


## Invariants

No new invariants — this is a pure refactoring to reduce cyclomatic complexity in `handle_agent_result_via_flow`. No behaviour changes.
