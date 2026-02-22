# Draft-50 Branch Merge Readiness Assessment

**Status:** Idea
**Captured:** 2026-02-22

## Raw

> The draft-50 branch is nearly ready to merge back to main. Assessment of whether we've covered all planned work, and whether there are gaps in integration testing after building the new features. CI is also broken.

## Assessment

### Branch Scope

35+ commits ahead of main. Major features:
- Actions as natural language instructions (orchestrator/actions.py deleted, action_data JSON field added)
- Human inbox tab (message-based communication between agents and humans)
- Drafts tab split into User/Agent sub-tabs with dynamic action buttons
- Agents tab reorganised into Flow Agents / Background Agents
- Animated spinners on running task cards
- Flow-specific kanban tabs with topological sort
- Task content moved to server (filesystem task files retired)
- Result handler extracted to orchestrator/result_handler.py
- Rejection feedback via message thread (no more task file rewriting)
- Codebase analyst agent (taskless spawn path)
- SDK additions: FlowsAPI, MessagesAPI, updated ActionsAPI

### CI Failures: 17 tests failing

Three categories of failure:

**1. Server submodule out of sync (most failures)**
CI uses `submodules: recursive` so it gets the pinned submodule commit. But the local submodule is behind the deployed server. The test server in CI doesn't have:
- `/api/v1/tasks/{id}/submit` endpoint behaving correctly (409 Conflict on claimed->provisional)
- `/api/v1/tasks/{id}/reject` endpoint (400 Bad Request)
- Proper scope isolation in claim

Tests affected: test_flow.py (12 tests), test_claim_content.py (2 tests)

**2. Missing requeue endpoint**
SDK calls `POST /api/v1/tasks/{id}/requeue` but server has no such endpoint. Tests: test_requeue_returns_to_incoming, test_implementer_failure_requeues

**3. `.octopoid/tasks` directory removed**
The filesystem task retirement (TASK-fb8c568c) removed the .octopoid/tasks directory, but handle_agent_result_via_flow still references `PosixPath('.octopoid/tasks')` somewhere. Error: `[Errno 2] No such file or directory: PosixPath('.octopoid/tasks')`. Tests: test_scheduler_mock.py (3 tests — happy path, idempotent result, merge_pr flows)

### Drafts Status

| Draft | Topic | Status |
|-------|-------|--------|
| 50 | Actions system | Core architecture done, inbox processor not yet built |
| 62 | Proposed queue | Done (pragmatic blocked_by approach) |
| 63 | Dead code cleanup | Done |
| 66 | Dashboard redesign | Done |
| 68 | Actions as instructions | Done (actions.py removed, action_data added) |
| 69 | Codebase analyst | Done (re-applied after revert) |
| 76 | Flow kanban tabs | Done |
| 78 | Human inbox tab | Done |
| 79 | Agents tab | Done |
| 80 | Remove local messages | Done |
| 81 | Message dispatcher | Enqueued (TASK-906a8ebf, just approved) |
| 82 | Retire filesystem tasks | Done |
| 83 | Agents tab two-tier | Done |
| 84 | Kanban activity indicators | Done |
| 85 | Manual intervention state | Enqueued (TASK-e62966cb) |

### Integration Test Coverage Gaps

**Tested:**
- Task CRUD, lifecycle, claim, flow transitions
- Orchestrator registration, heartbeat
- Scheduler lifecycle with mocked agents
- Dependency chains, backpressure

**Not tested:**
- Dashboard tabs (inbox, drafts, agents, work) — no rendering/interaction tests
- action_data button rendering and click handling
- Message creation/filtering via SDK (only basic creation in test_api_server.py)
- Codebase analyst agent spawning
- Message dispatcher / inbox processor pipeline
- Full action lifecycle: agent creates action -> button appears -> user clicks -> message -> worker

### Outstanding Tasks on Branch

- TASK-906a8ebf: Message dispatcher (just approved, in incoming)
- TASK-e62966cb: Manual intervention state (in incoming)

## Blockers Before Merge

### Must fix

1. **Update server submodule** — pin to a commit that has submit, reject, and content endpoints working. The deployed server is ahead of the submodule.
2. **Fix `.octopoid/tasks` reference** — the filesystem task retirement left a stale reference in the result handler path. The 3 test_scheduler_mock failures are caused by this.
3. **Decide on requeue endpoint** — either add it to the server or change the SDK to use PATCH with queue=incoming instead.

### Should fix

4. **Add integration test for action_data round-trip** — create action with action_data, fetch it, verify buttons render. At minimum an API-level test.
5. **Verify claim scoping works** — several tests fail on scope isolation, suggesting the test server doesn't enforce scopes properly.

### Can defer

6. Dashboard rendering tests (Textual testing is complex, can be a separate effort)
7. Message dispatcher e2e test (task 906a8ebf hasn't been implemented yet)
8. Manual intervention state (task e62966cb)

## Open Questions

- Should we merge draft-50 with the two remaining tasks (906a8ebf, e62966cb) still open, or wait for them?
- Is the message dispatcher (906a8ebf) a prerequisite for the branch to be useful, or can it land later?
- Should we do a server submodule update as a separate PR first, to get CI green on main?

## Possible Next Steps

- Fix the 3 stale `.octopoid/tasks` references in result handler
- Update server submodule to latest deployed version
- Run integration tests locally against updated server
- Fix or skip the requeue-related tests
- Merge to main once CI is green
