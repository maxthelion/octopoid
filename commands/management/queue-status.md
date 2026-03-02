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
data = {"heartbeat": None, "heartbeat_stale": False, "queues": {}, "problems": [], "system_health": {}}

# --- SYSTEM HEALTH DATA COLLECTION ---

# 1. Pause state
pause_path = Path(".octopoid/PAUSE")
pause_info = {"paused": False, "reason": None, "timestamp": None}
if pause_path.exists():
    content = pause_path.read_text().strip()
    pause_info["paused"] = True
    try:
        parsed = json.loads(content)
        pause_info["reason"] = parsed.get("reason")
        pause_info["timestamp"] = parsed.get("timestamp")
    except (json.JSONDecodeError, ValueError):
        pause_info["reason"] = content or None
data["system_health"]["pause"] = pause_info

# 2. Systemic failures
health_path = Path(".octopoid/runtime/system_health.json")
health_info = {"consecutive_failures": 0, "last_failure": None}
if health_path.exists():
    try:
        health_data = json.loads(health_path.read_text())
        health_info["consecutive_failures"] = health_data.get("consecutive_failures", 0)
        health_info["last_failure"] = health_data.get("last_failure")
    except (json.JSONDecodeError, OSError):
        pass
data["system_health"]["health"] = health_info

# 3. Orphan PIDs: collect all running PIDs from all agent blueprints
all_pids = {}  # pid -> {"task_id": ..., "instance_name": ...}
agents_dir = Path(".octopoid/runtime/agents")
if agents_dir.exists():
    for pids_file in agents_dir.glob("*/running_pids.json"):
        try:
            pids_data = json.loads(pids_file.read_text())
            for pid_str, info in pids_data.items():
                try:
                    pid = int(pid_str)
                    os.kill(pid, 0)  # signal 0 = check alive
                    all_pids[pid] = info
                except (ValueError, ProcessLookupError, PermissionError):
                    pass  # not running, skip
        except (json.JSONDecodeError, OSError):
            pass
data["system_health"]["running_pids"] = all_pids

# --- HEARTBEAT (last tick) ---
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
for queue in ['incoming', 'claimed', 'provisional', 'requires-intervention', 'done', 'failed']:
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

# Orphan PIDs: cross-reference running PIDs against claimed task IDs
claimed_task_ids = {t.get("id") for t in data["queues"].get("claimed", [])}
orphan_pids = {
    pid: info for pid, info in all_pids.items()
    if info.get("task_id") and info["task_id"] not in claimed_task_ids
}
data["system_health"]["orphan_pids"] = orphan_pids

# --- PROBLEM DETECTION ---

# system_paused problem (insert at front so it's most prominent)
if pause_info["paused"]:
    reason_str = ""
    if pause_info["timestamp"]:
        reason_str += f" auto-paused at {pause_info['timestamp']}"
    if pause_info["reason"]:
        reason_str += f" — reason: {pause_info['reason']}"
    data["problems"].insert(0, {
        "type": "system_paused",
        "detail": f"System is paused.{reason_str}",
        "suggestion": "Run /pause-system to unpause manually, or investigate the root cause first."
    })

# systemic_failures problem
if health_info["consecutive_failures"] > 0:
    last_str = ""
    if health_info["last_failure"]:
        last_str = f" — last: {health_info['last_failure']}"
    data["problems"].append({
        "type": "systemic_failures",
        "detail": f"{health_info['consecutive_failures']} consecutive systemic failures detected{last_str}. System may auto-pause soon.",
        "suggestion": "Investigate the scheduler logs in .octopoid/runtime/logs/octopoid.log for the root cause."
    })

# orphan_pids problem
if orphan_pids:
    pid_list = ", ".join(str(p) for p in list(orphan_pids.keys())[:5])
    data["problems"].append({
        "type": "orphan_pids",
        "detail": f"{len(orphan_pids)} orphan PID(s) found with no matching claimed task — PIDs: {pid_list}",
        "suggestion": "These processes may be stuck or their tasks were moved. Check running_pids.json in .octopoid/runtime/agents/*/."
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

# --- OUTPUT ---

# System health section (shown before queue counts)
print("--- SYSTEM HEALTH ---")
pause = data["system_health"]["pause"]
if pause["paused"]:
    pause_str = "YES"
    if pause["timestamp"]:
        pause_str += f" — auto-paused at {pause['timestamp']}"
    if pause["reason"]:
        pause_str += f" — reason: {pause['reason']}"
    print(f"Paused: {pause_str}")
else:
    print("Paused: no")

consecutive = data["system_health"]["health"]["consecutive_failures"]
last_failure = data["system_health"]["health"]["last_failure"]
if consecutive > 0:
    last_str = f" — last: {last_failure}" if last_failure else ""
    print(f"Systemic failures: {consecutive} consecutive{last_str}")
else:
    print("Systemic failures: 0 consecutive")

print(f"Last tick: {data['heartbeat'] or 'unknown'}")

orphan_count = len(data["system_health"]["orphan_pids"])
if orphan_count > 0:
    orphan_pid_list = ", ".join(str(p) for p in list(data["system_health"]["orphan_pids"].keys())[:5])
    print(f"Orphan PIDs: {orphan_count} — PIDs {orphan_pid_list} have no matching claimed task")
else:
    print("Orphan PIDs: 0")

# Queue summary
for queue in ['incoming', 'claimed', 'provisional', 'requires-intervention', 'done', 'failed']:
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
            if queue == 'requires-intervention':
                ctx_path = Path(f".octopoid/runtime/tasks/{tid}/intervention_context.json")
                if ctx_path.exists():
                    try:
                        ctx = json.loads(ctx_path.read_text())
                        extra = f" | {ctx.get('error_source', '?')}: {ctx.get('error_message', '?')[:40]}"
                    except (json.JSONDecodeError, OSError):
                        pass
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

After displaying the script output, review the problems list. **Look for patterns and root causes, not just symptoms.** If multiple problems share a common underlying issue, diagnose and suggest fixing the root cause rather than treating each symptom individually.

**Think holistically:**
- If several tasks failed at the same step, the step implementation is broken — suggest fixing the step, not requeuing each task
- If tasks are getting stuck in claimed, the error handling or flow transition logic may be wrong — suggest investigating the step runner, not just manually pushing each task through
- If tasks show as done but their work never landed on main, the step verification is missing — suggest adding verification, not just manually merging each one
- If the same class of failure keeps recurring across different tasks, there's a systemic issue — suggest a `/draft-idea` or `/enqueue` for the structural fix

**Per-problem guidance** (but always look for the bigger picture first):

- **System paused**: The system is paused — this is the top priority. Determine if the pause was automatic (systemic failure counter) or manual. Offer to run `/pause-system` to resume, but suggest investigating the root cause first.
- **Systemic failures**: N consecutive failures detected — investigate what type of failure is recurring (spawn, step, transition). Look at scheduler logs and recent failed tasks.
- **Orphan PIDs**: Processes running with no matching claimed task — check if these are stuck processes. They may be consuming resources or holding locks.
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
