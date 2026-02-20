# Dashboard: async data loading for snappy tab switching

**Priority:** P2

## Problem

The dashboard feels sluggish when switching tabs. The root cause is that `load_data()` runs synchronously in the main loop and makes 7+ API calls, 2 `gh` subprocess calls, and multiple file reads — all blocking the UI.

The main loop is:
```
getch(timeout=2000ms) → handle_input() → render() → getch() → timeout → load_data() → render()
```

Tab switches and cursor moves are instant (just set a state variable), but the next `getch()` blocks for up to 2 seconds, then `load_data()` takes another 1-3 seconds of API/subprocess calls before the screen updates again.

`load_data()` calls `get_project_report()` which makes these calls sequentially:
- `sdk.tasks.list(queue=incoming)`
- `sdk.tasks.list(queue=claimed)`
- `sdk.tasks.list(queue=provisional)`
- `sdk.tasks.list(queue=done)`
- `sdk.tasks.list(queue=failed)`
- `sdk.tasks.list(queue=recycled)`
- `sdk.tasks.list(queue=incoming)` again (health)
- `sdk.tasks.list(queue=claimed)` again (health)
- `gh pr list` subprocess
- `gh pr view` per PR subprocess
- Agent state file reads
- Draft file reads

## Fix

Separate rendering from data fetching. Render immediately from cached state on every keypress. Fetch data in a background thread on a timer.

### 1. Background data thread

```python
import threading

class Dashboard:
    def __init__(self, ...):
        ...
        self._data_lock = threading.Lock()
        self._data_thread = threading.Thread(target=self._data_loop, daemon=True)
        self._data_thread.start()

    def _data_loop(self):
        while self.running:
            report = load_report(self.state.demo_mode, sdk=self.sdk)
            drafts = load_drafts(self.state.demo_mode)
            with self._data_lock:
                self.state.last_report = report
                self.state.last_drafts = drafts
            time.sleep(self.refresh_interval)
```

### 2. Fast input loop

```python
def run(self):
    self.load_data()  # initial blocking load
    self.stdscr.timeout(100)  # 100ms — responsive input
    while self.running:
        self.render()  # always renders from cached state
        key = self.stdscr.getch()
        if key == -1:
            continue  # no input, just re-render (data updates in background)
        else:
            self.running = self.handle_input(key)
```

### 3. Remove load_data from input path

Tab switching and cursor moves should NOT trigger `load_data()`. They just change state and re-render from cache. The only exception is `_load_draft_content()` which reads a local file (fast, fine to keep inline).

Remove `self.load_data()` from:
- The `r`/`R` refresh handler (replace with a "force refresh" that signals the background thread)
- The timeout path in the main loop

## What NOT to change

- The render functions — they already read from `self.state.last_report`
- The data fetching logic in `reports.py` — that stays the same
- The refresh interval default (2s) — just move it to the background

## Acceptance Criteria

- [ ] Tab switching is instant (no perceptible delay)
- [ ] Cursor navigation (j/k) is instant
- [ ] Data refreshes automatically in the background every N seconds
- [ ] `r` key forces an immediate background refresh
- [ ] No race conditions between data thread and render thread
- [ ] Existing dashboard tests still pass
