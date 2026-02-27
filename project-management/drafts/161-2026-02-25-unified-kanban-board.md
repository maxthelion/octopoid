# Unified kanban board across flows

**Captured:** 2026-02-25
**Related:** Draft #159 (flow validation and protected core transitions)

## Problem

The dashboard kanban board (`packages/dashboard/tabs/work.py`) renders one tab per flow. If you have `default`, `qa`, and `fast` flows, incoming tasks are scattered across three tabs — you can't see all work at a glance without flicking between them.

But most flows share the same core stages: incoming, claimed, done. They only differ in how many review gates sit between claimed and done (the "provisional zone"). Splitting into per-flow tabs fragments a view that should be unified.

## Current implementation

`WorkTab` groups tasks by `(flow_name, queue)` and renders a `FlowKanban` per flow inside a `TabbedContent`. Each `FlowKanban` gets its own set of columns derived from the flow's states via topological sort. Terminal states (done, failed) are already hidden via `_HIDDEN_STATES`.

Key code: `packages/dashboard/tabs/work.py` lines 194-258.

## Proposed design: Matrix view

Replace the traditional kanban (cards inside wide columns) with a compact matrix/grid:

```
tasks               │ in │ claimed │ check1 │ check2 │ done │ fail │
────────────────────┼────┼─────────┼────────┼────────┼──────┼──────┤
task 1              │    │   >>>   │        │        │      │      │
project 1           │    │   >>>   │        │        │      │      │
  - project task 1  │    │         │  >>>   │        │      │      │
  - project task 2  │ □  │         │        │        │      │      │
────────────────────┼────┼─────────┼────────┼────────┼──────┼──────┤
task 2              │    │         │        │        │      │  ✕   │
```

**Key features:**

- **Task names as rows** on the left, stage columns on the right
- **Columns are narrow** — they only hold a small icon, not a full card
- **Animated `>>>` chevrons** for in-progress items (shows movement/activity)
- **Static icons** for waiting (□ in incoming) and terminal states (✕ for failed, ✓ for done)
- **Projects are expandable** with indented child tasks underneath, each with their own status indicator
- **All flows in one view** — columns are the union of all stage names across flows. Tasks only show an icon in the columns relevant to their flow
- **Done column** can be shown or hidden (currently hidden in the kanban — might want to show the ✓ in the matrix since it's just an icon, not a full card)

**Why this works better than kanban:**

1. **Density** — can show 20-30 tasks on screen vs 5-6 with card columns
2. **Unified** — all flows visible at once, no tab switching
3. **Progress at a glance** — animated chevrons immediately show what's active
4. **Projects** — parent/child relationship is natural with indentation, and you can see both the project's overall stage and each child's individual stage
5. **Failed is visible** — the ✕ icon makes failures immediately obvious without a dedicated column eating horizontal space

**Column headers** could be angled or abbreviated to save horizontal space (e.g. "in", "cl", "ch1", "ch2", "dn", "fail").

## Implementation notes

In Textual, this would be a `DataTable` or a custom widget with a grid layout. Each row is a task, each cell is either empty or contains a small status widget. The `>>>` animation can be done with Textual's `set_interval` timer cycling through `>  `, `>> `, `>>>`.

The left column (task names) would need to be wider and left-aligned, while stage columns are narrow and center-aligned. Projects could use a tree-like expand/collapse (Textual has a `Tree` widget, but a simpler approach is just toggling visibility of child rows).

Clicking/selecting a row could open the task detail modal (same as current kanban card selection).

## Open questions

- How to handle tasks in different flows that have different review stages? Show the superset of all columns, with empty cells for stages that don't apply to a task's flow?
- Should done tasks be shown (with ✓) or hidden? Showing them gives a sense of progress but adds noise
- How many columns before horizontal space becomes a problem? Terminal is typically 120-200 chars wide
- Should the stage columns be configurable/collapsible?

## Context

The dashboard uses Textual (terminal UI). The current `FlowKanban` and `WorkColumn` widgets would be replaced entirely by this matrix approach.
