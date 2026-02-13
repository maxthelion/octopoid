# Octopoid — Global Agent Instructions

You are working on **Octopoid**, a distributed AI orchestration system for software development. It uses Claude AI agents to implement features, fix bugs, and manage development workflows automatically.

## Architecture

- **Server:** Cloudflare Workers + D1 (SQLite at the edge) — REST API for tasks, projects, orchestrators
- **Orchestrator:** Python — scheduler, agents, worktrees, SDK client
- **SDK:** `octopoid-sdk` Python package — client for the server API
- **Config:** `.octopoid/config.yaml` — single source of truth for all components

## After Every Change

1. **CHANGELOG.md** — Add an entry under `## [Unreleased]` in the appropriate category (Added, Changed, Fixed, etc.). Keep it concise and user-focused.

2. **README.md** — Update if your change affects user-facing behaviour: setup steps, configuration options, CLI commands, architecture, or supported features.

3. **Comment on the GitHub issue** — If this task originated from a GitHub issue (look for `github_issue` in the task frontmatter, or a `[GH-<number>]` prefix in the title), comment on the issue when you're done. Use `gh issue comment <number> --body "..."` and describe what you changed and why. Keep it brief but specific — this is how the issue reporter knows what happened.

## Code Conventions

- **Python:** Use type hints on all function signatures. Follow existing patterns in the file you're editing.
- **Tests:** If you add or change a function, add or update tests. Use `pytest`. Test files live in `tests/`.
- **Commits:** Make atomic commits — one logical change per commit. Write clear commit messages.
- **Imports:** Keep imports sorted (stdlib, third-party, local). Use relative imports within the `orchestrator` package.
- **Error handling:** Don't swallow exceptions silently. Log errors with `self.log()` or `print()` as appropriate.
- **No secrets:** Never commit API keys, tokens, or credentials. Use environment variables.
