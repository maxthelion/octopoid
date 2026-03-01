#!/usr/bin/env bash
# Scan for architectural quality issues using Lizard (function metrics) and
# jscpd (copy-paste detection). Outputs a structured report of the worst offenders.
#
# Lizard covers: large functions, high cyclomatic complexity, deep nesting, many params.
# jscpd covers: duplicated code blocks across files.

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

print('=== Architecture Analysis ===')
print(f'Repo: {repo_root}')
print()

# ---------------------------------------------------------------------------
# Ensure Lizard is available
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Ensure jscpd is available
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Run Lizard — function-level metrics
# Thresholds: nloc > 50 lines, cyclomatic_complexity > 10
# ---------------------------------------------------------------------------
print('--- Lizard: Function Complexity Analysis ---')
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
                # data has: function_list (list of function dicts)
                for fn in data.get('function_list', []):
                    nloc = fn.get('nloc', 0)
                    ccn = fn.get('cyclomatic_complexity', 0)
                    params = fn.get('parameter_count', 0)
                    # Only report functions exceeding thresholds
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
                print(f'  WARNING: Could not parse lizard JSON output: {e}', file=sys.stderr)
        else:
            print(f'  WARNING: Lizard exited {result.returncode}: {result.stderr[:200]}', file=sys.stderr)
    except subprocess.TimeoutExpired:
        print('  WARNING: Lizard timed out', file=sys.stderr)
    except Exception as e:
        print(f'  WARNING: Lizard failed: {e}', file=sys.stderr)
else:
    print('  SKIPPED (lizard not available)')

# Sort by worst offender: prioritise high CCN first, then large functions
lizard_results.sort(key=lambda x: (x['ccn'], x['nloc']), reverse=True)

print(f'Functions exceeding thresholds (nloc>50 or ccn>10): {len(lizard_results)}')
print()

if lizard_results:
    print('Top 20 worst offenders:')
    for fn in lizard_results[:20]:
        rel_path = Path(fn['file']).relative_to(repo_root) if fn['file'] else fn['file']
        print(f'  [{fn["ccn"]:3d} CCN | {fn["nloc"]:4d} lines | {fn["params"]} params] '
              f'{rel_path}:{fn["start_line"]} — {fn["function"]}')
    print()

# ---------------------------------------------------------------------------
# Run jscpd — copy-paste detection
# ---------------------------------------------------------------------------
print('--- jscpd: Duplicate Code Detection ---')
jscpd_results = []
jscpd_report_dir = Path('/tmp/jscpd-architecture-report')
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
        # jscpd exits 0 even with duplicates; check for report file
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
                print(f'  WARNING: Could not parse jscpd report: {e}', file=sys.stderr)
        else:
            print(f'  WARNING: jscpd report not found at {report_file}', file=sys.stderr)
    except subprocess.TimeoutExpired:
        print('  WARNING: jscpd timed out', file=sys.stderr)
    except Exception as e:
        print(f'  WARNING: jscpd failed: {e}', file=sys.stderr)
else:
    print('  SKIPPED (jscpd not available)')

# Sort by largest duplication first
jscpd_results.sort(key=lambda x: x['lines'], reverse=True)

print(f'Duplicate blocks found (min 5 lines): {len(jscpd_results)}')
print()

if jscpd_results:
    print('Top 15 largest duplications:')
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

# ---------------------------------------------------------------------------
# Summary for agent
# ---------------------------------------------------------------------------
print('=== Summary ===')
print(f'Lizard offenders: {len(lizard_results)} functions exceed complexity thresholds')
print(f'jscpd duplicates: {len(jscpd_results)} duplicate blocks detected')
print()

if not lizard_results and not jscpd_results:
    print('No architectural issues found above thresholds.')
    sys.exit(0)

print('=== End of Report ===')
EOF
