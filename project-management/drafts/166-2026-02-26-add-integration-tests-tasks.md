# Add integration tests for tasks.py: create_task() content storage and claim cycle

**Author:** testing-analyst
**Captured:** 2026-02-26

## Gap

`octopoid/tasks.py` (659 lines) has **zero tests** — no unit tests, no integration tests. It contains the critical task lifecycle functions: `create_task()`, `claim_task()`, `submit_completion()`, `fail_task()`, and `approve_and_merge()`. This is the most important untested module in the codebase.

CLAUDE.md explicitly states: *"Always use `create_task()` from `orchestrator.tasks` to create tasks. Never bypass it with raw `sdk.tasks.create()` or `requests.post()` calls. `create_task()` stores task content on the server and handles branch defaulting via `get_base_branch()`. Bypassing it causes content to be missing from tasks, which makes agents fail."*

Yet `create_task()` itself has no test verifying that this invariant holds.

## Proposed Test

Add an integration test in `tests/integration/test_tasks_create_claim.py` using the `scoped_sdk` fixture (which patches `get_sdk()` to point at the local test server at localhost:9787).

Test scenario: **`create_task()` stores content and enables claim**

1. Call `create_task(title="...", role="implement", context="...", acceptance_criteria=["..."])` from `octopoid.tasks`
2. Assert the returned task ID is non-empty (8-char hex)
3. Use the `scoped_sdk` client to fetch the task directly: `sdk.tasks.get(task_id)` — assert `queue == "incoming"` and `content` is non-empty (not `None`)
4. Call `claim_task(role_filter="implement", agent_name="test-agent")` from `octopoid.tasks`
5. Assert the claimed task's ID matches the created task
6. Assert `sdk.tasks.get(task_id)["queue"] == "claimed"`

Second test: **`submit_completion()` moves task to provisional**

1. Create and claim a task (as above)
2. Call `submit_completion(task_id, commits_count=1, turns_used=3)`
3. Assert the returned result has `queue == "provisional"`
4. Assert `sdk.tasks.get(task_id)["queue"] == "provisional"`

Third test: **`fail_task()` moves task to failed**

1. Create and claim a task
2. Call `fail_task(task_id, reason="test failure", source="test")`
3. Assert `sdk.tasks.get(task_id)["queue"] == "failed"`

## Why This Matters

`create_task()` is the single authorised entry point for task creation — it constructs the content markdown, generates the ID, and stores it on the server. If this function regresses (e.g., content field is accidentally dropped, branch defaulting breaks, or the SDK call silently swallows an error), agents will claim tasks with no content and fail silently. This has happened before. Integration tests using `scoped_sdk` exercise the real API contract without hitting production, and would catch regressions immediately.

The `claim_task()` and `submit_completion()` functions drive the core task lifecycle that the entire orchestrator depends on — having no tests here is a significant gap given that these functions are called hundreds of times per day.

## Invariants

No new invariants. This draft proposes integration tests for existing required behaviour in `tasks.py`. The tests verify behaviour that is already required (`create_task()` stores content, `claim_task()` transitions queue) — they do not introduce new requirements.
