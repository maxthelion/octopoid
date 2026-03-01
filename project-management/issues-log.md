# Issues Log

Known symptoms and their root causes. **Consult this first when diagnosing a problem** — many issues recur.

## Scheduler not processing tasks

| Symptom | Likely cause | See |
|---|---|---|
| "Last tick: Xh ago" in queue-status | Scheduler crashed or launchd throttled | [2026-02-21 postmortem](postmortems/2026-02-21-scheduler-crash-orphaned-tasks.md) |
| Scheduler ticks but does nothing | Syntax error in scheduler.py (stale `__pycache__` masking it) | [2026-02-21 postmortem](postmortems/2026-02-21-scheduler-crash-orphaned-tasks.md) |
| Tasks stuck in `claimed` with empty `running_pids.json` | Orphaned tasks — result collection failed, lease expiry requeue loop | [2026-02-21 postmortem](postmortems/2026-02-21-scheduler-crash-orphaned-tasks.md) |
| Tasks cycling between `incoming` and `claimed` repeatedly | Lease expiry requeue fighting with failed result collection | [2026-02-21 postmortem](postmortems/2026-02-21-scheduler-crash-orphaned-tasks.md) |

## Agent worktree issues

| Symptom | Likely cause | See |
|---|---|---|
| Uncommitted changes in main tree matching agent work | Agent used absolute paths outside worktree; fix: project-relative permissions in worktree `.claude/settings.json` | [2026-02-21 postmortem](postmortems/2026-02-21-scheduler-crash-orphaned-tasks.md#fixes-applied-related-issues-found-during-investigation) |
| PR reverts bug fixes from main | Agent branch diverged from old main; gatekeeper should reject with "rebase first" | [PR #154 rejection comment](https://github.com/maxthelion/octopoid/pull/154) |

## Dashboard

| Symptom | Likely cause | See |
|---|---|---|
| Turn counter shows 0/100t for all tasks | PostToolUse hook not writing `tool_counter` file; check worktree `.claude/settings.json` | [2026-02-21 postmortem](postmortems/2026-02-21-scheduler-crash-orphaned-tasks.md#fixes-applied-related-issues-found-during-investigation) |
| Dashboard clears PID tracking | `cleanup_dead_pids()` called from wrong codepath; only `check_and_update_finished_agents` should remove PIDs | [test_pid_lifecycle.py](../tests/test_pid_lifecycle.py) |

## Stale state

| Symptom | Likely cause | See |
|---|---|---|
| Scheduler runs old code after editing `.py` files | Stale `__pycache__`; run `find octopoid -name '__pycache__' -type d -exec rm -rf {} +` | [CLAUDE.md](../CLAUDE.md#scheduler-and-python-caching) |
| Agent marked as failed despite completing work | Stale `result.json` from previous run | [2026-02-15 postmortem](postmortems/2026-02-15-TASK-stale-result-60f52b91.md) |
| Scheduler crashing with `No module named orchestrator.scheduler` | Package renamed from `orchestrator` to `octopoid` but launchd plist not updated. Fix: update `~/Library/LaunchAgents/com.octopoid.scheduler.plist` to use `octopoid.scheduler`, then `launchctl unload && launchctl load`. | 2026-02-26 session |

## Projects

| Symptom | Likely cause | See |
|---|---|---|
| Project tasks all done but no PR created | Project has `branch=null` — `create_project()` was called without `branch` param. `check_project_completion` silently skips branchless projects. | [2026-02-23 postmortem](postmortems/2026-02-23-project-branch-null-silent-failure.md) |
| Project tasks not visible on dashboard Work tab | Tasks have `flow=project` but no "project" flow registered on server. Fixed by pooling unregistered flows into default tab. | commit `b1b0982` |
| Dashboard 401 errors after auth migration | Stale `OCTOPOID_API_KEY` env var in shell. Remove from `.zshrc`, kill dashboard processes, provision new key via orchestrator registration. | — |

## Task incorrectly moved to failed

| Symptom | Likely cause | See |
|---|---|---|
| Task in `failed` but PR is merged and work landed | `update_changelog` step failed after `merge_pr` already ran and `sdk.tasks.accept()` moved task to `done`. Catch-all exception handler then overwrote `done` with `failed`. | Task 2a06729d, draft #164 |
| Task stuck in `failed` with no `execution_notes` | The `sdk.tasks.update(queue='failed')` call may have failed to save `execution_notes`, or the callsite didn't set it. 5 different callsites move tasks to failed with inconsistent logging. | Draft #164 (unified failure handling) |
| Can't recover task from `failed` to `done` | Server blocks `queue='done'` via PATCH (must use `/accept` endpoint) and `/accept` requires a valid flow transition from current queue. No `failed → done` transition exists. | Server `validate-queue.ts` |

## Merge / Rebase failures

| Symptom | Likely cause | See |
|---|---|---|
| Task approved by gatekeeper but fails at `merge_pr` | Branch conflicts with main. `merge_pr` treats any rebase conflict as fatal — discards all work and re-implements from scratch. | [2026-02-23 postmortem](postmortems/2026-02-23-rebase-conflicts-wasted-agent-runs.md) |
| Same task fails repeatedly at merge despite correct code | Main is being actively pushed to (human or other agents). Each re-attempt diverges again. | [2026-02-23 postmortem](postmortems/2026-02-23-rebase-conflicts-wasted-agent-runs.md), [Draft #93](../project-management/drafts/93-2026-02-23-resilient-rebase-at-merge.md) |
| Gatekeeper approves PR that CI rejects | Gatekeeper runs tests locally without the integration test server. Integration tests are silently skipped (`pytest.skip`). CI starts the server and catches failures the gatekeeper can't see. | TASK-c93fe7d2 (check_ci step) |

## API / Server

| Symptom | Likely cause | See |
|---|---|---|
| Cloudflare rate limiting | Too many agents polling simultaneously; consider request batching | — |
| `_gather_prs` burning API calls | Function not disabled when expected; verify before trusting plan claims | [CLAUDE.md](../CLAUDE.md#plan-verification-rule) |
| 409 on submit after lease expires | Agent finished and wrote `result.json` but scheduler didn't process the result before the lease expired. Server rejects `submit` because lease is invalid. Fix: requeue task, re-claim, then submit. Root cause: scheduler was down (see stale state section). | Task 543cd9d7 |
| 409 on reject/requeue/accept | Server's `canTransition()` checks registered flow transitions. Reverse transitions (reject back to incoming, requeue) must be explicitly registered. Fixed by `_implicit_reverse_transitions()` in `flow.py`. | Commit e210766 |

## Intervention triggered on never-started blocked tasks

| Symptom | Likely cause | See |
|---|---|---|
| Task has `needs_intervention=true` but empty logs, empty context, `attempt_count=0` | False alarm — intervention flag set on a task that was never started; blocker task still in flight | Task 326df326, 2026-02-28 |

## Fixer classifier crashes with "Inference error"

| Symptom | Likely cause | See |
|---|---|---|
| `error_source: "fixer-failed"`, `error_message: "Inference error: Command ['claude', '-p', ...]"` in intervention context | Fixer agent ran to completion but the classifier subprocess failed (timeout, context truncation, or LLM error). The task cycles back into intervention. Fix: run a new fixer agent — it will diagnose correctly and the task can proceed normally. | Task 326df326, 2026-03-01 |

## Fixer agents reporting "could not fix" despite fix being applied

| Symptom | Likely cause | See |
|---|---|---|
| Fixer agent reports "could not fix" but fix is already committed | Fixer (or original agent) ran `pytest` and saw pre-existing integration test failures unrelated to the task, then concluded tests failed. `test_failed_outcome_moves_to_failed` in `octopoid/tests/test_scheduler_lifecycle.py` hits production API with fake task ID `test123` and always gets a 404 — this is a pre-existing failure. Inspect git log for the fix commit and verify changes directly in the file. | Task 8a8e4590, 2026-02-28 |

## Codebase-analyst agent

| Symptom | Likely cause | See |
|---|---|---|
| Multiple duplicate codebase-analyst drafts created in same day | `guard.sh` crashing with `No module named orchestrator` — guard silently fails, agent runs without skipping | Task 6693d4d5 |
| `pytest --cov=orchestrator` shows "no data collected" | `run-quality-checks.sh` targeting wrong package name; fix: `--cov=octopoid` | Task 6693d4d5 |
| `vulture` or `wily` failing to find files | Scripts target `orchestrator/` path which doesn't exist; fix: use `octopoid/` | Task 6693d4d5 |
