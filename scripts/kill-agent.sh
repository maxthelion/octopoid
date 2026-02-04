#!/bin/bash
# Kill a specific agent and clean up its state
# Usage: ./kill-agent.sh <agent-name>

set -e

AGENT_NAME="$1"
BOXEN_DIR="${BOXEN_DIR:-/Users/maxwilliams/dev/boxen}"
AGENTS_DIR="$BOXEN_DIR/.orchestrator/agents"

if [ -z "$AGENT_NAME" ]; then
    echo "Usage: $0 <agent-name>"
    echo "Available agents:"
    ls "$AGENTS_DIR" 2>/dev/null || echo "  (none)"
    exit 1
fi

AGENT_DIR="$AGENTS_DIR/$AGENT_NAME"

if [ ! -d "$AGENT_DIR" ]; then
    echo "Agent '$AGENT_NAME' not found"
    exit 1
fi

echo "Killing agent: $AGENT_NAME"

# Kill the claude process for this agent
pkill -f "claude.*$AGENT_NAME" 2>/dev/null || true

# Read PID from state and kill if running
if [ -f "$AGENT_DIR/state.json" ]; then
    PID=$(python3 -c "import json; print(json.load(open('$AGENT_DIR/state.json')).get('pid', ''))" 2>/dev/null || true)
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "Killing process $PID"
        kill "$PID" 2>/dev/null || true
    fi
fi

# Remove task marker
rm -f "$AGENT_DIR/current_task.json"
echo "Removed task marker"

# Remove worktree
if [ -d "$AGENT_DIR/worktree" ]; then
    rm -rf "$AGENT_DIR/worktree"
    echo "Removed worktree"
fi

# Prune git worktrees
cd "$BOXEN_DIR" && git worktree prune 2>/dev/null || true

# Reset state
if [ -f "$AGENT_DIR/state.json" ]; then
    python3 -c "
import json
state_path = '$AGENT_DIR/state.json'
with open(state_path) as f:
    state = json.load(f)
state['running'] = False
state['pid'] = None
state['current_task'] = None
with open(state_path, 'w') as f:
    json.dump(state, f, indent=2)
" 2>/dev/null || true
    echo "Reset state"
fi

# Reset status (progress reporting)
rm -f "$AGENT_DIR/status.json"
echo "Reset status"

echo "Agent '$AGENT_NAME' killed and cleaned up"
