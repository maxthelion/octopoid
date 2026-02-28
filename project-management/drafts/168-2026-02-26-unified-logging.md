# Unified logging via Python logging module

**Captured:** 2026-02-26

## Raw

> Is there a log tool that we can set for all the error logs so they go to the same place? We can look in a single location for recent issues?

## Idea

Replace all `print()` and custom `debug_log()` calls with Python's standard `logging` module. All components write to a single unified log file at `.octopoid/runtime/logs/octopoid.log`. One file to check for any issue.

## Context

The scheduler was crashing for 5 hours due to the `orchestrator` → `octopoid` rename breaking the launchd plist. The only evidence was buried in `.octopoid/runtime/logs/launchd-stderr.log` — a file with no timestamps that nobody checks. Meanwhile, the heartbeat still showed "0m ago" because it reflected the last *successful* tick, not whether the scheduler was continuously running.

Debugging any issue requires checking 5 different locations with different formats.

## Current state

| What | Where | Timestamps? | Format |
|------|-------|-------------|--------|
| Scheduler crashes | `.octopoid/runtime/logs/launchd-stderr.log` | No | Raw Python tracebacks |
| Scheduler output | `.octopoid/runtime/logs/launchd-stdout.log` | Manual `[iso]` prefix | Free text |
| Scheduler debug | `.octopoid/runtime/logs/debug.log` | Yes | `[iso] [SCHEDULER] msg` |
| Per-task events | `.octopoid/logs/tasks/TASK-*.log` | Yes | `[iso] EVENT key=value` |
| Dashboard | `.octopoid/logs/dashboard.log` | ? | Unknown |

Problems:
- `print()` to stdout/stderr is invisible unless you know to check launchd logs
- `debug_log()` only runs when `--debug` flag is set
- No log rotation — files grow forever
- No structured format — can't filter by severity or component
- Errors in result_handler, steps, flow dispatch all use `print()` — they go to launchd-stdout mixed in with informational output

## Proposed fix

### 1. Configure logging in `octopoid/__init__.py`

Set up a root logger on package import:

```python
import logging
import logging.handlers
from pathlib import Path

def _configure_logging():
    log_dir = Path(".octopoid/runtime/logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        log_dir / "octopoid.log",
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3,
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))

    root = logging.getLogger("octopoid")
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)

_configure_logging()
```

### 2. Replace print() and debug_log() calls

Each module gets a named logger:

```python
# octopoid/scheduler.py
logger = logging.getLogger("octopoid.scheduler")

# Before:
print(f"[{datetime.now().isoformat()}] Scheduler starting")
debug_log(f"Circuit breaker tripped for {task_id}")

# After:
logger.info("Scheduler starting")
logger.warning("Circuit breaker tripped for %s", task_id)
```

### 3. Replace custom debug_log() entirely

The `debug_log()` function in scheduler.py and result_handler.py becomes unnecessary — `logger.debug()` handles it. The `--debug` flag just sets the handler level to DEBUG instead of INFO.

### 4. Keep per-task logs as supplementary

The per-task `.octopoid/logs/tasks/TASK-*.log` files are useful for per-task forensics. Keep them, but also log the same events to the unified log so they appear in both places.

### 5. Log rotation

`RotatingFileHandler` with 5MB max and 3 backups = max 20MB of logs. Old entries auto-rotate.

### 6. Stderr capture

Add a `StreamHandler` to stderr at WARNING level so critical errors still appear in launchd-stderr.log:

```python
stderr_handler = logging.StreamHandler()
stderr_handler.setLevel(logging.WARNING)
stderr_handler.setFormatter(...)
root.addHandler(stderr_handler)
```

## Output format

```
2026-02-26T20:05:55 [INFO ] octopoid.scheduler: Scheduler starting
2026-02-26T20:05:55 [INFO ] octopoid.scheduler: Tick complete (claimed=0 incoming=0 provisional=1)
2026-02-26T20:05:56 [WARN ] octopoid.result_handler: update_changelog failed for 2a06729d: git pull --ff-only failed
2026-02-26T20:05:57 [ERROR] octopoid.scheduler: ModuleNotFoundError: No module named orchestrator
2026-02-26T20:06:00 [INFO ] octopoid.scheduler: Circuit breaker: 543cd9d7 lease expired 3 times
2026-02-26T20:06:00 [ERROR] octopoid.result_handler: Flow dispatch error for 2a06729d: RuntimeError(...)
```

One file. `grep ERROR .octopoid/runtime/logs/octopoid.log` answers "what went wrong recently?"

## Scope

Files to change (in rough order):
- `octopoid/__init__.py` — add `_configure_logging()`
- `octopoid/scheduler.py` — replace ~40 `print()` calls and `debug_log()` function
- `octopoid/result_handler.py` — replace ~15 `print()` calls and `debug_log()` wrapper
- `octopoid/steps.py` — replace ~10 `print()` calls
- `octopoid/tasks.py` — replace ~5 `print()` calls
- `octopoid/pool.py`, `octopoid/jobs.py`, `octopoid/hooks.py` — scattered `print()` calls
- Remove `debug_log()` functions from scheduler.py and result_handler.py

## Open Questions

- Should the dashboard also log to the same file, or keep its own?
- Should we add a `/logs` skill to tail/search the unified log from the CLI?


## Invariants

- `unified-log-file`: All scheduler, agent, and hook activity is written to a single log file at `.octopoid/runtime/logs/octopoid.log`. Diagnosing any issue requires checking only one file.
