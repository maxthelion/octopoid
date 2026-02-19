# Postmortem: Agent Tasks Going to Failed Despite Completing Work

**Status:** Idea
**Captured:** 2026-02-16

## The Pattern

TASK-7a393cef (queue_utils refactor) has exhibited the same failure mode at least 3 times:

1. Agent does the work, makes commits, pushes to PR
2. Agent prints a completion summary to stdout
3. Agent process exits with code 1
4. No `result.json` is produced
5. Scheduler sees no exit_code file → assumes crash (exit code 1)
6. Scheduler calls `handle_agent_result()` → no result.json → `outcome = "error"` → task moves to `failed`
7. Human manually moves task to provisional

The work is done. The PR is updated. But the task ends up in `failed` every time, requiring manual intervention.

## Root Cause Analysis

### How the scheduler decides

```
Agent finishes → scheduler detects PID is gone
  ↓
read_agent_exit_code() → reads .octopoid/runtime/agents/<name>/exit_code
  ↓ (file doesn't exist)
exit_code = 1 (assumed crash)
  ↓
handle_agent_result(task_id, agent_name, task_dir)
  ↓
result.json exists?
  ├─ YES → read outcome, transition accordingly
  └─ NO → notes.md exists?
       ├─ YES → outcome = "needs_continuation"
       └─ NO → outcome = "error" → task goes to FAILED
```

### Why there's no result.json

The agent is supposed to call `../scripts/submit-pr` which:
1. Pushes the branch
2. Creates/updates the PR
3. Writes `result.json` with `{"outcome": "submitted", "pr_url": "...", "pr_number": N}`

Then `../scripts/finish` marks completion.

But the agent is **not calling these scripts**. It does the work, pushes commits directly with `git push`, prints a summary, and exits. The prompt tells it to use the scripts, but when the agent runs out of turns or decides it's "done", it just stops — without calling the lifecycle scripts.

### Why exit code is 1

The `claude` CLI exits with code 1 when it hits the max turns limit. The agent uses `--max-turns 200`, and for a complex task like a refactor with 34 failing tests, it can exhaust all turns doing the actual work and never get to the "call finish script" step.

Even when the agent doesn't hit the limit, if `claude` encounters any internal issue on shutdown, it exits 1. There's no `exit_code` file written because the agent wrapper doesn't always produce one.

### Why this keeps repeating

The task gets requeued → agent picks it up → same worktree with work already done → agent verifies everything is good → pushes → prints summary → exits without scripts → failed again.

## Impact

- TASK-7a393cef went through this cycle 3 times, each requiring human intervention
- Each failed run consumes agent turns (200 max) and wall-clock time (~20 min)
- The 4 blocked tasks (SDK ProjectsAPI, worktree lifecycle, integration test, gatekeeper) are delayed by each round-trip
- Human has to manually `sdk.tasks.update(queue='provisional')` or `sdk.tasks.submit()` to unblock

## Contributing Factors

1. **Max turns exhaustion** — 200 turns isn't always enough for "fix 34 tests + push + call scripts". The agent spends all turns on implementation and has none left for the lifecycle scripts.

2. **No exit_code file** — The agent wrapper should write an exit_code file, but doesn't always. The scheduler falls back to assuming crash (exit code 1).

3. **No graceful degradation** — If result.json is missing but the agent pushed commits to an existing PR, the scheduler should check git/PR state before declaring failure.

4. **Agent doesn't prioritize scripts** — The prompt says "use the provided scripts" but the agent treats it as advisory. When it's focused on fixing tests, the scripts are the last thing on its mind.

5. **No turn budget awareness** — The agent doesn't know how many turns it has left. It can't reserve the last few turns for "call submit-pr and finish".

## Proposed Fixes

### Fix 1: Scheduler should check PR state before declaring failure (short-term)

In `handle_agent_result()`, when there's no result.json:
1. Check if the task has a `pr_number`
2. If so, check if new commits were pushed since the task was claimed
3. If new commits exist on the PR, treat as `submitted` instead of `error`

This is a safety net — even if the agent forgets to call scripts, the scheduler detects the work.

### Fix 2: Agent wrapper should always write exit_code (short-term)

The script that spawns the agent (`env.sh` or the scheduler's `spawn_agent`) should trap the exit and write the exit code:

```bash
claude ... ; echo $? > exit_code
```

### Fix 3: Turn budget reservation (medium-term)

Reserve the last 5-10 turns for lifecycle scripts. Options:
- Pass `--max-turns 190` to claude and use a wrapper that calls submit-pr after
- Add a post-agent hook in the scheduler: "if agent exits cleanly and task dir has commits, auto-submit"

### Fix 4: Post-agent auto-submit (medium-term)

After the agent process finishes, the scheduler checks:
1. Does the worktree have commits not on origin? → push them
2. Does a PR exist? → submit the task
3. No PR but commits? → create PR and submit

This makes the lifecycle scripts optional rather than required. The agent calling them is a bonus (for custom PR descriptions etc.), but the scheduler handles the common case.

### Fix 5: Make scripts the first thing, not the last (long-term)

Restructure the agent flow:
1. Agent does work and commits
2. Agent calls `record-progress` periodically (saves context)
3. When agent thinks it's done, it calls `submit-pr` BEFORE writing the summary
4. `submit-pr` writes result.json
5. Agent can still print a summary after

The issue is that "summarize what I did" feels like the natural last step for an LLM, but "call the lifecycle script" should be.

## Recommendation

**Fix 4 (post-agent auto-submit)** is the most robust — it makes the system tolerant of agents that forget scripts, hit turn limits, or crash. The scheduler already knows which task the agent was working on and can inspect the git state.

Fixes 1 and 2 are quick wins that should be done regardless.

## Related

- Draft 7: Sanity-check gatekeeper (would catch incomplete submissions)
- TASK-ad3a4e7a: Gatekeeper implementation (in queue)
- TASK-7a393cef: The task that triggered this pattern 3 times
