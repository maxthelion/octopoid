# Dashboard TUI Refactor: Architecture and Best Practices

**Status:** Idea
**Captured:** 2026-02-19

## Raw

> Refactor octopoid dashboard into some logical chunks. Create some architectural patterns such as separating views from data. What are best practices for building this kind of TUI?

## Idea

`octopoid-dash.py` is a single 2073-line file containing data fetching, state management, rendering logic, input handling, and demo data all mixed together. As we add features (rich detail view, agent status badges, etc.), merge conflicts are frequent and changes are hard to reason about. It needs proper architecture.

## Context

The dashboard has grown organically — it started as a simple curses display and now has 6 tabs, detail views, keyboard navigation, scrolling, color theming, and live data refresh. Recent work (agent status badges, rich detail view from TASK-rich-detail) caused merge conflicts because everything lives in one file. The file is becoming unwieldy for both humans and agents to modify safely.

## Current Structure (single file)

Rough breakdown of `octopoid-dash.py`:
- Lines 1-50: Constants, tab definitions, color pairs
- Lines 50-180: `generate_report()` — data fetching
- Lines 180-530: Demo data generation
- Lines 530-700: Helper functions (formatting, drawing primitives)
- Lines 700-1350: Tab render functions (`render_work_tab`, `render_done_tab`, etc.)
- Lines 1350-1550: Detail renderers (`_render_work_detail`, `_render_done_detail`)
- Lines 1550-1700: State class + input handling
- Lines 1700-2073: Main loop, curses setup, Dashboard class

## TUI Best Practices

### 1. Model-View separation
- **Model**: Data classes holding report state, cursor positions, selected items
- **View**: Pure rendering functions that take a model + window and draw to it
- **Controller**: Input handling that maps keys to model mutations
- The report data itself already comes from `orchestrator/reports.py` — good. But the dashboard still does its own data massaging in `generate_report()`.

### 2. Component-based rendering
- Each visual element (task card, progress bar, status badge, detail panel) should be a self-contained component with its own render function
- Components accept data + bounds, return nothing (draw to window)
- Components should be individually testable (pass a mock curses window)

### 3. Layout management
- Separate layout logic (where things go) from content rendering (what they show)
- Use a simple layout system: panels with fixed/flex sizes
- Split-pane layouts (sidebar + content) should be reusable

### 4. State management
- Single state object (already have `DashboardState`)
- All mutations go through named actions/methods
- State should be serializable (for debugging, testing)

## Proposed Structure

```
octopoid-dash/
  __main__.py          # Entry point, curses setup, main loop
  state.py             # DashboardState + actions
  data.py              # Report fetching + formatting (wraps reports.py)
  theme.py             # Colors, constants, style definitions
  layout.py            # Panel/split layout system
  components/
    __init__.py
    primitives.py      # safe_addstr, draw_progress_bar, format_age, etc.
    task_card.py       # Task card rendering (work tab cards)
    task_detail.py     # Detail view rendering
    table.py           # Generic table renderer (for done tab, etc.)
    status_badge.py    # Agent status badges (ORPH/IDLE/running)
  tabs/
    __init__.py
    work.py            # Work tab (incoming, in_progress, in_review)
    prs.py             # PRs tab
    inbox.py           # Inbox/proposals tab
    agents.py          # Agents tab
    done.py            # Done/failed tab
    drafts.py          # Drafts tab
  demo.py              # Demo data generation (for --demo mode)
```

## Decisions

1. **Framework: Textual** — Full rewrite using Textual. Gets us a proper component model, CSS-like styling, async, and a much better foundation for future features.
2. **Transition: Parallel + swap** — Build the new Textual dashboard alongside the old `octopoid-dash.py`. Swap once complete. No incremental changes that keep old code paths.
3. **Location: `packages/dashboard/`** — Consistent with `packages/server/` and `packages/client/`. Entry point via `__main__.py` so it's runnable with `python -m packages.dashboard` or similar.
4. **Demo mode: Drop it** — No fake data. Test against the real local server.

## Proposed Structure (Textual)

```
packages/dashboard/
  __init__.py
  __main__.py          # Entry point
  app.py               # Textual App subclass, screen management
  data.py              # Report fetching (wraps orchestrator/reports.py)
  screens/
    __init__.py
    main.py            # Main screen with tab navigation
  widgets/
    __init__.py
    task_card.py       # Task card widget
    task_detail.py     # Detail panel widget
    task_table.py      # Table widget (for done tab, etc.)
    status_badge.py    # Agent status badge (ORPH/IDLE/running)
    progress_bar.py    # Turns progress bar
  tabs/
    __init__.py
    work.py            # Work tab
    prs.py             # PRs tab
    inbox.py           # Inbox/proposals tab
    agents.py          # Agents tab
    done.py            # Done/failed tab
    drafts.py          # Drafts tab
  styles/
    dashboard.tcss     # Textual CSS for theming
```

## Next Steps

1. Research Textual patterns — look at example apps for tab navigation, live data refresh, split panes
2. Scaffold `packages/dashboard/` with Textual app + one tab working
3. Port tabs one at a time into the new structure
4. Once feature-complete, swap: delete `octopoid-dash.py`, update any launch scripts
5. No demo mode — test against real server
