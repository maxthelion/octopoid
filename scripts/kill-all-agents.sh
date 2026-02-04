#!/bin/bash
# Kill all agents and clean up their state
# Usage: ./kill-all-agents.sh

set -e

BOXEN_DIR="${BOXEN_DIR:-/Users/maxwilliams/dev/boxen}"
AGENTS_DIR="$BOXEN_DIR/.orchestrator/agents"
SCRIPT_DIR="$(dirname "$0")"

echo "Killing all agents..."

# Kill all claude agent processes
pkill -f "claude.*agent" 2>/dev/null || true
echo "Killed claude agent processes"

# Clean up each agent directory
for agent_dir in "$AGENTS_DIR"/*/; do
    if [ -d "$agent_dir" ]; then
        agent_name=$(basename "$agent_dir")
        echo "Cleaning up: $agent_name"

        # Remove task marker
        rm -f "$agent_dir/current_task.json"

        # Remove worktree
        if [ -d "$agent_dir/worktree" ]; then
            rm -rf "$agent_dir/worktree"
        fi

        # Reset state
        if [ -f "$agent_dir/state.json" ]; then
            python3 -c "
import json
state_path = '$agent_dir/state.json'
try:
    with open(state_path) as f:
        state = json.load(f)
    state['running'] = False
    state['pid'] = None
    state['current_task'] = None
    with open(state_path, 'w') as f:
        json.dump(state, f, indent=2)
except: pass
" 2>/dev/null || true
        fi

        # Reset status (progress reporting)
        rm -f "$agent_dir/status.json"
    fi
done

# Prune git worktrees
cd "$BOXEN_DIR" && git worktree prune 2>/dev/null || true
echo "Pruned git worktrees"

# Clean up claimed queue (move back to incoming or delete stale)
CLAIMED_DIR="$BOXEN_DIR/.orchestrator/shared/queue/claimed"
INCOMING_DIR="$BOXEN_DIR/.orchestrator/shared/queue/incoming"

if [ -d "$CLAIMED_DIR" ]; then
    for task in "$CLAIMED_DIR"/*.md; do
        if [ -f "$task" ]; then
            task_name=$(basename "$task")
            echo "Moving claimed task back to incoming: $task_name"
            mv "$task" "$INCOMING_DIR/" 2>/dev/null || rm -f "$task"
        fi
    done
fi

echo "All agents killed and cleaned up"
