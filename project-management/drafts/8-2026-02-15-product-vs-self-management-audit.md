# Product vs Self-Management: Boundary Audit

## Problem

Octopoid uses itself to manage its own development. This creates a blurry line between code that's part of the **product** (available to all users) and code that's **self-management** (specific to this repo). Without a clear boundary, we risk:

- Agents fixing self-management code thinking it's the product
- Product features that only work for our repo's setup
- Users of octopoid not getting features we take for granted (like the gatekeeper, dashboard)
- Self-management hacks leaking into the product

## Current State

### Clearly Product
| Component | Location | Notes |
|-----------|----------|-------|
| Server API | `submodules/server/` | Separate submodule, independently deployable |
| Node.js CLI & client | `packages/client/` | Published npm package, `octopoid` command |
| Python SDK | `packages/python-sdk/` | Published package, API client |
| Shared types | `packages/shared/` | TypeScript types shared between server/client |
| Agent instructions | `commands/agent/` | Role templates (implement, review, test) |
| Management commands | `commands/management/` | Queue/agent management templates |

### Clearly Self-Management
| Component | Location | Notes |
|-----------|----------|-------|
| Our config | `.octopoid/config.yaml` | Points at our prod server, our machine |
| Our agents | `.octopoid/agents.yaml` | Our specific agent fleet |
| Our tasks | `.octopoid/tasks/`, `.octopoid/runtime/` | Our task queue state |
| Claude Code skills | `.claude/commands/` | `/enqueue`, `/queue-status` etc for our workflow |
| Utility scripts | `scripts/` | `create_task.py`, `approve_task.py` etc |
| Project management | `project-management/` | Our drafts, task docs |

### The Problem Zone: `orchestrator/`

This directory is a mess of both. It's the v1 Python orchestrator that we use to run our own agents, but it also contains code that should be product features.

#### Should be product (generic, any project could use)
- `roles/base.py` — Abstract base for all agent roles
- `roles/implementer.py` — Generic implementer agent
- `roles/gatekeeper.py` — Code review agent (this is what we want to build the sanity-check gatekeeper from)
- `roles/breakdown.py` — Task decomposition
- `roles/pre_check.py` — Pre-flight checks
- `config.py` — Configuration management
- `queue_utils.py` — Task queue operations
- `hook_manager.py`, `hooks.py` — Lifecycle hooks
- `git_utils.py` — Git operations

#### Should be self-management (specific to us)
- `roles/github_issue_monitor.py` — Polls *our* GitHub issues
- `roles/orchestrator_impl.py` — Specialist for implementing orchestrator Python code (literally a role for working on octopoid itself)
- `roles/proposer.py` — Generates proposals for octopoid development
- `roles/curator.py` — Manages our inbox
- `roles/product_manager.py` — Product planning for octopoid
- `approve_orch.py`, `review_orch.py` — Specific to our review workflow
- `planning.py` — Our task planning
- `reports.py` — Dashboard data aggregation (feeds `octopoid-dash.py`)
- `backpressure.py` — Our queue pressure management

#### Also problematic: `octopoid-dash.py`
Lives at project root. Is it a product feature (any octopoid user gets a dashboard)? Or is it our monitoring tool? Currently it imports `orchestrator.reports` which is self-management code. If it's a product feature, it should be in `packages/client/` or a separate package.

### Duplicate Implementations

The Node.js client (`packages/client/`) and Python orchestrator (`orchestrator/`) have parallel implementations of the same concepts:

| Concept | Python (orchestrator/) | TypeScript (packages/client/) |
|---------|----------------------|------------------------------|
| Gatekeeper | `roles/gatekeeper.py` | `src/roles/gatekeeper.ts` |
| Implementer | `roles/implementer.py` | `src/roles/implementer.ts` |
| Breakdown | `roles/breakdown.py` | `src/roles/breakdown.ts` |
| Scheduler | `scheduler.py` | `src/scheduler.ts` |
| Config | `config.py` | `src/config.ts` |
| Queue utils | `queue_utils.py` | `src/queue-utils.ts` |

Which is the product? Both? The Python one is what we actually run. The TypeScript one is what we publish. Are they in sync?

## Examples of the Confusion

1. **TASK-9438c90d** (PR #27): Fixes `orchestrator/reports.py` which feeds `octopoid-dash.py`. This is self-management code, not the product. But the agent treated it as a normal task.

2. **The gatekeeper we want to build**: Should be a product feature (any octopoid user gets automated review). But where does it live? `orchestrator/roles/gatekeeper.py` is the Python self-management version. `packages/client/src/roles/gatekeeper.ts` is the product version. We need to build the sanity-check gatekeeper in the product, not just for ourselves.

3. **GitHub issue monitor**: Currently in `orchestrator/roles/`. This is specific to us — other users would need their own issue sources. But the *concept* of an external task source is generic. Should there be a product-level plugin interface?

4. **`octopoid-dash.py`**: Sitting at project root, importing from `orchestrator/`. If a user installs octopoid, do they get a dashboard? Should they? If so, it needs to move into the product (`packages/client/`).

## Open Questions

1. **Is the Python orchestrator (`orchestrator/`) part of the product or not?**
   - If yes: it needs to be published, documented, and separated from self-management code
   - If no: the TypeScript client is the product, and `orchestrator/` is just our runner
   - Current reality: it's our runner, but it contains generic code that should be in the product

2. **Should the dashboard be a product feature?**
   - It's useful for any octopoid user
   - But it currently depends on self-management code (`reports.py`)
   - Could be rebuilt as a `packages/client/` command: `octopoid dashboard`

3. **How do we prevent agents from conflating the two?**
   - Agent instructions could explicitly say "you are working on the product" or "you are working on self-management"
   - Directory structure could enforce the boundary (move all self-management code to a `self/` or `internal/` directory)
   - The `.octopoid/global-instructions.md` could clarify

4. **What about the gatekeeper specifically?**
   - The sanity-check gatekeeper from draft #7 should be a product feature
   - It should live in `packages/client/src/roles/gatekeeper.ts` (evolve the existing one)
   - We should also use it ourselves via our `orchestrator/` runner
   - This means the Python orchestrator needs to be able to invoke the TypeScript gatekeeper, or we maintain two implementations

## Suggested Direction

The cleanest separation would be:

```
packages/           ← THE PRODUCT (what users get)
  client/           ← CLI, orchestrator, all roles
  server/           ← API server
  shared/           ← Shared types
  python-sdk/       ← Python SDK

orchestrator/       ← OUR RUNNER (self-management, uses the product)
  scheduler.py      ← Our scheduler that invokes product agents
  roles/            ← Our custom roles (github monitor, orchestrator_impl)
  reports.py        ← Our dashboard data

.octopoid/          ← OUR CONFIG (self-management)
scripts/            ← OUR UTILITIES (self-management)
```

Generic roles (implementer, gatekeeper, breakdown) should only exist in `packages/client/`. Our Python runner should invoke them via the CLI (`octopoid` commands or `claude -p` with product agent instructions), not re-implement them in Python.

Self-management roles that are specific to us (github_issue_monitor, orchestrator_impl, proposer) stay in `orchestrator/roles/` but are clearly marked as non-product.

## How Agent Directories (Draft #9) Fixes This

The agent directories proposal directly addresses the core confusion:

### Before (current)
- Agent type definitions scattered across `agents.yaml`, `orchestrator/roles/`, `commands/agent/`, `orchestrator/prompts/`, `orchestrator/agent_scripts/`
- No clear boundary between product code and our customisations
- Agents working on octopoid can't tell if they're changing the product or our config

### After (with agent directories)
```
packages/client/agents/         ← THE PRODUCT (templates, shipped to all users)
  implementer/
  gatekeeper/
  breakdown/

.octopoid/agents/               ← OUR SCAFFOLDED COPY (customised for this repo)
  implementer/                  ← may have our tweaks
  gatekeeper/
  github-issue-monitor/         ← ours only, not from template
```

This creates an obvious, filesystem-level boundary:
- **`packages/client/agents/`** = product. Changes here improve octopoid for everyone.
- **`.octopoid/agents/`** = our instance. Changes here only affect our setup.

### Rules for Agents Working on Octopoid

Because octopoid uses itself, agents need explicit rules about which side they're working on:

1. **Default to product improvements.** When a task says "improve the gatekeeper", change `packages/client/agents/gatekeeper/`, not `.octopoid/agents/gatekeeper/`. Product improvements benefit everyone.

2. **Never modify scaffolded copies unless explicitly told to.** The files in `.octopoid/agents/` are our customisations. An agent working on a product task should not touch them. If the product template changes, we decide whether to pull those changes into our scaffolded copy.

3. **Self-management tasks must be explicitly labelled.** Tasks like "update the github-issue-monitor" are clearly self-management (it only exists in `.octopoid/agents/`). But "add a sanity check to the gatekeeper" is a product task — it should change the template.

4. **The `.octopoid/` directory is like `.env` — instance-specific, not the product.** Agents should treat it the way they'd treat any project's config: read it to understand the setup, don't change it unless the task is specifically about our setup.

These rules should go in `.octopoid/global-instructions.md` (which agents read) and in the product's agent instructions template.
