# Add integration tests for jobs.py: run_due_jobs dispatch cycle with real server

**Author:** testing-analyst
**Captured:** 2026-02-28

## Gap

`octopoid/jobs.py` contains `run_due_jobs()` — the core job dispatch loop that runs all scheduler background jobs (lease expiry checks, agent monitoring, queue health, GitHub issue polling, etc.). This function is 572 lines and handles the entire periodic job system. Despite its criticality, it is only ever **patched away** in tests (`patch("octopoid.jobs.run_due_jobs")`), never actually invoked with a real server.

The unit tests in `tests/test_scheduler_poll.py` cover `is_job_due` and `record_job_run` in isolation, but they never execute the dispatch path:
- YAML loading → job classification → `_run_job` → registered job function → real server state change

If the dispatch logic breaks (e.g., wrong interval classification, broken YAML parsing, wrong `group` field handling, or `record_job_run` not being called), all background jobs silently stop. Tasks get stuck in `claimed` forever, finished agents are never processed, and queue health is never checked.

## Proposed Test

Add an integration test in `tests/integration/test_jobs_dispatch.py` using the `scoped_sdk` fixture (real server on port 9787).

**Scenario: expired lease re-queued via run_due_jobs dispatch**

1. Create a task via `scoped_sdk` and claim it (creating an active lease)
2. Build a `scheduler_state` dict where `check_and_requeue_expired_leases` is overdue (`last_run = epoch`)
3. Patch `datetime.now()` in `octopoid.scheduler` to return a far-future time (so the lease appears expired)
4. Call `run_due_jobs(scheduler_state)` — the real dispatch path
5. Assert the task queue is now `incoming` (the job ran, the lease was detected as expired, and the task was re-queued)
6. Assert `scheduler_state["jobs"]["check_and_requeue_expired_leases"]` was updated (job was recorded as run)

This is distinct from the existing `test_lease_recovery.py` tests which call `check_and_requeue_expired_leases()` directly, bypassing `run_due_jobs` entirely.

**Fixture to use:** `scoped_sdk` from `tests/integration/conftest.py` (real server, port 9787)

**File to create:** `tests/integration/test_jobs_dispatch.py`

## Why This Matters

`run_due_jobs` is the entry point for the entire background job system. All critical maintenance tasks (requeue expired leases, monitor agent PIDs, check queue health, register orchestrator, check project completion) flow through it. If the dispatch loop breaks silently — wrong YAML field name, changed `is_job_due` semantics, missing `record_job_run` call — the orchestrator appears healthy but all background maintenance stops. Tasks accumulate in `claimed`, agents pile up, and the queue silently stalls. This gap is compounded by the fact that the function is always mocked in tests, meaning a real regression in the dispatch logic would not be caught.
