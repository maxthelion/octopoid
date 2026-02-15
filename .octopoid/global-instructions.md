# Octopoid — Global Agent Instructions

You are working on **Octopoid**, a distributed AI orchestration system for software development. It uses Claude AI agents to implement features, fix bugs, and manage development workflows automatically.

## Architecture

- **Server:** Cloudflare Workers + D1 (SQLite at the edge) — REST API for tasks, projects, orchestrators
- **Orchestrator:** Python — scheduler, agents, worktrees, SDK client
- **SDK:** `octopoid-sdk` Python package — client for the server API
- **Config:** `.octopoid/config.yaml` — single source of truth for all components

## When to STOP and FAIL

If any of the following are true, do NOT improvise. Call `../scripts/fail` with a clear explanation:

- **Files don't exist:** The task tells you to edit a specific file or function, but it doesn't exist on this branch. Do not search for similar code elsewhere and edit that instead.
- **Contradictory instructions:** The task description contradicts itself (e.g. "add X" but also "do not modify the file where X would go").
- **Missing dependencies:** The task assumes code, APIs, or infrastructure that isn't present.
- **Scope mismatch:** The task describes changes to 5+ files but says "only modify one file", or vice versa.

Failing with a clear reason is far more useful than delivering the wrong change. The task will be re-examined and rewritten.

## After Every Change

1. **CHANGELOG.md** — Add an entry under `## [Unreleased]` in the appropriate category — UNLESS the task description explicitly says not to.

2. **README.md** — Update if your change affects user-facing behaviour — UNLESS the task description explicitly says not to.

3. **Comment on the GitHub issue** — If this task originated from a GitHub issue (look for `github_issue` in the task frontmatter, or a `[GH-<number>]` prefix in the title), comment on the issue when you're done. Use `gh issue comment <number> --body "..."` and describe what you changed and why. Keep it brief but specific — this is how the issue reporter knows what happened.

**Note:** Task-level instructions always override these global defaults. If a task says "do NOT update CHANGELOG", obey the task.

## MANDATORY: Use the Provided Scripts

Never run `gh pr create`, `gh pr merge`, `git push`, or equivalent commands directly. Always use the scripts in `../scripts/`:

- **`../scripts/submit-pr`** — Push and create a PR (handles base branch targeting, evidence recording, result tracking)
- **`../scripts/run-tests`** — Run the test suite and record results
- **`../scripts/finish`** — Mark the task as complete
- **`../scripts/fail <reason>`** — Mark the task as failed
- **`../scripts/record-progress <note>`** — Save progress context

These scripts read environment variables (like `BASE_BRANCH`) that ensure PRs target the correct branch. Bypassing them causes PRs to target the wrong branch and skips evidence recording.

## Code Conventions

- **Python:** Use type hints on all function signatures. Follow existing patterns in the file you're editing.
- **Tests:** If you add or change a function, add or update tests. Use `pytest`. Test files live in `tests/`.
- **Commits:** Make atomic commits — one logical change per commit. Write clear commit messages.
- **Imports:** Keep imports sorted (stdlib, third-party, local). Use relative imports within the `orchestrator` package.
- **Error handling:** Don't swallow exceptions silently. Log errors with `self.log()` or `print()` as appropriate.
- **No secrets:** Never commit API keys, tokens, or credentials. Use environment variables.
