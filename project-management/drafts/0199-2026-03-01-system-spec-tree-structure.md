# Restructure system-spec into a hierarchical tree with browsable viewer

**Captured:** 2026-03-01
**Author:** human + claude

## Raw

> The system spec is currently a flat list of 40 invariants in loose categories. Restructure it into a tree that resolves from high-level system identity ("tasks are the unit of work") down to verifiable leaf invariants. Add a lightweight HTML viewer in the spec directory root.

## The Problem

The current `system-spec.yaml` grew bottom-up from individual drafts. Each draft added invariants to ad-hoc categories (`failure-handling`, `pipeline`, `observability`). The result is a flat bag of 40 items with no hierarchy — you can't tell what the system *is* from reading it, only what specific guarantees it makes.

What's missing is the top-down view: what are the major subsystems, what principles do they follow, and how do the leaf invariants connect to those principles?

## Proposed Structure

A tree where each level adds granularity:

```
system-spec/
├── index.html              # Lightweight viewer (single file, no dependencies)
├── build.py                # Concatenates YAML into JSON inlined in index.html
├── _meta.yaml              # Spec-wide metadata (format version, last updated)
│
├── tasks/
│   ├── _section.yaml       # "Tasks are the unit of work"
│   ├── lifecycle.yaml      # Task states, transitions, terminal conditions
│   ├── flows.yaml          # Declarative transitions, steps, conditions
│   ├── steps.yaml          # Step protocol (pre_check/execute/verify)
│   ├── quality-gate.yaml   # Gatekeeper, CI-before-review, rejection feedback
│   └── resilience.yaml     # Intervention, fixer, circuit breaker, recovery paths
│
├── projects/
│   ├── _section.yaml       # "Projects group related tasks under a shared branch"
│   ├── lifecycle.yaml      # Draft → active → provisional → done
│   ├── child-flows.yaml    # How child tasks differ (no individual PRs, shared branch)
│   └── completion.yaml     # All children done → project transitions, aggregate changelog
│
├── drafts/
│   ├── _section.yaml       # "Drafts are the starting point for change"
│   ├── lifecycle.yaml      # Idea → discussion → processing → enqueued work
│   ├── invariants.yaml     # Drafts propose invariants, process-draft verifies
│   └── authorship.yaml     # Agents can author drafts, statuses tracked
│
├── agents/
│   ├── _section.yaml       # "Agents are stateless workers spawned by the scheduler"
│   ├── core-roles.yaml     # Implementer, gatekeeper, fixer — role-based, flow-driven
│   ├── background.yaml     # Analysts: no backpressure, observe & suggest
│   └── spawning.yaml       # Scheduler-controlled, conditions-based, max instances
│
├── scheduler/
│   ├── _section.yaml       # "The scheduler is the only component that spawns agents"
│   ├── tick-loop.yaml      # Regular frequency, job-based, local vs remote
│   ├── claiming.yaml       # Server as source of truth, atomic claim, lease-based
│   ├── jobs.yaml           # Job registration, intervals, local vs remote, no pileup
│   ├── multi-machine.yaml  # Multiple schedulers, scope-based isolation
│   └── programmatic-first.yaml  # LLM only when needed, guard scripts before agents
│
├── git/
│   ├── _section.yaml       # "Git isolates agent work and delivers it via PRs"
│   ├── worktrees.yaml      # Detached HEAD, preservation, retention, never stash
│   ├── branches.yaml       # Task branches, project branches, base branch
│   ├── rebasing.yaml       # When/how rebasing happens in the task lifecycle
│   └── conflict-reduction.yaml  # Small tasks, late rebase, changes.md pattern
│
├── github/
│   ├── _section.yaml       # "GitHub PRs are the delivery mechanism"
│   ├── pr-lifecycle.yaml   # Creation, review, merge, metadata storage
│   ├── ci.yaml             # CI before gatekeeper, programmatic failure handling
│   └── reviews.yaml        # PR comments for review, rejection, approval
│
├── dashboard/
│   ├── _section.yaml       # "The dashboard shows work moving through the system"
│   ├── visibility.yaml     # Tasks, queues, agent activity, problems
│   ├── drafts-view.yaml    # Draft statuses, linked work
│   └── user-actions.yaml   # Lightweight intervention via messages, approve/reject
│
├── communication/
│   ├── _section.yaml       # "Components communicate through messages, not files"
│   ├── messages.yaml       # Actor model, append-only, typed messages
│   ├── result-inference.yaml  # Stdout-based, CLI auth, no result.json
│   ├── announcements.yaml  # Completion notifications, self-contained context
│   └── actions.yaml        # Agent proposals, human approval, execution
│
├── server/
│   ├── _section.yaml       # "The server is the single source of truth"
│   ├── state-ownership.yaml  # Queues, tasks, projects, messages, flows
│   ├── api-contract.yaml   # REST, scoped, authenticated, versioned
│   └── multi-tenant.yaml   # Scopes, orchestrator registration
│
├── architecture/
│   ├── _section.yaml       # "Architectural principles that govern all components"
│   ├── pure-functions.yaml # Agents as pure functions, no side-channel state
│   ├── actors.yaml         # Message-based communication between components
│   └── complexity.yaml     # Reduce code complexity, prefer simple over clever
│
├── testing/
│   ├── _section.yaml       # "Tests verify invariants against a real server"
│   ├── philosophy.yaml     # Outside-in, integration-first, minimal mocking
│   ├── infrastructure.yaml # scoped_sdk, test server on 9787, fixtures
│   └── coverage.yaml       # Each invariant should have a corresponding test
│
├── security/
│   ├── _section.yaml       # "Secrets are never committed, access is scoped"
│   ├── api-keys.yaml       # .api_key file, env var override, key rotation
│   └── scoping.yaml        # Orchestrator scopes isolate data
│
├── configuration/
│   ├── _section.yaml       # "Configuration is declarative and lives in .octopoid/"
│   └── single-source.yaml  # No duplicated config between files
│
├── observability/
│   ├── _section.yaml       # "The system surfaces problems proactively"
│   ├── logging.yaml        # Unified log, structured entries
│   ├── health-scores.yaml  # Analyst scores, append-only logs, justifications
│   ├── agent-visibility.yaml  # Background agent runs visible in dashboard
│   └── queue-health.yaml   # Throttled health checks, stuck task detection
│
└── skills/
    ├── _section.yaml       # "Skills and SDK are the human interface"
    ├── cli-skills.yaml     # enqueue, queue-status, force-queue, draft-idea, etc.
    └── sdk.yaml            # Programmatic access, queue_utils, create_task()
```

### File format

Each `_section.yaml` defines the section's identity:

```yaml
# tasks/_section.yaml
title: Tasks
principle: "Tasks are the unit of work"
description: >
  A task is an atomic piece of work with a clear lifecycle. Tasks flow
  through declarative state machines (flows), are worked on by agents,
  reviewed by gatekeepers, and either completed or surfaced for human
  attention. Everything the system does is organised around tasks.
```

Each leaf file contains invariants for that topic:

```yaml
# tasks/resilience.yaml
title: Task Resilience
description: >
  How the system handles task failures, intervention, and recovery.

invariants:
  - id: self-correcting-failure
    description: >
      Every task failure goes through intervention before reaching the
      failed queue.
    status: enforced
    test: tests/test_requires_intervention.py::TestHandleFailOutcomeRouting
    source: draft-181

  - id: fixer-circuit-breaker
    description: >
      The fixer is limited to 3 attempts per task. After exhausting
      attempts, the task moves to terminal failed with a human message.
    status: enforced
    test: null
    source: postmortem-2026-02-28-fixer-loop
```

### The viewer

A single `index.html` file that:
- Loads all YAML files from the directory tree via fetch (or has them inlined at build time)
- Renders the tree as a collapsible sidebar
- Shows section principles at each level
- Lists invariants with status badges (enforced/aspirational)
- Filters by status, section, source
- No build step, no dependencies — just open the file

Since YAML can't be fetched cross-origin from `file://`, the pragmatic approach is a build script that concatenates all YAML into a single JSON blob inlined in the HTML. The script runs as part of any update to the spec.

## Suggestions You Might Have Missed

### 1. Projects
Projects are a first-class concept with their own lifecycle (draft → active → provisional → done), shared branches, child tasks, and project-level flows. They deserve their own section:

```
projects/
├── _section.yaml       # "Projects group related tasks under a shared branch"
├── lifecycle.yaml      # Draft → active → provisional → done
├── child-flows.yaml    # How child tasks differ (no individual PRs, shared branch)
└── completion.yaml     # All children done → project transitions, aggregate changelog
```

### 2. Configuration
There are invariants around single-source-of-truth for config (`agents.yaml`, `config.yaml`, `jobs.yaml`). This could be its own section:

```
configuration/
├── _section.yaml       # "Configuration is declarative and lives in .octopoid/"
├── agents.yaml         # Agent definitions, spawn mode, intervals
├── flows.yaml          # Flow definitions are YAML, not code
└── single-source.yaml  # No duplicated config between files
```

### 3. Git & Worktrees
The system has strong opinions about git: worktrees on detached HEAD, never stash, worktree preservation on requeue, failed worktree retention. The git lifecycle is tightly coupled to the task lifecycle — different stages have different git operations:

```
git/
├── _section.yaml       # "Git isolates agent work and delivers it via PRs"
├── worktrees.yaml      # Detached HEAD, preservation on requeue, retention policies,
│                       #   never cd into worktrees, never stash
├── branches.yaml       # Task branches created at push time (not claim time),
│                       #   project branches shared across children,
│                       #   base branch configurable per-project
├── rebasing.yaml       # When rebasing happens in the lifecycle:
│                       #   - rebase_on_base: before merge (auto-injected terminal step)
│                       #   - rebase_on_project_branch: before child task submit
│                       #   - Rebase conflicts abort cleanly (no dirty worktree)
│                       #   - Conflict reduction: rebase late (just before merge),
│                       #     small tasks, short-lived branches
└── conflict-reduction.yaml  # Strategies: small atomic tasks, late rebasing,
                             #   no long-lived feature branches (except projects),
                             #   CHANGELOG via changes.md (not direct edits),
                             #   agents don't touch shared files unnecessarily
```

### 4. GitHub
GitHub is the delivery mechanism — PRs are how work lands on main. The system has specific opinions about PR lifecycle and CI:

```
github/
├── _section.yaml       # "GitHub PRs are the delivery mechanism for completed work"
├── pr-lifecycle.yaml   # Creation (by create_pr step), review (by gatekeeper),
│                       #   merge (by merge_pr step), never --delete-branch,
│                       #   PR metadata stored on task (pr_number, pr_url)
├── ci.yaml             # CI runs before gatekeeper review (ci-before-gatekeeper),
│                       #   CI failure is programmatic (no LLM needed),
│                       #   CI failure bounces to implementer directly
└── reviews.yaml        # Gatekeeper posts review as PR comment,
                        #   rejection feedback on PR (visible to next implementer),
                        #   approval comment before merge
```

### 4. Testing
The testing philosophy (outside-in, real server, scoped_sdk) is an architectural principle with its own invariants:

```
testing/
├── _section.yaml       # "Tests verify invariants against a real server"
├── philosophy.yaml     # Outside-in, integration-first, minimal mocking
├── infrastructure.yaml # scoped_sdk, test server on 9787, fixtures
└── coverage.yaml       # Each invariant should have a corresponding test
```

### 5. Observability (expanded)
The current spec has some observability invariants but misses the broader picture: health scores, analyst logs, queue health checks, sweep logs:

```
observability/
├── _section.yaml       # "The system surfaces problems proactively"
├── logging.yaml        # Unified log, structured entries
├── health-scores.yaml  # Analyst scores, append-only logs, justifications
├── agent-visibility.yaml  # Background agent runs visible in dashboard
└── queue-health.yaml   # Throttled health checks, stuck task detection
```

### 6. Security & Auth
Not currently in the spec but exists in the system: API keys, scoped access, key rotation, gitignored secrets:

```
security/
├── _section.yaml       # "Secrets are never committed, access is scoped"
├── api-keys.yaml       # .api_key file, env var override, key rotation
└── scoping.yaml        # Orchestrator scopes isolate data
```

### 7. Jobs
Background jobs are distinct from agents — they're programmatic scheduler functions, not LLM invocations. The current spec doesn't cover job guarantees (idempotency, interval discipline, no pileup):

```
scheduler/
  ...
  jobs.yaml             # Job registration, intervals, local vs remote, no pileup
```

### 8. Actions
The action system (proposals from agents, human approve/execute) is a first-class feature not covered by any invariant:

```
communication/
  ...
  actions.yaml          # Agent proposals, human approval, execution
```

## Plan

### Phase 1: Convert existing spec to tree (no new invariants)

1. Create `project-management/system-spec/` directory
2. Write `_meta.yaml` with format version
3. Split existing 40 invariants from `system-spec.yaml` into the leaf files per the tree above
4. Write `_section.yaml` for each directory with title, principle, description
5. Each invariant keeps its existing id, status, test, source — no content changes
6. Keep the old `system-spec.yaml` as a redirect comment pointing to the new location
7. Update CLAUDE.md and any references to point to `system-spec/` instead of `system-spec.yaml`

### Phase 2: Build the viewer

1. Write a Python script `system-spec/build.py` that:
   - Walks the directory tree
   - Reads all `_section.yaml` and `*.yaml` files
   - Produces a single JSON blob with the full tree
   - Inlines it into `index.html` via template substitution
2. Write `index.html` template:
   - Collapsible tree sidebar showing sections
   - Main panel showing section principle + invariant list
   - Status badges (green for enforced, amber for aspirational)
   - Filter controls (status, source draft)
   - Search across invariant descriptions
   - Stats summary (X enforced, Y aspirational, Z total)
   - No external dependencies — vanilla HTML/CSS/JS
3. The build script is idempotent and fast — run it after any spec edit

### Phase 3: Add missing invariants (separate tasks)

The suggestions above identify ~8 new sections and potentially 20+ new invariants. These should be added incrementally via the normal draft process, not as part of the restructure. Each new invariant section would be a separate draft.

## Invariants

- `spec-is-hierarchical`: The system spec is organised as a directory tree where each level adds granularity. Top-level directories represent subsystems, `_section.yaml` files state the governing principle, and leaf YAML files contain verifiable invariants.
- `spec-has-viewer`: A self-contained HTML page exists at `project-management/system-spec/index.html` that renders the full invariant tree with status badges, filters, and search. No build step required to view (the built output is committed).
- `spec-viewer-no-dependencies`: The spec viewer is a single HTML file with no external dependencies (no React, no npm, no CDN). It works by opening the file directly in a browser.
- `invariants-traceable-to-source`: Every invariant has a `source` field linking it to the draft, postmortem, or discussion that introduced it. The viewer shows this provenance.

## Open Questions

- Should `_section.yaml` files also contain "parent invariants" (e.g. "tasks are the unit of work") that aren't testable in the same way leaf invariants are, or should they be purely descriptive?
- Should the build script also generate a flat `system-spec.yaml` for backwards compatibility (agents that read the old file)?
- Should the viewer show the tree of sections even when a section has no invariants yet (to show gaps)?
- How do we handle invariants that span multiple sections (e.g. "worktree preservation" touches both tasks and git)?
