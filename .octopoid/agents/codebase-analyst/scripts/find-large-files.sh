#!/usr/bin/env bash
# Find the largest source files in the codebase by line count.
# Outputs a sorted report (descending) of the top 30 candidates.
# Excludes generated files, dependencies, and runtime artifacts.

set -euo pipefail

# Find the repo root (works from inside a worktree too)
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

echo "=== Largest source files by line count ==="
echo "Repo root: $REPO_ROOT"
echo ""

# Find all source files, excluding common non-code paths
find "$REPO_ROOT" \
    \( \
        -name "*.py" \
        -o -name "*.ts" \
        -o -name "*.tsx" \
        -o -name "*.js" \
        -o -name "*.jsx" \
        -o -name "*.sh" \
    \) \
    -not -path "*/node_modules/*" \
    -not -path "*/.git/*" \
    -not -path "*/__pycache__/*" \
    -not -path "*/dist/*" \
    -not -path "*/build/*" \
    -not -path "*/.octopoid/runtime/*" \
    -not -path "*/.venv/*" \
    -not -path "*/venv/*" \
    -not -path "*/.tox/*" \
    -not -path "*/coverage/*" \
    -not -path "*/.next/*" \
    -print0 \
    | xargs -0 wc -l 2>/dev/null \
    | grep -v '^\s*0\s' \
    | grep -v '^\s*total$' \
    | sort -rn \
    | head -30 \
    | awk '{ printf "%6d lines  %s\n", $1, $2 }'

echo ""
echo "=== End of report ==="
