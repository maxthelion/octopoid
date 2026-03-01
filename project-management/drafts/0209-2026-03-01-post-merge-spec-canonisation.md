# Post-merge spec canonisation: workers update the specification after work is merged

**Captured:** 2026-03-01

## Raw

> The intent behind the work is canonised after it is done. In much the same way as the changelog is meant to be written after a task completes, we should have multiple workers making sure that work is recorded properly as part of the specification.

## Idea

After work is merged, the system should update its own specification. Just as the changelog step writes to CHANGELOG.md after merge, there should be workers that update the spec tree — adding new invariants, flipping `implemented` to true, or recording new capabilities. Without this, the spec drifts from reality and becomes a historical document rather than a living one.

This isn't just about one step. Multiple workers should be involved: the implementer could note which invariants their work addresses, a post-merge worker could verify the invariant actually holds, and the spec tree gets updated accordingly.

## Invariants

- **spec-updated-after-merge**: After work is merged, workers update the system spec to reflect what was built. New invariants are added, existing invariants are updated (e.g. `implemented` flipped to true), and the spec stays current with the actual system.
- **process-draft-checks-invariants**: /process-draft checks whether the draft's invariants actually hold in the code before archiving the draft — not just whether tasks completed, but whether the intent was achieved.

## Context

The spec tree (v2) was built by manually reading drafts and assessing the codebase. That was a one-off bootstrapping exercise. For the spec to remain accurate, it needs to be maintained as an ongoing process — the same way the changelog is maintained by a post-merge step.

Related: spec capability 1.10 ("The intent behind the work is canonised after it is done").

## Open Questions

- What does the post-merge spec update worker look like? Is it a flow step, a job, or a background agent?
- How does the implementer indicate which invariants their task addresses? A field in the task content? A file in the runtime dir?
- Should the spec update be verified (does the invariant actually hold?) or just recorded (the task said it addressed this invariant)?

## Possible Next Steps

- Add a `spec_invariants` field to task content so implementers declare which invariants they're addressing
- Add a post-merge flow step that reads this field and updates the spec YAML
- Have a background agent periodically verify that `implemented: true` invariants still hold
