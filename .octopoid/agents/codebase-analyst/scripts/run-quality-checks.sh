#!/usr/bin/env bash
# Run code quality checks: pytest-cov, vulture, and wily.
# Outputs structured results for the codebase analyst to interpret.
# Run from the agent's scripts/ directory — it will locate the repo root via git.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

echo "=== COVERAGE REPORT (pytest-cov) ==="
echo "Command: python3 -m pytest --cov=octopoid --cov-report=term-missing -q --no-header"
echo ""
python3 -m pytest --cov=octopoid --cov-report=term-missing -q --no-header 2>&1 \
    || echo "[pytest-cov exited non-zero — check output above for errors]"

echo ""
echo "=== UNUSED CODE REPORT (vulture) ==="
echo "Command: python3 -m vulture octopoid/ --min-confidence 80"
echo ""
# vulture exits 1 when it finds things, so suppress the exit code
python3 -m vulture octopoid/ --min-confidence 80 2>&1 || true

echo ""
echo "=== MAINTAINABILITY REPORT (wily) ==="
echo "Building wily index (--max-revisions 1 for speed)..."
echo ""
python3 -m wily build octopoid/ --max-revisions 1 2>&1 \
    || echo "[wily build failed — wily may not be installed or git history is shallow]"

echo ""
echo "Wily report — top files by maintainability index (ascending = worst first):"
echo ""
# Report on the octopoid directory; wily lists files sorted by the default metric
python3 -m wily report octopoid/ 2>&1 \
    || echo "[wily report failed — try 'python3 -m wily report octopoid/scheduler.py' manually]"

echo ""
echo "Wily detail for high-priority files:"
for f in \
    octopoid/scheduler.py \
    octopoid/jobs.py \
    octopoid/flow.py \
    octopoid/queue_utils.py \
    octopoid/agent_runner.py \
    octopoid/task_thread.py; do
    if [ -f "$REPO_ROOT/$f" ]; then
        echo ""
        echo "--- $f ---"
        python3 -m wily report "$f" 2>&1 | head -20 \
            || echo "[wily report failed for $f]"
    fi
done

echo ""
echo "=== End of quality checks ==="
