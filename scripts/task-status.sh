#!/bin/bash
# Show status of all tasks in the queue
# Usage: ./task-status.sh [--verbose]

BOXEN_DIR="${BOXEN_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
QUEUE_DIR="$BOXEN_DIR/.orchestrator/shared/queue"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VERBOSE=false

# Parse arguments
if [ "$1" = "--verbose" ] || [ "$1" = "-v" ]; then
    VERBOSE=true
fi

echo "=== TASK STATUS ==="
echo ""

# Count tasks in each queue
incoming=$(ls "$QUEUE_DIR/incoming/" 2>/dev/null | wc -l | tr -d ' ')
claimed=$(ls "$QUEUE_DIR/claimed/" 2>/dev/null | wc -l | tr -d ' ')
blocked=$(ls "$QUEUE_DIR/blocked/" 2>/dev/null | wc -l | tr -d ' ')
done=$(ls "$QUEUE_DIR/done/" 2>/dev/null | wc -l | tr -d ' ')
failed=$(ls "$QUEUE_DIR/failed/" 2>/dev/null | wc -l | tr -d ' ')

echo "Incoming: $incoming  Claimed: $claimed  Blocked: $blocked  Done: $done  Failed: $failed"
echo ""

# Show incoming tasks
if [ "$incoming" -gt 0 ]; then
    echo "INCOMING:"
    for f in "$QUEUE_DIR/incoming/"*.md; do
        [ -f "$f" ] || continue
        name=$(basename "$f" .md)
        task_id="${name#TASK-}"
        priority=$(grep -m1 "^PRIORITY:" "$f" 2>/dev/null | cut -d: -f2 | tr -d ' ')
        complexity=$(grep -m1 "^COMPLEXITY:" "$f" 2>/dev/null | cut -d: -f2 | tr -d ' ')
        role=$(grep -m1 "^ROLE:" "$f" 2>/dev/null | cut -d: -f2 | tr -d ' ')

        echo "  [$priority] $name ($role)"

        # Verbose display shows file path and claim history
        if [ "$VERBOSE" = true ]; then
            claim_count=$(python3 "$SCRIPT_DIR/task-log-info.py" "$task_id" claims 2>/dev/null || echo "0")
            if [ "$claim_count" != "0" ]; then
                first_claim_ago=$(python3 "$SCRIPT_DIR/task-log-info.py" "$task_id" first-claim-ago 2>/dev/null || echo "?")
                echo "    Previous claims: $claim_count (first: $first_claim_ago)"
            fi
            echo "    File: $f"
        fi
    done
    echo ""
fi

# Show claimed tasks with agent info
if [ "$claimed" -gt 0 ]; then
    echo "CLAIMED:"
    for f in "$QUEUE_DIR/claimed/"*.md; do
        [ -f "$f" ] || continue
        name=$(basename "$f" .md)
        task_id="${name#TASK-}"
        agent=$(grep -m1 "^CLAIMED_BY:" "$f" 2>/dev/null | cut -d: -f2 | tr -d ' ')

        # Try to get progress from agent status
        progress=""
        if [ -n "$agent" ]; then
            status_file="$BOXEN_DIR/.orchestrator/agents/$agent/status.json"
            if [ -f "$status_file" ]; then
                progress=$(python3 -c "import json; d=json.load(open('$status_file')); print(f\"{d.get('progress_percent', '?')}%\")" 2>/dev/null)
            fi
        fi

        # Get claim history from task log
        claim_count=$(python3 "$SCRIPT_DIR/task-log-info.py" "$task_id" claims 2>/dev/null || echo "?")
        last_claim_ago=$(python3 "$SCRIPT_DIR/task-log-info.py" "$task_id" last-claim-ago 2>/dev/null || echo "?")
        first_claim_ago=$(python3 "$SCRIPT_DIR/task-log-info.py" "$task_id" first-claim-ago 2>/dev/null || echo "?")

        # Basic display
        echo "  $name → $agent ${progress:+($progress)}"

        # Verbose display shows claim history and file path
        if [ "$VERBOSE" = true ]; then
            if [ "$claim_count" != "?" ] && [ "$claim_count" -gt 0 ]; then
                echo "    Claims: $claim_count (first: $first_claim_ago, last: $last_claim_ago)"
            fi
            echo "    File: $f"
            log_file="$BOXEN_DIR/.orchestrator/logs/tasks/$name.log"
            if [ -f "$log_file" ]; then
                echo "    Log: $log_file"
            fi
        fi
    done
    echo ""
fi

# Show blocked tasks
if [ "$blocked" -gt 0 ]; then
    echo "BLOCKED:"
    for f in "$QUEUE_DIR/blocked/"*.md; do
        [ -f "$f" ] || continue
        name=$(basename "$f" .md)
        blocked_by=$(grep -m1 "^BLOCKED_BY:" "$f" 2>/dev/null | cut -d: -f2 | tr -d ' ')
        echo "  $name (waiting for: $blocked_by)"
    done
    echo ""
fi

# Show recent failures
if [ "$failed" -gt 0 ]; then
    echo "FAILED (recent):"
    ls -t "$QUEUE_DIR/failed/"*.md 2>/dev/null | head -3 | while read f; do
        name=$(basename "$f" .md)
        echo "  $name"
    done
    echo ""
fi
