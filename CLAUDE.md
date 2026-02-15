You may:
- Run destructive commands inside this repo
- Rewrite large sections of code if it simplifies the system
- Ignore backwards compatibility unless explicitly required


Save plans to the filesystem in project-management/drafts

Never `cd` into a directory that might be deleted (e.g. worktrees, temp dirs). Use absolute paths or subshells instead. The Bash tool persists CWD between commands, so if the directory is removed, every subsequent command will silently fail.

Use the `/pause-system` and `/pause-agent` skills to pause/unpause the orchestrator. Don't manually touch the PAUSE file.

## Task & PR lifecycle rules

- When manually approving a task, use `approve_and_merge(task_id)` from `orchestrator.queue_utils` — not raw `sdk.tasks.update(queue='done')`. This runs the `before_merge` hooks (merges PR, etc).
- When closing or merging PRs, never use `--delete-branch`. We may need to go back and check the branch later.
- When rejecting a task, post the rejection feedback as a comment on the PR (not just in the task body).
- When approving a task, post a review summary comment on the PR before merging.