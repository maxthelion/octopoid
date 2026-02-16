# /queue-status - Show Queue State

Display the current state of the task queue.

## Implementation

Run this Python script to fetch and display queue status:

```python
from orchestrator.queue_utils import get_sdk
from datetime import datetime, timezone
import json, os, subprocess

sdk = get_sdk()

now = datetime.now(timezone.utc)

def time_ago(iso_str):
    if not iso_str:
        return '?'
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        mins = int((now - dt).total_seconds() / 60)
        if mins < 0:
            return 'future?'
        if mins < 60:
            return f'{mins}m ago'
        if mins < 1440:
            return f'{mins // 60}h {mins % 60}m ago'
        return f'{mins // 1440}d ago'
    except Exception:
        return '?'

def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False

# Load agent states to check PIDs
agent_pids = {}
try:
    agents_dir = os.path.expanduser('.octopoid/runtime/agents')
    if os.path.isdir(agents_dir):
        for fname in os.listdir(agents_dir):
            if fname.endswith('.json'):
                with open(os.path.join(agents_dir, fname)) as f:
                    st = json.load(f)
                    agent_name = fname.replace('.json', '')
                    pid = st.get('pid')
                    task_id = st.get('extra', {}).get('current_task_id')
                    if task_id and pid:
                        agent_pids[task_id] = {'pid': pid, 'alive': pid_alive(pid), 'agent': agent_name}
except Exception:
    pass

# Fetch and display paused tasks
paused_tasks = sdk.tasks.list(paused=1)
if paused_tasks:
    print(f'\nPAUSED ({len(paused_tasks)} tasks)')
    for t in paused_tasks:
        title = (t.get('title') or t.get('id', '?'))[:45]
        priority = t.get('priority', '?')
        tid = t.get('id', '?')
        queue = t.get('queue', '?')
        print(f'  {priority} | {tid:<28} | {title} | queue={queue}')

# Fetch and display tasks by queue
for queue in ['incoming', 'claimed', 'provisional', 'done', 'failed']:
    tasks = sdk.tasks.list(queue=queue)
    print(f'\n{queue.upper()} ({len(tasks)} tasks)')
    if tasks:
        for t in tasks:
            title = (t.get('title') or t.get('id', '?'))[:45]
            priority = t.get('priority', '?')
            tid = t.get('id', '?')
            extra = ''
            if queue == 'claimed':
                agent = t.get('claimed_by', '?')
                claimed_ago = time_ago(t.get('claimed_at'))
                commits = t.get('commits_count', 0)
                # Check if agent process is actually running
                pid_info = agent_pids.get(tid)
                if pid_info and pid_info['alive']:
                    status = 'running'
                elif pid_info and not pid_info['alive']:
                    status = 'ORPHANED'
                else:
                    status = 'no-pid'
                extra = f' | {agent} | {claimed_ago} | {commits}c | {status}'
            if queue == 'provisional':
                pr = t.get('pr_number', '?')
                submitted_ago = time_ago(t.get('submitted_at'))
                extra = f' | PR #{pr} | {submitted_ago}'
            print(f'  {priority} | {tid:<28} | {title}{extra}')
```

## Output Format

```
PAUSED (3 tasks)
  P1 | TASK-300fa689                | Fix broken lease monitor | queue=incoming
  P1 | TASK-2b4f120f                | Add worktree sweeper | queue=incoming
  P2 | TASK-e7198410                | Fix registration error | queue=incoming

INCOMING (4 tasks)
  P0 | TASK-abc12345                | Implement new feature

CLAIMED (2 tasks)
  P1 | TASK-def67890                | Add error handling | implementer-1 | 25m ago | 0c | running
  P1 | TASK-fe10a41c                | Fix scheduler spawn failure | implementer-2 | 1h 30m ago | 0c | ORPHANED

PROVISIONAL (1 tasks)
  P1 | TASK-9438c90d                | Fix dashboard Done tab | PR #27 | 2h ago

DONE (10 tasks)
  P1 | gh-9-4502b83d                | [GH-9] Add debugging endpoints

FAILED (0 tasks)
```

Claimed tasks show: agent name, time since claimed, commit count, and process status.
- **running**: agent PID is alive
- **ORPHANED**: agent PID is dead but task is still claimed (needs requeue)
- **no-pid**: no PID info found in agent state

## Related Commands

- `/enqueue` - Add new task
- `/agent-status` - Show agent states
- `/retry-failed` - Retry failed tasks
