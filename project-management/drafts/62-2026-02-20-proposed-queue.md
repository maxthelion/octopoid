# Proposed Queue: Parking Lot for Not-Yet-Ready Tasks

**Status:** Idea
**Captured:** 2026-02-20

## Raw

> a new queue for proposed tasks. These are tasks that we want to create but we're not ready to enqueue. Other agents might also put tasks here, such as refactoring suggestions

## Idea

A new queue (or pseudo-queue) called `proposed` for tasks that exist as ideas but aren't ready to be worked on. Unlike `incoming` tasks which agents immediately claim, proposed tasks sit in a holding area until a human (or another process) promotes them to `incoming`.

Agents could also write to this queue — e.g. an implementer that notices a refactoring opportunity, or a gatekeeper that spots a pattern worth addressing. This gives agents a way to surface observations without creating work that immediately gets picked up.

## Context

Currently there's a binary choice: either a task is in the queue and agents will claim it, or it doesn't exist. There's no middle ground for "we know we want this eventually but not right now." Drafts serve a similar purpose for ideas, but they're not tasks — they can't be promoted into the queue without being rewritten as task files.

## Open Questions

- Is this a new server-side queue value (like `incoming`, `in_progress`, `provisional`, `done`, `failed`) or something else?
- Should proposed tasks have the same schema as regular tasks, or a lighter-weight format?
- How does promotion work — manual only, or can flows/conditions auto-promote?
- How does this relate to drafts? Should drafts become proposed tasks, or are they separate concepts?
- Should there be a limit on how many proposed tasks can accumulate?
- Do proposed tasks need priority/ordering?

## Possible Next Steps

- Define the queue name and where it fits in the flow engine (before `incoming`?)
- Add server support for the new queue value
- Build a simple promotion mechanism (human command or skill)
- Allow agents to create proposed tasks via `create_task()` with a `queue="proposed"` parameter
