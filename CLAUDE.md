You may:
- Run destructive commands inside this repo
- Rewrite large sections of code if it simplifies the system
- Ignore backwards compatibility unless explicitly required


Save plans to the filesystem in project-management/drafts

Never `cd` into a directory that might be deleted (e.g. worktrees, temp dirs). Use absolute paths or subshells instead. The Bash tool persists CWD between commands, so if the directory is removed, every subsequent command will silently fail.

Use the `/pause-system` and `/pause-agent` skills to pause/unpause the orchestrator. Don't manually touch the PAUSE file.

For upgrading the local octopoid installation after code changes, see `docs/local-upgrade-guide.md`.

Read `docs/flows.md` for how the declarative flow system works — this is the core architecture for task transitions. All task state changes go through flows, not hardcoded logic.

## Scheduler and Python caching

The scheduler runs via launchd using `/opt/homebrew/bin/python3`. Python caches compiled bytecode in `__pycache__/` directories. After editing or replacing Python files in `orchestrator/`, clear the cache so the scheduler picks up changes:

```bash
find orchestrator -name '__pycache__' -type d -exec rm -rf {} +
```

If you skip this, the scheduler may keep running old code from stale `.pyc` files — even if the source file has been completely rewritten.

## Task & PR lifecycle rules

- **Always use `create_task()` from `orchestrator.tasks`** to create tasks. Never bypass it with raw `sdk.tasks.create()` or `requests.post()` calls. `create_task()` handles file placement (`.octopoid/tasks/TASK-{id}.md`), server registration with the correct `file_path`, and branch defaulting via `get_base_branch()`. Bypassing it causes file path mismatches that make agents fail.
- When manually approving a task, use `approve_and_merge(task_id)` from `orchestrator.queue_utils` — not raw `sdk.tasks.update(queue='done')`. This runs the `before_merge` hooks (merges PR, etc).
- When closing or merging PRs, never use `--delete-branch`. We may need to go back and check the branch later.
- When rejecting a task, post the rejection feedback as a comment on the PR (not just in the task body).
- **Always write the task file BEFORE changing task state** (reject, requeue, enqueue). The scheduler runs every 60s and agents claim tasks immediately. If you reject/requeue first, an agent may claim the task and read the old file before you've rewritten it.
- **Do NOT set BRANCH in task files** unless the task specifically needs a different branch. The system defaults to `repo.base_branch` from `.octopoid/config.yaml` (currently `main`). Hardcoding a branch in a task file overrides this and can cause agents to miss feature branch work.
- When rejecting a task, **rewrite the entire task file** to reflect only what remains to be done. Do not just prepend a rejection notice above the old description — the agent will follow the original instructions and ignore the notice. Remove or update any code examples, instructions, or acceptance criteria that contradict the rejection feedback. The task file should read as a clear, self-consistent set of instructions with no ambiguity.
- When approving a task, post a review summary comment on the PR before merging.

## Git hygiene

**Never use `git stash`.** Stashes are invisible — they don't appear in `git log`, `git status`, or branch history. A stashed fix is effectively lost. If you need to set aside work, commit it (even as a WIP commit on a throwaway branch). Commits are discoverable; stashes are not.

**Commit fixes immediately.** Don't leave fixes as uncommitted working-tree edits. Uncommitted changes are fragile — any stash, reset, or branch switch can lose them. A small standalone commit is always safer.

**Use `git reflog` and `git stash list` for forensics.** When changes seem to have disappeared — the history doesn't match expectations, or a fix that was "definitely made" isn't in the log — check reflog and stash list. Reflog tracks every HEAD movement including rebases and resets. Stash list reveals forgotten stashed work.

## Worktree rules

**Worktrees must always stay on detached HEAD.** Never call `git checkout <branch>` in a worktree. The agent creates a named branch only when it is ready to push (via `create_task_branch`). This prevents git from refusing to checkout a branch that is already checked out in another worktree.

## Fixing merge conflicts on PRs

When a PR has merge conflicts (mergeStateStatus: CONFLICTING or DIRTY), fix it in the task's worktree — not by cloning to /tmp.

1. Find the worktree: `.octopoid/runtime/tasks/<TASK-ID>/worktree`
2. Check the PR's base branch is correct: `gh pr view <N> --json baseRefName`. If it targets `main` instead of `feature/client-server-architecture`, fix it: `gh pr edit <N> --base feature/client-server-architecture`
3. Rebase in a subshell (never `cd` into worktrees):
   ```bash
   (cd .octopoid/runtime/tasks/<TASK-ID>/worktree && git fetch origin && git rebase origin/feature/client-server-architecture)
   ```
4. If conflicts, resolve them, then:
   ```bash
   (cd .octopoid/runtime/tasks/<TASK-ID>/worktree && git add <files> && GIT_EDITOR=true git rebase --continue)
   ```
5. Push from the worktree (its `origin` points to GitHub):
   ```bash
   (cd .octopoid/runtime/tasks/<TASK-ID>/worktree && git push origin HEAD --force-with-lease)
   ```
6. Verify: `gh pr view <N> --json mergeStateStatus`

## Investigating issues

- **Pull before investigating or writing tasks.** Before exploring the state of the codebase, diagnosing an issue, checking whether something is implemented, or creating a new task, run `git pull --recurse-submodules` to ensure you're looking at the latest code. Agents push to the remote constantly — without pulling first, you will make wrong conclusions about what exists and create duplicate work.
- Don't assume problems are known. When you encounter a systemic issue (e.g. a silent failure, a missing transition, a broken pipeline), always note it — either write a quick draft via `/draft-idea` or flag it to the user explicitly.
- Don't hand-wave with "the server didn't get the update" — investigate *why* and document the root cause or at least the symptoms.

## Plan verification rule

When a plan says something is "already done" or "already disabled", **verify it** before skipping the change. Read the actual file and confirm the current state matches the plan's claim. This rule exists because a plan once incorrectly stated `_gather_prs` was already set to `[]`, causing the implementing agent to skip the fix — the function kept running and burned 22k+ GitHub API calls/hour for days.

## Testing philosophy: outside-in

See `docs/testing.md` for the complete testing guide.

Prefer end-to-end tests with a real local server over mocked unit tests. The testing pyramid:

1. **Priority 1 — End-to-end:** Scheduler + real local server + real SDK. Test full lifecycles (create → claim → spawn → submit → accept). Use the `scoped_sdk` fixture for isolation.
2. **Priority 2 — Integration:** Real server, mocked spawn. API contract tests, flow transitions, migration correctness.
3. **Priority 3 — Unit:** Mocked dependencies. Only for pure logic (parsing, config merging, formatting) and edge cases that are hard to trigger end-to-end.

**Rules:**
- Use `scoped_sdk` (from `tests/integration/conftest.py`) for test isolation — each test gets its own scope on the local server.
- Only mock `get_sdk()` when you genuinely need to test behavior with specific return values (error paths, edge cases). Never mock just to avoid running the server.
- Integration tests run against `localhost:9787` (start with `tests/integration/bin/start-test-server.sh`). Never hit production from tests.
- When writing new tests, follow the patterns and templates in docs/testing.md.
