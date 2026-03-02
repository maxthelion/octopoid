#!/usr/bin/env bash
# Scan for cyclomatic complexity issues using Lizard.
# Outputs a focused report of the worst offenders (high CCN functions).
# Threshold: nloc > 50 or CCN > 10

set -euo pipefail

# Source environment variables if available
if [ -f "../env.sh" ]; then
    source "../env.sh"
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

python3 - "$REPO_ROOT" <<'EOF'
import sys
import subprocess
import json
from pathlib import Path

repo_root = Path(sys.argv[1])
octopoid_dir = repo_root / 'octopoid'

print('=== Complexity Analysis (Lizard) ===')
print(f'Repo: {repo_root}')
print()

def ensure_lizard():
    try:
        result = subprocess.run(['lizard', '--version'], capture_output=True, text=True)
        if result.returncode == 0:
            return True
    except FileNotFoundError:
        pass
    print('Lizard not found — installing...', file=sys.stderr)
    result = subprocess.run(
        [sys.executable, '-m', 'pip', 'install', 'lizard', '--quiet'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f'ERROR: Failed to install lizard: {result.stderr}', file=sys.stderr)
        return False
    return True

print('--- Lizard: Function Complexity Analysis ---')
print('Thresholds: nloc > 50 or cyclomatic_complexity > 10')
print()

lizard_results = []

if ensure_lizard():
    try:
        result = subprocess.run(
            [
                'lizard', str(octopoid_dir),
                '--json',
                '-T', 'nloc=50',
                '-T', 'cyclomatic_complexity=10',
            ],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode in (0, 1):  # lizard exits 1 when thresholds exceeded
            try:
                data = json.loads(result.stdout)
                for fn in data.get('function_list', []):
                    nloc = fn.get('nloc', 0)
                    ccn = fn.get('cyclomatic_complexity', 0)
                    params = fn.get('parameter_count', 0)
                    if nloc > 50 or ccn > 10:
                        lizard_results.append({
                            'file': fn.get('filename', ''),
                            'function': fn.get('name', ''),
                            'nloc': nloc,
                            'ccn': ccn,
                            'params': params,
                            'start_line': fn.get('start_line', 0),
                        })
            except json.JSONDecodeError as e:
                print(f'WARNING: Could not parse lizard JSON output: {e}', file=sys.stderr)
        else:
            print(f'WARNING: Lizard exited {result.returncode}: {result.stderr[:200]}', file=sys.stderr)
    except subprocess.TimeoutExpired:
        print('WARNING: Lizard timed out', file=sys.stderr)
    except Exception as e:
        print(f'WARNING: Lizard failed: {e}', file=sys.stderr)
else:
    print('SKIPPED (lizard not available)')

# Sort by worst offender: prioritise high CCN first, then large functions
lizard_results.sort(key=lambda x: (x['ccn'], x['nloc']), reverse=True)

print(f'Functions exceeding thresholds (nloc>50 or ccn>10): {len(lizard_results)}')
print()

if lizard_results:
    print('Top 20 worst offenders (sorted by CCN descending):')
    for fn in lizard_results[:20]:
        try:
            rel_path = Path(fn['file']).relative_to(repo_root) if fn['file'] else fn['file']
        except ValueError:
            rel_path = fn['file']
        print(f'  [{fn["ccn"]:3d} CCN | {fn["nloc"]:4d} lines | {fn["params"]} params] '
              f'{rel_path}:{fn["start_line"]} — {fn["function"]}')
    print()
else:
    print('No functions exceed complexity thresholds.')

print('=== End of Complexity Report ===')
EOF
