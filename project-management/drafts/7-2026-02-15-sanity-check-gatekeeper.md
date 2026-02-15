# Sanity-Check Gatekeeper

## Problem

Tasks reach `provisional` and sit there until a human reviews them. We have a gatekeeper role (`packages/client/src/roles/gatekeeper.ts`) that does LLM-based code review, but it only looks at the diff and makes a subjective judgement. It can't catch concrete failures like broken tests, missing functionality, or leftover debug code.

When I reviewed TASK-9438c90d (PR #27) manually, I did:
1. Read the task metadata to understand what it was supposed to do
2. Read the PR diff to see what actually changed
3. Cloned the branch and ran the test suite (46 tests, all passed)
4. Checked for CI status (none configured)
5. Verified the logic was correct against the task description

Steps 1-4 are automatable. Step 5 benefits from an LLM. The gatekeeper should do all of this.

## Proposal: LLM Agent with Scripted Tools

The gatekeeper should be an LLM agent (not purely programmatic) that has access to a set of automated scripts it can invoke. This gives it the flexibility to reason about edge cases while still getting hard pass/fail signals from deterministic checks.

### Why LLM, not purely programmatic?

- Programmatic checks can tell you "tests pass" but not "this test doesn't actually test the thing the task asked for"
- Acceptance criteria are written in natural language — only an LLM can evaluate whether they're met
- The LLM can decide which checks are relevant (don't run the full test suite for a docs-only change)
- Edge cases: a PR might technically pass tests but introduce a subtle regression the tests don't cover

### Why not purely LLM?

- LLMs can't reliably run tests or check syntax — they hallucinate results
- "Tests pass" must be a ground-truth signal, not an LLM opinion
- Build/lint failures are binary and should be checked programmatically
- Speed: scripted checks run in seconds, LLM calls take 30s+

### Architecture

```
provisional task
    │
    ▼
gatekeeper claims task
    │
    ▼
Phase 1: Automated checks (scripts)
    ├── run_tests.sh        → exit code + output
    ├── check_lint.sh       → exit code + output
    ├── check_diff_size.sh  → stats (files changed, lines added/removed)
    ├── check_debug_code.sh → grep for console.log, print(), TODO, debugger
    └── check_pr_exists.sh  → verify PR is open and not draft
    │
    ▼
Phase 2: LLM review (with script results as context)
    ├── Task description + acceptance criteria
    ├── PR diff
    ├── Commit messages
    ├── Script results from Phase 1
    └── Decision: accept / reject with reason
```

### Open Questions

1. **Where do the scripts live?** Options:
   - `hooks/gatekeeper/` alongside existing hooks
   - Inline in the gatekeeper role itself
   - Configurable per-project in `config.yaml`

2. **Which checks are mandatory vs advisory?**
   - Test failure = auto-reject (no LLM needed)?
   - Or should the LLM see the failure and decide? (e.g. a flaky test shouldn't block)
   - Suggestion: auto-reject on test failures, but the LLM can override with a "flaky test" annotation

3. **Should it run on every provisional task, or only certain types?**
   - Docs-only PRs probably don't need test runs
   - The LLM could decide which Phase 1 checks to run based on the diff

4. **Model choice?**
   - Current gatekeeper uses Opus for reviews — expensive for a check that runs on every task
   - Could use Sonnet for the sanity check and only escalate to Opus for ambiguous cases
   - Or: Haiku for Phase 2 triage ("are tests relevant?"), Sonnet for the actual review

5. **How does it interact with the existing gatekeeper?**
   - Replace it entirely?
   - Run as a pre-check before the existing review?
   - The existing gatekeeper already does LLM review but without any scripted checks — this would be an evolution of it, not a separate thing

6. **What happens on rejection?**
   - Current gatekeeper rejects with feedback and the task goes back to `incoming` for retry
   - Should it increment `review_round` so we can track how many review cycles happened?
   - Already implemented: max 3 rounds then escalate to human

### Suggested Script Inventory

| Script | What it checks | Auto-reject? |
|--------|---------------|-------------|
| `run_tests` | Full test suite passes | Yes |
| `check_lint` | No lint errors introduced | Yes |
| `check_debug_code` | No `console.log`, `print()`, `debugger`, `TODO` in diff | Advisory |
| `check_scope` | No changes to CHANGELOG.md, README.md, or other docs unless the task explicitly requires it | Advisory |
| `diff_stats` | Files changed, lines added/removed | Info only |
| `check_pr` | PR exists, is open, not draft | Yes |
| `check_conflicts` | No merge conflicts with base branch | Yes |

### PR Comment with Findings

The gatekeeper should post its findings as a comment on the PR, not just accept/reject silently. This gives visibility into what was checked and why. Format:

```markdown
## Sanity Check Results

### Automated Checks
- [x] Tests pass (46/46)
- [x] No lint errors
- [x] No debug code in diff
- [ ] Scope: CHANGELOG.md modified but task doesn't mention docs (advisory)
- [x] PR is open, no conflicts

### Review
The implementation correctly changes `_is_recent()` to prefer `completed_at`...

**Decision: APPROVED** / **Decision: REJECTED — [reason]**
```

Use `gh pr comment <number> --body "..."` to post. This also creates a paper trail for human reviewers to audit what the gatekeeper checked.

### What This Doesn't Cover

- Performance testing / benchmarks
- Security review (SAST/DAST)
- Visual/UI review
- Integration testing against live services

These could be added later as additional scripts.

### Lifecycle Rules

The gatekeeper MUST follow these rules (same as manual review):

1. **Use `approve_and_merge(task_id)`** to approve — never raw `sdk.tasks.update(queue='done')`. This runs `before_merge` hooks which merge the PR properly.
2. **Never delete branches** when closing or merging PRs. We may need to go back and check.
3. **Post rejection feedback as a PR comment** as well as writing it into the task body. Two audiences: humans reviewing the PR, and agents picking up the retry.
4. **Post a review summary comment on the PR** before approving/merging.

These rules apply equally to manual human review and automated gatekeeper review.

## Next Steps

Decide on the open questions above, then this becomes a task to evolve the existing `gatekeeper.ts` role with Phase 1 scripted checks feeding into the existing Phase 2 LLM review.
