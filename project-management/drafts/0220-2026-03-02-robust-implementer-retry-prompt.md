# Robust implementer prompt for retries: check existing work, focus on feedback

**Captured:** 2026-03-02

## Raw

> Fix the implementer prompt template to be more robust for retries. I seem to recall that in the past, we had to delete the previous task instructions and tell the implementer to only look at the feedback from the gatekeeper. Also, make the agent aware when they are doing a re-run.

## Idea

When an implementer is re-spawned after a gatekeeper rejection, it gets the same prompt template as a fresh attempt — plus the rejection feedback appended via `$review_section`. But the prompt doesn't tell the agent that it's a retry, that commits from the previous attempt are already on the branch, or that it should focus on the specific feedback rather than reimplementing from scratch.

This causes problems:
1. The agent may try to reimplement the entire task, creating conflicts with its own previous commits
2. It wastes turns re-reading and re-understanding code it already changed
3. If it gets confused by the state of its own previous work, it can burn all turns and exit with empty stdout — which the system treats as a crash
4. In the past, we had to manually delete the original task instructions and replace them with just the gatekeeper feedback to get retries to work

The fix is two-fold:

### 1. Make the prompt retry-aware

The prompt template should detect when `rejection_count > 0` and change its framing. Instead of the standard implementation guidelines, a retry prompt should say:

- **This is attempt N+1.** Your previous implementation was reviewed and rejected.
- **Your previous commits are on this branch.** Run `git log --oneline` to see what's already been done.
- **Focus on the rejection feedback below.** Do NOT reimplement from scratch. Fix only the specific issues identified.
- **Run tests first** to see the current state of failures before making changes.

### 2. Consider stripping the original task description on retries

On the first attempt, the full task description is essential. On a retry, it may actively mislead — the agent re-reads the acceptance criteria and decides to start over. The gatekeeper feedback is more actionable.

Options:
- **Minimal:** Keep the task description but add a prominent "RETRY — focus on feedback" header
- **Moderate:** Collapse the task description into a brief summary and foreground the rejection feedback
- **Aggressive:** On retry, replace the task description entirely with the rejection feedback plus a one-line reminder of the task's goal

The aggressive approach matches what we had to do manually in the past and may be the most effective for LLMs, which tend to follow the most prominent/recent instructions.

### 3. Template variables needed

The prompt renderer (`prepare_task_directory`) already knows `rejection_count` from the task dict. It needs to:
- Set a `$is_retry` flag (or `$attempt_number`)
- Conditionally change the implementation guidelines section
- Optionally truncate or summarise the task description on retries

## Invariants

- **retry-prompt-is-different**: When `rejection_count > 0`, the implementer prompt explicitly states this is a retry, references existing commits, and foregrounds the rejection feedback. The agent does not receive an identical prompt to its first attempt.
- **existing-work-acknowledged**: On retry, the prompt instructs the agent to check `git log` for previous commits before making changes. The agent should build on existing work, not start from scratch.
- **rejection-feedback-is-primary**: On retry, the gatekeeper's rejection feedback is the primary instruction. The original task description is secondary context, not the driving instruction.

## Context

Task cf0d23a6 (diagnostic agent) was rejected by the gatekeeper for duplicate function definitions after a rebase. On retry, the implementer was re-spawned with the full task description + rejection feedback appended. The agent made 31 tool calls but produced empty stdout — likely got confused trying to reconcile the "build this from scratch" task description with the existing implementation already on the branch. The fixer then also failed, and the circuit breaker fired.

Draft 51 (rejection feedback loop) solved the problem of getting rejection feedback into the prompt at all — `$review_section` now works via the task message thread. But the prompt template itself doesn't change its framing for retries.

Draft 175 (preserve worktrees on requeue) mentioned updating the prompt template to check for existing work but this was never implemented.

## Open Questions

- Should the retry prompt include `git log --oneline` output directly (computed at render time), or just instruct the agent to run it?
- Should there be a different template file for retries, or conditional sections in the same template?
- At what rejection count should we stop retrying and escalate to human? (Draft 51 mentions `max_rejections: 3` exists in config but isn't enforced.)
- Should the `$continuation_section` (for `needs_continuation` retries) get similar treatment?

## Possible Next Steps

- Update the implementer prompt template (`.octopoid/agents/implementer/prompt.md` and `octopoid/data/agents/implementer/prompt.md`) with conditional retry framing
- Update `prepare_task_directory()` to pass `rejection_count` / `is_retry` to the template renderer
- Test with a deliberately rejected task to verify the agent focuses on feedback
