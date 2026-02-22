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

## Audit: Current Filesystem Task References

### How it works today

`create_task()` in `orchestrator/tasks.py:473-586` does two things:
1. Writes markdown to `.octopoid/tasks/TASK-{id}.md` (line 542)
2. POSTs metadata to server via `sdk.tasks.create()` (lines 556-574)

The server gets: id, title, role, priority, branch, queue, flow, hooks, and a `metadata` dict (created_by, blocked_by, project_id, checks, breakdown_depth). The server does NOT get the full markdown body (context + acceptance criteria as prose).

The file gets: everything — the full markdown body is the agent's instruction set.

### Where task files are read (6 locations)

| Location | What it does |
|---|---|
| `orchestrator/tasks.py:80` (`claim_task`) | Reads file content into `task["content"]` when claiming |
| `orchestrator/tasks.py:453` (`find_task_by_id`) | Same pattern — populates `content` from file |
| `orchestrator/scheduler.py:243-295` (`guard_task_description_nonempty`) | Validates content exists, fails task if file missing/empty |
| `orchestrator/scheduler.py:895` (`prepare_task_directory`) | Substitutes `$task_content` into agent prompt template |
| `packages/dashboard/widgets/task_detail.py:74` | Reads file for "Desc" tab display |
| `orchestrator/reports.py:639` (`_extract_title_from_file`) | Fallback title extraction from file |

### Where task files are written (1 location)

Only `create_task()` writes task files. Called from:
- `scripts/create_task.py` (CLI wrapper)
- Integration tests (35+ occurrences mock `get_tasks_file_dir()`)

### Path infrastructure

- `orchestrator/config.py:120-128` — `get_tasks_file_dir()` returns `.octopoid/tasks/`, creates dir
- `orchestrator/init.py:85-86` — creates the directory on init
- `.gitignore:68` — `.octopoid/tasks/` is gitignored (files are ephemeral)

### Data duplication

These fields exist in BOTH the file and server metadata:
- `created_by`, `blocked_by`, `project_id`, `checks`, `breakdown_depth`

The only data that's file-only is the **full markdown body** (the `## Context` and `## Acceptance Criteria` sections). This is the critical gap — the server doesn't store the prose content.

### Documentation referencing task files

- `CLAUDE.md` — "Always use `create_task()`... handles file placement"
- `CLAUDE.md` — "Always write the task file BEFORE changing task state"
- `README.md:578` — describes task file creation
- `.claude/commands/enqueue.md` — references file location
- `CHANGELOG.md` — multiple entries about task file handling

## What needs to change

### Server side (octopoid-server)
1. Add `content` text field to tasks table — stores the full markdown body
2. Accept `content` in POST/PATCH endpoints

### Orchestrator side
1. **`create_task()`** — POST content to server, stop writing file
2. **`claim_task()`** — read content from server response instead of disk
3. **`find_task_by_id()`** — same, drop file read
4. **`guard_task_description_nonempty()`** — check `task["content"]` from server, remove file existence checks
5. **`prepare_task_directory()`** — no change needed (already uses `task.get("content")`)
6. **`get_tasks_file_dir()`** — delete function
7. **`orchestrator/init.py`** — stop creating `.octopoid/tasks/` directory

### Dashboard
1. **`task_detail.py:74`** — read from task dict (fetched via SDK) instead of filesystem
2. **`reports.py:639`** — title already on server, remove file fallback

### Tests
- 35+ integration test files reference `file_path` patterns — update to use server content
- Remove all `get_tasks_file_dir()` mocking

### Documentation
- Remove "write file BEFORE changing state" rule from CLAUDE.md
- Remove `create_task()` file placement docs
- Update README task creation description

## Open Questions

- Does the server schema need a single `content` text field (the full markdown), or structured fields (context + acceptance_criteria separately)?
- Should `.octopoid/tasks/` disappear entirely or stick around as an optional cache?
- Does this affect draft files too? Drafts have the same pattern (server metadata + local markdown content)
- Migration: do we need to backfill content for existing tasks, or just start fresh?

## Possible Next Steps

1. Add `content` field to server tasks table
2. Modify `create_task()` to POST content to server
3. Modify `claim_task()` / `find_task_by_id()` to read content from server response
4. Update dashboard to read from SDK
5. Remove `.octopoid/tasks/` directory and all file-management code
6. Update CLAUDE.md rules and documentation
7. Update integration tests
