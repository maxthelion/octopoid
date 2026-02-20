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
#   MOCK_CRASH           if "true", exit non-zero without writing result.json
#   MOCK_SLEEP           seconds to sleep before acting (default: 0)
#
# Required env vars (set by scheduler):
#   TASK_WORKTREE        path to the task's git worktree
#   TASK_DIR             path to the task directory (result.json written here)

set -euo pipefail

MOCK_OUTCOME="${MOCK_OUTCOME:-success}"
MOCK_DECISION="${MOCK_DECISION:-}"
MOCK_COMMENT="${MOCK_COMMENT:-}"
MOCK_REASON="${MOCK_REASON:-}"
MOCK_COMMITS="${MOCK_COMMITS:-1}"
MOCK_CRASH="${MOCK_CRASH:-false}"
MOCK_SLEEP="${MOCK_SLEEP:-0}"

# Crash mode: exit immediately without writing result.json
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

# Build result.json using Python for safe JSON encoding
python3 - <<PYEOF
import json, os

mock_outcome = os.environ.get('MOCK_OUTCOME', 'success')
mock_decision = os.environ.get('MOCK_DECISION', '')
mock_comment = os.environ.get('MOCK_COMMENT', '')
mock_reason = os.environ.get('MOCK_REASON', '')
task_dir = os.environ.get('TASK_DIR', '.')

if mock_decision:
    # Gatekeeper mode
    if mock_decision == 'approve':
        result = {'status': 'success', 'decision': 'approve', 'comment': mock_comment}
    else:
        result = {'status': 'failure', 'decision': 'reject', 'comment': mock_comment}
else:
    # Implementer mode
    if mock_outcome == 'failure':
        result = {'outcome': 'failed', 'reason': mock_reason}
    elif mock_outcome == 'needs_continuation':
        result = {'outcome': 'needs_continuation'}
    else:
        result = {'outcome': 'done'}

result_path = os.path.join(task_dir, 'result.json')
with open(result_path, 'w') as f:
    json.dump(result, f)

print(f"mock-agent: wrote {result_path}: {result}")
PYEOF
