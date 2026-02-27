# Infer agent results from stdout instead of requiring result.json

**Status:** Idea
**Captured:** 2026-02-27

## Raw

> What if asking the LLM agent to write the result.json is expecting it to be too deterministic? What if we need to put the responsibility onto the scheduler to look at the stdout of agents that have completed and infer a result from them?

## Idea

Stop relying on agents to write `result.json` as a structured protocol step. Instead, the scheduler reads the agent's stdout after it exits and uses an LLM call to infer the outcome. The agent just does its work and talks naturally — the system figures out what happened.

This should be the only path — not a fallback. We either infer from stdout or we don't.

## Context

We just had a task (bd7c9a09) where the agent did 163 tool calls of correct implementation work, passed 731 tests, wrote a clear summary to stdout saying "All tasks are complete" — and then exited without writing `result.json`. The prompt mentions result.json 8 times. The agent had 37 turns remaining. It simply didn't follow through on the mechanical step.

This isn't a turn budget problem or a prompt clarity problem. The agent understood the task, did the work correctly, and communicated that it was done — just not through the expected file. Asking an LLM to reliably perform a specific mechanical action (write a specific file in a specific format to a specific path) after a long creative session is fighting against what LLMs are good at.

The current design treats agents as programs that must follow a protocol. But they're not programs — they're language models. They communicate through natural language. The system should meet them where they are.

## Current flow

```
Agent does work → Agent writes result.json → Scheduler reads file → processes result
                         ^
                    This step fails silently
                    when the agent forgets
```

## Proposed flow

```
Agent does work → Agent exits → Scheduler reads stdout → LLM infers result → processes result
                                                              ^
                                                    Single path for all agents.
                                                    No result.json required.
```

## How it would work

### 1. Agent exits

The agent process exits. The scheduler detects the exit via PID tracking, same as today.

### 2. Scheduler infers result from stdout

The scheduler reads the agent's `stdout.log` (last 2000 chars) and makes a haiku call to classify the outcome:

```python
def infer_result_from_stdout(stdout_path: Path, agent_role: str) -> dict:
    """Use haiku to classify agent outcome from stdout."""
    stdout = stdout_path.read_text()
    tail = stdout[-2000:]

    if agent_role == "gatekeeper":
        schema = '{"outcome": "done", "decision": "approve|reject", "comment": "..."}'
    else:
        schema = '{"outcome": "done|failed", "reason": "..."}'

    response = llm_call(
        model="haiku",
        system=(
            "You classify the outcome of an AI agent's work session. "
            "Read the agent's output and return a JSON object.\n\n"
            f"Return format: {schema}\n\n"
            "Rules:\n"
            "- If the agent describes completed work, passing tests, and a summary, "
            "the outcome is 'done' even if it also describes problems it encountered.\n"
            "- If the agent says it wrote result.json with a FAILURE reason, "
            "the outcome is 'failed'.\n"
            "- Agents are verbose — they describe obstacles they overcame. "
            "This does not mean they failed.\n"
            "- If you genuinely cannot determine the outcome, return "
            '{"outcome": "unknown"}.'
        ),
        prompt=f"Agent stdout (last 2000 chars):\n{tail}\n\nClassify the outcome.",
    )
    return json.loads(response)
```

### 3. Handle uncertain outcomes

If the inference returns `{"outcome": "unknown"}`, route to `requires-intervention` (draft #170). The fixer agent or a human can investigate.

## Feasibility test results

We tested this approach against 283 real task stdouts from our history, using haiku as the classifier.

### Run 1: Conservative (haiku chose "unknown" when unsure)

| Metric | Value |
|---|---|
| Tasks classified | 169 |
| Committed to a classification | 108 |
| Correct | 107 |
| Wrong | 1 |
| Said "unknown" | 102 |
| **Accuracy on committed classifications** | **99.1%** |

When haiku was confident enough to commit, it was almost always right. The one error: a gatekeeper approval misread as failed because the stdout mentioned "Exception" in context.

### Run 2: Assertive (pushed haiku to always commit)

| Metric | Value |
|---|---|
| Tasks classified | 283 |
| Correct outcome | 196 |
| Wrong outcome | 41 |
| Unknown | 9 |
| **Outcome accuracy** | **82.7%** |

Error breakdown:

| Error type | Count | Risk |
|---|---|---|
| False done (said done, actually failed) | 2 | **Dangerous** — would wrongly close a failed task |
| False failed (said failed, actually done) | 39 | **Safe** — routes to intervention, no data loss |
| Decision mismatch (outcome correct) | 24 | **Minor** — gatekeeper approve/reject confused |
| Unknown | 9 | **Safe** — routes to intervention |

### Key findings

1. **When haiku is confident, it's 99.1% accurate.** The conservative mode is extremely reliable.

2. **The error profile is safe.** Of 41 wrong outcomes, 39 are false negatives (said "failed" when actually done). These would route to `requires-intervention`, not cause data loss. Only 2 false positives across 283 tasks.

3. **The 2 false positives are fixable.** Both cases: agent described why it failed in detail, then said "I've written result.json with failure reason." Haiku read the detailed description as success. Prompt tuning ("if the agent says it wrote a failure result, classify as failed") would catch these.

4. **False negatives are haiku being pessimistic.** Agents describe problems they overcame before summarising success. Haiku read the problem descriptions as failure. Prompt tuning ("agents are verbose about obstacles — this doesn't mean they failed") would reduce these.

5. **All outcomes are clearly present in stdout.** Every task we examined — implementer done, implementer failed, gatekeeper approve, gatekeeper reject — had clear natural language indicators in the stdout. The signal is there; the question is just prompt quality.

### Recommended approach

Use haiku with a **confidence threshold**:
- If haiku is confident → use the classification
- If haiku is uncertain → route to `requires-intervention`

This gives the 99.1% accuracy of the conservative mode while still classifying the majority of tasks. The `requires-intervention` queue (draft #170) provides the safety net.

## Benefits

- **Removes a fragile protocol step.** Agents no longer need to write a specific file in a specific format. They just do their work.
- **Meets LLMs where they are.** Agents communicate through natural language. The system interprets that text.
- **Safe failure mode.** Uncertain classifications go to `requires-intervention`, not to `done` or `failed`.
- **Cheap.** One haiku call per agent completion. ~2000 input tokens, ~50 output tokens.
- **Removes prompt complexity.** No more "IMPORTANT: write result.json" repeated 8 times in the prompt.

## Risks

- **Adds an LLM dependency to the scheduler.** Currently the scheduler is pure Python with no LLM calls. This is an architectural change.
- **Stdout might be empty or unhelpful.** If the agent crashed early or Claude Code itself errored, stdout may have no useful signal. Mitigation: route to `requires-intervention`.
- **Cost per agent.** One haiku call per completion adds ~$0.001 per task. Negligible.

## Additional signals beyond stdout

The inference could also incorporate:
- **Worktree state** — are there uncommitted changes? New commits? Do tests pass?
- **Tool counter** — how many tool calls were made?
- **stderr** — any error messages? "Reached max turns"?
- **Git diff** — does the diff match the acceptance criteria?

These heuristics could supplement the LLM call for edge cases.

## Relationship to other drafts

- **Draft #170** (requires-intervention): The "uncertain" inference outcome maps perfectly to requires-intervention — if the system can't tell whether the agent succeeded, send it to the fixer.
- **Draft #171** (actor model / messages): Both drafts are about improving how agents communicate results. This draft solves the immediate reliability problem; draft #171 is the longer-term architectural shift.
- **Draft #172 supersedes the need for prompt improvements** — rather than making the prompt louder about result.json, accept that agents won't always follow mechanical instructions and build resilience into the scheduler.

## Open Questions

- Should the inference LLM call run synchronously in the scheduler tick, or be queued as a lightweight job?
- Should we also infer results for agents that DID write result.json but wrote something malformed?
- What confidence signal should haiku use? (e.g. return a confidence score, or just use "unknown" as the low-confidence indicator)
- Should the prompt be agent-role-specific (implementer vs gatekeeper vs fixer)?
