# /queue-status - Show Queue State

Display the current state of the task queue, diagnose problems, and suggest fixes.

## Implementation

### Step 1: Collect data

Run this Python script to fetch queue data and diagnostics:

```python
import json, os
from pathlib import Path
from orchestrator.queue_utils import get_sdk
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
                    "suggestion": "Check if scheduler is running: `launchctl list | grep octopoid`. Check logs in .octopoid/logs/scheduler.log for errors."
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
                        "suggestion": f"Agent likely crashed or ran out of turns. Check .octopoid/runtime/tasks/{tid}/stdout.log and stderr.log. Consider requeuing with `/retry-failed` or manually investigate."
                    })
        except (ValueError, TypeError):
            pass

# Diagnose failed tasks — check stdout.log and common patterns
failed_tasks = data["queues"].get("failed", [])
if failed_tasks:
    no_stdout = []
    errored = []
    for t in failed_tasks:
        tid = t.get("id", "?")
        stdout_path = Path(f".octopoid/runtime/tasks/{tid}/stdout.log")
        if not stdout_path.exists() or not stdout_path.read_text().strip():
            no_stdout.append(t)
        else:
            errored.append(t)

    if no_stdout:
        data["problems"].append({
            "type": "ran_out_of_turns",
            "detail": f"{len(no_stdout)} failed task(s) have no stdout.log — agent likely crashed or ran out of turns.",
            "task_ids": [t.get("id") for t in no_stdout],
            "suggestion": "These tasks were never completed. Review what the agent did in .octopoid/runtime/tasks/<id>/worktree, then requeue or rewrite the task."
        })
    if errored:
        data["problems"].append({
            "type": "agent_reported_failure",
            "detail": f"{len(errored)} task(s) failed. Check stdout.log for agent output and agent_result messages for the inferred classification.",
            "task_ids": [t.get("id") for t in errored],
            "suggestion": "Check .octopoid/runtime/tasks/<id>/stdout.log and task messages for failure details, then requeue or rewrite the task."
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
```

### Step 2: Analyse and suggest

After displaying the script output, review the problems list. **Look for patterns and root causes, not just symptoms.** If multiple problems share a common underlying issue, diagnose and suggest fixing the root cause rather than treating each symptom individually.

**Think holistically:**
- If several tasks failed at the same step, the step implementation is broken — suggest fixing the step, not requeuing each task
- If tasks are getting stuck in claimed, the error handling or flow transition logic may be wrong — suggest investigating the step runner, not just manually pushing each task through
- If tasks show as done but their work never landed on main, the step verification is missing — suggest adding verification, not just manually merging each one
- If the same class of failure keeps recurring across different tasks, there's a systemic issue — suggest a `/draft-idea` or `/enqueue` for the structural fix

**Per-problem guidance** (but always look for the bigger picture first):

- **Stale heartbeat**: Offer to check scheduler logs or restart it
- **Stuck claimed tasks**: Investigate *why* — check step_progress.json, intervention_context.json, and stderr.log. Is the error handling swallowing exceptions? Is a flow transition being rejected by the server?
- **Ran out of turns**: Check what the agent accomplished and whether to cherry-pick or requeue with a simpler scope
- **Rejected tasks**: Look at the PR comments and suggest rewriting the task
- **Agent failures**: Read the failure reason and suggest fixing the underlying issue
- **Empty incoming + active failed**: Point out that work is blocked and suggest triaging the failed queue
- **API errors / 401s**: Flag the auth issue and suggest checking OCTOPOID_API_KEY
- **Ghost completions** (task done but work not on main): Check step_progress.json — if steps are marked completed that clearly didn't succeed, the step execution pipeline needs verification logic

**Don't just suggest band-aids.** If the queue shows broken state, suggest the systemic fix (draft, task, or code investigation) alongside any immediate unblocking actions. The goal is to stop the problem from recurring, not just clean up after it.

## Related Commands

- `/enqueue` - Add new task
- `/agent-status` - Show agent states
- `/retry-failed` - Retry failed tasks
