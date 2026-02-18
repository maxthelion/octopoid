# Agents as Pure Functions: Success/Failure with Orchestrator-Driven Lifecycle

**Status:** Idea
**Captured:** 2026-02-17
**Related:** Draft 30 (why octopoid keeps breaking), Draft 17 (declarative task flows), Draft 34 (messages table / actor mailboxes)

## Raw

> Maybe all agent spawnings should return success or failure. Any scripts or commands that need to be run should happen outside, rather than relying on agents to do it. Eg an implementer does some work and finishes with success or failure. If success, it goes to the next stage where things like merging commits happens programmatically. If that fails, it gets thrown back with a message. Gatekeepers just return success or failure. Depending on that, the task proceeds or is thrown back. Might that simplify things?

## Idea

Agents become pure functions: they receive a task, do their work, and return **success** or **failure** with an optional message. Everything else — PR creation, merging, rebasing, state transitions, rejection feedback — happens in the orchestrator as deterministic steps between agent invocations.

### Current model: agents drive their own lifecycle

```
Implementer:
  1. Receives task
  2. Writes code
  3. Runs tests         ← agent decides
  4. Creates PR          ← agent calls submit-pr script
  5. Submits to server   ← agent calls _submit_to_server()
  6. Calls finish        ← agent signals completion
```

The agent is responsible for 6 steps, any of which can silently fail. The `submit-pr` script catches exceptions. The server call might 500. The agent might run out of turns before calling finish. Each failure mode requires its own recovery logic.

### Proposed model: agents are pure, orchestrator drives lifecycle

```
Implementer:
  1. Receives task + worktree
  2. Writes code, makes commits
  3. Returns: SUCCESS or FAILURE + message

Orchestrator (on SUCCESS):
  4. Run tests programmatically
  5. If tests fail → return to agent with "fix these failures"
  6. Create PR programmatically
  7. Submit to server
  8. Move task to provisional

Orchestrator (on FAILURE):
  4. Read failure message
  5. Move task to failed with reason
```

The agent's ONLY job is to write code and commit. The orchestrator handles everything mechanical.

### What this means for each agent type

**Implementer:**
- Input: task description + worktree
- Output: commits on the task branch (or nothing if failed)
- Returns: `{status: "success"}` or `{status: "failure", message: "couldn't resolve merge conflict"}`
- Does NOT: create PRs, push branches, run tests, call server APIs

**Gatekeeper:**
- Input: task description + PR diff + worktree (read-only)
- Output: review comment text
- Returns: `{status: "success", decision: "approve", comment: "..."}` or `{status: "success", decision: "reject", comment: "..."}`
- Does NOT: post PR comments, merge PRs, move tasks between queues

**GitHub issue monitor:**
- Input: list of open issues
- Output: list of tasks to create
- Returns: `{status: "success", tasks: [{title: "...", context: "..."}]}`
- Does NOT: call the SDK to create tasks

### The orchestrator becomes a state machine executor

```
after_implementer_success(task, result):
    run_tests(task.worktree)
    if tests_fail:
        return requeue_with_message(task, "Tests failed: ...")
    create_pr(task)
    submit_to_server(task)
    move_to_provisional(task)

after_gatekeeper_success(task, result):
    post_pr_comment(task.pr_number, result.comment)
    if result.decision == "approve":
        merge_pr(task.pr_number)
        move_to_done(task)
    else:
        rewrite_task_file(task, result.comment)
        move_to_incoming(task)
```

These are deterministic, testable, and don't depend on Claude doing the right thing.

## Ramifications

### What gets simpler

1. **No more silent failures in agent scripts.** `submit-pr`, `_submit_to_server()`, `finish`, `fail` — all gone from the agent's responsibility. The orchestrator does it once, correctly, with proper error handling.

2. **Agents can't get stuck.** Currently an agent can create a PR but fail to submit to the server, leaving the task in limbo. In the pure model, the agent either returns or doesn't. The orchestrator handles the rest atomically.

3. **Testing becomes straightforward.** Agent output is a JSON blob. Orchestrator lifecycle is a deterministic function. No need to mock Claude to test "does the PR get created correctly?"

4. **Agent scripts shrink dramatically.** The implementer agent directory needs: prompt.md, instructions.md. No submit-pr, no finish, no fail, no run-tests scripts. The agent just writes code.

5. **Gatekeeper becomes trivial.** It reads a diff, writes a review, returns approve/reject. No SDK calls, no state transitions, no queue manipulation.

6. **Retry/recovery is orchestrator-level.** "Tests failed" → re-invoke agent with failure context. "PR has conflicts" → rebase programmatically and retry. The agent never sees infrastructure failures.

### What gets harder

1. **Agent can't iterate on test failures.** Currently the implementer runs tests, sees failures, and fixes them in the same session. In the pure model, the orchestrator runs tests AFTER the agent finishes. If tests fail, the task gets re-queued and a new agent session starts — losing the context of what was tried.

   **Mitigation:** The orchestrator could run tests as a "check" step and re-invoke the SAME agent session with "your code had these test failures, fix them" before closing the session. This is like a mid-flight check, not a post-flight check. The agent is still pure — it doesn't run the tests itself — but the orchestrator can loop.

2. **Agents lose autonomy.** Sometimes agents make smart decisions: "this test is flaky, let me skip it." In the pure model, the orchestrator runs tests mechanically. Flaky test → rejection → wasted cycle.

   **Mitigation:** The orchestrator can be smart about test interpretation. Or the agent's result can include advisory notes: `{status: "success", notes: "test_random_seed is flaky, ignore if it fails"}`.

3. **More orchestrator complexity.** The lifecycle logic moves from agent scripts to orchestrator code. The orchestrator needs to handle: test running, PR creation, PR merging, comment posting, task file rewriting, rebase, etc.

   **Mitigation:** These are all deterministic operations. They're easier to test, debug, and fix than the same operations scattered across agent scripts where failures are silent.

4. **Result contract needs definition.** What exactly does the agent return? Need a clear schema for each agent type's output.

   **Mitigation:** Simple JSON schemas, validated by the orchestrator before processing.

### How it feeds into draft #30's suggestions

- **One spawn path (#1):** Strengthened. Every agent spawns the same way, returns the same way. The only difference is the prompt and the post-processing pipeline.

- **Server as truth (#2):** Simplified. Only the orchestrator talks to the server. Agents never call the SDK. No race conditions between agent and scheduler updating the same task.

- **Fail loudly (#3):** Easier. The orchestrator is a single codebase with proper error handling. No distributed silent failures.

- **Convention over config (#4):** Natural fit. The result contract IS the convention. `result.json` has a defined schema. Agent directories have a defined structure.

- **Smaller scheduler (#5):** The scheduler becomes: spawn agents, read results, execute lifecycle. The lifecycle steps are extracted into their own module.

### Connection to flows (draft #17)

Declarative flows define transitions: `incoming → claimed → provisional → done`. This proposal says: each transition has a deterministic lifecycle function. The agent handles `claimed` (do the work). The orchestrator handles everything between transitions.

```yaml
transitions:
  "incoming → claimed":
    agent: implementer
    after_success:
      - run_tests
      - create_pr
      - submit_to_provisional
    after_failure:
      - move_to_failed

  "provisional → done":
    agent: gatekeeper
    after_success:
      approve:
        - post_review_comment
        - merge_pr
        - move_to_done
      reject:
        - post_review_comment
        - rewrite_task_file
        - move_to_incoming
```

The flow config becomes the single source of truth for what happens at each stage.

## Relationship to the Actor Model

This proposal is ~80% of the actor model (Hewitt, 1973) without naming it. Making the connection explicit gives us useful vocabulary and proven patterns.

### How it maps

| Actor model concept | Octopoid equivalent |
|---|---|
| Actor | Agent (Claude instance) |
| Message (inbox) | Task + worktree + context |
| Reply | result.json (`{status, decision, comment}`) |
| Supervisor | Orchestrator |
| Supervision strategy | `after_success` / `after_failure` pipelines |
| Mailbox | Task queue (incoming, provisional) |

The orchestrator is an OTP-style supervisor: it spawns agents, reads their results, handles failures, and drives lifecycle transitions. Agents are isolated — no shared state, no direct communication with each other, no access to the server.

### What the actor model adds

1. **Supervision strategies.** OTP defines restart policies: one-for-one (restart just the failed actor), one-for-all (restart all siblings), rest-for-one (restart everything after the failed one). Currently our `consecutive_failures` counter is a crude version. Formalizing this gives us: "if an agent fails 3 times on the same task, escalate to human" or "if the gatekeeper rejects, re-invoke the implementer on the same task (one-for-one)."

2. **"Let it crash" philosophy.** Agents shouldn't try to handle infrastructure errors. If something breaks, crash — the supervisor handles recovery. This aligns perfectly with "no silent failures" — agents don't catch exceptions from SDK calls because agents don't *make* SDK calls.

3. **Location transparency.** Actors don't care if peers are local or remote. If we want distributed orchestrators (multiple machines), the message-passing contract already supports it — the server is the message bus.

4. **Backpressure via bounded mailboxes.** The queue already does this (max incoming tasks, backpressure guards), but the actor model formalizes it as a property of the mailbox, not of the scheduler loop.

### Where we diverge (deliberately)

- **No actor-to-actor messaging.** Strictly hub-and-spoke (orchestrator↔agent). Agents never talk to each other. This is simpler than the full model and avoids coordination bugs.
- **Stateless actors.** Classic actors maintain state between messages. Our agents are pure functions — no memory between invocations. This is simpler and matches the reality that each Claude session starts fresh.
- **Synchronous lifecycle.** The orchestrator waits for an agent to finish before processing its result. Classic actors are fully async. We could go async later, but synchronous is easier to reason about.

## Branch Lifecycle: Scheduler Owns It

**Added 2026-02-18** after post-mortem on TASK-5eb215f6 (agent committed to base branch instead of task branch, no PR created, task stuck in provisional).

### The problem today

There are two competing mechanisms for PR creation:
- **Path A (agent-driven):** `submit-pr` script creates branch, pushes, creates PR, submits to server
- **Path B (scheduler-driven):** Flow steps `push_branch → run_tests → create_pr → submit_to_server` defined in flow.py

Path A preempts Path B — the submit-pr script does everything before the scheduler gets a chance. If Path A fails silently (agent doesn't call submit-pr, or it errors), Path B never fires because `handle_agent_result` doesn't execute flow steps for implementers.

### The fix: scheduler creates branches before spawning

The scheduler should set up the branch in `prepare_task_directory`, before the agent starts:

```
1. create_task_worktree() → detached HEAD on start point
2. Determine branch name via get_task_branch()
3. If branch doesn't exist: git checkout -b {branch_name}
   If branch exists locally: git checkout {branch_name}
   If branch exists on remote only: git checkout -b {branch_name} origin/{branch_name}
4. Spawn agent on the named branch
```

The agent makes commits on that branch. After it exits:

```
5. Scheduler pushes: git push -u origin {branch_name}
6. Scheduler runs tests
7. Scheduler creates PR: gh pr create --base {base_branch} --head {branch_name}
8. Scheduler submits task to provisional
```

### Branch creation rules

Not every task gets a new branch:

| Situation | Branch action |
|-----------|--------------|
| Standalone task, first attempt | Create `agent/TASK-xxx` |
| Standalone task, retry | Checkout existing `agent/TASK-xxx` |
| First task in project | Create project branch (e.g. `feature/foo`) |
| Subsequent task in project | Checkout existing project branch |
| Task with existing remote branch | Fetch and checkout |

`get_task_branch()` already computes the correct branch name. The scheduler just needs to:
1. Check if it exists (`git branch --list` / `git ls-remote`)
2. Create or checkout accordingly

### What this means for the flow model

The flow definition's `claimed → provisional` transition currently lists steps that the agent's `submit-pr` script preempts. With this change:

```yaml
transitions:
  "incoming → claimed":
    # Scheduler: create worktree, setup branch, spawn agent
    agent: implementer
    after_success:
      - push_branch
      - run_tests
      - create_pr
      - submit_to_provisional
    after_failure:
      - move_to_failed
```

`handle_agent_result` must execute these `after_success` steps instead of just calling `sdk.tasks.submit()` directly. This means implementers need to go through the flow system too — not just gatekeepers.

### What agents keep

- `result.json` with `{outcome: "done"}` or `{outcome: "failed", reason: "..."}`
- `run-tests` script — agents should still iterate on test failures during development
- `record-progress` script — for saving context on long tasks

### What agents lose

- `submit-pr` — scheduler handles branch push and PR creation
- `finish` — redundant; `result.json` is the signal
- `fail` — redundant; `result.json` with `outcome: failed` is the signal
- All server API calls — agents never talk to the server

### Migration path

1. Wire `handle_agent_result` to execute flow steps (not just `sdk.tasks.submit()`)
2. Add branch setup to `prepare_task_directory`
3. Keep `submit-pr` temporarily (it's idempotent — if the agent calls it, the scheduler's flow steps will see the PR already exists)
4. Once stable, remove `submit-pr` from agent scripts and update prompts

## Open Questions

- Should agents be able to run tests themselves during development (for iteration), with the orchestrator running them again as verification? Or strictly orchestrator-only?
- What does the result.json schema look like? Is it the same for all agent types, or per-type?
- How does the orchestrator handle long-running agents that need mid-flight feedback (e.g., "you're on the right track, keep going" vs "stop, you're going the wrong direction")?
- Should we adopt OTP supervision strategy vocabulary explicitly? (e.g., `max_restarts: 3, restart_window: 600` per agent blueprint)

## Possible Next Steps

1. **Wire flow steps into `handle_agent_result`** — implementers should execute the flow's `after_success` pipeline, not bypass it
2. **Add branch setup to `prepare_task_directory`** — create/checkout named branch before spawning agent
3. **Define result.json schema** — `{outcome: "done"|"failed", reason?: string}` for implementers
4. **Remove `submit-pr` from implementer agents** — after flow steps handle push/PR/submit
5. **Remove `finish` and `fail` scripts** — `result.json` is the only contract
6. **Define supervision strategies** per agent type (max retries, escalation policy)
