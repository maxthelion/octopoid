# Replace tool_counter with structured agent activity log

**Captured:** 2026-02-20 (original), 2026-02-24 (rewritten)

## Raw

> The progress bar is kind of useless for an unbounded number. It's interesting to know what qualitative work is being done by an agent. I proposed that agents write to a log file in their directory on each tick, rather than incrementing the counter. We could count lines in the file for the number of turns information. We could also show the actual turn operations in the task detail view. This would need to persist through multiple turns, and should be useable by other agents such as gatekeepers.

## Problems with the current approach

The current `tool_counter` mechanism (a PostToolUse hook that appends a byte per tool call) has three bugs:

1. **Counts tool calls, not turns.** A single API turn with 5 parallel tool calls registers as 5 in the counter. The comment at `scheduler.py:933` says "tool calls = turns used" — this is wrong. The progress bar in the dashboard compares this inflated number against `_ROLE_TURN_LIMITS` in `reports.py`, making agents look like they're burning far more turns than they actually are.

2. **Never resets on requeue.** `prepare_task_directory()` cleans `result.json` and `notes.md` but not `tool_counter`. When a task is rejected and requeued, the counter accumulates across all attempts. A task that took 80 turns across 3 attempts shows as 240.

3. **Progress bar against a fixed limit is misleading.** The bar shows `tool_calls / max_turns` but since tool calls >> turns, the bar often overflows or looks maxed out while the agent still has turns left.

## Proposed replacement: `activity.log`

Replace the byte-append `tool_counter` with a structured log file. One line per **turn** (not per tool call), written by a hook or by the agent itself.

### Format

Each line is a timestamped summary of what the turn did:

```
2026-02-24T14:32:01Z Read orchestrator/scheduler.py, orchestrator/flow.py
2026-02-24T14:32:15Z Edited orchestrator/flow.py (flow_to_server_registration)
2026-02-24T14:32:30Z Ran pytest tests/test_flow.py — 12 passed
2026-02-24T14:33:01Z Wrote tests/test_flow_serialization.py (new file)
2026-02-24T14:33:20Z Ran git diff, reviewed changes
2026-02-24T14:33:45Z Committed: "fix: preserve conditions in flow serialization"
```

### What this gives us

1. **Accurate turn count** — `wc -l activity.log` = actual turns used
2. **Qualitative visibility** — the dashboard task detail view can show what the agent is actually doing, not just a number
3. **Gatekeeper context** — when reviewing a task, the gatekeeper can read `activity.log` to understand what was attempted, what tests were run, what files were changed and why
4. **Requeue-safe** — on requeue, prepend a separator line (`--- requeued at <timestamp> ---`) so attempts are visually separated but history is preserved
5. **Debugging aid** — when an agent runs out of turns or produces bad output, the log shows exactly where it went wrong

### How to generate the summaries

Option A: **PostToolUse hook parses tool name + args.** The hook receives the tool name and can write a one-liner. Downside: one line per tool call, not per turn. Would need deduplication or batching.

Option B: **Use Claude Code's `--output-format` or conversation export.** If Claude Code can export a turn summary, we could capture it. Need to investigate what's available.

Option C: **Agent writes its own log.** Add to the agent's system prompt: "After each significant action, append a one-line summary to `$OCTOPOID_TASK_DIR/activity.log`." Simple but relies on agent compliance.

Option D: **Hybrid — hook writes raw events, post-processing summarizes.** The hook captures raw tool call data, and a lightweight post-processing step (or the dashboard itself) summarizes it into human-readable form.

## Context

Came up while investigating why agents appear to use hundreds of turns. The tool_counter was inflating numbers (counting tool calls not turns) and never resetting on requeue. The dashboard progress bar was actively misleading.

## Open Questions

- Which generation approach (A/B/C/D) is most reliable? Option C is simplest but agents might forget. Option A is mechanical but noisy.
- Should the log be structured (JSON lines) or human-readable (plain text)? JSON is easier to parse programmatically but harder to read in the dashboard.
- How does the gatekeeper access the log? It runs in a different worktree. The log lives in `.octopoid/runtime/tasks/{task-id}/activity.log` which is outside the worktree — gatekeepers would need the task dir path.
- Should old activity.log entries be pruned, or do we keep the full history?

## Possible Next Steps

- Spike: try Option C (agent self-logging) with a simple prompt instruction and see if agents comply reliably
- Update `prepare_task_directory()` to handle `activity.log` on requeue (separator line, not deletion)
- Update dashboard task detail view to render the log
- Update `_read_live_turns()` in reports.py to count lines instead of bytes
- Remove `tool_counter` hook and `_ROLE_TURN_LIMITS` comparison once the new system is in place
