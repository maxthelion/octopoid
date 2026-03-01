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

## Gap analysis: current vs proposed

The current tree has 15 sections. The proposed vision mentions ~8 top-level categories with a different emphasis:

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

- Is this a restructure of the existing tree, or a parallel "narrative view" of the same invariants?
- Should the top-level identity statements BE the section principles (the `_section.yaml` descriptions), just rewritten to be more assertive? That might be the minimal change.
- Is the skills/SDK section about invariants or about documentation? What's the invariant — "skills exist" or "skills do X correctly"?
- Do git/github/communication/security/observability keep their own sections or fold into the narratives they support?

## Possible Next Steps

- Option A (minimal): Rewrite `_section.yaml` principles as identity statements. Restructure a few sections (promote background agents, add skills/SDK, merge git into tasks). Keep the YAML structure.
- Option B (moderate): Restructure the tree to match the proposed categories. Move invariants between sections. This is a bigger change but produces the intended narrative.
- Option C (parallel view): Keep the current component tree as-is. Add a second "narrative" view in the spec viewer that groups invariants by identity statements. Same data, different presentation.
