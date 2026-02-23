# Postmortem: Rebase conflicts wasted 5 agent runs across 3 tasks

**Date:** 2026-02-23
**Severity:** Medium — correct work was discarded and had to be manually recovered
**Duration:** ~2 hours (from first task claim to manual resolution)
**Wasted compute:** 5 full agent cycles (3 implementer + 2 gatekeeper runs thrown away)

## Summary

Three tasks were correctly implemented and approved by the gatekeeper, but all failed at the `merge_pr` flow step because their branches conflicted with concurrent pushes to main. The system's response to a rebase conflict is to discard all work and re-implement from scratch — which then conflicted again. A human eventually had to manually rebase and merge.

## Tasks affected

| Task | Title | Attempts | Outcome |
|------|-------|----------|---------|
| TASK-08857ec2 | Add architecture analyst background agent | 1 impl + 1 gatekeeper | Failed at merge_pr: "PR not mergeable" |
| TASK-9e0bce3c | Add jobs.yaml and analyst agents to init | 1 impl + 1 gatekeeper | Failed at merge_pr: "Rebase conflicts" |
| TASK-c11040e2 | Add jobs.yaml and analyst agents to init | 1 impl + 1 gatekeeper | Failed at merge_pr: "Rebase conflicts" |

(9e0bce3c and c11040e2 were the same task re-attempted after the first failure.)

## Timeline

### TASK-08857ec2 (architecture analyst)

```
18:29:59  Implementer claimed and started
18:34:37  Implementer completed (done in ~5 min)
18:34:42  Flow: push_branch, run_tests, create_pr → moved to provisional (PR #209)
18:35:21  Gatekeeper claimed for review
18:37:57  Gatekeeper approved
18:38:01  merge_pr FAILED: "PR not mergeable"
          → Task sent to failed
```

**Root cause:** During the 8 minutes between implementation and merge, direct pushes to main had made the PR unmergeable. The `merge_pr` step attempts a rebase but doesn't handle conflicts — any conflict is a hard failure.

### TASK-9e0bce3c and TASK-c11040e2 (init scaffolding)

Two instances of the same task were spawned (the task was enqueued twice accidentally):

```
19:16:48  9e0bce3c implementer started
19:17:53  c11040e2 implementer started
19:20:36  9e0bce3c completed → provisional (PR #214)
19:20:59  c11040e2 completed → provisional (PR #215)
19:21:13  9e0bce3c gatekeeper claimed
19:23:07  9e0bce3c gatekeeper approved
19:23:09  9e0bce3c merge_pr FAILED: "Rebase conflicts with origin/main"
19:23:19  c11040e2 gatekeeper claimed
19:25:34  c11040e2 gatekeeper approved
19:25:37  c11040e2 merge_pr FAILED: "Rebase conflicts with origin/main"
```

**Root cause:** Same as above. Both PRs modified `orchestrator/init.py` and `README.md` which had been changed on main during this session (theme change `88c723e`, project_id fix `7583f1e`). The rebase conflicted and both tasks were discarded.

### Manual resolution

```
19:28:xx  Human rebased c11040e2 worktree onto main (clean rebase)
19:29:xx  Force-pushed to agent/c11040e2
19:33:xx  CI ran — unit tests failed (pre-existing test_dashboard bug)
19:35:xx  Fixed stale unit test on main, rebased PR again
19:36:xx  CI passed (all green)
19:37:xx  PR #215 merged, PR #214 closed as duplicate

19:40:xx  Checked 08857ec2 — rebase onto main produced empty diff (all additive files)
19:41:xx  Cherry-picked original commit to main (clean apply)
19:42:xx  Pushed, closed PR #209
```

Total human time: ~15 minutes. The system had spent ~25 minutes of agent compute to produce work that a 2-minute rebase would have saved.

## Root causes

### 1. Rebase failure = total loss

The `merge_pr` step in `orchestrator/steps.py` treats any rebase conflict as a fatal error. The task goes to `failed` and must be re-implemented from scratch. There is no intermediate state like "needs-rebase" where the work is preserved and only the rebase needs to be resolved.

### 2. No conflict recovery strategy

The system has only one response to a merge conflict: discard everything and start over. It doesn't attempt:
- Auto-resolution for trivial conflicts (e.g. CHANGELOG, README)
- Retry with a different merge strategy
- Holding the task for manual rebase while preserving the approved code

### 3. Active main increases conflict probability

When a human is actively pushing to main while agents are working, the window for conflicts grows. The agents' branches diverge further from main with each push, making conflicts more likely — and re-attempts equally likely to conflict again.

### 4. Gatekeeper can't prevent merge failures

The gatekeeper reviews code quality but has no visibility into merge state. It approves a PR that is already unmergeable, wasting the gatekeeper run.

### 5. Duplicate task spawning

TASK-9e0bce3c and TASK-c11040e2 were both spawned for the same task (two `create_task()` calls — the first appeared to fail silently but actually succeeded). This doubled the wasted compute.

## Impact

- **5 agent runs wasted** (3 implementer, 2 gatekeeper): all produced correct output that was discarded
- **3 PRs created and abandoned** (#209, #214, #215 — only #215 eventually merged after manual intervention)
- **~25 minutes of agent compute** for work that took a human 2 minutes to resolve
- **CI spam**: 6 CI runs triggered for the abandoned PRs

## Fixes

### Already done
- Manually rebased and merged the init task (PR #215)
- Cherry-picked the architecture analyst commit to main
- Draft #93 filed: "Make rebase-at-merge more resilient to concurrent main changes"

### Recommended

1. **Add a `needs-rebase` state** — When merge_pr fails due to conflicts, move to a holding state instead of discarding. Preserve the branch and approved status. Alert the human or retry the rebase periodically.

2. **Add `check_ci` step before merge** (TASK-c93fe7d2 already enqueued) — Check CI status before attempting merge. Won't fix rebase conflicts but prevents merging code that CI would reject.

3. **Rebase retry with auto-resolution** — For trivial conflicts in files like CHANGELOG.md, README.md, or additive-only changes, attempt auto-resolution before giving up.

4. **Deduplication guard** — Prevent `create_task()` from silently creating duplicate tasks with the same title when the first call appears to fail.

## Lessons

- The "rebase at merge time" design (Draft #70, now complete) correctly moved rebasing to the last possible moment. But it didn't account for what happens when the rebase fails — the failure mode is still "throw everything away."
- Additive-only changes (new files, appending to existing files) should almost never conflict fatally. The system should recognise this pattern and handle it more gracefully.
- When a human is actively working alongside agents, pausing the system (`/pause-system`) would prevent this class of issue entirely. But that's a workflow workaround, not a systemic fix.
