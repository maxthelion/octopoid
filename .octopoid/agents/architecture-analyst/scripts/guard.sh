#!/usr/bin/env bash
# Guard: check for existing architecture-analyst drafts with status=idea or in_progress.
# If any exist, outputs SKIP so the agent exits early without doing work.
# This prevents duplicate proposals when one is still pending user review or being implemented.

set -euo pipefail

# Source environment variables if available
if [ -f "../env.sh" ]; then
    source "../env.sh"
fi

python3 - <<'EOF'
import sys

try:
    from octopoid.queue_utils import get_sdk
    sdk = get_sdk()
    for status in ('idea', 'in_progress'):
        if sdk.drafts.list(status=status, author='architecture-analyst'):
            print('SKIP')
            break
except Exception as e:
    # If we can't reach the server, don't block the agent — let it run
    import sys
    print(f'Guard check failed: {e}', file=sys.stderr)
EOF
