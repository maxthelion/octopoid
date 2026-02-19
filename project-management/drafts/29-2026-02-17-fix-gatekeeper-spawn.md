# Fix Gatekeeper: Pure Function Model

**Status:** Idea
**Captured:** 2026-02-17
**Related:** Draft 31 (agents as pure functions), Draft 30 (why octopoid keeps breaking), Draft 7 (original gatekeeper design)

## Problem

The gatekeeper has two implementations, both broken:

1. **Python role module** (`orchestrator/roles/sanity_check_gatekeeper.py`) — 102 consecutive failures, 0 successes. Broken for v2.0: filters on `worktree_path` (not in schema), reads from old file queue path, uses non-existent fields (`gatekeeper_reviewed`, `gatekeeper_approved`).

2. **Agent directory** (`.octopoid/agents/gatekeeper/`) — well-designed prompt.md + review scripts. Never used because scheduler spawns the Python module instead.

## Proposed Fix: Gatekeeper as First Pure-Function Agent

Rather than patching the broken gatekeeper to work like the implementer (scripts-based, lifecycle-driving), redesign it as the first agent using the pure-function model from draft #31. The gatekeeper is the simplest agent type — it reads a diff and returns approve/reject — making it the ideal proof of concept.

### What the gatekeeper agent does

```
Input:  task description + PR diff (provided by orchestrator)
Output: result.json with decision + review comment
```

The agent:
1. Reads the task description and acceptance criteria
2. Reads the PR diff
3. Runs automated checks (tests, scope, debug code)
4. Writes a review comment
5. Returns: `{status: "success", decision: "approve"|"reject", comment: "..."}`

The agent does NOT: post PR comments, merge PRs, call the SDK, move tasks between queues, call `finish` or `fail`.

### What the orchestrator does

```python
after_gatekeeper_result(task, result):
    if result.status == "failure":
        # Agent crashed or couldn't complete review
        log_error(task, result.message)
        requeue_task(task)  # try again later
        return

    # Post the review comment to the PR
    post_pr_comment(task.pr_number, result.comment)

    if result.decision == "approve":
        merge_pr(task.pr_number)
        sdk.tasks.accept(task.id, accepted_by="gatekeeper")
    elif result.decision == "reject":
        rewrite_task_file(task, result.comment)
        sdk.tasks.reject(task.id, feedback=result.comment)
```

All lifecycle logic is in the orchestrator — deterministic, testable, no Claude involved.

### Server changes needed

The server's `/api/v1/tasks/claim` endpoint is hardcoded to `WHERE queue = 'incoming'`. The gatekeeper needs to claim from `provisional`.

**Option A: Add `queue` parameter to claim endpoint (recommended)**

```typescript
// POST /api/v1/tasks/claim
// body: { ..., queue: "provisional" }
const claimQueue = body.queue || 'incoming'
const transitionName = claimQueue === 'provisional' ? 'claim_for_review' : 'claim'
```

Plus a new transition in the state machine:

```typescript
claim_for_review: {
  from: 'provisional',
  to: 'provisional',  // stays in provisional, but claimed_by is set
  action: 'claim_for_review',
  guards: [{ type: 'role_matches' }],
  side_effects: [
    { type: 'record_history', params: { event: 'review_claimed' } },
    { type: 'update_lease' },
  ],
}
```

The task stays in `provisional` (it's still submitted work) but `claimed_by` prevents two gatekeepers reviewing the same task.

**Option B: Orchestrator just lists provisional and picks one**

No server changes. The orchestrator calls `GET /api/v1/tasks?queue=provisional`, picks the oldest one without `claimed_by`, PATCHes `claimed_by` to the gatekeeper name, then spawns the agent. Less safe (no lease, no optimistic locking) but simpler.

### Agent directory structure

```
.octopoid/agents/gatekeeper/
  agent.yaml          # config (role, interval, claim_from)
  prompt.md           # review prompt template
  instructions.md     # review guidelines
  scripts/
    run-tests         # ← agent CAN run these for info gathering
    check-scope       # ← advisory checks
    check-debug-code  # ← advisory checks
    diff-stats        # ← informational
```

Scripts the agent does NOT need:
- `submit-pr` — no PR to create (already exists)
- `finish` — orchestrator handles lifecycle
- `fail` — orchestrator handles lifecycle
- `post-review` — orchestrator posts the comment from result.json

### agent.yaml

```yaml
role: gatekeeper
claim_from: provisional    # not incoming
spawn_mode: scripts        # Claude-based
needs_worktree: false      # reuse implementer's worktree (read-only)
result_schema: gatekeeper  # validates result.json against gatekeeper schema
```

### Result schema

```json
{
  "status": "success",
  "decision": "approve",
  "comment": "## Gatekeeper Review\n\n### Automated Checks\n- [x] Tests pass...\n\n### Decision\n**APPROVED**",
  "checks": {
    "tests_pass": true,
    "scope_ok": true,
    "no_debug_code": true,
    "diff_stats": { "files": 3, "added": 45, "removed": 12 }
  }
}
```

The `checks` field is structured data the orchestrator can use for its own decision-making (e.g., "tests failed but agent said approve — override and reject").

### How the orchestrator spawns and handles the gatekeeper

```python
# In scheduler tick:
def try_spawn_gatekeeper(blueprint):
    # 1. Find a provisional task to review
    task = sdk.tasks.claim(
        queue="provisional",
        agent_name=blueprint.name,
        orchestrator_id=ORCHESTRATOR_ID,
    )
    if not task:
        return  # nothing to review

    # 2. Prepare context (no new worktree — reuse implementer's)
    task_dir = get_task_dir(task.id)
    worktree = task_dir / "worktree"
    pr_diff = get_pr_diff(task.pr_number)

    # 3. Render prompt with task + diff context
    prompt = render_gatekeeper_prompt(task, pr_diff)

    # 4. Spawn Claude agent
    pid = invoke_claude(prompt, cwd=worktree, scripts_dir=blueprint.scripts_dir)
    track_agent(blueprint.name, pid, task.id)

# When agent finishes:
def handle_gatekeeper_result(task, result_path):
    result = read_and_validate(result_path, schema="gatekeeper")

    if result.status == "failure":
        requeue_task(task.id)
        return

    # Post review comment
    post_pr_comment(task.pr_number, result.comment)

    if result.decision == "approve":
        merge_pr(task.pr_number)
        sdk.tasks.accept(task.id, accepted_by="gatekeeper")
    else:
        rewrite_task_file(task, result.comment)
        sdk.tasks.reject(task.id, feedback=result.comment)
```

## Files to Change

### Delete
- `orchestrator/roles/sanity_check_gatekeeper.py` — fundamentally broken, replaced by pure-function model

### Server (submodules/server)
- `src/routes/tasks.ts` — add `queue` param to claim endpoint
- `src/state-machine.ts` — add `claim_for_review` transition
- `src/types/shared.ts` — add `queue` to `ClaimTaskRequest`

### Orchestrator
- `orchestrator/scheduler.py` — add `handle_gatekeeper_result()` pipeline in result handler
- `orchestrator/config.py` — add gatekeeper to `CLAIMABLE_AGENT_ROLES` with `claim_from: provisional`
- `orchestrator/queue_utils/tasks.py` — add `queue` param to `claim_task()`
- `orchestrator/roles/__init__.py` — remove sanity_check_gatekeeper import

### Agent directory
- `.octopoid/agents/gatekeeper/agent.yaml` — add `claim_from`, `result_schema`, remove scripts agent doesn't need
- `.octopoid/agents/gatekeeper/prompt.md` — simplify: remove `finish`/`fail`/`post-review` instructions, tell agent to write result.json instead

## What Does NOT Change

- Gatekeeper instructions.md — review guidelines are still correct
- Gatekeeper review scripts (run-tests, check-scope, check-debug-code, diff-stats) — agent uses these for information
- Server state machine transitions: `accept` (provisional→done) and `reject` (provisional→incoming) — already correct
- Implementer spawn path — unchanged

## Migration Path

1. **Server:** Add `queue` param to claim + `claim_for_review` transition
2. **Delete** Python role module
3. **Update** gatekeeper agent.yaml and prompt.md for pure-function model
4. **Add** `handle_gatekeeper_result()` to orchestrator
5. **Add** gatekeeper to claim chain with `claim_from: provisional`
6. **Test** with a real provisional task

This is intentionally the first agent migrated to the pure-function model. If it works well, the implementer follows — with the orchestrator handling PR creation, test running, and submission.

## Open Questions

- Should `claim_for_review` keep the task in `provisional` (recommended) or move it to a new `reviewing` queue state?
- Should the orchestrator trust the gatekeeper's test results, or re-run tests independently as verification?
- If the gatekeeper agent crashes (no result.json), should the task be automatically requeued or flagged for human review?
