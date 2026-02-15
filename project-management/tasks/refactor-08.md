# refactor-08: Create gatekeeper agent directory template

ROLE: implement
PRIORITY: P2
BRANCH: feature/client-server-architecture
CREATED: 2026-02-15T00:00:00Z
CREATED_BY: human
SKIP_PR: true

## Context

The gatekeeper is an LLM agent that reviews provisional tasks (PRs) using a combination of automated scripts (Phase 1) and LLM reasoning (Phase 2). Currently the gatekeeper role is implemented in `packages/client/src/roles/gatekeeper.ts` with no agent directory structure.

This task creates the gatekeeper template directory in `packages/client/agents/gatekeeper/`, following the same structure as the implementer directory (refactor-07).

Reference:
- `project-management/drafts/9-2026-02-15-agent-directories.md` -- agent directories design
- `project-management/drafts/7-2026-02-15-sanity-check-gatekeeper.md` -- gatekeeper design with scripted checks

## What to do

Create the directory `packages/client/agents/gatekeeper/` with the following files:

### 1. `agent.yaml`

```yaml
role: gatekeeper
model: sonnet
max_turns: 100
interval_seconds: 120
spawn_mode: scripts
lightweight: false
allowed_tools:
  - Read
  - Glob
  - Grep
  - Bash
```

Note: gatekeeper doesn't need Write/Edit (it reviews, doesn't modify code). Lower max_turns than implementer since reviews are faster.

### 2. `prompt.md`

Create a gatekeeper prompt template. Use `$variable` substitution matching the pattern in the implementer's prompt.md. The prompt should instruct the agent to:

1. Read the task description and acceptance criteria
2. Check out the PR branch
3. Run automated checks (using the scripts in `$scripts_dir/`)
4. Review the diff against the task requirements
5. Post findings as a PR comment
6. Decide: approve (call finish) or reject (call fail with reason)

Template variables to use:
- `$task_id` -- the task being reviewed
- `$task_title` -- task title
- `$task_content` -- full task description with acceptance criteria
- `$scripts_dir` -- path to the scripts directory
- `$global_instructions` -- global project instructions
- `$pr_number` -- PR number (if available in task metadata)
- `$pr_branch` -- PR branch name

Include the lifecycle rules from the gatekeeper design doc:
- Use `approve_and_merge(task_id)` to approve, never raw queue updates
- Never delete branches
- Post rejection feedback as PR comment AND in task body
- Post review summary comment on PR before approving

### 3. `instructions.md`

Create review guidelines covering:

- **What to check:**
  - Do changes match the task's acceptance criteria?
  - Do tests pass?
  - Is the scope appropriate (no unnecessary changes)?
  - Is there leftover debug code?
  - Is the diff size reasonable?

- **How to report findings:**
  - Use the markdown checklist format from the design doc (automated checks + review summary + decision)
  - Be specific about failures (line numbers, file names)
  - Distinguish between blocking issues and advisory notes

- **Decision criteria:**
  - Auto-reject on test failures (unless clearly flaky)
  - Advisory on scope issues (CHANGELOG/README edits when not required)
  - Advisory on debug code (console.log, print, TODO)
  - Approve if acceptance criteria are met and no blocking issues

### 4. `scripts/` directory

Create the following scripts. These are NEW scripts (not copies from agent_scripts). Each should be a bash script that:
- Sources `../env.sh` for environment variables
- Performs its check
- Outputs results to stdout
- Exits 0 on success, non-zero on failure

Scripts to create:

#### `run-tests`
Run the project test suite on the PR branch. Should:
- Detect test runner (pytest, npm test, jest, etc.)
- Run tests
- Report pass/fail count
- Exit 0 if all pass, 1 if any fail

#### `check-scope`
Flag out-of-scope changes. Should:
- Get the diff (`git diff main..HEAD`)
- Check for changes to CHANGELOG.md, README.md, or other documentation files
- Flag files that seem unrelated to the task
- Exit 0 always (advisory only), output findings

#### `check-debug-code`
Find leftover debug code in the diff. Should:
- Get the diff
- Search for: `console.log`, `print(`, `debugger`, `TODO`, `FIXME`, `HACK`, `XXX`
- Report findings with file and line context
- Exit 0 always (advisory only), output findings

#### `post-review`
Post review findings as a PR comment. Should:
- Take findings text from stdin or as argument
- Use `gh pr comment <number> --body "..."` to post
- Exit 0 on success, 1 on failure

#### `diff-stats`
Report diff statistics. Should:
- Run `git diff --stat main..HEAD`
- Count files changed, lines added, lines removed
- Output formatted summary
- Exit 0 always (informational only)

### Directory structure

```
packages/client/agents/
  gatekeeper/
    agent.yaml
    prompt.md
    instructions.md
    scripts/
      run-tests
      check-scope
      check-debug-code
      post-review
      diff-stats
```

### Important notes

- Follow the same structural conventions as the implementer directory (refactor-07)
- Scripts should be generic -- work with any project, not octopoid-specific
- Make all scripts executable (`chmod 755`)
- Use Unix line endings (LF)

## Key files

- `packages/client/agents/gatekeeper/` -- directory to create (NEW)
- `packages/client/agents/implementer/` -- reference for structure (from refactor-07)
- `project-management/drafts/7-2026-02-15-sanity-check-gatekeeper.md` -- gatekeeper design
- `project-management/drafts/9-2026-02-15-agent-directories.md` -- agent directories design

## Acceptance criteria

- [ ] `packages/client/agents/gatekeeper/` directory exists with all files
- [ ] `agent.yaml` has correct config: role, model, max_turns, interval_seconds, spawn_mode, lightweight, allowed_tools
- [ ] `prompt.md` has a review-focused prompt template with $variable substitution
- [ ] `instructions.md` has review guidelines (what to check, how to report, decision criteria)
- [ ] `scripts/` has all 5 scripts: run-tests, check-scope, check-debug-code, post-review, diff-stats
- [ ] All scripts are executable
- [ ] Scripts are generic (work with any project)
- [ ] Follows same structure as implementer directory
- [ ] All existing tests pass
