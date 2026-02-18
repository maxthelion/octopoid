# Deploy Project Flow

**Status:** Idea
**Captured:** 2026-02-18
**Related:** Draft 20 (Flows as Single Integration Path)

## Problem

`flow.py` has `generate_project_flow()` which produces a complete project flow YAML, but only `default.yaml` is deployed in `.octopoid/flows/`. Projects don't have their own flow file.

The project flow defines:
- **Child tasks** skip PR creation — they commit to a shared branch
- **Project itself** creates a PR after all children complete
- Children rebase onto the project branch, not main

Without the deployed flow, project tasks either use the default flow (which creates individual PRs per child task) or rely on hardcoded special-case logic.

## Proposal

1. Deploy `.octopoid/flows/project.yaml` from the existing `generate_project_flow()` template
2. Wire project task creation to assign `flow: project` and child tasks to use the child_flow
3. Ensure the scheduler respects `child_flow` when dispatching transitions for project children

## Open Questions

- Are projects actively used right now? If not, this can wait until they are.
- Does the child_flow model in `flow.py` actually get read by the scheduler, or is it just modeled but unused?
