#!/usr/bin/env python3
"""
Octopoid Dashboard - Terminal UI for monitoring agent orchestration.

Usage:
    python dashboard.py [--refresh N]    # Normal mode (requires .orchestrator)
    python dashboard.py --demo           # Demo mode with sample data
"""

import argparse
import curses
import json
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Global flag for demo mode
DEMO_MODE = False


# ═══════════════════════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AgentState:
    name: str
    role: str
    interval_seconds: int
    paused: bool
    running: bool
    pid: Optional[int]
    last_started: Optional[str]
    last_finished: Optional[str]
    last_exit_code: Optional[int]
    consecutive_failures: int
    total_runs: int
    total_successes: int
    total_failures: int
    current_task: Optional[str]
    blocked_reason: Optional[str] = None


@dataclass
class Task:
    filename: str
    title: str
    role: str
    priority: str
    status: str


# ═══════════════════════════════════════════════════════════════════════════════
# Data Loading
# ═══════════════════════════════════════════════════════════════════════════════

def get_orchestrator_dir() -> Path:
    """Get the .orchestrator directory path."""
    return Path.cwd() / ".orchestrator"


def load_agents_config() -> list[dict]:
    """Load agent configuration from agents.yaml."""
    import yaml
    config_path = get_orchestrator_dir() / "agents.yaml"
    if not config_path.exists():
        return []
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
    return config.get("agents", [])


def load_agent_state(agent_name: str) -> Optional[dict]:
    """Load agent state from state.json."""
    state_path = get_orchestrator_dir() / "agents" / agent_name / "state.json"
    if not state_path.exists():
        return None
    try:
        with open(state_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def get_all_agents() -> list[AgentState]:
    """Get all agents with their current states."""
    if DEMO_MODE:
        return get_demo_agents()

    agents = []
    for config in load_agents_config():
        name = config.get("name", "unknown")
        state = load_agent_state(name) or {}
        extra = state.get("extra", {})
        agents.append(AgentState(
            name=name,
            role=config.get("role", "unknown"),
            interval_seconds=config.get("interval_seconds", 300),
            paused=config.get("paused", False),
            running=state.get("running", False),
            pid=state.get("pid"),
            last_started=state.get("last_started"),
            last_finished=state.get("last_finished"),
            last_exit_code=state.get("last_exit_code"),
            consecutive_failures=state.get("consecutive_failures", 0),
            total_runs=state.get("total_runs", 0),
            total_successes=state.get("total_successes", 0),
            total_failures=state.get("total_failures", 0),
            current_task=state.get("current_task"),
            blocked_reason=extra.get("blocked_reason"),
        ))
    return agents


def get_tasks_by_status() -> dict[str, list[Task]]:
    """Get all tasks grouped by status."""
    if DEMO_MODE:
        return get_demo_tasks()

    queue_dir = get_orchestrator_dir() / "shared" / "queue"
    tasks: dict[str, list[Task]] = {
        "incoming": [],
        "claimed": [],
        "done": [],
        "failed": [],
    }

    for status in tasks.keys():
        status_dir = queue_dir / status
        if not status_dir.exists():
            continue
        for task_file in sorted(status_dir.glob("*.md")):
            try:
                content = task_file.read_text()
                # Parse title from first heading
                title = "Untitled"
                role = "unknown"
                priority = "P2"
                for line in content.split("\n"):
                    if line.startswith("# "):
                        title = line[2:].strip()
                    elif line.startswith("ROLE:"):
                        role = line.split(":", 1)[1].strip()
                    elif line.startswith("PRIORITY:"):
                        priority = line.split(":", 1)[1].strip()
                tasks[status].append(Task(
                    filename=task_file.name,
                    title=title[:50] + "..." if len(title) > 50 else title,
                    role=role,
                    priority=priority,
                    status=status,
                ))
            except IOError:
                pass
    return tasks


def get_recent_logs(max_lines: int = 100) -> list[str]:
    """Get recent log lines from today's scheduler log."""
    if DEMO_MODE:
        return get_demo_logs()

    logs_dir = get_orchestrator_dir() / "logs"
    if not logs_dir.exists():
        return ["No logs directory found"]

    # Find today's scheduler log
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = logs_dir / f"scheduler-{today}.log"

    if not log_file.exists():
        # Try to find most recent log file
        log_files = sorted(logs_dir.glob("scheduler-*.log"), reverse=True)
        if log_files:
            log_file = log_files[0]
        else:
            # Try agent logs
            log_files = sorted(logs_dir.glob("*.log"), reverse=True)
            if log_files:
                log_file = log_files[0]
            else:
                return ["No log files found"]

    try:
        lines = log_file.read_text().split("\n")
        return lines[-max_lines:] if len(lines) > max_lines else lines
    except IOError:
        return ["Could not read log file"]


# ═══════════════════════════════════════════════════════════════════════════════
# Demo Mode Data Generation
# ═══════════════════════════════════════════════════════════════════════════════

DEMO_AGENTS_CONFIG = [
    {"name": "pm-agent", "role": "product_manager", "interval_seconds": 600, "paused": False},
    {"name": "impl-agent-1", "role": "implementer", "interval_seconds": 180, "paused": False},
    {"name": "impl-agent-2", "role": "implementer", "interval_seconds": 180, "paused": False},
    {"name": "test-agent", "role": "tester", "interval_seconds": 120, "paused": False},
    {"name": "review-agent", "role": "reviewer", "interval_seconds": 300, "paused": True},
    {"name": "proposer-arch", "role": "proposer", "interval_seconds": 3600, "paused": False},
]

DEMO_TASKS = {
    "incoming": [
        ("TASK-a1b2c3d4", "Add user authentication flow", "implement", "P1"),
        ("TASK-e5f6g7h8", "Fix memory leak in data processor", "implement", "P0"),
        ("TASK-i9j0k1l2", "Update API documentation", "implement", "P2"),
    ],
    "claimed": [
        ("TASK-m3n4o5p6", "Implement caching layer for queries", "implement", "P1"),
        ("TASK-q7r8s9t0", "Write unit tests for auth module", "test", "P1"),
    ],
    "done": [
        ("TASK-u1v2w3x4", "Set up CI/CD pipeline", "implement", "P1"),
        ("TASK-y5z6a7b8", "Database schema migration", "implement", "P0"),
    ],
    "failed": [
        ("TASK-c9d0e1f2", "Integration tests for payment API", "test", "P1"),
    ],
}

DEMO_LOG_MESSAGES = [
    ("[INFO] [SCHEDULER] Starting scheduler tick", "INFO"),
    ("[INFO] [pm-agent] Checking for work", "INFO"),
    ("[INFO] [impl-agent-1] Claimed task TASK-m3n4o5p6", "INFO"),
    ("[DEBUG] [impl-agent-1] Reading file src/cache.py", "DEBUG"),
    ("[INFO] [impl-agent-1] Writing implementation", "INFO"),
    ("[INFO] [test-agent] Running test suite", "INFO"),
    ("[WARNING] [test-agent] Flaky test detected: test_concurrent_access", "WARNING"),
    ("[INFO] [test-agent] 47/48 tests passed", "INFO"),
    ("[ERROR] [impl-agent-2] Failed to acquire lock - retrying", "ERROR"),
    ("[INFO] [impl-agent-2] Lock acquired, continuing", "INFO"),
    ("[INFO] [SCHEDULER] Agent pm-agent completed successfully", "INFO"),
    ("[DEBUG] [proposer-arch] Analyzing codebase structure", "DEBUG"),
    ("[INFO] [proposer-arch] Generated 3 proposals", "INFO"),
    ("[INFO] [SCHEDULER] Tick complete, sleeping for 60s", "INFO"),
    ("[INFO] [SCHEDULER] Starting scheduler tick", "INFO"),
    ("[WARNING] [review-agent] Agent is paused, skipping", "WARNING"),
    ("[INFO] [impl-agent-1] Creating pull request", "INFO"),
    ("[INFO] [impl-agent-1] PR #42 created successfully", "INFO"),
    ("[INFO] [test-agent] Claimed task TASK-q7r8s9t0", "INFO"),
    ("[DEBUG] [test-agent] Setting up test environment", "DEBUG"),
]

# Store demo state for animation
_demo_state = {
    "tick": 0,
    "agent_states": {},
}


def get_demo_agents() -> list[AgentState]:
    """Generate demo agent data with simulated activity."""
    global _demo_state
    _demo_state["tick"] += 1
    tick = _demo_state["tick"]

    agents = []
    now = datetime.now()

    for i, config in enumerate(DEMO_AGENTS_CONFIG):
        name = config["name"]

        # Initialize state if not exists
        if name not in _demo_state["agent_states"]:
            _demo_state["agent_states"][name] = {
                "running": False,
                "last_started": (now - timedelta(seconds=random.randint(10, config["interval_seconds"]))).isoformat(),
                "total_runs": random.randint(5, 50),
                "total_successes": 0,
                "total_failures": 0,
                "consecutive_failures": 0,
            }
            state = _demo_state["agent_states"][name]
            state["total_successes"] = int(state["total_runs"] * random.uniform(0.85, 0.98))
            state["total_failures"] = state["total_runs"] - state["total_successes"]

        state = _demo_state["agent_states"][name]

        # Simulate running/idle transitions
        if not config["paused"]:
            if state["running"]:
                # 20% chance to finish running
                if random.random() < 0.2:
                    state["running"] = False
                    state["total_runs"] += 1
                    if random.random() < 0.9:  # 90% success rate
                        state["total_successes"] += 1
                        state["consecutive_failures"] = 0
                    else:
                        state["total_failures"] += 1
                        state["consecutive_failures"] += 1
            else:
                # Check if overdue and should start
                last = datetime.fromisoformat(state["last_started"])
                if (now - last).total_seconds() > config["interval_seconds"]:
                    if random.random() < 0.3:  # 30% chance to start
                        state["running"] = True
                        state["last_started"] = now.isoformat()

        # Determine current task
        current_task = None
        if state["running"] and config["role"] in ["implementer", "tester", "reviewer"]:
            claimed_tasks = DEMO_TASKS.get("claimed", [])
            if claimed_tasks:
                current_task = claimed_tasks[i % len(claimed_tasks)][0]

        agents.append(AgentState(
            name=name,
            role=config["role"],
            interval_seconds=config["interval_seconds"],
            paused=config["paused"],
            running=state["running"],
            pid=random.randint(10000, 99999) if state["running"] else None,
            last_started=state["last_started"],
            last_finished=state.get("last_finished"),
            last_exit_code=0 if state["consecutive_failures"] == 0 else 1,
            consecutive_failures=state["consecutive_failures"],
            total_runs=state["total_runs"],
            total_successes=state["total_successes"],
            total_failures=state["total_failures"],
            current_task=current_task,
            blocked_reason=None,
        ))

    return agents


def get_demo_tasks() -> dict[str, list[Task]]:
    """Generate demo task data."""
    tasks: dict[str, list[Task]] = {
        "incoming": [],
        "claimed": [],
        "done": [],
        "failed": [],
    }

    for status, task_list in DEMO_TASKS.items():
        for task_id, title, role, priority in task_list:
            tasks[status].append(Task(
                filename=f"{task_id}.md",
                title=f"[{task_id}] {title}",
                role=role,
                priority=priority,
                status=status,
            ))

    return tasks


def get_demo_logs() -> list[str]:
    """Generate demo log data with timestamps."""
    now = datetime.now()
    logs = []

    for i, (msg, _) in enumerate(DEMO_LOG_MESSAGES):
        ts = (now - timedelta(seconds=(len(DEMO_LOG_MESSAGES) - i) * 5)).strftime("%Y-%m-%dT%H:%M:%S")
        logs.append(f"[{ts}] {msg}")

    # Add some live-looking entries
    logs.append(f"[{now.strftime('%Y-%m-%dT%H:%M:%S')}] [INFO] [SCHEDULER] Dashboard connected")

    return logs


# ═══════════════════════════════════════════════════════════════════════════════
# UI Components
# ═══════════════════════════════════════════════════════════════════════════════

class Colors:
    """Color pair indices."""
    DEFAULT = 0
    HEADER = 1
    RUNNING = 2
    SUCCESS = 3
    FAILURE = 4
    WARNING = 5
    PAUSED = 6
    BORDER = 7
    HIGHLIGHT = 8
    P0 = 9
    P1 = 10
    P2 = 11
    BLOCKED = 12


def init_colors():
    """Initialize color pairs."""
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(Colors.HEADER, curses.COLOR_CYAN, -1)
    curses.init_pair(Colors.RUNNING, curses.COLOR_GREEN, -1)
    curses.init_pair(Colors.SUCCESS, curses.COLOR_GREEN, -1)
    curses.init_pair(Colors.FAILURE, curses.COLOR_RED, -1)
    curses.init_pair(Colors.WARNING, curses.COLOR_YELLOW, -1)
    curses.init_pair(Colors.PAUSED, curses.COLOR_MAGENTA, -1)
    curses.init_pair(Colors.BORDER, curses.COLOR_BLUE, -1)
    curses.init_pair(Colors.HIGHLIGHT, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(Colors.P0, curses.COLOR_RED, -1)
    curses.init_pair(Colors.P1, curses.COLOR_YELLOW, -1)
    curses.init_pair(Colors.P2, curses.COLOR_WHITE, -1)
    curses.init_pair(Colors.BLOCKED, curses.COLOR_YELLOW, -1)


def draw_box(win, y: int, x: int, height: int, width: int, title: str = ""):
    """Draw a box with optional title."""
    if height < 2 or width < 2:
        return

    # Draw corners and edges
    try:
        win.attron(curses.color_pair(Colors.BORDER))

        # Top edge
        win.addch(y, x, curses.ACS_ULCORNER)
        win.hline(y, x + 1, curses.ACS_HLINE, width - 2)
        win.addch(y, x + width - 1, curses.ACS_URCORNER)

        # Bottom edge
        win.addch(y + height - 1, x, curses.ACS_LLCORNER)
        win.hline(y + height - 1, x + 1, curses.ACS_HLINE, width - 2)
        try:
            win.addch(y + height - 1, x + width - 1, curses.ACS_LRCORNER)
        except curses.error:
            pass  # Bottom-right corner can fail

        # Sides
        for i in range(1, height - 1):
            win.addch(y + i, x, curses.ACS_VLINE)
            try:
                win.addch(y + i, x + width - 1, curses.ACS_VLINE)
            except curses.error:
                pass

        win.attroff(curses.color_pair(Colors.BORDER))

        # Title
        if title and width > len(title) + 4:
            win.attron(curses.color_pair(Colors.HEADER) | curses.A_BOLD)
            win.addstr(y, x + 2, f" {title} ")
            win.attroff(curses.color_pair(Colors.HEADER) | curses.A_BOLD)
    except curses.error:
        pass


def draw_progress_bar(win, y: int, x: int, width: int, progress: float, color: int):
    """Draw a progress bar."""
    if width < 3:
        return

    bar_width = width - 2
    filled = int(bar_width * min(1.0, max(0.0, progress)))

    try:
        win.addstr(y, x, "[")
        win.attron(curses.color_pair(color))
        win.addstr(y, x + 1, "█" * filled)
        win.attroff(curses.color_pair(color))
        win.addstr(y, x + 1 + filled, "░" * (bar_width - filled))
        win.addstr(y, x + width - 1, "]")
    except curses.error:
        pass


def format_time_delta(seconds: float) -> str:
    """Format seconds as human-readable time."""
    if seconds < 0:
        return "overdue"
    elif seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    else:
        return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"


def calculate_next_run_progress(agent: AgentState) -> tuple[float, str]:
    """Calculate progress until next run and time remaining."""
    if agent.paused:
        return 0.0, "PAUSED"
    if agent.running:
        return 1.0, "RUNNING"
    if agent.blocked_reason:
        # Show the reason (e.g., "pr_limit:10/10" -> "10/10 PRs")
        parts = agent.blocked_reason.split(":")
        if len(parts) == 2 and parts[0] == "pr_limit":
            return 0.0, f"{parts[1]} PRs"
        elif len(parts) == 2 and parts[0] == "claimed_limit":
            return 0.0, f"{parts[1]} claimed"
        elif parts[0] == "no_tasks":
            return 0.0, "no tasks"
        else:
            return 0.0, parts[0][:12]
    if not agent.last_started:
        return 1.0, "READY"

    try:
        last = datetime.fromisoformat(agent.last_started.replace("Z", "+00:00"))
        now = datetime.now(last.tzinfo) if last.tzinfo else datetime.now()
        elapsed = (now - last).total_seconds()
        remaining = agent.interval_seconds - elapsed
        progress = min(1.0, elapsed / agent.interval_seconds)

        if remaining <= 0:
            return 1.0, "READY"
        return progress, format_time_delta(remaining)
    except (ValueError, TypeError):
        return 0.0, "???"


# ═══════════════════════════════════════════════════════════════════════════════
# Panel Renderers
# ═══════════════════════════════════════════════════════════════════════════════

def render_agents_panel(win, y: int, x: int, height: int, width: int, agents: list[AgentState]):
    """Render the agents status panel."""
    draw_box(win, y, x, height, width, "🐙 Agents")

    if height < 4 or width < 20:
        return

    inner_y = y + 1
    inner_x = x + 1
    inner_width = width - 2
    inner_height = height - 2

    # Header row
    try:
        win.attron(curses.color_pair(Colors.HEADER) | curses.A_BOLD)
        header = f"{'Name':<16} {'Role':<12} {'Status':<10} {'Next Run':<12}"
        win.addnstr(inner_y, inner_x, header, inner_width)
        win.attroff(curses.color_pair(Colors.HEADER) | curses.A_BOLD)
    except curses.error:
        pass

    # Agent rows
    for i, agent in enumerate(agents):
        row_y = inner_y + 1 + i * 2
        if row_y >= y + height - 2:
            break

        # Determine status and color
        if agent.paused:
            status = "PAUSED"
            color = Colors.PAUSED
        elif agent.running:
            status = "RUNNING"
            color = Colors.RUNNING
        elif agent.blocked_reason:
            # Show shortened blocked reason
            reason = agent.blocked_reason.split(":")[0]  # e.g., "pr_limit" from "pr_limit:10/10"
            status = f"BLOCKED"
            color = Colors.BLOCKED
        elif agent.consecutive_failures > 0:
            status = f"FAIL({agent.consecutive_failures})"
            color = Colors.FAILURE
        else:
            status = "IDLE"
            color = Colors.SUCCESS

        progress, time_str = calculate_next_run_progress(agent)

        try:
            # Agent info line
            win.attron(curses.color_pair(color))
            name_display = agent.name[:16].ljust(16)
            role_display = agent.role[:12].ljust(12)
            status_display = status[:10].ljust(10)
            info_line = f"{name_display} {role_display} {status_display} {time_str:<12}"
            win.addnstr(row_y, inner_x, info_line, inner_width)
            win.attroff(curses.color_pair(color))

            # Progress bar (if space permits)
            if row_y + 1 < y + height - 1 and inner_width > 20:
                bar_width = min(30, inner_width - 2)
                if agent.running:
                    bar_color = Colors.RUNNING
                elif agent.blocked_reason:
                    bar_color = Colors.BLOCKED
                elif agent.paused:
                    bar_color = Colors.PAUSED
                else:
                    bar_color = Colors.SUCCESS
                draw_progress_bar(win, row_y + 1, inner_x, bar_width, progress, bar_color)

                # Current task if running
                if agent.running and agent.current_task and inner_width > 35:
                    task_display = f" → {agent.current_task}"
                    win.addnstr(row_y + 1, inner_x + bar_width + 1, task_display, inner_width - bar_width - 2)
        except curses.error:
            pass


def render_tasks_panel(win, y: int, x: int, height: int, width: int, tasks: dict[str, list[Task]]):
    """Render the tasks by status panel."""
    draw_box(win, y, x, height, width, "📋 Tasks")

    if height < 4 or width < 20:
        return

    inner_y = y + 1
    inner_x = x + 1
    inner_width = width - 2

    # Status counts header
    counts = f"Incoming: {len(tasks['incoming'])}  Claimed: {len(tasks['claimed'])}  Done: {len(tasks['done'])}  Failed: {len(tasks['failed'])}"
    try:
        win.attron(curses.color_pair(Colors.HEADER))
        win.addnstr(inner_y, inner_x, counts, inner_width)
        win.attroff(curses.color_pair(Colors.HEADER))
    except curses.error:
        pass

    current_row = inner_y + 2

    # Show tasks by status
    status_colors = {
        "incoming": Colors.WARNING,
        "claimed": Colors.RUNNING,
        "failed": Colors.FAILURE,
    }

    for status in ["incoming", "claimed", "failed"]:
        if current_row >= y + height - 1:
            break

        status_tasks = tasks[status]
        if not status_tasks:
            continue

        # Status header
        try:
            win.attron(curses.color_pair(status_colors.get(status, Colors.DEFAULT)) | curses.A_BOLD)
            win.addnstr(current_row, inner_x, f"── {status.upper()} ──", inner_width)
            win.attroff(curses.color_pair(status_colors.get(status, Colors.DEFAULT)) | curses.A_BOLD)
        except curses.error:
            pass
        current_row += 1

        # Show tasks
        for task in status_tasks[:5]:  # Limit to 5 per status
            if current_row >= y + height - 1:
                break

            # Priority color
            priority_color = {
                "P0": Colors.P0,
                "P1": Colors.P1,
                "P2": Colors.P2,
            }.get(task.priority, Colors.DEFAULT)

            try:
                win.attron(curses.color_pair(priority_color))
                task_line = f"  [{task.priority}] {task.title}"
                win.addnstr(current_row, inner_x, task_line, inner_width)
                win.attroff(curses.color_pair(priority_color))
            except curses.error:
                pass
            current_row += 1

        if len(status_tasks) > 5:
            try:
                win.addnstr(current_row, inner_x, f"  ... and {len(status_tasks) - 5} more", inner_width)
            except curses.error:
                pass
            current_row += 1

        current_row += 1  # Space between sections


def render_logs_panel(win, y: int, x: int, height: int, width: int, logs: list[str], scroll_offset: int = 0):
    """Render the logs panel."""
    draw_box(win, y, x, height, width, "📜 Logs")

    if height < 3 or width < 20:
        return

    inner_y = y + 1
    inner_x = x + 1
    inner_width = width - 2
    inner_height = height - 2

    # Calculate which logs to show
    visible_logs = logs[-(inner_height + scroll_offset):]
    if scroll_offset > 0:
        visible_logs = visible_logs[:-scroll_offset]
    visible_logs = visible_logs[-inner_height:]

    for i, line in enumerate(visible_logs):
        if i >= inner_height:
            break

        try:
            # Color based on log level
            color = Colors.DEFAULT
            if "[ERROR]" in line or "ERROR" in line:
                color = Colors.FAILURE
            elif "[WARN]" in line or "WARNING" in line:
                color = Colors.WARNING
            elif "[INFO]" in line:
                color = Colors.SUCCESS
            elif "[DEBUG]" in line:
                color = Colors.PAUSED

            win.attron(curses.color_pair(color))
            # Truncate long lines
            display_line = line[:inner_width] if len(line) > inner_width else line
            win.addnstr(inner_y + i, inner_x, display_line, inner_width)
            win.attroff(curses.color_pair(color))
        except curses.error:
            pass


def render_stats_panel(win, y: int, x: int, height: int, width: int, agents: list[AgentState], tasks: dict[str, list[Task]]):
    """Render a small stats panel."""
    draw_box(win, y, x, height, width, "📊 Stats")

    if height < 3 or width < 15:
        return

    inner_y = y + 1
    inner_x = x + 1
    inner_width = width - 2

    running = sum(1 for a in agents if a.running)
    total_agents = len(agents)
    total_runs = sum(a.total_runs for a in agents)
    total_success = sum(a.total_successes for a in agents)
    success_rate = (total_success / total_runs * 100) if total_runs > 0 else 0

    stats = [
        f"Agents: {running}/{total_agents} running",
        f"Total runs: {total_runs}",
        f"Success rate: {success_rate:.1f}%",
        f"Queue depth: {len(tasks['incoming'])}",
    ]

    for i, stat in enumerate(stats):
        if inner_y + i >= y + height - 1:
            break
        try:
            win.addnstr(inner_y + i, inner_x, stat, inner_width)
        except curses.error:
            pass


def render_help_bar(win, max_y: int, max_x: int):
    """Render the help bar at the bottom."""
    help_text = " q:Quit  r:Refresh  j/k:Scroll logs  ?:Help "
    try:
        win.attron(curses.color_pair(Colors.HIGHLIGHT))
        win.addnstr(max_y - 1, 0, help_text.ljust(max_x), max_x)
        win.attroff(curses.color_pair(Colors.HIGHLIGHT))
    except curses.error:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# Main Dashboard
# ═══════════════════════════════════════════════════════════════════════════════

class Dashboard:
    def __init__(self, stdscr, refresh_interval: float = 2.0):
        self.stdscr = stdscr
        self.refresh_interval = refresh_interval
        self.log_scroll = 0
        self.running = True

        # Initialize curses
        curses.curs_set(0)  # Hide cursor
        self.stdscr.nodelay(True)  # Non-blocking input
        self.stdscr.timeout(int(refresh_interval * 1000))
        init_colors()

    def handle_input(self, key: int) -> bool:
        """Handle keyboard input. Returns False to quit."""
        if key == ord('q') or key == ord('Q'):
            return False
        elif key == ord('r') or key == ord('R'):
            self.log_scroll = 0
        elif key == ord('j') or key == curses.KEY_DOWN:
            self.log_scroll = max(0, self.log_scroll - 1)
        elif key == ord('k') or key == curses.KEY_UP:
            self.log_scroll += 1
        elif key == ord('g'):
            self.log_scroll = 0  # Go to bottom (newest)
        elif key == ord('G'):
            self.log_scroll = 1000  # Go to top (oldest)
        return True

    def render(self):
        """Render the entire dashboard."""
        self.stdscr.clear()
        max_y, max_x = self.stdscr.getmaxyx()

        if max_y < 10 or max_x < 40:
            try:
                self.stdscr.addstr(0, 0, "Terminal too small!")
            except curses.error:
                pass
            return

        # Load data
        agents = get_all_agents()
        tasks = get_tasks_by_status()
        logs = get_recent_logs(200)

        # Calculate layout
        # For small windows, stack vertically
        # For larger windows, use a more complex layout

        header_height = 1
        help_height = 1
        available_height = max_y - header_height - help_height

        # Header
        title = "🐙 OCTOPOID DASHBOARD"
        if DEMO_MODE:
            title += " [DEMO]"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.stdscr.attron(curses.color_pair(Colors.HEADER) | curses.A_BOLD)
            self.stdscr.addstr(0, 0, title)
            self.stdscr.attroff(curses.color_pair(Colors.HEADER) | curses.A_BOLD)
            if DEMO_MODE:
                # Highlight DEMO in yellow
                demo_pos = len("🐙 OCTOPOID DASHBOARD ")
                self.stdscr.attron(curses.color_pair(Colors.WARNING) | curses.A_BOLD)
                self.stdscr.addstr(0, demo_pos, "[DEMO]")
                self.stdscr.attroff(curses.color_pair(Colors.WARNING) | curses.A_BOLD)
            self.stdscr.addstr(0, max_x - len(timestamp) - 1, timestamp)
        except curses.error:
            pass

        if max_x >= 100:
            # Wide layout: [Agents | Tasks] over [Logs | Stats]
            left_width = max_x * 2 // 3
            right_width = max_x - left_width
            top_height = available_height // 2
            bottom_height = available_height - top_height

            render_agents_panel(self.stdscr, header_height, 0, top_height, left_width, agents)
            render_tasks_panel(self.stdscr, header_height, left_width, top_height, right_width, tasks)
            render_logs_panel(self.stdscr, header_height + top_height, 0, bottom_height, left_width + right_width * 2 // 3, logs, self.log_scroll)
            render_stats_panel(self.stdscr, header_height + top_height, left_width + right_width * 2 // 3, bottom_height, right_width // 3 + 1, agents, tasks)

        elif max_x >= 60:
            # Medium layout: Agents on top, Tasks + Logs below
            agents_height = min(len(agents) * 2 + 3, available_height // 2)
            remaining = available_height - agents_height
            tasks_height = remaining // 2
            logs_height = remaining - tasks_height

            render_agents_panel(self.stdscr, header_height, 0, agents_height, max_x, agents)
            render_tasks_panel(self.stdscr, header_height + agents_height, 0, tasks_height, max_x // 2, tasks)
            render_logs_panel(self.stdscr, header_height + agents_height, max_x // 2, tasks_height + logs_height, max_x - max_x // 2, logs, self.log_scroll)

        else:
            # Narrow layout: Stack everything vertically
            panel_height = available_height // 3

            render_agents_panel(self.stdscr, header_height, 0, panel_height, max_x, agents)
            render_tasks_panel(self.stdscr, header_height + panel_height, 0, panel_height, max_x, tasks)
            render_logs_panel(self.stdscr, header_height + panel_height * 2, 0, available_height - panel_height * 2, max_x, logs, self.log_scroll)

        # Help bar
        render_help_bar(self.stdscr, max_y, max_x)

        self.stdscr.refresh()

    def run(self):
        """Main loop."""
        while self.running:
            try:
                self.render()
                key = self.stdscr.getch()
                if key != -1:
                    self.running = self.handle_input(key)
            except KeyboardInterrupt:
                break
            except curses.error:
                pass


def main(stdscr, refresh_interval: float):
    """Main entry point."""
    dashboard = Dashboard(stdscr, refresh_interval)
    dashboard.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Octopoid Dashboard")
    parser.add_argument("--refresh", type=float, default=2.0, help="Refresh interval in seconds")
    parser.add_argument("--demo", action="store_true", help="Run in demo mode with sample data")
    args = parser.parse_args()

    # Set demo mode
    if args.demo:
        DEMO_MODE = True
    else:
        # Check if .orchestrator directory exists
        if not get_orchestrator_dir().exists():
            print("Error: .orchestrator directory not found.")
            print("Please run this from an octopoid project directory,")
            print("or use --demo flag to see a demo with sample data.")
            sys.exit(1)

    try:
        curses.wrapper(lambda stdscr: main(stdscr, args.refresh))
    except KeyboardInterrupt:
        pass
