# Fix result handler: auth, incomplete migration, and unknown outcome routing

**Captured:** 2026-02-27

## Raw

> There's a bug in the new result handler. The agent wrote result.json with {"outcome": "done"}, but the new haiku-based stdout inference returned unknown, overriding it. Task 96be4eda completed successfully — 5 files modified, 1571 tests pass, clean branch — but got killed by the result handler bug.

## The Incident

Task 96be4eda (guard script update) was completed successfully by an implementer agent. The agent:
- Modified 5 files
- All 1571 tests pass
- Wrote `result.json` with `{"outcome": "done"}`
- Clean branch, ready for review

The new result handler (commit 92b8f5d) called haiku to infer the result from stdout. The haiku call failed with an auth error, returned `{"outcome": "unknown"}`, and the handler routed the task straight to `failed`.

## Three Bugs

### Bug 1: Auth mismatch — `anthropic` SDK vs Claude CLI

`result_handler.py:_call_haiku()` (line 81-90) uses the `anthropic` Python SDK:

```python
client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
message = client.messages.create(model="claude-haiku-4-5-20251001", ...)
```

The scheduler runs via launchd. The plist sets `HOME`, `PATH`, `PYTHONPATH` — but not `ANTHROPIC_API_KEY`. The `anthropic.Anthropic()` constructor throws `AuthenticationError`.

Meanwhile, spawned Claude agents work fine because `claude` CLI uses its own OAuth/session auth stored in `~/.claude/`, which is available because `HOME` is set.

**The haiku test worked** because we ran it from an interactive shell where `ANTHROPIC_API_KEY` was exported. The scheduler has never had this key.

**Fix:** Replace the `anthropic` Python SDK call with `claude -p` invocation, matching how agents are spawned. This uses the same auth that already works. The call is tiny (10 max tokens, ~2000 chars input) so overhead of spawning a subprocess is negligible.

```python
def _call_haiku(prompt: str) -> str:
    result = subprocess.run(
        ["claude", "-p", prompt, "--model", "haiku", "--max-turns", "1"],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "CLAUDECODE": ""},  # unset CLAUDECODE like invoke_claude
    )
    return result.stdout.strip().lower()
```

### Bug 2: Incomplete migration — agents still told to write result.json

Commit 92b8f5d removed the result.json *reader* but left result.json *writer* instructions in agent prompts:

| File | Still references result.json |
|------|------------------------------|
| `octopoid/init.py:464` | `GATEKEEPER_PROMPT_STUB` says "Write your decision to result.json" |
| `.octopoid/agents/gatekeeper/instructions.md:139` | "You MUST rewrite the task file before writing result.json" |
| `.octopoid/agents/fixer/instructions.md:7,18,31,38` | Full result.json format section |
| `octopoid/data/agents/gatekeeper/instructions.md` | Packaged copy of above |
| `octopoid/data/agents/implementer/scripts/run-tests:12` | `RESULT_FILE` env var |

The implementer template was cleaned up, but gatekeeper and fixer were not. This means:
- Gatekeepers still write result.json, but nothing reads it
- Fixers still write result.json, but nothing reads it
- The implementing agent on task 96be4eda was spawned before 92b8f5d landed, so it used the old prompt and wrote result.json

**Fix:** Remove all result.json references from agent prompts. Agents should just do their work and let their stdout speak for itself. For gatekeepers, the stdout already contains "APPROVED" or "REJECTED" clearly. For fixers, the stdout describes what they did.

### Bug 3: Unknown outcome routes to failed, not requires-intervention

In `result_handler.py:handle_agent_result()` (line 784-785):

```python
else:
    return _handle_fail_outcome(sdk, task_id, task, f"Unknown outcome: {outcome}", current_queue)
```

When the outcome is `unknown` (haiku failed, empty stdout, unrecognized response), the task goes straight to `failed` via `_handle_fail_outcome()`. This bypasses `fail_task()` and `request_intervention()`.

The docstring on line 742-743 even says "unknown routes to requires-intervention via fail_task if available" — but the code doesn't do this.

An `unknown` outcome means "we don't know what happened." That's a human-judgment situation, not an automatic failure. The task might be perfectly fine (as in this case).

**Fix:** Route `unknown` outcomes to `request_intervention()`:

```python
else:
    # Unknown outcome — we can't determine what happened.
    # Route to requires-intervention for human review, not automatic failure.
    request_intervention(
        task_id,
        reason=f"Could not determine outcome: {result.get('reason', 'unknown')}",
        source="result-handler",
    )
    return True
```

## Implementation Plan

### Phase 1: Fix the auth (critical, do first)

1. Replace `_call_haiku()` in `result_handler.py` to use `claude -p` via subprocess instead of the `anthropic` Python SDK
2. Remove the `import anthropic` dependency
3. Test by running the scheduler manually and checking a task result is inferred correctly

### Phase 2: Fix unknown routing (critical, do second)

1. Change the `else` branch in `handle_agent_result()` to call `request_intervention()` instead of `_handle_fail_outcome()`
2. Do the same in `handle_agent_result_via_flow()` if applicable
3. Update the unit tests in `test_scheduler_lifecycle.py` that test unknown outcomes

### Phase 3: Complete the migration (cleanup)

1. Remove result.json references from gatekeeper instructions (`.octopoid/agents/gatekeeper/instructions.md` and `octopoid/data/agents/gatekeeper/instructions.md`)
2. Remove result.json references from fixer instructions (`.octopoid/agents/fixer/instructions.md` and `octopoid/data/agents/fixer/prompt.md`)
3. Remove `GATEKEEPER_PROMPT_STUB` result.json reference in `octopoid/init.py`
4. Remove `RESULT_FILE` from `octopoid/data/agents/implementer/scripts/run-tests`
5. Update `scheduler.py` comments that still reference result.json (lines 1068, 1825, 1890)
6. Update `test_scheduler_lifecycle.py` — tests still write result.json as setup; they should write stdout.log instead

### Phase 4: Recover task 96be4eda

Push the task through manually — it completed successfully and just needs to be moved from failed to the right queue.

## Open Questions

- Should haiku inference have a timeout? If `claude -p` hangs, the scheduler tick blocks. The subprocess call should have a `timeout=30` or similar.
- Should we log the raw haiku response for debugging? Currently only logged at debug level. A warning when haiku returns something unexpected would help diagnose future issues.
- Should `handle_agent_result_via_flow()` (the gatekeeper/flow path) also route unknown to requires-intervention? It currently returns a failure dict that the flow engine handles, but the principle is the same.
