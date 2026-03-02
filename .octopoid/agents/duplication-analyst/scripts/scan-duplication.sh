#!/usr/bin/env bash
# Scan for copy-paste code blocks using jscpd.
# Outputs a focused report of the largest duplicate code blocks.
# Threshold: min 5 lines

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

print('=== Duplication Analysis (jscpd) ===')
print(f'Repo: {repo_root}')
print()

def ensure_jscpd():
    try:
        result = subprocess.run(['jscpd', '--version'], capture_output=True, text=True)
        if result.returncode == 0:
            return True
    except FileNotFoundError:
        pass
    print('jscpd not found — installing...', file=sys.stderr)
    result = subprocess.run(
        ['npm', 'install', '-g', 'jscpd'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f'ERROR: Failed to install jscpd: {result.stderr}', file=sys.stderr)
        return False
    return True

print('--- jscpd: Duplicate Code Detection ---')
print('Threshold: min 5 lines')
print()

jscpd_results = []
jscpd_report_dir = Path('/tmp/jscpd-duplication-report')
jscpd_report_dir.mkdir(parents=True, exist_ok=True)

if ensure_jscpd():
    try:
        result = subprocess.run(
            [
                'jscpd',
                str(octopoid_dir),
                '--min-lines', '5',
                '--reporters', 'json',
                '--output', str(jscpd_report_dir),
                '--silent',
            ],
            capture_output=True, text=True, timeout=120
        )
        report_file = jscpd_report_dir / 'jscpd-report.json'
        if report_file.exists():
            try:
                data = json.loads(report_file.read_text())
                duplicates = data.get('duplicates', [])
                for dup in duplicates:
                    first_file = dup.get('firstFile', {})
                    second_file = dup.get('secondFile', {})
                    lines = dup.get('lines', 0)
                    jscpd_results.append({
                        'lines': lines,
                        'file_a': first_file.get('name', ''),
                        'start_a': first_file.get('start', 0),
                        'end_a': first_file.get('end', 0),
                        'file_b': second_file.get('name', ''),
                        'start_b': second_file.get('start', 0),
                        'end_b': second_file.get('end', 0),
                    })
            except (json.JSONDecodeError, Exception) as e:
                print(f'WARNING: Could not parse jscpd report: {e}', file=sys.stderr)
        else:
            print(f'WARNING: jscpd report not found at {report_file}', file=sys.stderr)
    except subprocess.TimeoutExpired:
        print('WARNING: jscpd timed out', file=sys.stderr)
    except Exception as e:
        print(f'WARNING: jscpd failed: {e}', file=sys.stderr)
else:
    print('SKIPPED (jscpd not available)')

# Sort by largest duplication first
jscpd_results.sort(key=lambda x: x['lines'], reverse=True)

print(f'Duplicate blocks found (min 5 lines): {len(jscpd_results)}')
print()

if jscpd_results:
    print('Top 15 largest duplications (sorted by size descending):')
    for dup in jscpd_results[:15]:
        try:
            rel_a = Path(dup['file_a']).relative_to(repo_root) if dup['file_a'] else dup['file_a']
        except ValueError:
            rel_a = dup['file_a']
        try:
            rel_b = Path(dup['file_b']).relative_to(repo_root) if dup['file_b'] else dup['file_b']
        except ValueError:
            rel_b = dup['file_b']
        print(f'  [{dup["lines"]:3d} lines] {rel_a}:{dup["start_a"]}-{dup["end_a"]}')
        print(f'           {rel_b}:{dup["start_b"]}-{dup["end_b"]}')
    print()
else:
    print('No duplicate blocks found above threshold.')

print('=== End of Duplication Report ===')
EOF
