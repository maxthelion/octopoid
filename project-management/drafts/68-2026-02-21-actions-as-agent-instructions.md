---
**Processed:** 2026-02-22
**Mode:** human-guided
**Actions taken:**
- Most work was implemented then lost in batch revert (22f8488)
- Post-revert rebuilds covered: inbox tab (a40d1ed3), default draft actions (abeed963), messages API
- Enqueued TASK-cbe5f20d for remaining cleanup: re-delete actions.py, re-add action_data support
- Inbox processor covered separately by Draft 81 (message dispatcher)
**Outstanding items:** TASK-cbe5f20d (cleanup), Draft 81 (inbox processor)
---

# Actions as Agent Instructions, Not Python Functions

**Status:** Active
**Captured:** 2026-02-21
**Supersedes:** The handler registry approach from Draft 50

## Raw

> This isn't quite what I'd imagined. The functions are kind of brittle. The idea was more that agents were the main part of it. When an agent does a job, they can attach actions to an entity. This should have a description of what they've done, alongside a set of actions. These actions should have button text, and instructions. If the user selects the action, the instructions get put in an inbox for a worker to read. This should be a relatively generic system within scheduler -> see if there are any new messages. If there are, spawn agents with the instructions. The agents who create the actions can be instructed to describe which scripts can be used etc. Eg an agent could propose a draft, and then create actions for enqueing the work, or archiving that attach to it. The user responds, automatically putting instructions in the inbox. We need to get rid of the python functions completely.

## Idea

Actions are not hardcoded Python handler functions. They are **agent-generated proposals** attached to entities. The entire execution path is agent-driven:

1. **An agent does work** (e.g. a draft curator scans drafts). As part of its output, it attaches an action group to an entity. The action has:
   - **Description** — shown to the user. Explains what the agent found and why it's suggesting these options.
   - **Action data** — a JSON object containing button definitions. Each button has a label (displayed text) and a command (instructions for a worker agent).

   Example action data:
   ```json
   {
     "description": "Draft 50 has active work in PR #178 but the design has changed. The handler registry approach is being replaced by agent instructions (Draft 68).",
     "buttons": [
       {"label": "Archive", "command": "Set draft 50 status to superseded via the SDK. It has been replaced by draft 68."},
       {"label": "Enqueue remaining work", "command": "Create a task to implement the inbox processor from draft 68. Priority P1, role implement."}
     ]
   }
   ```

2. **User clicks a button** in the dashboard. The command from that button gets posted to an **inbox** (messages table), along with a reference to the entity and the action context.

3. **User types free text** via the "Other" option. The free text gets posted to the inbox too, but with context about the action it refers to (entity reference, the agent's description, the available options) so the worker has enough background.

4. **Scheduler picks up the message** — generic loop: check for new inbox messages, spawn a worker agent with the instructions. The worker reads the command/text and executes it.

5. **No Python registry needed** — the proposing agent writes the commands in natural language. The executing agent interprets them. The system is just message passing.

## Context

The current implementation (Draft 50 / PR #178) uses a `@register_action_handler` decorator mapping action types to Python functions. This is brittle — every new action type needs a new handler function, and the actions can only do what's been hardcoded. The user's vision is more like an actor model: agents propose, humans approve, agents execute — all through natural language instructions, not code.

## Key Design Differences from Current Implementation

| Current (Draft 50) | Proposed (this draft) |
|---|---|
| `@register_action_handler("archive_draft")` | Agent writes: "Set draft 50 status to superseded via SDK" |
| Fixed set of action types | Any action an agent can describe |
| Python functions execute actions | Worker agents execute instructions |
| `process_actions` scheduler job dispatches to registry | Generic "process inbox" job spawns agents |
| Action types must be pre-registered | New action types need zero code changes |

## What to Remove

- `orchestrator/actions.py` — the handler registry (`_HANDLER_REGISTRY`, `@register_action_handler`, `get_handler`)
- `process_actions` job in `orchestrator/jobs.py`
- `process_actions` entry in `.octopoid/jobs.yaml`

## What to Keep

- Server actions table — still stores proposals with entity_type, entity_id, label, status lifecycle
- SDK ActionsAPI — create/list/execute/complete/fail still useful
- Dashboard action buttons — still render from pending actions, still call execute
- Report integration — `_gather_drafts` fetching actions per draft

## What to Build

- **Action instructions field** — actions need an `instructions` text field (may already be in `description` or `payload`)
- **Inbox integration** — when action status → `execute_requested`, copy instructions into the messages/inbox system
- **Generic inbox processor** — scheduler job that checks for new inbox messages and spawns worker agents
- **Proposer agent pattern** — agents that scan entities and create actions with full instructions

## Inbox Integration — Messages Table

Use the **existing messages table** (Draft 34) as the inbox. The messages table already has `from_actor`, `to_actor`, `type`, and `content` fields — this maps naturally onto action commands.

### How actions flow through messages

1. **Agent attaches actions** to an entity. At the same time, it posts a message to the user inbox:
   - `to_actor: "human"`
   - `type: "action_proposal"`
   - `content`: JSON with entity reference, description, and action ID
   - This surfaces in the dashboard inbox tab as a notification: "Draft curator has suggestions for Draft 50"

2. **User clicks a button** in the dashboard (either from the inbox or the drafts action bar). The command gets posted as a message:
   - `to_actor: "worker"` (or a generic inbox actor)
   - `type: "action_command"`
   - `content`: JSON with the command text, entity reference, action ID, and the proposing agent's description for context

3. **User types free text** via an "Other" option. Same as above but:
   - `type: "action_freetext"`
   - `content`: JSON with the user's text, plus context (entity reference, the agent's description, the available button options) so the worker understands what the user is responding to

4. **User asks a question** about an action. Posts to inbox:
   - `to_actor: "worker"` (or the original proposing agent)
   - `type: "action_question"`
   - `content`: The question, with action context
   - Worker responds with a message back to `to_actor: "human"`

5. **Scheduler inbox processor** checks for unprocessed messages where `to_actor = "worker"`. Spawns an agent with the message content as instructions.

### Dashboard inbox tab

The inbox tab already shows three columns (Proposals, Messages, Drafts). Action proposals would appear in the Proposals column. When the user selects one, they see the description and action buttons — same UI pattern as the drafts action bar but accessible from the inbox too.

### Current state of messages infrastructure

- **Server**: Messages table and API endpoints are **live** (`POST /api/v1/messages`, `GET /api/v1/messages` with filters)
- **SDK**: `MessagesAPI` already built (`sdk.messages.create()`, `sdk.messages.list()`)
- **Dashboard**: Inbox tab exists but currently reads from file-based `message_utils.py` — needs to switch to server-based messages

No server-side prerequisites remain. The inbox processor and dashboard migration are the main work items.

## Open Questions

- What pipeline/flow do inbox-spawned workers use? Lightweight (no PR/gatekeeper) or standard?
- How does the proposing agent specify which tools/scripts the executing agent should use?
- Should there be a max instruction length or structure (e.g. acceptance criteria)?
- How should the inbox tab transition from file-based messages to server-based? Gradual or all-at-once?

## Possible Next Steps

1. ~~Implement the messages table (Draft 34)~~ — **done**, server has it
2. Update the actions table schema to ensure there's a `description` and `action_data` (JSON) field
3. Build a minimal inbox processor job (check messages where `to_actor="worker"` → spawn agent)
4. Create a test proposer agent (draft curator) that generates actions with real instructions
5. Add "Other" text input and question flow to the dashboard action bar
6. Remove the Python handler registry
