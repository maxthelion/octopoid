# Dashboard Tab Redesign — Drafts, Tasks, Remove PRs

Three changes to the Textual dashboard (`packages/dashboard/`).

## 1. Remove PRs tab

The PRs tab calls `gh pr view` per open PR every 5 seconds, burning through the GitHub API rate limit. Remove it entirely.

**`packages/dashboard/app.py`**
- Remove `PRsTab` import and its `TabPane`
- Remove the `"p"` keybinding
- Remove `#prs-tab` from `_apply_report`

**`orchestrator/reports.py`**
- `_gather_prs` is already disabled (returns `[]`). Leave as-is.

## 2. Redesign Drafts tab — server data, status filters, compact layout

Currently the Drafts tab reads from local markdown files. Switch to server data and add filtering.

### Data source

**`orchestrator/reports.py`**
- Add `_gather_drafts(sdk)` function: calls `sdk.drafts.list()`, returns list of `{id, title, status, file_path, created_at}`
- Add `"drafts": _gather_drafts(sdk)` to the report dict in `get_project_report()`

### Drafts tab rewrite

**`packages/dashboard/tabs/drafts.py`** — full rewrite
- Remove `_load_drafts()` and `_load_draft_content()` (filesystem scan functions)
- `DraftsTab` receives drafts from `report["drafts"]` via `update_data()`
- Add a horizontal row of small filter buttons at top of left panel: `Active`, `Idea`, `Partial`, `Complete`, `Archived`
  - All on by default **except** `Archived` (which maps to `superseded` status)
  - Toggle on/off with button press
- Each draft line shows a colored status tag + title, compact (1 line per item):
  - `active` → `ACT` green `#66bb6a`
  - `idea` → `IDEA` cyan `#4fc3f7`
  - `partial` → `PART` orange `#ffa726`
  - `complete` → `DONE` gray `#616161`
  - `superseded` → `ARCH` dim red `#ef5350`
- Content panel loads from `file_path` field on the selected draft

**`packages/dashboard/styles/dashboard.tcss`**
- Add compact styles for draft filter buttons (small, horizontal, 1-line height)
- Tighten draft list item spacing to 1 line per item
- Add draft status color classes

## 3. Replace "Done" tab with "Tasks" tab with sub-tabs

**`packages/dashboard/tabs/done.py`** → create new **`packages/dashboard/tabs/tasks.py`**

The existing `DoneTab` widget stays as a child component. The new `TasksTab` wraps it with a nested `TabbedContent`:

```
Tasks [T]
  ├── Done (existing DoneTab logic — completed + recycled, last 7 days)
  ├── Failed (filtered view — only final_queue == "failed")
  └── Proposed (placeholder — "Proposed tasks — coming soon")
```

**`packages/dashboard/app.py`**
- Replace `DoneTab` import with `TasksTab`
- Replace `Done [D]` tab pane with `Tasks [T]`
- Update keybinding: `"d"` → `"t"` for `show_tab('tasks')`
- Update `_apply_report` to reference `#tasks-tab`

## Final tab layout

```
Work [W] | Inbox [I] | Agents [A] | Tasks [T] | Drafts [F]
```

## Important notes

- The dashboard uses Textual 8. `TabPane` needs `height: 1fr` (already set in dashboard.tcss). When using nested `TabbedContent`, inner `TabPane` also needs proper height.
- `recompose()` is async in Textual 8 — always use `self.call_later(self.recompose)`, never bare `self.recompose()`.
- Dashboard polls every 5 seconds via `set_interval(5, self._fetch_data)`. Do NOT add any `gh` CLI calls to the data path.

## Acceptance criteria

- PRs tab is gone
- Drafts tab shows server-sourced drafts with colored status tags and working filter buttons
- Archived drafts hidden by default, toggling the filter shows them
- Tasks tab has Done/Failed/Proposed sub-tabs
- All tabs render correctly (no blank tabs — verify height: 1fr on TabPane)
