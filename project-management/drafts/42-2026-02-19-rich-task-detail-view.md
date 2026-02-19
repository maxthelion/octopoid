# Rich Task Detail View: Diff, Description, Logs, Result

**Status:** Idea
**Captured:** 2026-02-19

## Raw

> show more information in the task detail view of octopoid - things to consider: diff in their worktree, task description (including latest amendments such as rebasing), result json, task logs. maybe have the summary info as a panel along the top, then a split below with things to view on the left as a menu, and a section on the right for the details of those things

## Idea

Redesign the task detail overlay in `octopoid-dash.py` to show much more than the current metadata summary. The detail view should become a multi-panel layout:

- **Top panel**: Summary metadata (ID, title, agent, status, turns, priority) — compact, always visible
- **Left sidebar**: Menu/tab list for switching between content views
- **Right content area**: The selected view's content, scrollable

Content views to support:
1. **Diff** — `git diff` output from the task's worktree (`.octopoid/runtime/tasks/<id>/worktree`)
2. **Task description** — the current task file contents (including any amendments from rejection/requeue/rebase)
3. **Result JSON** — the agent's `result.json` output after submission
4. **Task logs** — agent execution logs for this task

## Context

Currently the task detail view only shows metadata (ID, title, role, priority, branch, agent, turns, commits, created date). When reviewing tasks you constantly need to shell out to check the diff, read the task file, or look at logs. Having this inline in the dashboard would make the review workflow much faster.

## Open Questions

- Where do task logs live? Need to identify the log path per task.
- Should the diff be a full `git diff` or `git diff --stat` with expandable files?
- How to handle large diffs / long logs in a curses window — virtual scrolling needed?
- Should the description view show the raw markdown or a rendered version?
- Result JSON location — is it in the worktree or the runtime dir?

## Possible Next Steps

- Audit current paths for worktree, result.json, and logs per task
- Prototype the split-panel layout in curses (top summary + left menu + right content)
- Add scrollable content rendering (curses pad or manual offset tracking)
- Wire up data loading for each content view
