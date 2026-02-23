#!/usr/bin/env bash
# Reset the testing-analyst last-run timestamp to epoch.
# This causes the next run to analyse all done tasks from the beginning.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
LAST_RUN_FILE="$REPO_ROOT/.octopoid/runtime/testing-analyst-last-run"
EPOCH="2000-01-01T00:00:00"

if [ -f "$LAST_RUN_FILE" ]; then
    OLD="$(cat "$LAST_RUN_FILE")"
    echo "$EPOCH" > "$LAST_RUN_FILE"
    echo "Reset testing-analyst last-run: $OLD -> $EPOCH"
else
    mkdir -p "$(dirname "$LAST_RUN_FILE")"
    echo "$EPOCH" > "$LAST_RUN_FILE"
    echo "Created testing-analyst last-run file: $EPOCH"
fi
