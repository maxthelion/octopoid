#!/bin/bash
# Show worktree status for an agent
# Usage: ./agent-worktree.sh <agent-name>

AGENT_NAME="$1"
BOXEN_DIR="${BOXEN_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
AGENTS_DIR="$BOXEN_DIR/.orchestrator/agents"

if [ -z "$AGENT_NAME" ]; then
    echo "Usage: $0 <agent-name>"
    echo ""
    echo "Available agents:"
    ls "$AGENTS_DIR" 2>/dev/null | sed 's/^/  /'
    exit 1
fi

worktree="$AGENTS_DIR/$AGENT_NAME/worktree"

if [ ! -d "$worktree" ]; then
    echo "No worktree for $AGENT_NAME"
    exit 1
fi

echo "=== $AGENT_NAME worktree ==="
echo ""
echo "Branch: $(cd "$worktree" && git branch --show-current 2>/dev/null || echo 'detached')"
echo ""

echo "Uncommitted changes:"
changes=$(cd "$worktree" && git status --short | grep -v '.claude/' | head -10)
if [ -n "$changes" ]; then
    echo "$changes"
else
    echo "  (none)"
fi
echo ""

echo "Recent commits:"
cd "$worktree" && git log --oneline -5
echo ""

# Show plan if it exists
plan_file="$AGENTS_DIR/$AGENT_NAME/plan.md"
if [ -f "$plan_file" ]; then
    echo "=== Plan ==="
    # Show task name and progress
    head -1 "$plan_file"
    echo ""
    # Count completed vs total steps
    total=$(grep -c '^\- \[' "$plan_file" 2>/dev/null || echo 0)
    done=$(grep -c '^\- \[x\]' "$plan_file" 2>/dev/null || echo 0)
    echo "Steps: $done / $total completed"
fi
