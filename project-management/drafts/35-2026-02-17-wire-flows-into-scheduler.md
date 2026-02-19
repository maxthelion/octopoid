# Wire Flow Definitions Into Scheduler Execution

**Status:** In Progress
**Captured:** 2026-02-17
**Related:** Draft 17 (declarative task flows), Draft 31 (agents as pure functions), Draft 33 (roadmap)

## Raw

> The flow module exists with full data model (Condition, Transition, Flow classes, YAML loading, validation, default/project generators) but the scheduler doesn't import or reference it. Transitions are entirely hardcoded. The pure-function result handlers we're building will be more hardcoded if/else in the scheduler when they should be the `runs:` steps driven by flow config. The whole emphasis of the e2e testing strategy is based on flows, which aren't wired in.

## The Gap

`orchestrator/flow.py` defines:
- `Flow` — loaded from YAML, contains transitions
- `Transition` — `from → to` with `conditions` (gates) and `runs` (actions)
- `Condition` — script, agent, or manual gate
- `Flow.get_transitions_from(state)` — lookup transitions by current state
- Default flow YAML generator: `incoming→claimed→provisional→done`

`orchestrator/scheduler.py` has:
- Zero imports from `flow.py`
- Hardcoded spawn logic per agent type
- Hardcoded result handling: `if agent_role == "gatekeeper": handle_gatekeeper_result()`
- Hardcoded `claim_from` in agent config instead of reading from flow transitions

The flow module is dead code. The scheduler reimplements everything it defines.

## Base Level Integration

The scheduler needs to read flows to answer three questions it currently hardcodes:

| Question | Currently | Flow-driven |
|---|---|---|
| Which queue does this agent claim from? | `claim_from: provisional` in agents.yaml | Flow transition `from_state` where agent is referenced |
| What happens after agent success? | `if agent_role == "gatekeeper": ...` | Execute `transition.runs` from step registry |
| What happens after agent failure? | Hardcoded per-role logic | `condition.on_fail` state from the flow |

### What to build

1. **Step registry** (`orchestrator/steps.py`) — named functions extracted from existing code:
   - `post_review_comment(task, result, task_dir)` — from `handle_gatekeeper_result`
   - `merge_pr(task, result, task_dir)` — from `approve_and_merge`
   - `rewrite_task_with_feedback(task, result, task_dir)` — from rejection logic
   - `push_branch(task, result, task_dir)` — for implementer (future)
   - `run_tests(task, result, task_dir)` — for implementer (future)
   - `create_pr(task, result, task_dir)` — for implementer (future)
   - `submit_to_server(task, result, task_dir)` — for implementer (future)

   Only the gatekeeper steps need implementing now. Implementer steps are placeholders for TASK-2bf1ad9b.

2. **Default flow YAML** — create `.octopoid/flows/default.yaml` with gatekeeper condition:
   ```yaml
   name: default
   description: Standard implementation with review

   transitions:
     "incoming -> claimed":
       agent: implementer

     "claimed -> provisional":
       runs: [push_branch, run_tests, create_pr, submit_to_server]

     "provisional -> done":
       conditions:
         - name: gatekeeper_review
           type: agent
           agent: gatekeeper
           on_fail: incoming
       runs: [post_review_comment, merge_pr]
   ```

3. **Scheduler loads flow at result-handling time**:
   ```python
   def handle_agent_result(task_id, agent_name, task_dir):
       task = sdk.tasks.get(task_id)
       result = read_result_json(task_dir)
       flow = load_flow(task.get("flow", "default"))

       # Find the transition this agent was handling
       current_queue = task["queue"]
       transitions = flow.get_transitions_from(current_queue)

       if result["status"] == "success":
           for step_name in transition.runs:
               STEP_REGISTRY[step_name](task, result, task_dir)
       else:
           # Use on_fail from condition, or default to incoming
           ...
   ```

4. **Scheduler reads flow for claim queue** — instead of `claim_from` in agents.yaml, the flow defines which transition the agent handles, and `from_state` is the claim queue:
   ```python
   def get_claim_queue_for_role(role, flow):
       for transition in flow.transitions:
           for condition in transition.conditions:
               if condition.type == "agent" and condition.agent == role:
                   return transition.from_state
       return "incoming"  # default
   ```

### What this means for each agent

**Gatekeeper (now):** Flow says `provisional → done` has `agent: gatekeeper` condition. Scheduler claims from `provisional`, spawns gatekeeper, reads result, executes `[post_review_comment, merge_pr]` on approve or moves to `incoming` on reject.

**Implementer (TASK-2bf1ad9b, next):** Flow says `incoming → claimed` has `agent: implementer`. After success, `claimed → provisional` runs `[push_branch, run_tests, create_pr, submit_to_server]`. Just register the step functions — no new hardcoded branches.

## Open Questions

- Should flows be loaded once at scheduler start, or per-task? Tasks have a `flow` column that could reference different flows.
- The `condition.type == "agent"` vs `transition.agent` distinction — the default flow uses `agent:` on the transition for implementer but `conditions: [{type: agent}]` for gatekeeper. Should we standardize?
- Error handling in steps: if `create_pr` fails mid-pipeline, do we roll back or leave the task in a partial state?

## Possible Next Steps

- Enqueue as task, blocked by TASK-639ee879, blocking TASK-2bf1ad9b
- Gatekeeper is the proof — verify it works through flows
- Implementer conversion (TASK-2bf1ad9b) becomes "add steps to registry + flow YAML" instead of "write another hardcoded handler"
