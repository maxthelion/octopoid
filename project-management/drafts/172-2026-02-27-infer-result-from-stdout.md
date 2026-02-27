# Infer agent results from stdout instead of requiring result.json

**Status:** Idea
**Captured:** 2026-02-27

## Raw

> What if asking the LLM agent to write the result.json is expecting it to be too deterministic? What if we need to put the responsibility onto the scheduler to look at the stdout of agents that have completed and infer a result from them?

## Idea

Stop relying on agents to write `result.json` as a structured protocol step. Instead, the scheduler reads the agent's stdout after it exits and uses an LLM call to infer the outcome. The agent just does its work and talks naturally — the system figures out what happened.

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
                                                    Small, fast, deterministic call
                                                    (e.g. haiku summarising stdout)
```

## How it would work

### 1. Agent exits (with or without result.json)

The agent process exits. The scheduler detects the exit via PID tracking, same as today.

### 2. Scheduler checks for result.json first

If `result.json` exists, use it — backwards compatible. This is the fast path for agents that do write the file.

### 3. If no result.json, infer from stdout

The scheduler reads the agent's `stdout.log` (last N lines) and makes a small LLM call to classify the outcome:

```python
def infer_result_from_stdout(stdout_path: Path) -> dict:
    """Use a fast LLM to classify agent outcome from stdout."""
    stdout = stdout_path.read_text()
    # Take last 2000 chars — that's where the conclusion is
    tail = stdout[-2000:]

    response = llm_call(
        model="haiku",
        system="You classify the outcome of an AI agent's work session. "
               "Read the agent's output and return a JSON object with: "
               "outcome ('done' or 'failed') and a brief reason.",
        prompt=f"Agent stdout:\n{tail}\n\nWhat was the outcome?",
    )
    return json.loads(response)
```

This is a tiny, cheap call — haiku reading 2000 chars of stdout and returning a one-line JSON. It runs only when result.json is missing, which should be rare.

### 4. For gatekeepers, infer decision

Gatekeeper agents produce richer results (approve/reject + comment). The inference call would extract:

```json
{
  "status": "success",
  "decision": "approve",
  "comment": "<extracted from stdout>"
}
```

The gatekeeper's stdout already contains the review — the LLM just needs to classify the decision and extract the comment.

## Benefits

- **Resilient to agent forgetfulness.** The system works even when agents don't follow the protocol perfectly.
- **Meets LLMs where they are.** Agents communicate naturally through text. The system interprets that text.
- **Backwards compatible.** result.json is still checked first. The inference is a fallback.
- **Cheap.** One haiku call per missing result.json. Rare in practice.
- **Reduces prompt complexity.** The result.json instructions in the prompt become less critical. The agent can focus on the work.

## Risks

- **LLM inference can be wrong.** The haiku call might misclassify an outcome. Mitigation: conservative classification — if uncertain, default to `requires-intervention` rather than `done` or `failed`.
- **Adds an LLM dependency to the scheduler.** Currently the scheduler is pure Python with no LLM calls. This changes the architecture slightly. Mitigation: only fires when result.json is missing.
- **Stdout might be empty or unhelpful.** If the agent crashed early, stdout might not contain useful information. Mitigation: check for uncommitted work in the worktree as additional signal.

## Additional signals beyond stdout

The inference could also look at:
- **Worktree state** — are there uncommitted changes? New commits? Do tests pass?
- **Tool counter** — how many tool calls were made? An agent that used 163/200 turns and has committed code probably succeeded.
- **stderr** — any error messages?
- **Git diff** — does the diff match the acceptance criteria?

These heuristics could supplement or even replace the LLM call for common cases.

## Relationship to other drafts

- **Draft #170** (requires-intervention): The "uncertain" inference outcome maps perfectly to requires-intervention — if the system can't tell whether the agent succeeded, send it to the fixer.
- **Draft #171** (actor model / messages): Both drafts are about improving how agents communicate results. This draft solves the immediate reliability problem; draft #171 is the longer-term architectural shift.
- **Draft #172 supersedes the need for prompt improvements** — rather than making the prompt louder about result.json, accept that agents won't always follow mechanical instructions and build resilience into the scheduler.

## Open Questions

- Should the inference LLM call run synchronously in the scheduler tick, or be queued as a lightweight job?
- Should we also infer results for agents that DID write result.json but wrote something malformed?
- Could we skip the LLM entirely and use heuristics (uncommitted work + tests pass = done)?
