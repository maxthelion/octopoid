#!/usr/bin/env bash
# Reset the testing_analyst last-run timestamp so it triggers on the next scheduler tick.
# Also resets the scan script's internal timestamp so it re-analyses from the beginning.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
STATE_FILE="$REPO_ROOT/.octopoid/runtime/scheduler_state.json"
SCAN_TIMESTAMP="$REPO_ROOT/.octopoid/runtime/testing-analyst-last-run"

# Reset scheduler state (controls when the job next runs)
if [ ! -f "$STATE_FILE" ]; then
  echo "State file not found: $STATE_FILE"
  exit 1
fi

python3 -c "
import json, sys
f = '$STATE_FILE'
state = json.load(open(f))
jobs = state.get('jobs', {})
if 'testing_analyst' not in jobs:
    print('testing_analyst not in scheduler state')
    sys.exit(1)
old = jobs['testing_analyst']
jobs['testing_analyst'] = '2000-01-01T00:00:00.000000'
with open(f, 'w') as fh:
    json.dump(state, fh, indent=2)
print(f'Reset testing_analyst: {old} -> epoch')
"

# Also reset the scan script's internal timestamp
if [ -f "$SCAN_TIMESTAMP" ]; then
  echo "2000-01-01T00:00:00" > "$SCAN_TIMESTAMP"
  echo "Reset scan timestamp: $SCAN_TIMESTAMP"
fi
