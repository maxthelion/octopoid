# Guard Against Spawning Agents for Empty Task Descriptions

**Status:** Idea
**Captured:** 2026-02-19

## Raw

> TASK-cd01c12d was enqueued with just a title ("Refactor scheduler to use poll endpoint + per-job intervals") but no task file on disk and no description. The scheduler claimed it, spawned an agent, and the agent correctly failed with "Task description is empty". This wastes an agent turn.

## What Happened

1. Task was created on the server with a title but the `.octopoid/tasks/TASK-cd01c12d.md` file was never written to disk
2. The scheduler claimed the task and built `prompt.md` — the `## Task Description` section was empty
3. The agent was spawned, read the empty prompt, and immediately failed
4. Task went to `failed` queue

## Where to Guard

Two layers:

1. **Scheduler spawn guard** (most important): Before spawning an agent, check that the prompt has a non-empty task description section. This catches all cases — missing files, empty files, server-only tasks without local content.

2. **Enqueue validation** (nice to have): When creating a task via `/enqueue` or SDK, validate that the task file exists and has content beyond the YAML frontmatter.

## Possible Next Steps

- Add a `guard_task_has_description` to the scheduler's guard chain that checks the built prompt for a non-empty description section
- If the guard fails, move the task to `failed` with a clear reason instead of spawning
