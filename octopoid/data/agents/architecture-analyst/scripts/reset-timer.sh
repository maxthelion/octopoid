#!/usr/bin/env bash
# Reset the architecture-analyst last-run timestamp to epoch.
# This causes the next run to analyse the full codebase from the beginning.
# (Currently the architecture analyst always does a full scan, but this script
# is provided for consistency and future use.)

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
LAST_RUN_FILE="$REPO_ROOT/.octopoid/runtime/architecture-analyst-last-run"
EPOCH="2000-01-01T00:00:00"

if [ -f "$LAST_RUN_FILE" ]; then
    OLD="$(cat "$LAST_RUN_FILE")"
    echo "$EPOCH" > "$LAST_RUN_FILE"
    echo "Reset architecture-analyst last-run: $OLD -> $EPOCH"
else
    mkdir -p "$(dirname "$LAST_RUN_FILE")"
    echo "$EPOCH" > "$LAST_RUN_FILE"
    echo "Created architecture-analyst last-run file: $EPOCH"
fi
