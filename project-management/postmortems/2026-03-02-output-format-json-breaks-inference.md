# Postmortem: --output-format json breaks agent result inference

**Date:** 2026-03-02
**Duration:** ~1.5 hours (12:51 UTC merge → 14:13 UTC revert)
**Impact:** All agent runs after merge produced "unknown" outcomes. At least 3 tasks affected (570d1d48, 5d7a6ef5, a3feaf6f). $5+ burned on agent runs that couldn't be classified. P0 CI fix task (570d1d48) delayed.

## Timeline

- **12:28** — Task cb4741a4 commits: adds `--output-format json` to `invoke_claude()`, adds `_parse_json_stdout()` to result_handler.py, adds 20 unit tests. All 1104 tests pass.
- **12:33** — PR #299 created automatically.
- **12:51** — PR #299 merged to main. All new agent spawns now use JSON output.
- **~13:15** — Task 570d1d48 (P0 CI fix) claimed. Agent spawns with `--output-format json`.
- **~13:30** — Task 5d7a6ef5 claimed. Same.
- **~14:05** — 570d1d48 agent exits after 51 turns. stdout.log contains only JSON blob. Result handler classifies as "unknown". Fixer triggered. Fixer also produces JSON stdout → also "unknown" → circuit breaker → task moved to failed.
- **14:13** — `--output-format json` flag removed from scheduler.py. Commit b6917ad pushed.
- **14:15** — Both orphan agent processes (PIDs 66029, 72112) killed manually.

## Root Causes

### 1. Wrong subtype string (the actual bug)

The result handler checks for `"error_max_turns_exceeded"` (line 244 of result_handler.py) but the Claude CLI outputs `"error_max_turns"`. String mismatch. The max_turns detection — the primary motivation for the JSON format change — never worked.

```python
# What the code checks:
if subtype == "error_max_turns_exceeded":

# What the Claude CLI actually outputs:
{"type": "result", "subtype": "error_max_turns", ...}
```

This means even when the JSON was parsed correctly, the max_turns case fell through to text extraction, found an empty `result` field, and returned `{"outcome": "unknown"}`.

### 2. Empty `result` field in JSON output

When an agent hits max_turns, the Claude CLI's JSON output has `"result": null` or an empty string — there's no final text response because the agent was cut off. The code correctly extracts this field but then has nothing to inference on:

```python
text = parsed.get("result") or ""  # → empty string
if not text.strip():               # → True
    return {"outcome": "unknown", "reason": "Empty result in JSON stdout"}
```

So even if the subtype string had matched, a max_turns exit with no final text would have been misclassified for any subtype other than the (wrong) max_turns check.

### 3. Unit tests didn't test against real CLI output

The 20 new tests in `test_result_inference.py` all mock the JSON structure with hand-crafted payloads. None tested against actual Claude CLI output. The wrong subtype string (`error_max_turns_exceeded` vs `error_max_turns`) was never caught because the tests used the same wrong string.

### 4. No integration test for the full inference pipeline

There's no test that: spawns a real Claude session with `--output-format json` → captures stdout → runs the inference pipeline → verifies the outcome. The change was tested in isolation (unit tests for parsing) but never end-to-end.

## Contributing Factors

### Laptop sleep and lease expiry

Both claimed tasks showed `claimed_by: None` on the server despite having active processes. This is likely caused by laptop sleep: the 1-hour lease expires while the laptop is asleep, the server clears claim metadata, but the agent process resumes on wake and continues working against an expired lease. When it finishes, any submit/accept calls fail with 409 (invalid lease).

This is a pre-existing issue that compounds the JSON output problem — even if the inference had worked, the expired lease would have caused a 409 on submission.

### The `claimed_by` leak

The server's claim endpoint sets `claimed_by = body.agent_name` (tasks.ts line 628). The SDK sends `agent_name` in the claim request. But claimed tasks consistently show `claimed_by: None`. Possible causes:
- Lease expiry clearing the field while the task stays in `claimed` queue
- A race between claim and some other update that nulls it
- The server not actually storing it (needs investigation)

This has been observed repeatedly but never investigated. It makes it impossible to know which agent is working on which task from the queue status alone.

### Pattern: fix one thing, break another

This is the third instance of this pattern in recent weeks:
1. **PR #251** removed `debug_log()` in favour of `logger`. **PR #252** (merged same day) still used `debug_log()` → 13 test failures, CI red for 4 days.
2. **PR #264** added `check_ci` for PR branches. Didn't add main-branch CI checking → 48 PRs merged to broken main without detection.
3. **PR #299** added `--output-format json` to fix empty stdout. Wrong subtype string, no integration test → all agent outcomes misclassified.

The common thread: changes ship with unit tests that verify the change in isolation, but no integration tests that verify the change works with its consumers. The agents implementing these changes don't understand the full dependency chain.

## What's Still Broken

### Empty stdout (the original problem)

The revert puts us back to square one. The `--output-format json` change was motivated by draft 222: agents that hit max_turns or crash produce empty stdout, which the result handler classifies as "unknown", triggering the fixer loop. This problem is not solved.

The JSON infrastructure is still in result_handler.py (`_parse_json_stdout()`, the subtype routing, 20 unit tests) — it's just not activated because the spawn command no longer includes the flag.

### What's needed to re-enable JSON output

1. Fix the subtype string: `"error_max_turns_exceeded"` → `"error_max_turns"`
2. Handle empty `result` field for non-max-turns exits (agent crash, API error) — classify based on `subtype` + `is_error` fields rather than requiring text
3. Add an integration test that runs `claude --output-format json` with a trivial prompt and verifies the full inference pipeline produces the correct outcome
4. Test against ALL Claude CLI subtypes: `success`, `error_max_turns`, `error_tool_use`, `error_api`, etc.

### The `claimed_by` leak

Needs investigation:
1. Add logging to the SDK claim() call to verify `agent_name` is being sent
2. Check server-side: does lease expiry clear `claimed_by`? Does any other codepath null it?
3. If it's a sleep/lease issue: consider extending leases or adding a lease renewal mechanism

### Laptop sleep resilience

The system has no mechanism to handle laptop sleep gracefully:
- No lease renewal (agents can't extend their lease mid-run)
- No detection of "woke from sleep" to trigger health checks
- No protection against expired leases causing 409s on submit
- Scheduler heartbeat goes stale during sleep but recovers automatically on wake

## Holistic Fix

The root issue is that changes to the agent pipeline (spawn → run → capture → infer → route) are being made without end-to-end verification. Each component is tested in isolation but the pipeline as a whole isn't.

**What's needed:**

1. **Pipeline integration test.** A test that exercises the full path: spawn agent with known prompt → capture stdout → run inference → verify outcome classification → verify queue transition. This test should run in CI and cover both text and JSON output formats.

2. **Real CLI output fixtures.** Capture actual Claude CLI output for each exit mode (success, max_turns, error) in both text and JSON formats. Use these as test fixtures instead of hand-crafted mocks.

3. **Dependency-aware task descriptions.** When creating tasks that modify one part of a pipeline, the task description should explicitly list downstream consumers and require them to be tested. "Add --output-format json" should have required testing the result handler against real JSON output.

4. **Lease renewal or sleep detection.** Either agents should periodically renew their lease, or the scheduler should detect wake-from-sleep and reconcile claimed tasks with running processes.

### Fixer agents overwrite original stdout

Each agent run goes through `prepare_task_directory()` which deletes `stdout.log` and saves the last 3000 chars to `prev_stdout.log`. When a fixer runs, it overwrites the original agent's stdout. A second fixer overwrites the first fixer's prev_stdout. By the time a task reaches failed after the fixer circuit breaker, the original agent's output — the only thing that explains what went wrong — is gone.

This made diagnosing this incident harder: we could only see "Error: Reached max turns (50)" from the fixer, not the original implementer's JSON blob or any work it logged.

Fix: preserve stdout per-attempt (e.g. `stdout-attempt-0.log`, `stdout-fixer-1.log`) instead of overwriting a single file.

## Symptoms for Issues Log

- Agent outcomes classified as "unknown" despite successful runs → check if `--output-format json` is enabled; the subtype string may be wrong
- `claimed_by: None` on claimed tasks → likely lease expiry from laptop sleep; investigate server-side claim clearing
- Agent runs 51 turns but max_turns is 200 → possible laptop sleep causing session timeout or API disconnect
