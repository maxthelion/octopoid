#!/usr/bin/env bash
# Scan for unused code using vulture.
# Outputs a focused report of unused symbols (imports, functions, variables, re-exports).
# Min confidence: 80%

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

echo "=== Dead Code Analysis (vulture) ==="
echo "Command: python3 -m vulture octopoid/ --min-confidence 80"
echo ""
# vulture exits 1 when it finds things, so suppress the exit code
python3 -m vulture octopoid/ --min-confidence 80 2>&1 || true

echo ""
echo "=== End of Dead Code Report ==="
