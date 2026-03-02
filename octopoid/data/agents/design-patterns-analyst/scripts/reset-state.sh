#!/usr/bin/env bash
# Reset the design-patterns-analyst module rotation state.
# The next run will start from the first module in the rotation list.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
STATE_FILE="$REPO_ROOT/.octopoid/runtime/design-patterns-analyst-state.json"

if [ -f "$STATE_FILE" ]; then
    rm "$STATE_FILE"
    echo "Removed design-patterns-analyst state file — next run will start from the beginning"
else
    echo "State file not found — nothing to reset"
fi
