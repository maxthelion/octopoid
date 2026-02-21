# /queue-status - Show Queue State

Display the current state of the task queue.

## Implementation

Run this Python script to fetch and display queue status:

```python
from orchestrator.queue_utils import get_sdk
from datetime import datetime, timezone

sdk = get_sdk()

# Get last heartbeat from orchestrator
try:
    orchs = sdk._request('GET', '/api/v1/orchestrators')
    if orchs.get('orchestrators'):
        last_hb = orchs['orchestrators'][0].get('last_heartbeat')
        if last_hb:
            hb_time = datetime.fromisoformat(last_hb.replace('Z', '+00:00'))
            delta = datetime.now(timezone.utc) - hb_time
            mins = int(delta.total_seconds() / 60)
            if mins < 60:
                ago = f"{mins}m ago"
            elif mins < 1440:
                ago = f"{mins // 60}h {mins % 60}m ago"
            else:
                ago = f"{mins // 1440}d ago"
            print(f"Last tick: {ago}")
except Exception:
    pass

# Fetch and display tasks by queue
for queue in ['incoming', 'claimed', 'done', 'failed']:
    tasks = sdk.tasks.list(queue=queue)
    print(f"\n{queue.upper()} ({len(tasks)} tasks)")
    if tasks:
        for t in tasks:
            title = (t.get('title') or t.get('id', '?'))[:50]
            priority = t.get('priority', '?')
            tid = t.get('id', '?')
            extra = ''
            if queue == 'claimed':
                extra = f" | {t.get('claimed_by', '?')}"
            requeue_count = t.get('requeue_count') or 0
            last_error = t.get('last_error') or ''
            health = ''
            if requeue_count > 0:
                health += f" | requeued:{requeue_count}"
            if last_error:
                health += f" | err:{last_error[:60]}"
            print(f"  {priority} | {tid:<28} | {title}{extra}{health}")
```

## Output Format

```
Last tick: 5m ago

INCOMING (5 tasks)
  P1 | gh-9-71fac947              | [GH-9] Add debugging endpoints

CLAIMED (1 tasks)
  P1 | gh-11-2f54e962             | [GH-11] Fix file path inconsistency | implementer-2

DONE (1 tasks)
  P1 | gh-8-2a4ad137              | [GH-8] Improve octopoid init UX

FAILED (2 tasks)
  P1 | gh-7-3b950eb4              | [GH-7] Declare command whitelist | requeued:3 | err:Step failure after 3 attempts: ...
```

## Related Commands

- `/enqueue` - Add new task
- `/agent-status` - Show agent states
- `/retry-failed` - Retry failed tasks
