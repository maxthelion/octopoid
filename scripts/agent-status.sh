#!/bin/bash
# Show status of all agents
# Usage: ./agent-status.sh

BOXEN_DIR="${BOXEN_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
AGENTS_DIR="$BOXEN_DIR/.orchestrator/agents"

echo "=== AGENT STATUS ==="
echo ""

for agent_dir in "$AGENTS_DIR"/*/; do
    [ -d "$agent_dir" ] || continue
    agent=$(basename "$agent_dir")

    # Get state
    state_file="$agent_dir/state.json"
    if [ -f "$state_file" ]; then
        running=$(python3 -c "import json; print(json.load(open('$state_file')).get('running', False))" 2>/dev/null)
        pid=$(python3 -c "import json; print(json.load(open('$state_file')).get('pid', ''))" 2>/dev/null)
    else
        running="?"
        pid=""
    fi

    # Get current task and progress
    status_file="$agent_dir/status.json"
    if [ -f "$status_file" ]; then
        task=$(python3 -c "import json; print(json.load(open('$status_file')).get('task_id', ''))" 2>/dev/null)
        progress=$(python3 -c "import json; print(json.load(open('$status_file')).get('progress_percent', ''))" 2>/dev/null)
        subtask=$(python3 -c "import json; print(json.load(open('$status_file')).get('current_subtask', ''))" 2>/dev/null)
    else
        task=""
        progress=""
        subtask=""
    fi

    # Check if process is actually running
    if [ -n "$pid" ] && [ "$pid" != "None" ]; then
        if kill -0 "$pid" 2>/dev/null; then
            state="RUNNING"
        else
            state="STALE"
        fi
    elif [ "$running" = "True" ]; then
        state="RUNNING?"
    else
        state="idle"
    fi

    # Format output
    printf "%-15s %s" "$agent" "$state"
    if [ -n "$task" ] && [ "$task" != "" ]; then
        printf "  %s" "$task"
        if [ -n "$progress" ] && [ "$progress" != "" ]; then
            printf " (%s%%)" "$progress"
        fi
        if [ -n "$subtask" ] && [ "$subtask" != "" ]; then
            printf " - %s" "$subtask"
        fi
    fi
    echo ""
done
