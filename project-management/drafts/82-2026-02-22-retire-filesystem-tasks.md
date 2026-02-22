# Retire Filesystem Task Files — Store Task Content on Server

**Status:** Idea
**Captured:** 2026-02-22
**Related:** Draft 81 (message dispatcher), Draft 68 (actions as agent instructions), Draft 31 (agents as pure functions)

## Raw

> Retire filesystem tasks in favour of content being on server.

## Idea

Currently `create_task()` writes a markdown file to `.octopoid/tasks/TASK-{id}.md` AND registers the task on the server. The local file is the agent's instruction set — the scheduler reads it to build the agent's prompt. But the server already stores title, context, acceptance criteria, and all task metadata. The local file is redundant.

Retire the filesystem component: task content lives on the server, agents receive their instructions via the SDK, and `.octopoid/tasks/` goes away.

## Context

This came up while designing the message dispatcher (draft 81). Action agents spawned from inbox messages don't need task files — they receive the message content as their prompt. But if an action agent calls `create_task()`, it currently needs to write a file to disk. If task content is server-only, the action agent can be a pure function with no file writes, which means it can run read-only in the main repo with no worktree and no contamination risk.

More broadly, the local task file has been a source of bugs: file path mismatches between server and disk, agents reading stale files, the "always write the task file BEFORE changing task state" rule in CLAUDE.md. Making the server the single source of truth eliminates this class of problems.

## Open Questions

- Does the server schema need a `content` or `body` field for the full task description, or can the existing fields (title, context, acceptance_criteria) cover it?
- How does the scheduler build the agent prompt without a local file? Read from SDK at spawn time?
- What about offline/disconnected scenarios? (Probably not relevant — we're API-only now)
- Should `.octopoid/tasks/` become a cache directory that's regenerated from server state, or just disappear entirely?
- Does this affect draft files too? Drafts have a similar pattern (server metadata + local markdown)

## Possible Next Steps

- Add a `content` field to the server's tasks table (full markdown body)
- Modify `create_task()` to POST content to server instead of writing a file
- Modify the scheduler to read task content from SDK at spawn time
- Remove `.octopoid/tasks/` directory and related file-management code
- Update CLAUDE.md rules that reference task file ordering
