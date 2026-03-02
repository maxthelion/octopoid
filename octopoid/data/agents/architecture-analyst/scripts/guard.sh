#!/usr/bin/env bash
# Guard: check for existing architecture-analyst drafts with status=idea or in_progress.
# Exits 1 (block spawn) if any exist, exits 0 (allow spawn) when none found.
# Used as pre_check in agent.yaml with pre_check_trigger: exit_zero.

# Source environment variables if available
if [ -f "../env.sh" ]; then
    source "../env.sh"
fi

python3 - <<'EOF'
import os
import sys

orchestrator_path = os.environ.get('ORCHESTRATOR_PYTHONPATH', '')
if orchestrator_path:
    sys.path.insert(0, str(__import__('pathlib').Path(orchestrator_path).parent))

try:
    from orchestrator.queue_utils import get_sdk
    sdk = get_sdk()
    for status in ('idea', 'in_progress'):
        if sdk.drafts.list(status=status, author='architecture-analyst'):
            sys.exit(1)
except Exception as e:
    # If we can't reach the server, don't block the agent — let it run
    print(f'Guard check failed: {e}', file=sys.stderr)
    sys.exit(0)
EOF
