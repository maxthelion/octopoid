#!/bin/bash
# Show status of all tasks in the queue
# Usage: ./task-status.sh

BOXEN_DIR="${BOXEN_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
QUEUE_DIR="$BOXEN_DIR/.octopoid/runtime/shared/queue"

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
        priority=$(grep -m1 "^PRIORITY:" "$f" 2>/dev/null | cut -d: -f2 | tr -d ' ')
        complexity=$(grep -m1 "^COMPLEXITY:" "$f" 2>/dev/null | cut -d: -f2 | tr -d ' ')
        echo "  [$priority] $name ($complexity)"
    done
    echo ""
fi

# Show claimed tasks with agent info
if [ "$claimed" -gt 0 ]; then
    echo "CLAIMED:"
    for f in "$QUEUE_DIR/claimed/"*.md; do
        [ -f "$f" ] || continue
        name=$(basename "$f" .md)
        agent=$(grep -m1 "^CLAIMED_BY:" "$f" 2>/dev/null | cut -d: -f2 | tr -d ' ')
        # Try to get progress from agent status
        progress=""
        if [ -n "$agent" ]; then
            status_file="$BOXEN_DIR/.octopoid/runtime/agents/$agent/status.json"
            if [ -f "$status_file" ]; then
                progress=$(python3 -c "import json; d=json.load(open('$status_file')); print(f\"{d.get('progress_percent', '?')}%\")" 2>/dev/null)
            fi
        fi
        echo "  $name → $agent ${progress:+($progress)}"
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
