# Unify Result Handlers: One Handler, One Format, Flow-Driven Dispatch

**Status:** Idea
**Captured:** 2026-02-18
**Related:** Draft 20 (Flows as Single Integration Path), Draft 34 (Messages Table), Draft 41 (Remove Hook Manager)

## Problem

There are currently **three** overlapping mechanisms that process agent results:

### 1. `handle_agent_result` (scheduler.py ~line 1110)
For implementers. Reads `result.json` with format `{"outcome": "done"}`. Hardcodes:
- `if outcome == "done" and current_queue == "claimed"` → run flow steps
- `if outcome == "failed"` → move to failed queue
- `if outcome == "needs_continuation"` → move to needs_continuation

### 2. `handle_agent_result_via_flow` (scheduler.py ~line 1019)
For gatekeeper. Reads `result.json` with format `{"status": "success", "decision": "approve"}`. Hardcodes:
- `if status == "failure"` → find on_fail in flow, reject
- `if decision == "reject"` → reject with feedback
- `if decision == "approve"` → run transition.runs

### 3. `process_orchestrator_hooks` (scheduler.py ~line 1210)
Legacy hook manager. Runs `before_merge` hooks on provisional tasks independently of flows.

The router in `check_and_update_finished_agents` picks between handler 1 and 2 based on `claim_from != "incoming"`. Three separate functions, two result formats, and the flow isn't actually driving the dispatch — it's just used as a lookup table for which steps to run.

## What's wrong

- **Two result formats**: `{"outcome": "done"}` vs `{"status": "success", "decision": "approve"}`. Adding a new agent type means choosing which format to use and which handler to route to.
- **Flow isn't driving dispatch**: Both handlers hardcode the transition logic. The flow YAML declares transitions, but the handlers contain the actual if/else logic for what to do. This defeats the purpose of declarative flows.
- **Three paths for the same concept**: "Agent finished, now what?" shouldn't need three functions.
- **Hook manager runs independently**: `process_orchestrator_hooks` polls provisional tasks on every scheduler tick, completely separate from flow transitions. This is Draft 41's issue.

## Solution: One handler, flow-driven

### Unified result format

```json
{"outcome": "success"}
{"outcome": "success", "decision": "approve", "comment": "LGTM"}
{"outcome": "success", "decision": "reject", "comment": "Tests fail"}
{"outcome": "failure", "reason": "Could not complete review"}
{"outcome": "needs_continuation"}
```

One format. `outcome` is always present. `decision` is optional — only agents acting as conditions (gatekeeper) include it.

### One handler

```python
def handle_agent_result(task_id: str, agent_name: str, task_dir: Path) -> None:
    result = read_result_json(task_dir)
    task = sdk.tasks.get(task_id)
    flow = load_flow(task.get("flow", "default"))
    transition = flow.get_transition_from(task["queue"])

    match result["outcome"]:
        case "failure":
            target = find_on_fail(transition) or "failed"
            sdk.tasks.update(task_id, queue=target)

        case "needs_continuation":
            sdk.tasks.update(task_id, queue="needs_continuation")

        case "success":
            match result.get("decision"):
                case "reject":
                    target = find_on_fail(transition) or "incoming"
                    reject_with_feedback(task, result, task_dir)

                case "approve" | None:
                    # None = implementer (no decision needed)
                    # "approve" = gatekeeper approving
                    execute_steps(transition.runs, task, result, task_dir)
```

The flow declares the transitions. The handler reads the result and follows the flow. No role-specific branching.

### `on_fail` comes from the flow

The flow already declares `on_fail` on conditions. The handler uses it:

```yaml
"provisional -> done":
  conditions:
    - name: gatekeeper_review
      type: agent
      agent: gatekeeper
      on_fail: incoming      # ← handler reads this
  runs: [post_review_comment, merge_pr]
```

If the agent rejects or fails, the handler follows `on_fail`. If no `on_fail` is declared, it defaults to `failed` queue for failures, `incoming` for rejections.

## With messages as a first-class citizen

If we had the messages table from Draft 34, this becomes even cleaner.

### Current: file-based, polled

```
Agent writes result.json → dies
Scheduler polls PID → finds dead → reads result.json → dispatches
```

The scheduler is doing two things: detecting completion (poll PID) and processing the result (read file, dispatch). These are entangled. The result lives on disk, prone to stale file bugs.

### With messages: event-driven

```
Agent posts message → {type: "result", outcome: "success", ...}
Scheduler receives message → dispatches via flow
```

The handler becomes a **message handler** — a pure function that takes a message and a flow, and returns a list of effects (state transitions, steps to run):

```python
def handle_result_message(message: Message, task: Task, flow: Flow) -> list[Effect]:
    transition = flow.get_transition_from(task.queue)

    match message.outcome:
        case "failure":
            return [MoveTo(find_on_fail(transition) or "failed")]

        case "needs_continuation":
            return [MoveTo("needs_continuation")]

        case "success" if message.decision == "reject":
            return [
                RejectWithFeedback(message.comment),
                MoveTo(find_on_fail(transition) or "incoming"),
            ]

        case "success":
            return [RunSteps(transition.runs)]
```

This is a **pure function** — no SDK calls, no side effects. It takes data in, returns effects out. The scheduler applies the effects. Testable with no mocks.

### What messages unlock for this specific problem

1. **No PID polling**: The scheduler doesn't need to check if agents are alive. Agents post a message when done. If an agent crashes without posting, a lease timeout fires and posts a failure message on its behalf.

2. **No stale result.json**: Results are messages, not files. No disk state to get out of sync.

3. **Mid-flight feedback without restart**: The gatekeeper could reject with feedback and the implementer could fix it in the same session — no requeue, no context loss. The flow's `on_fail` becomes `on_fail: retry_with_feedback` instead of `on_fail: incoming`.

4. **Unified audit trail**: Every result, every rejection, every state transition is a message. "What happened to this task?" is one query, not five.

5. **Handler becomes truly pure**: `handle_result_message` takes a message and returns effects. No SDK, no file I/O, no process management. The scheduler loop is just: receive messages → compute effects → apply effects.

### The flow YAML would look the same

Messages don't change the flow format. They change the *transport* — how the result gets from agent to scheduler. The flow still declares transitions, conditions, steps, and on_fail targets.

## Phasing

### Phase 1: Unify handlers (no messages needed)
- Merge `handle_agent_result` and `handle_agent_result_via_flow` into one function
- Standardize on one result format (`outcome` + optional `decision`)
- Handler reads `on_fail` from flow instead of hardcoding
- Delete `process_orchestrator_hooks` (Draft 41)

### Phase 2: Return effects instead of executing them
- Handler returns `list[Effect]` instead of calling SDK directly
- Scheduler applies effects
- Handler becomes a pure function — trivially testable

### Phase 3: Messages replace result.json (Draft 34)
- Agent posts result as a message to the server
- Scheduler processes messages instead of polling PIDs + reading files
- Lease timeout posts failure message if agent crashes
- Handler is now: `Message → Flow → list[Effect]`

## Open Questions

- Should `needs_continuation` be a flow concept (e.g. `on_incomplete: needs_continuation` on transitions) or stay as a special outcome?
- For Phase 2, what's the `Effect` type? Something like `MoveTo(queue)`, `RunSteps(steps)`, `RejectWithFeedback(comment)`, `PostMessage(content)`?
- In Phase 3, does the agent need the SDK to post messages? Currently agents are pure (no SDK). Could use a lightweight HTTP POST instead, or the finish script could post the message.
