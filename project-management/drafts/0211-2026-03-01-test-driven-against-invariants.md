# Test-driven implementation against invariants: start with a failing test, then build

**Captured:** 2026-03-01

## Raw

> We currently test as and when we remember. Ideally, when work is enqueued, it either adds new invariants in the appropriate place in the tree, replaces invariants, or is a solution to an invariant. I'd like us to have a test-driven mentality in this respect, if the invariants are changing, we should start with a failing integration test and then do the functionality to make it pass. A single implementer may well do this, but it will be part of their instructions.

## Idea

Every enqueued task should relate to the spec: it either adds new invariants, replaces existing ones, or implements a solution to an existing invariant. There is no work that exists outside the spec's knowledge of the system.

When invariants are changing (new capability or modified behaviour), the implementer starts with a failing integration test derived from the invariant, then builds the functionality to make it pass. Test and code ship together in the same PR, written by the same implementer. This is part of their instructions, not a separate task.

This is test-driven development, but the "test" is derived from the spec's invariants rather than invented by the implementer. The invariant says what should be true; the test proves it is.

## Invariants

- **work-relates-to-invariants**: Every enqueued task either adds new invariants to the spec tree, replaces existing invariants, or is a solution to an existing invariant.
- **changing-invariants-start-with-failing-test**: When work changes or adds invariants, the implementer starts by writing a failing integration test derived from the invariant, then builds the functionality to make it pass.
- **single-implementer-does-both**: The implementer writes both the integration test and the implementation as part of the same task.

## Context

The current state is that tests are written ad-hoc, sometimes as separate follow-up tasks, sometimes not at all. Draft 208 identified that 0 of 102 invariants have `tested: true`. The system spec exists but nothing connects it to the test suite.

Related: spec capability 1.12 ("Testing is done alongside implementation, particularly against intent"), draft 204 (spec completion and test audit), draft 087 (testing analyst agent).

## Open Questions

- How does the task indicate which invariants it relates to? A field in task content? A reference to the spec path?
- When creating a task via /process-draft, should the draft's invariants automatically become the test targets?
- Should the implementer instructions template include a section about writing the failing test first?
- What about pure refactoring tasks that don't change invariants? They should still have tests but the failing-test-first pattern doesn't apply.

## Possible Next Steps

- Update the implementer prompt template to include "write a failing integration test for the invariant first"
- Add a `spec_invariants` field to task content linking to specific invariant IDs
- Update /process-draft to include invariant references in generated tasks
- Start manually: next few tasks we create, explicitly include the invariant and require the test
