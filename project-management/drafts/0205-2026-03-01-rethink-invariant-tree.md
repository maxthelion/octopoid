# Rethink invariant tree: top-down from system identity to specific rules

**Captured:** 2026-03-01
**Author:** human

## Raw

> Re the invariants system. I think we might need some sort of classification of them. Potentially a tree structure that resolves into greater granularity. At the top might be categories such as architecture, testing, functionality. Some high level invariants are things like: tasks are the unit of work. Then it might say that tasks flow through flows, that there are transitions between flows, that steps are run, that gatekeeper agents are involved, that mechanical steps are included, that problems are surfaced quickly, that agentic intervention is preferred. Another section could be about drafts: that they are the starting point of a discussion about new functionality and invariants, that they can be processed, that their work can be enqueued, that they have statuses, that they have authors who can be agents. Another section would be background agents: they run when there is no backpressure - they don't pile up work. They look at facets of the app and make suggestions and even queue work. Another section would be the dashboard, it shows work moving through the system and problems occurring, it shows drafts and their statuses, it shows what agents are doing, it allows lightweight actions to be taken by the user using messages. Another section would be the scheduler: it ticks at a regular frequency, it can spawn agents to do work when conditions are met; we try not to use LLMs where we can and make programmatic checks before, it uses the server as source of truth, multiple schedulers can be run on different machines and claim work for agents they spawn. Another would be the skills and scripts available, particularly the sdk. Another section would be the server. Another would be architectural patterns such as pure functions, Actors, reducing code complexity. I'm not sure the tree we have in the invariants is the same as the one I envisaged.

## Idea

The current spec tree (draft 199, now implemented) organises invariants by **component**: architecture, tasks, scheduler, agents, git, github, etc. But the original vision was a tree that starts from **system identity** — what the system IS — and resolves downward into increasing specificity. The difference is subtle but important:

**Current structure (component-oriented):**
```
tasks/
  lifecycle.yaml     → task flows through queues
  resilience.yaml    → failures go through intervention
  quality-gate.yaml  → gatekeeper reviews before done
scheduler/
  jobs.yaml          → background jobs in YAML
  tick-loop.yaml     → scheduler ticks at interval
```

**Proposed structure (identity-oriented):**
```
Tasks are the unit of work
  ├── They flow through declarative flows
  │   ├── Flows have transitions with conditions
  │   ├── Steps run during transitions (mechanical)
  │   ├── Gatekeeper agents review before completion
  │   └── Problems are surfaced quickly, not hidden
  ├── Agentic intervention is preferred over manual fixing
  └── ...

Drafts are the starting point for change
  ├── They are discussions about new functionality and invariants
  ├── They can be processed into tasks
  ├── They have statuses (idea → in_progress → complete)
  └── They have authors — both humans and agents

Background agents observe without backpressure
  ├── They don't pile up work
  ├── They examine facets of the app
  ├── They make suggestions (drafts)
  └── They can queue work (with approval)

The dashboard shows work moving through the system
  ├── Problems occurring
  ├── Draft statuses
  ├── What agents are doing
  └── Lightweight user actions via messages

The scheduler drives all activity
  ├── Ticks at regular frequency
  ├── Spawns agents when conditions are met
  ├── Programmatic checks preferred over LLM
  ├── Server is the source of truth
  └── Multiple schedulers on different machines

Skills and scripts (SDK)
  ├── ...

Server
  ├── ...

Architecture
  ├── Agents as pure functions
  ├── Actor model (message-based communication)
  └── Reducing code complexity
```

The key difference: the current tree is a **reference manual** (look up "scheduler/jobs" to find scheduler job rules). The proposed tree is a **narrative** (start from "tasks are the unit of work" and drill into what that means). The top-level nodes aren't components — they're **statements about the system's identity**.

This matters because an agent reading the spec should be able to understand the SYSTEM, not just look up rules about a component. "Tasks are the unit of work" tells you something fundamental. "tasks/" as a directory name does not.

## The missing dimension: three top-level categories

The current tree is flat — 15 sections all at the same level. But the original vision had **categories** above the sections: "architecture, testing, functionality." These are fundamentally different kinds of knowledge:

**Functionality** — what the system is designed to do. Tasks flow through queues. Drafts are the starting point for change. The dashboard shows work moving. Background agents observe and suggest. The scheduler spawns agents. These are the "what" — they inform integration tests, QA, and documentation.

**Architecture** — how the code is built to meet the functional need. What components exist (scheduler, server, SDK, dashboard). What tech is used (Cloudflare Workers, Python, Claude CLI). Which services it interacts with (GitHub, Anthropic API). Which patterns we employ (pure functions, actor model, declarative flows, complexity reduction). These are the "how" — they inform code review, refactoring, and onboarding.

**Testing** — meta-level: how we verify the system matches its description. Outside-in philosophy. Spec-to-test mapping. Coverage tracking. This is "how we know" — it's about the verification process itself.

This gives us a three-tier hierarchy:

```
Functionality (what the system does)
  ├── Tasks are the unit of work
  │   ├── They flow through declarative flows
  │   ├── Gatekeeper agents review before completion
  │   ├── Problems are surfaced quickly
  │   └── Agentic intervention preferred over manual fixing
  ├── Drafts are the starting point for change
  │   ├── They can be processed into tasks
  │   ├── They have statuses and authors (including agents)
  │   └── They carry invariants
  ├── Background agents observe without backpressure
  │   ├── They examine facets and suggest improvements
  │   └── They can queue work (with approval)
  ├── The dashboard shows work moving through the system
  │   ├── Problems, draft statuses, agent activity
  │   └── Lightweight user actions via messages
  ├── The scheduler drives all activity
  │   ├── Ticks at regular frequency
  │   ├── Spawns agents when conditions are met
  │   └── Programmatic checks preferred over LLM
  └── Skills, scripts, and SDK
      └── ...

Architecture (how it's built)
  ├── Components
  │   ├── Server (Cloudflare Workers + D1, source of truth)
  │   ├── Scheduler (Python, single supervisor)
  │   ├── Agents (Claude CLI processes, stateless)
  │   ├── Dashboard (TUI, real-time monitoring)
  │   └── SDK (Python client for server API)
  ├── External services
  │   ├── GitHub (PRs, CI, issue tracking)
  │   └── Anthropic (Claude API via CLI)
  ├── Patterns
  │   ├── Agents as pure functions
  │   ├── Actor model (message-based communication)
  │   ├── Declarative flows (YAML state machines)
  │   └── Complexity reduction (no premature abstraction)
  ├── Git strategy
  │   ├── Worktrees for isolation
  │   ├── Late rebasing (just before merge)
  │   └── Conflict reduction mechanisms
  └── Security
      ├── Secrets never committed
      └── Scope-based isolation

Testing (how we verify it)
  ├── Outside-in philosophy (real server preferred)
  ├── Spec-to-test mapping (invariants drive test backlog)
  ├── Infrastructure (test server, scoped_sdk)
  └── Coverage tracking (enforced vs aspirational)
```

The current spec tree mixes these categories. `tasks/resilience` is functional (what the system does about failures). `architecture/pure-functions` is architectural (a code pattern). `testing/philosophy` is meta. They're all at the same level — 15 peer sections. The proposed hierarchy puts a category above them, which:

1. **Tells you what kind of knowledge you're looking at.** A functional invariant ("tasks flow through flows") informs QA differently than an architectural invariant ("agents are pure functions") informs code review.
2. **Helps prioritise.** Functional invariants are the ones you test. Architectural invariants are the ones you audit. Testing invariants are the ones you maintain.
3. **Maps to different verification mechanisms.** Functional → integration tests. Architectural → analyst auditing (draft 200). Testing → meta-checks.

## Gap analysis: current vs proposed

The current tree has 15 sections. The proposed vision has three top-level categories with ~8 functional sections, ~5 architectural sections, and ~3 testing sections:

| Proposed category | Current coverage | Gap |
|---|---|---|
| Tasks are the unit of work | tasks/ (lifecycle, flows, resilience, steps, quality-gate) | Good coverage but framed as component, not identity |
| Drafts are the starting point for change | drafts/ (authorship, invariants, lifecycle) | Good coverage |
| Background agents observe without backpressure | agents/background | Covered but buried under agents/ |
| Dashboard shows work moving | dashboard/ (visibility, actions, drafts-view) | Good coverage |
| Scheduler drives all activity | scheduler/ (claiming, jobs, tick-loop, etc) | Good coverage |
| Skills and scripts / SDK | **missing entirely** | Not in current tree |
| Server | server/ (api-contract, multi-tenant, state-ownership) | Covered |
| Architecture | architecture/ (pure-functions, actors, complexity) | Good coverage |
| Testing | testing/ (philosophy, infrastructure, coverage) | In current tree but not in the proposed list (might fold into another) |
| Git / GitHub | git/, github/ | In current tree but not explicitly in the proposed list |
| Projects | projects/ | In current tree but not in proposed list |
| Communication | communication/ | In current tree but not in proposed list |
| Security / Config | security/, configuration/ | In current tree but not in proposed list |
| Observability | observability/ | In current tree, not explicitly in proposed list |

### What's different

1. **Top-level nodes are identity statements, not component names.** "Tasks are the unit of work" vs "tasks/".
2. **Background agents get top-level status.** Currently buried under agents/background.
3. **Skills/SDK is missing entirely** from the current tree.
4. **The tree reads as a narrative** — each section tells you something about the system's nature, then drills into the specifics.
5. **Some current sections might merge.** Git and GitHub could fold into the tasks narrative (since git is HOW tasks deliver work). Communication might fold into architecture (actor model). Testing might be its own pillar or fold into a "verification" section.

## Open Questions

- Does the three-category split (functionality/architecture/testing) need to be reflected in the directory structure, or can it be metadata on each section (`category: functionality`)?
- Some invariants straddle categories. "Rebase before merge" is functional (when it happens in the lifecycle) AND architectural (git strategy). Where does it live?
- Is the skills/SDK section functional ("you can pause the system") or architectural ("the SDK wraps the REST API")? Probably both — which category owns it?
- Should the spec viewer show the three-tier hierarchy, or flatten to the current two-tier (section → leaf) with a category filter?
- Do git/github stay as architectural sections, or do parts fold into the functional "tasks" narrative (since git is HOW tasks deliver work)?

## Possible Next Steps

- Option A (metadata): Add a `category: functionality|architecture|testing` field to each `_section.yaml`. Rewrite principles as identity statements. Update the viewer to group by category. Minimal file restructuring.
- Option B (restructure): Create three top-level directories (`functionality/`, `architecture/`, `testing/`). Move existing sections underneath. Rewrite principles. Update build.py and viewer.
- Option C (parallel view): Keep the current structure. Add a "narrative view" to the spec viewer that reorganises the same invariants into the three-category hierarchy. Same data, different lens.
