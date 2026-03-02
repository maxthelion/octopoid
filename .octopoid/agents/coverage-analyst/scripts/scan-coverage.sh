#!/usr/bin/env bash
# Scan test coverage using pytest-cov.
# Outputs a focused coverage table showing files with lowest coverage first.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

echo "=== Coverage Analysis (pytest-cov) ==="
echo "Command: python3 -m pytest --cov=octopoid --cov-report=term-missing -q --no-header"
echo ""
python3 -m pytest --cov=octopoid --cov-report=term-missing -q --no-header 2>&1 \
    || echo "[pytest-cov exited non-zero — check output above for errors]"

echo ""
echo "=== End of Coverage Report ==="
