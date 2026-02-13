#!/bin/bash
# Toggle the orchestrator pause state.
# Usage: ./scripts/pause.sh          (toggle)
#        ./scripts/pause.sh on       (pause)
#        ./scripts/pause.sh off      (resume)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PAUSE_FILE="$PROJECT_DIR/.octopoid/PAUSE"

case "${1:-toggle}" in
  on|pause)
    touch "$PAUSE_FILE"
    echo "Orchestrator paused (created $PAUSE_FILE)"
    ;;
  off|resume)
    rm -f "$PAUSE_FILE"
    echo "Orchestrator resumed (removed $PAUSE_FILE)"
    ;;
  toggle)
    if [ -f "$PAUSE_FILE" ]; then
      rm -f "$PAUSE_FILE"
      echo "Orchestrator resumed (removed $PAUSE_FILE)"
    else
      touch "$PAUSE_FILE"
      echo "Orchestrator paused (created $PAUSE_FILE)"
    fi
    ;;
  status)
    if [ -f "$PAUSE_FILE" ]; then
      echo "Orchestrator is PAUSED"
    else
      echo "Orchestrator is RUNNING"
    fi
    ;;
  *)
    echo "Usage: $0 [on|off|toggle|status]"
    exit 1
    ;;
esac
