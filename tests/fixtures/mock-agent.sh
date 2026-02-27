#!/usr/bin/env bash
# Mock agent script — simulates agent behavior without calling Claude.
# Reads configuration from environment variables.
#
# Variables:
#   MOCK_OUTCOME         success | failure | needs_continuation  (default: success)
#   MOCK_DECISION        approve | reject  (if set, gatekeeper mode)
#   MOCK_COMMENT         comment text for gatekeeper approve/reject
#   MOCK_REASON          failure reason text for implementer failure
#   MOCK_COMMITS         number of git commits to make (default: 1)
#   MOCK_CRASH           if "true", exit non-zero without writing stdout.log
#   MOCK_SLEEP           seconds to sleep before acting (default: 0)
#
# Required env vars (set by scheduler):
#   TASK_WORKTREE        path to the task's git worktree
#   TASK_DIR             path to the task directory (stdout.log written here)

set -euo pipefail

MOCK_OUTCOME="${MOCK_OUTCOME:-success}"
MOCK_DECISION="${MOCK_DECISION:-}"
MOCK_COMMENT="${MOCK_COMMENT:-}"
MOCK_REASON="${MOCK_REASON:-}"
MOCK_COMMITS="${MOCK_COMMITS:-1}"
MOCK_CRASH="${MOCK_CRASH:-false}"
MOCK_SLEEP="${MOCK_SLEEP:-0}"

# Crash mode: exit immediately without writing stdout.log
if [ "$MOCK_CRASH" = "true" ]; then
    echo "mock-agent: crash mode, exiting non-zero" >&2
    exit 1
fi

# Sleep before acting (for lease timeout testing)
if [ "$MOCK_SLEEP" -gt 0 ] 2>/dev/null; then
    sleep "$MOCK_SLEEP"
fi

# Change to the task worktree
cd "$TASK_WORKTREE"

# Make N git commits
for i in $(seq 1 "$MOCK_COMMITS"); do
    echo "change $i" >> mock-changes.txt
    git add .
    git commit -m "mock commit $i"
done

# Write stdout.log with a natural language summary the scheduler can infer from
python3 - <<PYEOF
import os

mock_outcome = os.environ.get('MOCK_OUTCOME', 'success')
mock_decision = os.environ.get('MOCK_DECISION', '')
mock_comment = os.environ.get('MOCK_COMMENT', '')
mock_reason = os.environ.get('MOCK_REASON', '')
task_dir = os.environ.get('TASK_DIR', '.')

if mock_decision:
    # Gatekeeper mode — write a review summary
    if mock_decision == 'approve':
        summary = f"""## Gatekeeper Review

### Automated Checks
- [x] Tests pass
- [x] No blocking issues found

### Review Summary
{mock_comment or 'All acceptance criteria are met. The implementation looks correct.'}

### Decision
**DECISION: APPROVED**
"""
    else:
        summary = f"""## Gatekeeper Review

### Automated Checks
- [ ] Tests fail

### Review Summary
{mock_comment or 'The implementation does not meet the acceptance criteria.'}

### Decision
**DECISION: REJECTED**

**Reason:** {mock_comment or 'Tests are failing and acceptance criteria not met.'}
"""
else:
    # Implementer mode — write an implementation summary
    if mock_outcome == 'failure':
        summary = f"Mock agent could not complete the task. Reason: {mock_reason or 'Simulated failure.'}\n\nThe task has failed and cannot be completed."
    elif mock_outcome == 'needs_continuation':
        summary = "Mock agent made partial progress. Continuation needed to finish the remaining work."
    else:
        summary = "Mock agent successfully completed all implementation work. All commits made, all acceptance criteria are met. Implementation is done."

import os
stdout_path = os.path.join(task_dir, 'stdout.log')
with open(stdout_path, 'w') as f:
    f.write(summary)

print(f"mock-agent: wrote {stdout_path}")
print(summary)
PYEOF
