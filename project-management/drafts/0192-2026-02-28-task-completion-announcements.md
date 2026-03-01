# Task Completion Announcements to Trigger Follow-up Agents

**Captured:** 2026-02-28

## Raw

> scheduler should post an announcement message when a task is completed. This could be used to spawn other agents to do work. Particularly tidying up drafts, making sure invariants are updated etc.

## Idea

When the scheduler moves a task to `done`, it should post a structured announcement message (via `sdk.messages.create()`) declaring the completion. Other agents — or the message dispatcher — can watch for these announcements and trigger follow-up work automatically.

Use cases:
- **Draft processing:** When a task that was enqueued from a draft completes, a follow-up agent runs `/process-draft` to check if all tasks from that draft are done, verify invariants are met, and archive the draft.
- **Invariant verification:** A post-completion agent checks whether the completed task's acceptance criteria actually hold in the code (not just "agent said it's done").
- **Changelog/docs updates:** Trigger a lightweight agent to update docs or changelog entries after a merge lands.
- **Unblocking:** If task B is `blocked_by` task A, the announcement for A's completion triggers B to move to `incoming`.

This builds on the existing messages infrastructure (sdk.messages) and the message dispatcher (which already polls the inbox and spawns action agents). The announcement is just a new message type that the dispatcher knows how to route.

## Invariants

- **completion-announced**: Every task that transitions to `done` has a corresponding `task_completed` message posted by the scheduler
- **announcement-has-context**: The announcement message includes the task ID, title, linked draft ID (if any), and PR number (if any) — enough for follow-up agents to act without re-fetching

## Context

Currently when a task completes, the scheduler marks it done and moves on. Any follow-up work (processing drafts, verifying invariants, updating system-spec.yaml) relies on a human noticing and running `/process-draft` manually. This creates a gap where tasks are "done" but their broader effects haven't been propagated.

The message dispatcher already exists and can route messages to agents. Adding a `task_completed` announcement type would let it trigger post-completion workflows automatically.

## Open Questions

- Should the announcement trigger specific named agents, or should it be a generic event that any agent can subscribe to?
- Should the dispatcher handle routing (match announcement → agent), or should individual agents poll for announcements relevant to them?
- How do we prevent infinite loops? (task completes → triggers follow-up → follow-up creates task → that task completes → triggers follow-up...)
- Should this replace or complement the existing `runs:` steps in flow transitions? (e.g. `update_changelog` is currently a transition step — should it become a post-completion agent instead?)

## Possible Next Steps

- Add a `_post_completion_announcement()` call in the scheduler's done-transition path
- Define the `task_completed` message schema (task_id, title, draft_id, pr_number, flow, role)
- Add a message dispatcher rule that matches `task_completed` messages and spawns a draft-processing agent
- Create a lightweight "post-accept" agent that runs `/process-draft` for completed tasks linked to drafts
