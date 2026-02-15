# refactor-06: Test scheduler refactor end-to-end

ROLE: implement
PRIORITY: P1
BRANCH: feature/client-server-architecture
CREATED: 2026-02-15T00:00:00Z
CREATED_BY: human
SKIP_PR: true
DEPENDS_ON: refactor-05

## Context

Tasks refactor-01 through refactor-05 restructured `run_scheduler()` into a pipeline architecture. This task verifies the refactor works correctly and adds unit tests for the new functions.

The scheduler refactor was purely structural -- no behaviour changes. All existing tests must pass, and new tests must cover the extracted functions.

Reference: `project-management/drafts/10-2026-02-15-scheduler-refactor.md`

## What to do

### 1. Run existing tests

```bash
pytest tests/
```

All existing tests must pass. If any fail, fix the refactored code (not the tests) -- the refactor should have been behaviour-preserving.

### 2. Run scheduler with --debug --once

```bash
python -m orchestrator.scheduler --debug --once
```

Verify in `.octopoid/runtime/logs/scheduler-*.log`:
- All housekeeping job names appear (or their failures are logged)
- Agent evaluation produces guard chain log messages like `"Agent <name>: blocked by <guard>: <reason>"`
- No Python errors or tracebacks

### 3. Add unit tests

Create `tests/test_scheduler_refactor.py` with tests for:

#### AgentContext

- Test creating an AgentContext with all fields
- Test that `claimed_task` defaults to `None`

#### Guard functions

Test each guard returns `(True/False, reason)` correctly:

- **test_guard_enabled**: paused agent returns `(False, "paused")`, enabled returns `(True, "")`
- **test_guard_not_running_idle**: agent not running returns `(True, "")`
- **test_guard_not_running_alive**: agent with running PID returns `(False, "still running ...")`
- **test_guard_not_running_crashed**: agent marked running but PID dead returns `(True, "")` and updates state
- **test_guard_interval_due**: overdue agent returns `(True, "")`
- **test_guard_interval_not_due**: non-overdue agent returns `(False, "not due yet")`
- **test_guard_backpressure_blocked**: blocked role returns `(False, "backpressure: ...")`
- **test_guard_backpressure_clear**: unblocked role returns `(True, "")`, clears blocked_reason
- **test_guard_pre_check_pass**: pre-check returns True -> `(True, "")`
- **test_guard_pre_check_fail**: pre-check returns False -> `(False, "pre-check: no work")`
- **test_guard_claim_task_non_claimable**: non-claimable role returns `(True, "")` without calling claim
- **test_guard_claim_task_success**: claimable role with available task returns `(True, "")`, sets ctx.claimed_task
- **test_guard_claim_task_none**: claimable role with no tasks returns `(False, "no task available")`

To test guards, create `AgentContext` instances with mock/test data. Use `unittest.mock.patch` to mock external calls:
- `is_process_running` for guard_not_running
- `check_backpressure_for_role` for guard_backpressure
- `run_pre_check` for guard_pre_check
- `claim_and_prepare_task` for guard_claim_task

Use `AgentState()` from `orchestrator.state_utils` to create test states. Use `tmp_path` for `state_path`.

#### evaluate_agent

- **test_evaluate_agent_all_pass**: all guards pass -> returns `True`
- **test_evaluate_agent_stops_at_first_fail**: first failing guard stops the chain, remaining guards not called
- **test_evaluate_agent_first_guard_fails**: `guard_enabled` fails -> returns `False` immediately

#### get_spawn_strategy

- **test_get_spawn_strategy_implementer**: implementer role with claimed_task -> `spawn_implementer`
- **test_get_spawn_strategy_lightweight**: lightweight config -> `spawn_lightweight`
- **test_get_spawn_strategy_worktree**: non-lightweight, non-implementer -> `spawn_worktree`

#### run_housekeeping

- **test_run_housekeeping_calls_all_jobs**: mock all jobs, verify each is called
- **test_run_housekeeping_continues_on_failure**: one job raises exception, others still called

### 4. Verify edge cases manually

Run the scheduler with specific configurations to verify:
- Paused system exits early (set `paused: true` in `.octopoid/agents.yaml`)
- Paused agent is skipped (set `paused: true` on an agent entry)
- Running agent is skipped (leave an agent process running)
- Backpressure blocks with reason (if achievable, otherwise trust unit tests)

Remember to restore config after testing.

## Key files

- `orchestrator/scheduler.py` -- the refactored code to test
- `tests/test_scheduler_refactor.py` -- new test file to create
- `tests/test_scheduler_branch.py` -- existing scheduler tests (verify still passing)
- `orchestrator/state_utils.py` -- AgentState used in test fixtures

## Acceptance criteria

- [ ] All existing tests pass (`pytest tests/`)
- [ ] New unit tests exist in `tests/test_scheduler_refactor.py`
- [ ] Tests cover: all 6 guard functions, evaluate_agent, get_spawn_strategy, run_housekeeping
- [ ] At least 20 test cases total
- [ ] Scheduler runs cleanly with `--debug --once` (no tracebacks in log)
- [ ] No regressions in scheduler functionality
