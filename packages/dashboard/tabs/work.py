"""Work tab — flow-based kanban board with one nested tab per flow."""

from __future__ import annotations

from collections import deque

from rich.text import Text
from textual.app import ComposeResult
from textual.events import Key
from textual.message import Message
from textual.widget import Widget
from textual.widgets import DataTable, Label, ListView, TabbedContent, TabPane
from textual.containers import Horizontal

from ..widgets.task_card import TaskCard
from .base import TabBase


class TaskSelected(Message):
    """Posted when the user selects a task card (presses Enter or clicks)."""

    def __init__(self, task: dict) -> None:
        super().__init__()
        self.task = task


class WorkColumn(Widget):
    """A single kanban column: header + scrollable list of task cards."""

    DEFAULT_CSS = """
    WorkColumn {
        width: 1fr;
        height: 100%;
        border-right: solid $panel-darken-2;
        padding: 0 1;
    }
    WorkColumn:last-of-type {
        border-right: none;
    }
    """

    def __init__(
        self,
        title: str,
        tasks: list,
        show_progress: bool = False,
        agent_map: dict | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._col_title = title
        self._tasks = tasks
        self._show_progress = show_progress
        self._agent_map = agent_map or {}

    def compose(self) -> ComposeResult:
        count = len(self._tasks)
        yield Label(f" {self._col_title} ({count}) ", classes="column-header")
        with ListView(classes="task-list"):
            for task in self._tasks:
                agent = task.get("agent")
                agent_status = "idle"
                if self._show_progress and agent:
                    agent_info = self._agent_map.get(agent)
                    if agent_info:
                        if agent_info.get("paused"):
                            agent_status = "paused"
                        else:
                            agent_status = agent_info.get("status", "idle")
                    else:
                        # Task is claimed but we have no record of the agent
                        agent_status = "orphaned"
                yield TaskCard(
                    task,
                    show_progress=self._show_progress,
                    agent_status=agent_status,
                )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Open detail modal when a task card is selected (Enter or click)."""
        if isinstance(event.item, TaskCard):
            self.post_message(TaskSelected(event.item.task_data))

    def on_key(self, event: Key) -> None:
        """Navigate between columns with left/right arrow keys."""
        if event.key not in ("left", "right"):
            return
        parent = self.parent
        if parent is None:
            return
        columns = list(parent.query(WorkColumn))
        try:
            idx = columns.index(self)
            direction = 1 if event.key == "right" else -1
            new_idx = idx + direction
            if 0 <= new_idx < len(columns):
                event.stop()
                columns[new_idx].query_one(ListView).focus()
        except Exception:
            pass


def _order_states_by_transitions(states: list[str], transitions: list[dict]) -> list[str]:
    """Order states by lifecycle using topological sort on forward transitions.

    The transition list may include reverse/implicit transitions (e.g.
    provisional → incoming for reject, claimed → incoming for requeue).
    These create cycles that break topological sort, so we filter them out
    by only keeping edges that move forward (i.e. don't point back to an
    earlier state in the lifecycle).

    Well-known lifecycle states have a fixed canonical order. Custom states
    are inserted via topological sort on the remaining forward edges.
    Terminal states (done, failed) are always last.
    """
    # Canonical lifecycle position for well-known states.
    # Lower number = earlier in the pipeline.
    _CANONICAL_ORDER: dict[str, int] = {
        "incoming": 0,
        "claimed": 1,
        "needs_continuation": 2,
        "provisional": 3,
        "done": 100,
        "failed": 101,
    }

    known = [s for s in states if s in _CANONICAL_ORDER]
    custom = [s for s in states if s not in _CANONICAL_ORDER]

    # Sort known states by canonical order
    known.sort(key=lambda s: _CANONICAL_ORDER[s])

    if not custom:
        return known

    # For custom states, use transitions to find their position.
    # Check both directions: a known state → custom (insert after source)
    # and custom → known state (insert before target).
    all_ordered = list(known)
    for cs in custom:
        best_pos = None
        for t in transitions:
            if t.get("to") == cs:
                # Something transitions INTO this custom state — place after source
                src = t["from"]
                try:
                    idx = all_ordered.index(src)
                    pos = idx + 1
                    if best_pos is None or pos < best_pos:
                        best_pos = pos
                except ValueError:
                    pass
            if t.get("from") == cs:
                # This custom state transitions INTO something — place before target
                tgt = t["to"]
                try:
                    idx = all_ordered.index(tgt)
                    if best_pos is None or idx < best_pos:
                        best_pos = idx
                except ValueError:
                    pass
        # Default: before terminal states (done/failed)
        if best_pos is None:
            best_pos = len(all_ordered)
            for i, s in enumerate(all_ordered):
                if _CANONICAL_ORDER.get(s, -1) >= 100:
                    best_pos = i
                    break
        # Clamp before terminal states
        terminal_start = len(all_ordered)
        for i, s in enumerate(all_ordered):
            if _CANONICAL_ORDER.get(s, -1) >= 100:
                terminal_start = i
                break
        best_pos = min(best_pos, terminal_start)
        all_ordered.insert(best_pos, cs)

    return all_ordered


class MatrixView(Widget):
    """All-tasks matrix view: rows are tasks, columns are flow stages.

    In-progress tasks show animated >>> chevrons; incoming shows □, done ✓,
    failed ✕. Project tasks show a parent row with child tasks indented below.
    Selecting a row posts TaskSelected so the detail modal opens.
    """

    DEFAULT_CSS = """
    MatrixView {
        height: 100%;
    }
    """

    _CHEVRON_FRAMES: list[str] = [">   ", ">>  ", ">>> "]
    _STATIC_STATES: frozenset[str] = frozenset({"incoming", "done", "failed"})

    def __init__(
        self,
        all_tasks: list[dict],
        flows: list[dict],
        agent_map: dict,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._all_tasks = all_tasks
        self._flows = flows
        self._agent_map = agent_map
        self._chevron_frame: int = 0
        self._row_tasks: dict[str, dict] = {}  # task_id -> task dict

    def _get_ordered_columns(self) -> list[str]:
        """Return the ordered union of all states across all flows."""
        all_states: list[str] = []
        all_transitions: list[dict] = []
        seen: set[str] = set()
        for flow in self._flows:
            for s in flow.get("states", []):
                if s not in seen:
                    all_states.append(s)
                    seen.add(s)
            all_transitions.extend(flow.get("transitions", []))
        # Also include any queue names present in tasks but missing from flow defs
        for task in self._all_tasks:
            q = task.get("queue")
            if q and q not in seen:
                all_states.append(q)
                seen.add(q)
        return _order_states_by_transitions(all_states, all_transitions)

    def _state_icon(self, state: str, frame: int) -> str:
        if state == "incoming":
            return "□"
        if state == "done":
            return "✓"
        if state == "failed":
            return "✕"
        return self._CHEVRON_FRAMES[frame]

    def _cell_value(self, task: dict, state: str, frame: int) -> Text:
        if (task.get("queue") or "incoming") != state:
            return Text("")
        icon = self._state_icon(state, frame)
        if state not in self._STATIC_STATES:
            turns = int(task.get("turns") or 0)
            turn_str = f"{turns}" if turns else ""
            cell = Text(justify="right")
            if turn_str:
                cell.append(turn_str, style="bold #aaaaaa")
            cell.append(icon, style="bold #4fc3f7")
            return cell
        return Text(icon, justify="center")

    def _task_recency_key(self, task: dict) -> str:
        """Return the best available timestamp for a task (for descending sort)."""
        for field in ("updated_at", "claimed_at", "created_at"):
            val = task.get(field)
            if val:
                return val
        return ""

    def _build_rows(self) -> list[tuple[dict, str]]:
        """Return ordered (task, indent_prefix) pairs.

        Rows are sorted by most recent activity first (updated_at > claimed_at >
        created_at). Done tasks are capped at the 5 most recent. Parent tasks
        come first with their children indented beneath them. Orphaned children
        appear last with the indent prefix.
        """
        # Limit done tasks to the 5 most recent
        done_tasks = [t for t in self._all_tasks if (t.get("queue") or "incoming") == "done"]
        done_tasks_sorted = sorted(done_tasks, key=self._task_recency_key, reverse=True)
        allowed_done_ids: set[str] = {t.get("id", "") for t in done_tasks_sorted[:5]}

        filtered: list[dict] = [
            t for t in self._all_tasks
            if (t.get("queue") or "incoming") != "done" or t.get("id", "") in allowed_done_ids
        ]

        # Sort all tasks by most recent activity (descending)
        filtered.sort(key=self._task_recency_key, reverse=True)

        children_map: dict[str, list[dict]] = {}
        for task in filtered:
            pid = task.get("parent_id")
            if pid:
                children_map.setdefault(pid, []).append(task)

        rows: list[tuple[dict, str]] = []
        seen: set[str] = set()

        for task in filtered:
            tid = task.get("id", "")
            if tid in seen or task.get("parent_id"):
                continue
            rows.append((task, ""))
            seen.add(tid)
            for child in children_map.get(tid, []):
                cid = child.get("id", "")
                if cid and cid not in seen:
                    rows.append((child, "  - "))
                    seen.add(cid)

        # Orphaned children whose parent is not in the task list
        for task in filtered:
            tid = task.get("id", "")
            if tid and tid not in seen:
                rows.append((task, "  - "))
                seen.add(tid)

        return rows

    def compose(self) -> ComposeResult:
        columns = self._get_ordered_columns()
        table: DataTable = DataTable(
            classes="matrix-table",
            cursor_type="row",
            zebra_stripes=True,
        )
        table.add_column("Task", key="task_name", width=42)
        for col in columns:
            # Abbreviate headers to keep columns narrow
            abbrev = col[:7]
            table.add_column(abbrev, key=f"col_{col}", width=10)
        yield table

    def on_mount(self) -> None:
        self._populate_table()
        self.set_interval(0.4, self._tick)

    def _populate_table(self) -> None:
        table = self.query_one(DataTable)
        columns = self._get_ordered_columns()
        self._row_tasks = {}
        for task, prefix in self._build_rows():
            tid = task.get("id", "")
            title = task.get("title") or "Untitled"
            short_id = tid[:8] if tid else ""
            task_label = f"{prefix}{short_id} {title}" if short_id else f"{prefix}{title}"
            cells: list[str | Text] = [task_label]
            for col in columns:
                cells.append(self._cell_value(task, col, self._chevron_frame))
            row_key = tid if tid else None
            table.add_row(*cells, key=row_key)
            if tid:
                self._row_tasks[tid] = task

    def _tick(self) -> None:
        """Advance chevron animation frame and update in-progress cells."""
        self._chevron_frame = (self._chevron_frame + 1) % 3
        frame = self._chevron_frame
        try:
            table = self.query_one(DataTable)
        except Exception:
            return
        for tid, task in self._row_tasks.items():
            queue = task.get("queue") or "incoming"
            if queue in self._STATIC_STATES:
                continue
            try:
                table.update_cell(tid, f"col_{queue}", self._cell_value(task, queue, frame))
            except Exception:
                pass

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Post TaskSelected when the user selects a row."""
        key_val = event.row_key.value
        if key_val is not None:
            task = self._row_tasks.get(str(key_val))
            if task:
                self.post_message(TaskSelected(task))


class FlowKanban(Widget):
    """Kanban board for a single flow: one column per state."""

    DEFAULT_CSS = """
    FlowKanban {
        height: 100%;
    }
    """

    def __init__(
        self,
        flow: dict,
        tasks_by_queue: dict[str, list],
        agent_map: dict,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._flow = flow
        self._tasks_by_queue = tasks_by_queue
        self._agent_map = agent_map

    # Terminal states excluded from the kanban board — they are static
    # and take up columns without adding value to the active work view.
    _HIDDEN_STATES = {"done", "failed"}

    def compose(self) -> ComposeResult:
        states = self._flow.get("states", [])
        transitions = self._flow.get("transitions", [])
        ordered_states = _order_states_by_transitions(states, transitions)
        ordered_states = [s for s in ordered_states if s not in self._HIDDEN_STATES]
        flow_name = self._flow.get("name", "default")
        with Horizontal(classes="kanban-board"):
            for state in ordered_states:
                tasks = self._tasks_by_queue.get(state, [])
                show_progress = state not in ("incoming", "done")
                yield WorkColumn(
                    state.title(),
                    tasks,
                    show_progress=show_progress,
                    agent_map=self._agent_map if show_progress else None,
                    classes="kanban-column",
                    id=f"col-{flow_name}-{state}",
                )


class WorkTab(TabBase):
    """Kanban board with nested tabs, one per flow."""

    def compose(self) -> ComposeResult:
        work = self._report.get("work", {})
        flows = self._report.get("flows", [])
        agents = self._report.get("agents", [])
        agent_map: dict = {a["name"]: a for a in agents if "name" in a}

        # Collect all active tasks from all work queues
        all_tasks: list[dict] = []
        for key in ("incoming", "in_progress", "checking", "in_review", "done_today"):
            all_tasks.extend(work.get(key, []))

        # Fall back to a default flow definition if server returned none
        if not flows:
            flows = [{"name": "default", "states": ["incoming", "claimed", "provisional"]}]

        # Group tasks by (flow_name, queue)
        # Pool "project" tasks into the "default" tab — project tasks use the
        # same state machine but belong to a project parent.
        registered_flow_names = {f.get("name") for f in flows}
        tasks_by_flow_queue: dict[str, dict[str, list]] = {}
        for task in all_tasks:
            flow_name = task.get("flow") or "default"
            if flow_name not in registered_flow_names:
                flow_name = "default"
            queue_name = task.get("queue") or "incoming"
            if flow_name not in tasks_by_flow_queue:
                tasks_by_flow_queue[flow_name] = {}
            if queue_name not in tasks_by_flow_queue[flow_name]:
                tasks_by_flow_queue[flow_name][queue_name] = []
            tasks_by_flow_queue[flow_name][queue_name].append(task)

        with TabbedContent(classes="flow-tabs"):
            with TabPane("Matrix", id="flow-tab-matrix"):
                yield MatrixView(all_tasks, flows, agent_map, id="matrix-view")
            for flow in flows:
                flow_name = flow.get("name") or "default"
                tasks_by_queue = tasks_by_flow_queue.get(flow_name, {})
                with TabPane(flow_name.title(), id=f"flow-tab-{flow_name}"):
                    yield FlowKanban(
                        flow,
                        tasks_by_queue,
                        agent_map,
                        id=f"flow-kanban-{flow_name}",
                    )

    def on_mount(self) -> None:
        """Focus the first column's task list on initial mount."""
        self._focus_first_column()

    def on_show(self) -> None:
        """Restore focus to the first column when the tab becomes active."""
        self._focus_first_column()

    def _focus_first_column(self) -> None:
        try:
            columns = list(self.query(WorkColumn))
            if columns:
                columns[0].query_one(ListView).focus()
        except Exception:
            pass

    def _refresh(self) -> None:
        self.refresh(recompose=True)
