# Issues Log

Known symptoms and their root causes. **Consult this first when diagnosing a problem** — many issues recur.

## CI red on main but PRs keep merging

| Symptom | Likely cause | Fix |
|---|---|---|
| Main branch CI failing for days, no alerts, PRs still merge | `check_ci` only checks PR branch CI, not main. `merge_pr` runs without verifying main is green. | Needs structural fix: add main-CI gate before merge_pr step. See [postmortem](postmortems/2026-03-02-ci-red-on-main-4-days.md). |
| Tests assert `queue="failed"` but get `"claimed"` or `"provisional"` | `fail_task()` / `request_intervention()` sets `needs_intervention=True` flag but doesn't change queue. Tests expect queue transition. | `request_intervention()` in tasks.py should also set `queue="requires-intervention"`. Task 570d1d48. |

## Agent completes work but stdout empty → false fixer trigger

| Symptom | Likely cause | Fix |
|---|---|---|
| Task enters `requires-intervention` with "Empty stdout — agent may have crashed" but implementation commit exists in worktree | Agent hit max_turns (or API error) after committing but before printing a final text response to stdout. Orchestrator saw empty stdout → classified as `unknown` → triggered fixer. | In the task worktree: rebase detached HEAD onto origin/main, create `agent/<task-id>` branch. The scheduler will push and create a PR. The root fix is switching to `--output-format json` but the first attempt (PR #299) was reverted due to wrong subtype string. See [postmortem](postmortems/2026-03-02-output-format-json-breaks-inference.md). |
| All agent outcomes classified as "unknown" after enabling `--output-format json` | result_handler.py checks for subtype `"error_max_turns_exceeded"` but Claude CLI outputs `"error_max_turns"`. String mismatch causes max_turns detection to fail, falls through to empty text extraction. | Fix the subtype string in result_handler.py line 244. Also handle empty `result` field by classifying from `subtype` + `is_error` fields. See [postmortem](postmortems/2026-03-02-output-format-json-breaks-inference.md). |

## claimed_by leak / laptop sleep issues

| Symptom | Likely cause | Fix |
|---|---|---|
| Claimed tasks show `claimed_by: None` on server | Server submit endpoint was not clearing `claimed_by` and `lease_expires_at` after transition to provisional — tasks in provisional still had claim metadata set, causing the scheduler's lease monitor to treat them as actively reviewed. Root cause fixed server-side in commit `61c0918` (clear on submit) and `73d3511` (shared transition table for consistent clearing). The server's claim endpoint has always correctly set `claimed_by = body.agent_name`. | Fixed: server commits `61c0918` + `73d3511`. Added `claimed_by` + `lease_expires_at` assertions to `test_claim_returns_task_when_available`. |
| `check_and_requeue_expired_leases` leaves stale `lease_expires_at` on provisional tasks where `claimed_by` is already None | Scheduler skipped provisional tasks where `claimed_by` was falsy, regardless of `lease_expires_at`. If `claimed_by` was cleared by one code path without also clearing `lease_expires_at`, the stale timestamp was never cleaned up. | Fixed: changed skip condition to `not claimed_by and not lease_expires_at` so tasks with a stale lease are still processed. Removed xfail from `test_stale_lease_without_claimed_by_is_still_cleared`. |
| Agent uses 51 turns but max_turns is 200, exits with `error_max_turns` | Laptop sleep suspends Claude CLI process. On wake, session may timeout or API connection drops, causing early exit. | No fix yet. Consider lease renewal or sleep detection. |
| Task submit fails with 409 after agent completes work | Lease expired during laptop sleep. Agent finishes on wake but server rejects submit because lease is invalid. | Requeue task, re-claim, then submit. Root fix: lease renewal mechanism. |

## Fixer agent overwrites original stdout

| Symptom | Likely cause | Fix |
|---|---|---|
| Failed task's stdout.log contains fixer output, not the original agent's | `prepare_task_directory()` deletes stdout.log before each run. Fixer overwrites implementer output. Second fixer overwrites first fixer's prev_stdout.log. Original diagnostic output is destroyed. | Preserve stdout per-attempt (e.g. `stdout-attempt-0.log`, `stdout-fixer-1.log`). See [postmortem](postmortems/2026-03-02-output-format-json-breaks-inference.md). |

## Fixer agent: broken tooling when worktree is deleted

| Symptom | Likely cause | Fix |
|---|---|---|
| Fixer agent crashes with empty stdout | Fixer session CWD is the task worktree, which was deleted before the fixer ran. Bash/Glob/Grep tools all fail. | Edit/Read/Write tools still work via absolute paths. Use those to make file changes. Cannot commit — human must `git add` + `git commit` manually in the main repo. |
| Gatekeeper says files were "deleted" but they don't exist | Agent renamed files (e.g. 183-... → 0183-...) in a prior commit; gatekeeper referenced old unpadded names that never existed in that form on main. | Verify with `git log --all -- path/to/file`. If file truly never existed by that name, the gatekeeper was wrong — no action needed. |
| `if rejected:` NameError in queue-status.md | Out-of-scope change added a `rejected_by_gatekeeper` problem type but never populated the `rejected` list. | Remove the `if rejected:` block (lines 113-119). Fixed in tasks 72ff0d4e and 7818ecfa. |

## Duplicate task: PR creation fails with "No commits between main and branch"

| Symptom | Likely cause | Fix |
|---|---|---|
| `create_pr` step fails: "No commits between main and agent/XXXXX" | Duplicate task — another task did the same work and was merged first. When this task's agent ran `rebase_on_base`, the rebase incorporated all its changes (they were already in main), leaving zero unique commits. The branch tip equals main's tip. | The task's acceptance criteria are already satisfied — no code change needed. Mark the task done manually: `sdk._request('POST', f'/api/v1/tasks/{task_id}/force-queue', json={'queue': 'done', 'reason': 'duplicate — work already merged by another task'})`. Long-term fix: analyst should check if the target symbols are still present before creating a task. |

## Rebase conflict: parallel systemic-failure work in scheduler.py

| Symptom | Likely cause | Fix |
|---|---|---|
| Rebase fails with conflict in `octopoid/scheduler.py` around spawn failure handler | Two PRs modified the same spawn-failure path: one added `_requeue_task_blameless` + simple `_record_systemic_failure`, the other added `_handle_systemic_failure` + diagnostic agent spawn | Use `_requeue_task_blameless` (blameless requeue) + `_handle_systemic_failure` (triggers diagnostic). Remove the duplicate `_record_systemic_failure` (returning None) and `_SYSTEMIC_FAILURE_THRESHOLD` constant that HEAD added, since our new version at earlier line returns int and is used by `_handle_systemic_failure`. |

## Scheduler not processing tasks

| Symptom | Likely cause | See |
|---|---|---|
| "Last tick: Xh ago" in queue-status | Scheduler crashed or launchd throttled | [2026-02-21 postmortem](postmortems/2026-02-21-scheduler-crash-orphaned-tasks.md) |
| Scheduler ticks but does nothing | Syntax error in scheduler.py (stale `__pycache__` masking it) | [2026-02-21 postmortem](postmortems/2026-02-21-scheduler-crash-orphaned-tasks.md) |
| Tasks stuck in `claimed` with empty `running_pids.json` | Orphaned tasks — result collection failed, lease expiry requeue loop | [2026-02-21 postmortem](postmortems/2026-02-21-scheduler-crash-orphaned-tasks.md) |
| Tasks cycling between `incoming` and `claimed` repeatedly | Lease expiry requeue fighting with failed result collection | [2026-02-21 postmortem](postmortems/2026-02-21-scheduler-crash-orphaned-tasks.md) |

## Gatekeeper approve completes steps but task never reaches done

| Symptom | Likely cause | See |
|---|---|---|
| Task stuck after gatekeeper approves — PR merged but task not in `done` | `_handle_approve_and_run_steps` in result_handler.py never calls `_perform_transition()` after executing steps | [2026-03-01 postmortem](postmortems/2026-03-01-gatekeeper-approve-no-transition.md) |
| Fixer loops 3x saying "already complete" then circuit breaker moves to failed | Same root cause — steps ran (PR merged) but queue never updated, lease expires, fixer can't resume correct transition | [2026-03-01 postmortem](postmortems/2026-03-01-gatekeeper-approve-no-transition.md) |

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
| Task in `requires-intervention` with "Empty stdout — agent may have crashed" and `step_that_failed: "merge_pr"` but no PR exists | Agent completed code changes and made a commit, but crashed (OOM or timeout) before writing stdout. The scheduler ran `rebase_on_base` (succeeded) then tried `merge_pr` which failed since no branch/PR was created. The fix commit is in the worktree's detached HEAD. Fixer agent should verify commit is correct and write "Fixed" — `handle_fixer_result()` will resume the `claimed -> provisional` flow steps (`push_branch`, `create_pr`) to push the branch and create the PR. | Task cdd7cdce, 2026-03-01 |
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

## Fixer agent fails with "could not fix" but the fix was already applied

| Symptom | Likely cause | Fix |
|---|---|---|
| `error_source: fixer-failed`, `error_message: "could not fix"`, working tree has correct changes but they're uncommitted | Fixer agent applied the code change but didn't commit it; the scheduler then inferred "could not fix" from stdout and re-queued for intervention | Check `git diff` in the worktree — if the fix is present but uncommitted, just commit it and push |

## `test_scheduler_has_no_direct_failed_update` fails in CI

| Symptom | Likely cause | Fix |
|---|---|---|
| CI `unit-tests` job fails with `scheduler.py contains a direct sdk.tasks.update(queue='failed') call outside fail_task()` | A new callsite was added to `scheduler.py` that bypasses `fail_task()` | Replace direct `sdk.tasks.update(queue="failed")` with `queue_utils.fail_task()` | Task 61cc36d6 |
