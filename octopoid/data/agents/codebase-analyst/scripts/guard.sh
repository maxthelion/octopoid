#!/usr/bin/env bash
# Guard: check for existing codebase-analyst drafts with status=idea.
# If any exist, outputs SKIP so the agent exits early without doing work.
# This prevents duplicate proposals when one is still pending user review.

set -euo pipefail

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
    drafts = sdk.drafts.list(status='idea', author='codebase-analyst')
    if drafts:
        print('SKIP')
except Exception as e:
    # If we can't reach the server, don't block the agent — let it run
    import sys
    print(f'Guard check failed: {e}', file=sys.stderr)
EOF
