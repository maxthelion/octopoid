# Switch agent spawning to --output-format json

**Captured:** 2026-03-02

## Raw

> We always want to capture something from agent runs. `claude -p` with default text output writes nothing to stdout if the agent hits max_turns without a final text response, or on API errors. Switching to `--output-format json` guarantees structured output in all cases.

## Idea

`invoke_claude()` in `scheduler.py` spawns agents with `claude -p` using the default text output format. This writes only the final assistant text response to stdout. If the agent exhausts its turns on tool calls, or hits an API error, or exits abnormally — stdout is empty. The result handler sees empty stdout, infers `outcome=unknown`, and triggers intervention. This accounts for ~5 genuine empty stdout incidents across implementers and gatekeepers.

The fix: add `--output-format json` to the `claude -p` command. This produces a JSON object on every exit:

```json
{
  "type": "result",
  "subtype": "success",           // or "error_max_turns_exceeded", etc.
  "result": "The agent's text",   // may be partial on max_turns
  "is_error": false,
  "num_turns": 15,
  "cost_usd": 0.025,
  "session_id": "...",
  "duration_ms": 45000
}
```

### Changes required

**1. `invoke_claude()` in `scheduler.py`**

Add `"--output-format", "json"` to the cmd list. No other changes to spawning.

**2. `infer_result_from_stdout()` in `result_handler.py`**

Currently reads stdout as plain text and takes the last 2000 chars for classification. Needs to:

1. Try to parse stdout as JSON first
2. If valid JSON with a `result` field:
   - Extract `result` as the text to classify (pass to existing `_infer_implementer`/`_infer_gatekeeper`/`_infer_fixer`)
   - If `result` is empty but `subtype` is `error_max_turns_exceeded`, return a meaningful outcome instead of "agent may have crashed"
   - Log `num_turns`, `cost_usd` for observability
3. If not valid JSON (backwards compat with any agents spawned before the change): fall through to existing plain-text parsing

This preserves backward compatibility — old stdout.log files from in-flight agents still work.

**3. Empty stdout elimination**

With JSON output, the "empty stdout" path in `infer_result_from_stdout()` should become unreachable for agents spawned with the new flag. The JSON object always has content. If stdout IS empty with JSON format, it means the process truly crashed (SIGKILL, OOM) — a much more specific signal.

### What NOT to change

- The inference prompts (`_infer_implementer`, `_infer_gatekeeper`, `_infer_fixer`) stay the same — they receive the text from `result` field, same as before
- Agent prompt templates don't change — agents still write their output as text, `claude -p` wraps it in JSON
- The `tool_counter` hook stays — `num_turns` from JSON is the API turn count, tool_counter is tool-call count

## Invariants

- **always-captures-output**: Every agent run produces non-empty stdout.log. The only exception is process-level crashes (SIGKILL, OOM), which are logged as such rather than "agent may have crashed."
- **backwards-compatible-parsing**: The result handler gracefully handles both JSON and plain-text stdout.log files. In-flight agents spawned before the change are processed correctly.
- **max-turns-is-diagnosable**: When an agent hits max_turns, the result handler reports this specifically (not as "unknown" or "crashed"). The `subtype` field distinguishes normal completion from turn exhaustion.

## Context

Investigation of empty stdout across all tasks found 27 occurrences: 17 from fixers (secondary, caused by stale `needs_intervention` — draft 221), 5 from implementers, 5 from gatekeepers. Of the 10 non-fixer cases, 5 were genuine (before any approval) and 5 were spurious (agents spawned against already-done tasks). The genuine cases are the target for this fix.

## Open Questions

- Should we also capture `cost_usd` and `num_turns` in the task record or messages for observability?
- Should `session_id` be saved so we can `--resume` a session that hit max_turns?

## Possible Next Steps

- Update `invoke_claude()` to add `--output-format json`
- Update `infer_result_from_stdout()` to parse JSON, extract text, handle error subtypes
- Test with a deliberately low `max_turns` to verify max_turns_exceeded is captured correctly
- Integration test: agent produces JSON stdout, result handler classifies correctly
