# /queue-status - Show Queue State

Display the current state of the task queue, diagnose problems, and suggest fixes.

## Implementation

### Step 1: Collect data

Run this Python script to fetch queue data and diagnostics:

```python
import json, os
from pathlib import Path
from octopoid.queue_utils import get_sdk
from datetime import datetime, timezone

sdk = get_sdk()
now = datetime.now(timezone.utc)
data = {"heartbeat": None, "heartbeat_stale": False, "queues": {}, "problems": []}

# Get last heartbeat from orchestrator
try:
    orchs = sdk._request('GET', '/api/v1/orchestrators')
    if orchs.get('orchestrators'):
        last_hb = orchs['orchestrators'][0].get('last_heartbeat')
        if last_hb:
            hb_time = datetime.fromisoformat(last_hb.replace('Z', '+00:00'))
            delta = now - hb_time
            mins = int(delta.total_seconds() / 60)
            if mins < 60:
                data["heartbeat"] = f"{mins}m ago"
            elif mins < 1440:
                data["heartbeat"] = f"{mins // 60}h {mins % 60}m ago"
            else:
                data["heartbeat"] = f"{mins // 1440}d ago"
            if mins > 5:
                data["heartbeat_stale"] = True
                data["problems"].append({
                    "type": "stale_heartbeat",
                    "detail": f"Last heartbeat was {data['heartbeat']}. Scheduler may be stopped or crashing.",
                    "suggestion": "Check if scheduler is running: `launchctl list | grep octopoid`. Check logs in .octopoid/runtime/logs/octopoid.log for errors."
                })
except Exception as e:
    data["problems"].append({
        "type": "api_error",
        "detail": f"Failed to reach server: {e}",
        "suggestion": "Check server URL in .octopoid/config.yaml. If seeing 401, the OCTOPOID_API_KEY env var may be stale."
    })

# Fetch tasks by queue
for queue in ['incoming', 'claimed', 'provisional', 'done', 'failed']:
    try:
        tasks = sdk.tasks.list(queue=queue)
        data["queues"][queue] = tasks
    except Exception as e:
        data["queues"][queue] = []
        data["problems"].append({
            "type": "api_error",
            "detail": f"Failed to list {queue}: {e}",
            "suggestion": "Server may be down or auth may be failing."
        })

# Diagnose claimed tasks that look stuck (claimed > 30 min ago)
for t in data["queues"].get("claimed", []):
    claimed_at = t.get("claimed_at")
    if claimed_at:
        try:
            ct = datetime.fromisoformat(claimed_at.replace('Z', '+00:00'))
            age_mins = int((now - ct).total_seconds() / 60)
            t["_age_mins"] = age_mins
            if age_mins > 60:
                # Check if the agent process is still running
                tid = t.get("id", "?")
                pid_file = Path(f".octopoid/runtime/tasks/{tid}/pid")
                pid_alive = False
                if pid_file.exists():
                    try:
                        pid = int(pid_file.read_text().strip())
                        os.kill(pid, 0)  # signal 0 = check if alive
                        pid_alive = True
                    except (ValueError, ProcessLookupError, PermissionError):
                        pass
                if not pid_alive:
                    hours = age_mins // 60
                    data["problems"].append({
                        "type": "stuck_task",
                        "detail": f"Task {tid} claimed {hours}h ago by {t.get('claimed_by','?')} but agent process is not running.",
                        "suggestion": f"Agent likely crashed or ran out of turns. Check .octopoid/runtime/tasks/{tid}/result.json and stderr.log. Consider requeuing with `/retry-failed` or manually investigate."
                    })
        except (ValueError, TypeError):
            pass

# Diagnose failed tasks — check for result.json and common patterns
failed_tasks = data["queues"].get("failed", [])
if failed_tasks:
    no_result = []
    rejected = []
    errored = []
    for t in failed_tasks:
        tid = t.get("id", "?")
        result_path = Path(f".octopoid/runtime/tasks/{tid}/result.json")
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text())
                if result.get("outcome") == "done":
                    rejected.append(t)
                elif result.get("outcome") == "failed":
                    t["_failure_reason"] = result.get("reason", "unknown")
                    errored.append(t)
                else:
                    errored.append(t)
            except (json.JSONDecodeError, IOError):
                no_result.append(t)
        else:
            no_result.append(t)

    if no_result:
        data["problems"].append({
            "type": "ran_out_of_turns",
            "detail": f"{len(no_result)} failed task(s) have no result.json — agent likely ran out of turns.",
            "task_ids": [t.get("id") for t in no_result],
            "suggestion": "These tasks were never completed. Review what the agent did in .octopoid/runtime/tasks/<id>/worktree, then requeue or rewrite the task."
        })
    if rejected:
        data["problems"].append({
            "type": "rejected_by_gatekeeper",
            "detail": f"{len(rejected)} failed task(s) completed but were rejected by the gatekeeper.",
            "task_ids": [t.get("id") for t in rejected],
            "suggestion": "Check PR review comments for rejection reasons. Rewrite the task file to address feedback, then requeue."
        })
    if errored:
        data["problems"].append({
            "type": "agent_reported_failure",
            "detail": f"{len(errored)} task(s) where the agent explicitly reported failure.",
            "task_ids": [t.get("id") for t in errored],
            "reasons": {t.get("id"): t.get("_failure_reason", "unknown") for t in errored},
            "suggestion": "Read the failure reasons and either fix the underlying issue or rewrite the task."
        })

# Print queue summary
print(f"Last tick: {data['heartbeat'] or 'unknown'}")

for queue in ['incoming', 'claimed', 'provisional', 'done', 'failed']:
    tasks = data["queues"].get(queue, [])
    print(f"\n{queue.upper()} ({len(tasks)} tasks)")
    if tasks:
        for t in tasks:
            title = (t.get('title') or t.get('id', '?'))[:50]
            priority = t.get('priority', '?')
            tid = t.get('id', '?')
            extra = ''
            if queue == 'claimed':
                age = t.get('_age_mins')
                age_str = f" {age}m" if age else ""
                extra = f" | {t.get('claimed_by', '?')}{age_str}"
            if queue == 'failed':
                reason = t.get('_failure_reason')
                if reason:
                    extra = f" | {reason[:40]}"
            print(f"  {priority} | {tid:<28} | {title}{extra}")

# Print problems section
if data["problems"]:
    print(f"\n--- PROBLEMS ({len(data['problems'])}) ---")
    for i, p in enumerate(data["problems"], 1):
        print(f"\n{i}. [{p['type']}] {p['detail']}")
        if p.get('task_ids'):
            print(f"   Tasks: {', '.join(p['task_ids'][:5])}")
        if p.get('reasons'):
            for tid, reason in list(p['reasons'].items())[:3]:
                print(f"   {tid}: {reason[:80]}")
        print(f"   -> {p['suggestion']}")
else:
    print("\nNo problems detected.")

# Print recent errors from unified log
log_path = Path(".octopoid/runtime/logs/octopoid.log")
if log_path.exists():
    lines = log_path.read_text().splitlines()
    errors = [l for l in lines if "[ERROR]" in l or "[WARN" in l]
    recent = errors[-10:] if errors else []
    if recent:
        print(f"\n--- RECENT ERRORS (last {len(recent)} from octopoid.log) ---")
        for line in recent:
            print(f"  {line}")
    else:
        print("\n--- LOG: no recent errors in octopoid.log ---")
else:
    # Fall back to launchd stderr
    stderr_path = Path(".octopoid/runtime/logs/launchd-stderr.log")
    if stderr_path.exists():
        lines = stderr_path.read_text().splitlines()
        recent = lines[-10:] if lines else []
        if recent:
            print(f"\n--- RECENT ERRORS (last {len(recent)} from launchd-stderr.log) ---")
            for line in recent:
                print(f"  {line}")
```

### Step 2: Analyse and suggest

After displaying the script output, review the problems list and proactively suggest concrete next steps. For example:

- **Stale heartbeat**: Offer to check scheduler logs or restart it
- **Stuck claimed tasks**: Offer to investigate the worktree and requeue
- **Ran out of turns**: Offer to check what the agent accomplished and whether to cherry-pick or requeue with a simpler scope
- **Rejected tasks**: Offer to look at the PR comments and rewrite the task
- **Agent failures**: Offer to read the failure reason and fix the underlying issue
- **Empty incoming + active failed**: Point out that work is blocked and suggest triaging the failed queue
- **API errors / 401s**: Flag the auth issue and suggest checking OCTOPOID_API_KEY

Be specific — reference task IDs, suggest exact commands, and offer to take action.

## Related Commands

- `/enqueue` - Add new task
- `/agent-status` - Show agent states
- `/retry-failed` - Retry failed tasks
