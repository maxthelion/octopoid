#!/usr/bin/env bash
# Guard: exit 1 (block spawn) when dead-code-analyst drafts with status=idea or in_progress exist.
# exit 0 (allow spawn) when no pending proposals exist.
# This is the hard gate enforced by the scheduler before spawning — prevents duplicate proposals.

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
        if sdk.drafts.list(status=status, author='dead-code-analyst'):
            print('Pending dead-code-analyst draft found — blocking spawn', file=sys.stderr)
            sys.exit(1)
    sys.exit(0)
except Exception as e:
    # If we can't reach the server, allow the agent to run
    print(f'Guard check failed: {e}', file=sys.stderr)
    sys.exit(0)
EOF
