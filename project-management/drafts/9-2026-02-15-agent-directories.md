# Agent Directories

## Problem

Agent configuration is scattered across the codebase:

- **What to run:** `agents.yaml` (model, interval, max_turns)
- **How to behave:** `orchestrator/prompts/implementer.md` (prompt template)
- **General guidance:** `commands/agent/implement.md` (implementation guidelines)
- **Scripts:** `orchestrator/agent_scripts/` (finish, fail, submit-pr, run-tests)
- **Role logic:** `orchestrator/roles/*.py` (Python classes, mostly gutted)
- **TS role logic:** `packages/client/src/roles/*.ts` (TypeScript implementations, different from Python)

To understand what an "implementer" is, you need to look in 4+ places. To add a new agent type, you need to touch all of them. And there's no clear separation between product agents (implementer, gatekeeper) and our self-management agents (github-issue-monitor).

## Proposal

Replace `agents.yaml` and the scattered files with a single `agents/` directory in the product (`packages/client/`). Each agent type gets its own directory containing everything it needs.

```
packages/client/agents/
  implementer/
    agent.yaml          # config: model, max_turns, allowed_tools, etc
    prompt.md           # the system prompt template
    instructions.md     # implementation guidelines (currently commands/agent/implement.md)
    scripts/
      submit-pr         # push branch, create PR
      run-tests          # detect and run test suite
      finish             # mark task complete
      fail               # mark task failed
      record-progress    # save progress note
  gatekeeper/
    agent.yaml
    prompt.md
    instructions.md
    scripts/
      check-tests        # run tests on PR branch
      check-scope        # flag out-of-scope changes
      post-review        # post findings as PR comment
  breakdown/
    agent.yaml
    prompt.md
    instructions.md
```

### agent.yaml

Replaces the per-agent entries in `agents.yaml`. Contains the agent type's defaults:

```yaml
# agents/implementer/agent.yaml
role: implementer
model: sonnet
max_turns: 200
interval_seconds: 60
allowed_tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - Skill
lightweight: false    # needs a worktree
```

### prompt.md

The full prompt template that gets rendered and passed to `claude -p`. Currently this is `orchestrator/prompts/implementer.md` with `$variable` substitution. Stays the same format, just moves here.

### instructions.md

General role guidance. Currently `commands/agent/implement.md`. This gets appended to the prompt or injected as context.

### scripts/

The scripts the agent can invoke. Currently in `orchestrator/agent_scripts/`. Each agent type can have its own scripts — a gatekeeper doesn't need `submit-pr`, an implementer doesn't need `post-review`.

## Instance Configuration

The `agents/` directory defines agent **types**. The fleet configuration (how many of each, overrides) stays in `.octopoid/agents.yaml` but becomes much simpler:

```yaml
# .octopoid/agents.yaml
paused: false

queue_limits:
  max_claimed: 3
  max_incoming: 20
  max_open_prs: 10

fleet:
  - name: implementer-1
    type: implementer        # references agents/implementer/
    enabled: true

  - name: implementer-2
    type: implementer
    enabled: true
    model: opus              # override the type default

  - name: gatekeeper-1
    type: gatekeeper
    enabled: true

  # Self-management agent — type defined locally, not in the product
  - name: github-issue-monitor
    type: custom
    path: .octopoid/agents/github-issue-monitor/
    enabled: true
    interval_seconds: 900
    lightweight: true
```

### Scaffolding

The agent directories in `packages/client/agents/` are **templates**. When you run `octopoid init`, they get copied to `.octopoid/agents/` in your project — just like `templates/config.yaml` gets copied to `.octopoid/config.yaml` today.

Once scaffolded, the user owns those files. They can:
- Edit the prompt to fit their codebase conventions
- Add project-specific scripts (e.g. a custom test runner)
- Change the model or max_turns
- Add entirely new agent types (like our github-issue-monitor)

The product ships the defaults. The project customises them.

```
packages/client/agents/         ← templates (shipped with octopoid)
  implementer/
  gatekeeper/
  breakdown/

.octopoid/agents/               ← scaffolded copy (owned by the project)
  implementer/                  ← customised for this repo
  gatekeeper/
  github-issue-monitor/         ← project-specific, not from template
```

This cleanly separates:
- **Product templates** (`packages/client/agents/`) — shipped defaults, copied on init
- **Project agents** (`.octopoid/agents/`) — the scaffolded, customisable copies
- **Fleet config** (`.octopoid/agents.yaml`) — how many of each, overrides

## What Moves Where

| Current Location | New Location | Notes |
|-----------------|-------------|-------|
| `agents.yaml` agent type config | `packages/client/agents/<type>/agent.yaml` | Model, turns, tools |
| `agents.yaml` fleet config | `.octopoid/agents.yaml` (simplified) | Names, counts, overrides |
| `orchestrator/prompts/implementer.md` | `packages/client/agents/implementer/prompt.md` | |
| `commands/agent/implement.md` | `packages/client/agents/implementer/instructions.md` | |
| `commands/agent/review.md` | `packages/client/agents/gatekeeper/instructions.md` | |
| `orchestrator/agent_scripts/*` | `packages/client/agents/implementer/scripts/` | Shared scripts copied to each type that needs them |
| `orchestrator/roles/github_issue_monitor.py` | `.octopoid/agents/github-issue-monitor/` | Self-management, not product |
| `orchestrator/roles/orchestrator_impl.py` | Delete or `.octopoid/agents/` | Self-management |
| `packages/client/src/roles/implementer.ts` | Scheduler uses agent dir directly | May not need TS role classes anymore |
| `packages/client/src/roles/gatekeeper.ts` | `packages/client/agents/gatekeeper/` | Logic moves to scripts + prompt |

## How the Scheduler Uses This

Currently `prepare_task_directory()` assembles a task directory by copying scripts and rendering a prompt. With agent directories, it becomes:

1. Look up agent type from fleet config
2. Find the agent directory (`packages/client/agents/<type>/` or custom path)
3. Copy `scripts/` into the task worktree
4. Render `prompt.md` with task variables
5. Append `instructions.md` as context
6. Invoke `claude -p` with the rendered prompt

The TS role classes (`packages/client/src/roles/*.ts`) may become unnecessary — they currently duplicate what the prompt + scripts already do. The scheduler just needs to know how to assemble and invoke, not implement each role in TypeScript.

## Open Questions

1. **Do we keep the TS role classes?** They have logic (claim task, create branch, etc) that the scripts also handle. Could be fully replaced by scripts + prompt, or kept as an alternative invocation mode.

2. **Script sharing:** Some scripts (finish, fail, record-progress) are common to all agents. Options:
   - Copy them into each agent directory (simple, some duplication)
   - Have a `shared/scripts/` directory and symlink or merge at runtime
   - Each agent.yaml lists which shared scripts to include

3. **Custom agent format for self-management agents:** The github-issue-monitor is a Python class, not a prompt+scripts agent. How does it fit? Options:
   - Custom agents can be either prompt-based or code-based
   - `agent.yaml` has a `mode: script | code` field
   - Code-based agents specify an entrypoint: `entrypoint: github_issue_monitor.py`

4. **Where does the gatekeeper sanity-check logic live?** The scripts (`check-tests`, `check-scope`) are straightforward. But the LLM review phase needs a prompt that includes the script results. This is a prompt-with-tool-results pattern, not just a static prompt.

## Rules for Octopoid's Own Agents

Because octopoid uses itself, our agents need guardrails:

1. **Default to product improvements.** When a task says "improve the implementer", change `packages/client/agents/implementer/` (the template), not `.octopoid/agents/implementer/` (our copy). Product changes benefit all users.

2. **Never modify scaffolded copies unless explicitly told to.** `.octopoid/agents/` is our instance config. Product tasks don't touch it. If we improve a template, we separately decide whether to pull the change into our scaffolded copy.

3. **Self-management tasks are explicitly scoped.** "Update the github-issue-monitor" is clearly ours (it only exists in `.octopoid/agents/`). "Add sanity checks to the gatekeeper" is a product task — change the template.

These rules go in `.octopoid/global-instructions.md` and in `CLAUDE.md`.

See also: `project-management/drafts/8-2026-02-15-product-vs-self-management-audit.md`

## Benefits

- **Self-contained:** Everything about an agent is in one directory
- **Discoverable:** `ls packages/client/agents/` shows all available agent types
- **Portable:** Users can copy an agent directory to create a variant
- **Clean separation:** Product agents vs instance fleet vs custom agents
- **Simpler scheduler:** Just find the directory and assemble, no role class dispatch
