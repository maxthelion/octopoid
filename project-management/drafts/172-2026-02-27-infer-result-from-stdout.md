# Infer agent results from stdout instead of requiring result.json

**Status:** Ready to implement
**Captured:** 2026-02-27

## Decision

Replace `result.json` with stdout inference. The scheduler reads the agent's stdout after it exits and uses a role-specific haiku call to classify the outcome. Agents no longer need to write any structured output file.

## Context

We had a task (bd7c9a09) where the agent did 163 tool calls of correct implementation work, passed 731 tests, wrote a clear summary to stdout saying "All tasks are complete" — and then exited without writing `result.json`. The prompt mentions result.json 8 times. The agent had 37 turns remaining. It simply didn't follow through on the mechanical step.

Asking an LLM to reliably perform a specific mechanical action (write a specific file in a specific format to a specific path) after a long creative session is fighting against what LLMs are good at. The system should meet agents where they are — they communicate through natural language.

## Proposed flow

```
Agent does work → Agent exits → Scheduler reads stdout → Haiku infers result → processes result
```

Single path. No result.json. No fallback. The scheduler is responsible for interpreting the agent's natural language output.

## Feasibility test results

We tested this against all 283 task stdouts in our history, spawning haiku agents the same way the scheduler does (`claude -p <prompt> --model haiku`).

### Key insight: role-specific prompts are critical

Giving haiku context about what each agent type does — what "done" and "failed" look like for implementers vs gatekeepers — dramatically improves accuracy.

### Final results (role-specific prompts)

**Implementer agents (163 tasks):**

| Metric | Value |
|---|---|
| With ground truth | 148 |
| Correct | 148 |
| Wrong | 0 |
| Unknown | 5 (all without ground truth) |
| **Accuracy** | **100%** |

**Gatekeeper agents (120 tasks):**

| Metric | Value |
|---|---|
| With ground truth | 120 |
| Correct (approve/reject) | 120 |
| Wrong | 0 |
| Unknown | 0 |
| **Accuracy** | **100%** |

**Combined: 268/268 correct. Zero misclassifications.**

The 5 unknowns are genuinely unclassifiable: empty stdout (0 bytes) or only "Error: Reached max turns" with no work summary. These would route to `requires-intervention`.

### What made the difference

Early runs with a generic prompt scored 82.7%. The improvements:

1. **Role-specific prompting.** Telling haiku "this is an implementer agent that writes code and creates PRs" vs "this is a gatekeeper that reviews PRs and approves/rejects" lets it interpret stdout correctly.

2. **Explicit classification rules.** Short stdout that says "Done." or "task is complete" is sufficient — not every task produces a long summary. "Written result.json with failure" means failed, not done.

3. **Agent behaviour context.** "Agents are verbose — they describe obstacles they overcame. This does not mean they failed."

### Test progression

| Run | Approach | Accuracy |
|---|---|---|
| 1 | Generic prompt, conservative | 99.1% (but 102 unknowns) |
| 2 | Generic prompt, assertive | 82.7% (39 false negatives) |
| 3 | Role-specific prompts | **100%** (0 wrong, 5 unknowns) |

## How it would work

### 1. Agent exits

The agent process exits. The scheduler detects the exit via PID tracking, same as today.

### 2. Scheduler infers result from stdout

The scheduler reads `stdout.log` (last 2000 chars) and makes a role-specific haiku call:

```python
def infer_result_from_stdout(stdout_path: Path, agent_role: str) -> dict:
    """Use haiku to classify agent outcome from stdout."""
    stdout = stdout_path.read_text()
    if not stdout.strip():
        return {"outcome": "unknown", "reason": "empty stdout"}

    tail = stdout[-2000:]

    if agent_role in ("gatekeeper", "sanity-check-gatekeeper"):
        result = _infer_gatekeeper(tail)
    else:
        result = _infer_implementer(tail)

    return result
```

Implementer prompt classifies as `done` or `failed`. Gatekeeper prompt classifies as `approve` or `reject`.

### 3. Handle uncertain outcomes

If the inference returns `{"outcome": "unknown"}`, route to `requires-intervention` (draft #170).

## What changes

1. **Remove result.json from agent prompts.** No more "IMPORTANT: write result.json" repeated 8 times. Agents just do their work and describe what happened.

2. **Add `infer_result_from_stdout()` to the scheduler.** Called in `check_and_update_finished_agents()` after detecting a dead PID.

3. **Add `anthropic` to scheduler dependencies.** The scheduler needs to make haiku API calls. This is the biggest architectural change — the scheduler currently has no LLM dependency.

4. **Remove result.json parsing from `read_result_json()`.** Replace with the inference call.

5. **Update agent scripts.** Remove `scripts/finish` and `scripts/fail` that write result.json. Remove `RESULT_FILE` from env.sh.

## Benefits

- **Removes a fragile protocol step.** Agents no longer need to write a specific file in a specific format.
- **Meets LLMs where they are.** They communicate through natural language. The system interprets that.
- **Simplifies prompts.** Remove ~20 lines of result.json instructions from each prompt template.
- **Safe failure mode.** Uncertain classifications go to `requires-intervention`.
- **Cheap.** One haiku call per agent completion. ~2000 input tokens, ~50 output tokens. ~$0.001 per task.

## Risks

- **Adds an LLM dependency to the scheduler.** Currently pure Python. Mitigation: the call is small, fast, and only happens once per agent completion.
- **Stdout might be empty.** If the agent crashed early. Mitigation: route to `requires-intervention`.
- **Haiku API could be down.** Mitigation: retry with backoff, or fall back to `requires-intervention`.

## Relationship to other drafts

- **Draft #170** (requires-intervention): Unknown inference outcomes route to requires-intervention.
- **Draft #171** (actor model / messages): Longer-term architectural shift. This draft solves the immediate reliability problem.
