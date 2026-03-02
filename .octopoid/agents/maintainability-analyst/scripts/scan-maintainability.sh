#!/usr/bin/env bash
# Scan maintainability metrics using wily.
# Outputs Maintainability Index (MI) scores for octopoid source files.
# Lower MI = harder to maintain.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

echo "=== Maintainability Analysis (wily) ==="
echo ""

echo "Building wily index (--max-revisions 1 for speed)..."
python3 -m wily build octopoid/ --max-revisions 1 2>&1 \
    || echo "[wily build failed — wily may not be installed or git history is shallow]"

echo ""
echo "--- wily report: all octopoid files (ascending MI = worst first) ---"
python3 -m wily report octopoid/ 2>&1 \
    || echo "[wily report failed — try 'python3 -m wily report octopoid/scheduler.py' manually]"

echo ""
echo "--- wily detail: high-priority files ---"
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
echo "=== End of Maintainability Report ==="
