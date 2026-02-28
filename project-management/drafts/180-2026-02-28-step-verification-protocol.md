# Step verification protocol: three-phase Step abstraction with pre_check, execute, verify

**Captured:** 2026-02-28

## Raw

> Replace bare step functions with a three-phase Step abstraction (pre_check, execute, verify) that makes steps idempotent, verifiable, and properly classifies errors. Fixes ghost completions, stuck tasks from swallowed errors, and non-idempotent retries.

## The Problem

The step execution pipeline (`octopoid/steps.py`) has no verification layer. Steps are bare functions `(task, result, task_dir) -> None`. The runner calls them, and if no exception is thrown, assumes success. This causes three classes of failure:

### 1. Ghost completions
`step_progress.json` marks a step as completed immediately after the function returns without verifying the action durably took effect. Example: `merge_pr` calls `approve_and_merge()`, which merges the PR on GitHub, but the subsequent `sdk.tasks.accept()` call fails with a network error. The step function raises, but GitHub already merged. On retry, the fixer sees `merge_pr` in `completed` and skips it — but the task was never marked `done` on the server.

**Real incident:** Task e483d3c1 — `step_progress.json` shows `merge_pr` completed, task is `done` on server, but the commit never reached `main`. The PR was never created. Ghost completion.

### 2. Non-idempotent retries
When a step fails and the fixer retries, it re-runs from the failed step. But some steps fail on retry because they don't check whether their work is already done. Example: `push_branch` fails if the branch already exists on the remote from a previous attempt.

**Real incident:** Task 76ce7e3f — `push_branch` failed because the branch already existed from a prior run. Circuit breaker tripped after 3 attempts. Task stuck in `claimed`.

### 3. Error classification failures
The error handling chain has a scoping bug and catches errors too broadly:
- `RetryableStepError` handler in `handle_agent_result_via_flow` (line 779) references `current_queue` which is defined inside the `try` block — `NameError` at runtime
- Only `RuntimeError` is caught specifically (line 646); other exception types hit the catch-all
- Catch-all (line 782) moves everything to `failed`, even transient errors

### 4. Unprotected post-action SDK calls
`approve_and_merge()` merges the PR on GitHub, then calls `sdk.tasks.accept()` without error handling. If the SDK call fails, the PR is merged but the task isn't marked done. The step raises an exception, but the irreversible action (merge) already happened.

## Proposed Design

Replace bare step functions with a **Step protocol** that has three phases:

```python
@dataclass
class StepContext:
    """Everything a step needs — replaces the (task, result, task_dir) tuple."""
    task: dict
    result: dict
    task_dir: Path


class Step:
    """Base class for flow steps. Each step has three phases."""
    name: str

    def pre_check(self, ctx: StepContext) -> bool:
        """Is this step already done? Return True to skip execution.

        This is the idempotency mechanism. If a previous attempt partially
        succeeded (e.g. branch was pushed but PR creation failed), pre_check
        detects the completed work and skips re-execution.
        """
        return False

    def execute(self, ctx: StepContext) -> None:
        """Perform the action. May raise on failure."""
        raise NotImplementedError

    def verify(self, ctx: StepContext) -> None:
        """Confirm the action took durable effect.

        Called after execute(). Raises StepVerificationError if the action
        didn't actually work despite no exception from execute().
        Default: no verification (for backwards compatibility during migration).
        """
        pass
```

### The runner becomes:

```python
def execute_steps(steps: list[Step], ctx: StepContext) -> None:
    completed: list[str] = []
    for step in steps:
        if step.pre_check(ctx):
            completed.append(step.name)
            _write_step_progress(ctx.task_dir, completed, failed=None)
            logger.info(f"Step {step.name}: pre_check passed, skipping (already done)")
            continue
        try:
            step.execute(ctx)
            step.verify(ctx)
            completed.append(step.name)
            _write_step_progress(ctx.task_dir, completed, failed=None)
        except RetryableStepError:
            _write_step_progress(ctx.task_dir, completed, failed=step.name)
            raise
        except StepVerificationError:
            _write_step_progress(ctx.task_dir, completed, failed=step.name)
            raise
        except Exception:
            _write_step_progress(ctx.task_dir, completed, failed=step.name)
            raise
```

### Error types:

```python
class StepVerificationError(RuntimeError):
    """Execute ran but verify failed — action may have partially completed."""

class RetryableStepError(RuntimeError):
    """Transient failure — retry on next tick."""
    # Already exists

class PermanentStepError(RuntimeError):
    """Won't succeed on retry — needs intervention."""
```

## What pre_check and verify look like per step

| Step | pre_check() | verify() |
|------|------------|----------|
| `push_branch` | Branch exists on remote with same HEAD? Skip. | `git ls-remote origin <branch>` — branch exists? |
| `create_pr` | PR already exists for this branch? Skip (store pr_number). | `gh pr view <branch>` — PR exists? `pr_number` set on task? |
| `merge_pr` | PR state is already MERGED? Skip. | `gh pr view <number> --json state` — state is MERGED? Task queue is `done`? |
| `rebase_on_base` | HEAD already a descendant of `origin/<base>`? Skip. | HEAD is ahead of `origin/<base>` with no divergence? |
| `run_tests` | N/A (always run). | Exit code already checked in execute. No extra verify needed. |
| `check_ci` | N/A (always check). | Already has its own polling logic. |
| `post_review_comment` | N/A (idempotent by nature). | No verify needed (comment posting is best-effort). |
| `update_changelog` | N/A (already has internal skip logic). | No verify needed (non-fatal after merge). |

## Why this is testable

Each method is independently testable:

1. **verify() in isolation**: Set up the expected end state (e.g. create a branch on remote), call verify(), confirm it passes. Remove the branch, call verify(), confirm it raises `StepVerificationError`.

2. **pre_check() in isolation**: Mock git/gh output to simulate "already done" state, confirm pre_check returns True. Mock "not done" state, confirm it returns False.

3. **The runner with fake steps**: Create test steps with controllable pre_check/execute/verify. Test that the runner skips on pre_check, calls verify after execute, writes correct step_progress, and classifies errors correctly.

4. **Error classification**: Have execute succeed but verify fail — confirm the runner raises `StepVerificationError`, not a generic Exception.

5. **Idempotency**: Run a step, have it partially succeed (execute passes, verify fails). Run again — pre_check detects the partial work and either skips or retries correctly.

## Migration path

1. Keep the existing `STEP_REGISTRY` dict working during migration (backwards compat)
2. Add `Step` base class and `StepContext`
3. Convert one step at a time (start with `push_branch` — it has the clearest pre_check/verify)
4. Update `execute_steps` to handle both old-style functions and new-style Step objects
5. Once all steps are converted, remove the old function-based path

## Also fixes the error handling chain

The `handle_agent_result_via_flow` scoping bug (line 779) goes away because error classification moves into the runner. The runner catches typed exceptions and the caller doesn't need to reference `current_queue` in exception handlers. The catch-all at line 782 becomes a true last-resort handler instead of the primary error path.

## Context

This came up after two incidents in one session:
- Task e483d3c1: gatekeeper approved, step_progress showed merge_pr completed, but commit never reached main. Had to manually push and create PR.
- Task 76ce7e3f: push_branch failed because branch existed from previous attempt. Circuit breaker tripped, error handler tried invalid queue transition, task stuck in claimed.

Both are symptoms of the same root cause: the step pipeline assumes "no exception = success" and has no idempotency or verification.

## Open Questions

- Should pre_check and verify share a common "query the state" method? They're checking similar things — pre_check asks "is it done?" and verify asks "did it work?" — which is the same question at different times.
- Should we add a `rollback()` method for steps where partial execution is dangerous (e.g. merge_pr merges the PR but fails to update the task)? Or is verify + manual intervention sufficient?
- How do we handle steps where verify is expensive (e.g. run_tests would mean running tests twice)? Probably just don't add verify to those steps.

## Possible Next Steps

- Implement the `Step` base class, `StepContext`, and error types
- Convert `push_branch` first (clearest pre_check/verify, has a real incident to test against)
- Convert `create_pr` and `merge_pr`
- Fix the `handle_agent_result_via_flow` error handling chain
- Add integration tests for the runner with mock steps
- Add integration tests for each step's pre_check and verify methods
