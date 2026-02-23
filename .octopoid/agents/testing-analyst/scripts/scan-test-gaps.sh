#!/usr/bin/env bash
# Scan for testing gaps in recently completed tasks.
# Reads the last-run timestamp, queries done tasks completed since then,
# checks each task's changed files for corresponding tests, and outputs
# a structured report categorising gaps as 'no tests' (critical) or
# 'unit tests only' (improvement).
# On success, writes the current timestamp to the last-run file.

set -euo pipefail

# Source environment variables if available
if [ -f "../env.sh" ]; then
    source "../env.sh"
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
LAST_RUN_FILE="$REPO_ROOT/.octopoid/runtime/testing-analyst-last-run"

python3 - "$LAST_RUN_FILE" "$REPO_ROOT" <<'EOF'
import os
import sys
import subprocess
import re
from datetime import datetime, timezone
from pathlib import Path

last_run_file = Path(sys.argv[1])
repo_root = Path(sys.argv[2])

orchestrator_path = os.environ.get('ORCHESTRATOR_PYTHONPATH', '')
if orchestrator_path:
    sys.path.insert(0, str(Path(orchestrator_path).parent))

# Read last-run timestamp (default to epoch if file missing — first run)
if last_run_file.exists():
    since_ts = last_run_file.read_text().strip()
else:
    since_ts = '2000-01-01T00:00:00'
    print(f'No last-run file found — analysing all done tasks (first run)', file=sys.stderr)

print(f'=== Testing Gap Analysis ===')
print(f'Scanning tasks completed after: {since_ts}')
print()

try:
    from orchestrator.queue_utils import get_sdk
    sdk = get_sdk()
    all_done = sdk.tasks.list(queue='done')
except Exception as e:
    print(f'ERROR: Could not reach server: {e}', file=sys.stderr)
    sys.exit(1)

# Filter tasks completed after the last-run timestamp
def parse_ts(ts_str):
    if not ts_str:
        return None
    # Handle both with and without microseconds/Z suffix
    for fmt in ('%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%SZ',
                '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S',
                '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    return None

since_dt = parse_ts(since_ts)
recent_tasks = []
for task in all_done:
    completed_at = task.get('completed_at')
    if not completed_at:
        continue
    task_dt = parse_ts(str(completed_at))
    if task_dt and since_dt and task_dt > since_dt:
        recent_tasks.append(task)

print(f'Found {len(recent_tasks)} tasks completed since last run')
print()

if not recent_tasks:
    print('No recent tasks to analyse.')
    # Still update the last-run file
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    last_run_file.parent.mkdir(parents=True, exist_ok=True)
    last_run_file.write_text(now)
    sys.exit(0)

tests_root = repo_root / 'tests'
integration_tests_root = tests_root / 'integration'

gaps = []  # list of dicts: task_id, title, file, gap_type, details

for task in recent_tasks:
    task_id = task.get('id', 'unknown')
    title = task.get('title', '(no title)')
    pr_number = task.get('pr_number')

    # Find files changed in this task via git log (look for merge commits or branch)
    changed_files = []
    try:
        # Try to find changed files from the task branch via git log
        # Look for commits referencing the task ID
        result = subprocess.run(
            ['git', 'log', '--all', '--name-only', '--pretty=format:', f'--grep=TASK-{task_id}'],
            cwd=str(repo_root),
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line and not line.startswith('commit') and '.' in line:
                    changed_files.append(line)
    except (subprocess.TimeoutExpired, Exception):
        pass

    if not changed_files and pr_number:
        # Try to get changed files from the PR branch via git log with PR commit messages
        try:
            result = subprocess.run(
                ['git', 'log', '--all', '--name-only', '--pretty=format:',
                 f'--grep=agent/{task_id[:8]}'],
                cwd=str(repo_root),
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    line = line.strip()
                    if line and '.' in line:
                        changed_files.append(line)
        except (subprocess.TimeoutExpired, Exception):
            pass

    # Deduplicate and filter to source files only
    # Skip: test files (they ARE the tests), config/docs, and agent scripts
    changed_files = list(dict.fromkeys(
        f for f in changed_files
        if f.endswith(('.py', '.ts', '.tsx', '.js', '.jsx', '.sh'))
        and not f.startswith('tests/')
        and not f.startswith('.octopoid/')
        and not f.startswith('project-management/')
        and not f.endswith('__init__.py')
        and '/tests/' not in f  # skip embedded test dirs (e.g. orchestrator/tests/)
        and not Path(f).stem.startswith('test_')  # skip test files anywhere
        and 'CHANGELOG' not in f
        and 'README' not in f
        and 'conftest' not in f
    ))

    if not changed_files:
        # Can't determine changed files — skip but note it
        continue

    for changed_file in changed_files:
        file_stem = Path(changed_file).stem
        file_name = Path(changed_file).name

        # Build search patterns: match the module by import or filename reference
        # Use the full filename (e.g. "reports.py") and import pattern (e.g. "from orchestrator.reports")
        # to avoid false matches (e.g. "reports" matching "test_reports_unrelated")
        module_path = changed_file.replace('/', '.').replace('.py', '')
        search_patterns = [file_name]  # e.g. "reports.py"
        if '.' in module_path:
            # e.g. "from orchestrator.reports" or "import orchestrator.reports"
            search_patterns.append(module_path.rsplit('.', 1)[0] + '.' + file_stem)

        # Check if any unit test exists for this file
        unit_tests_found = []
        # First check for test_<stem>.py naming convention
        for test_dir in tests_root.iterdir() if tests_root.exists() else []:
            if test_dir.is_file() and test_dir.name == f'test_{file_stem}.py':
                unit_tests_found.append(str(test_dir))
        # Then grep for imports/references in non-integration tests
        for pattern in search_patterns:
            try:
                result = subprocess.run(
                    ['grep', '-rl', pattern, str(tests_root)],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    unit_tests_found.extend(
                        f for f in result.stdout.strip().splitlines()
                        if 'integration' not in f and f not in unit_tests_found
                    )
            except (subprocess.TimeoutExpired, Exception):
                pass

        # Check if any integration test covers this file
        integration_tests_found = []
        if integration_tests_root.exists():
            for pattern in search_patterns:
                try:
                    result = subprocess.run(
                        ['grep', '-rl', pattern, str(integration_tests_root)],
                        capture_output=True, text=True, timeout=10
                    )
                    if result.returncode == 0:
                        integration_tests_found.extend(
                            f for f in result.stdout.strip().splitlines()
                            if f not in integration_tests_found
                        )
                except (subprocess.TimeoutExpired, Exception):
                    pass

        # Categorise the gap
        if not unit_tests_found and not integration_tests_found:
            gaps.append({
                'task_id': task_id,
                'title': title,
                'file': changed_file,
                'gap_type': 'no tests',
                'severity': 'critical',
                'details': f'No tests found for {file_name}',
            })
        elif not integration_tests_found and unit_tests_found:
            gaps.append({
                'task_id': task_id,
                'title': title,
                'file': changed_file,
                'gap_type': 'unit tests only',
                'severity': 'improvement',
                'details': (
                    f'Only unit tests found for {file_name} '
                    f'({len(unit_tests_found)} file(s)). '
                    f'No integration/e2e coverage.'
                ),
            })
        # else: has integration tests — no gap

# Deduplicate gaps by file — if multiple tasks changed the same file,
# report it once (keep the first occurrence, which is the most recent task)
seen_files = set()
deduped_gaps = []
for gap in gaps:
    if gap['file'] not in seen_files:
        seen_files.add(gap['file'])
        deduped_gaps.append(gap)
gaps = deduped_gaps

# Output the structured report
critical = [g for g in gaps if g['severity'] == 'critical']
improvements = [g for g in gaps if g['severity'] == 'improvement']

print(f'=== Gap Report ===')
print(f'Critical gaps (no tests): {len(critical)}')
print(f'Improvement opportunities (unit tests only): {len(improvements)}')
print()

if critical:
    print('--- CRITICAL: No Tests ---')
    for gap in critical:
        print(f'  [TASK-{gap["task_id"]}] {gap["title"]}')
        print(f'    File: {gap["file"]}')
        print(f'    Gap:  {gap["details"]}')
        print()

if improvements:
    print('--- IMPROVEMENT: Unit Tests Only ---')
    for gap in improvements:
        print(f'  [TASK-{gap["task_id"]}] {gap["title"]}')
        print(f'    File: {gap["file"]}')
        print(f'    Gap:  {gap["details"]}')
        print()

if not gaps:
    print('No testing gaps found in recently completed tasks.')
    print()

print(f'=== End of Report ===')

# Update the last-run timestamp
now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
last_run_file.parent.mkdir(parents=True, exist_ok=True)
last_run_file.write_text(now)
print(f'\nUpdated last-run timestamp: {now}', file=sys.stderr)
EOF
