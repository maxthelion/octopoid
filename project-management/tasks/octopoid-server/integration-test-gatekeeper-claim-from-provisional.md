# Integration test: gatekeeper claims from provisional queue

**Priority:** P0
**Context:** Gatekeeper claimed tasks from wrong queue in production. No test covers this path.

## Problem

The existing integration tests only test claiming from `incoming` (the default). There is zero test coverage for claiming from `provisional` — the gatekeeper's primary operation. This gap allowed a bug where `queue='provisional'` was silently ignored, causing the gatekeeper to claim incoming tasks.

## What to Write

Add a `TestGatekeeperClaim` class to `tests/integration/test_task_lifecycle.py` (or a new `test_gatekeeper_lifecycle.py`).

### Tests needed

**1. `test_claim_from_provisional_returns_provisional_task`**
- Create task, claim it (incoming→claimed), submit it (claimed→provisional)
- Claim with `queue='provisional'` — should return the task
- Verify the task is still in `provisional` (NOT moved to `claimed`)
- Verify `claimed_by` is set

**2. `test_claim_from_provisional_ignores_incoming_tasks`**
- Create two tasks: leave one in `incoming`, move the other to `provisional`
- Claim with `queue='provisional'` — should only return the provisional task
- The incoming task must not be returned

**3. `test_claim_from_provisional_without_role_filter`**
- Create task with `role='implement'`, move to provisional
- Claim with `queue='provisional'` and no `role_filter` — should still return it
- This is the gatekeeper's actual claim pattern (reviews all roles)

**4. `test_full_gatekeeper_lifecycle`**
- Create task → claim (implementer) → submit → claim from provisional (gatekeeper) → accept → done
- Verify each transition and final state

**5. `test_claim_from_provisional_does_not_move_to_claimed`**
- This is the critical assertion: after claiming from provisional, `task['queue']` must be `'provisional'`, not `'claimed'`
- This is the exact bug that broke the gatekeeper

### Example

```python
class TestGatekeeperClaim:
    def test_claim_from_provisional_returns_provisional_task(
        self, sdk, orchestrator_id, clean_tasks
    ):
        # Setup: create and move task to provisional
        sdk.tasks.create(
            id="gk-test-001",
            file_path="/tmp/gk-test-001.md",
            title="Gatekeeper Test",
            role="implement",
        )
        sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="impl-agent",
            role_filter="implement",
        )
        sdk.tasks.submit(task_id="gk-test-001", commits_count=1, turns_used=5)

        # Act: gatekeeper claims from provisional
        reviewed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="gatekeeper-agent",
            queue="provisional",
        )

        # Assert
        assert reviewed is not None
        assert reviewed["id"] == "gk-test-001"
        assert reviewed["queue"] == "provisional"  # NOT 'claimed'!
        assert reviewed["claimed_by"] == "gatekeeper-agent"
```

## Acceptance Criteria

- [ ] At least 4 tests covering gatekeeper claim from provisional
- [ ] Critical assertion: claim from provisional keeps task in provisional
- [ ] Tests pass against local test server (port 9787)
- [ ] Existing tests still pass
