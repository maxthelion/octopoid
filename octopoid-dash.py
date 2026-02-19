#!/usr/bin/env python3
"""
Octopoid Dashboard - Tabbed project management TUI for v2.0 API.

Reads server URL from .octopoid/config.yaml automatically. No flags needed
if config.yaml has server.enabled: true and server.url set.

Usage:
    python octopoid-dash.py                                      # Auto-connect via config.yaml
    python octopoid-dash.py --server https://...                 # Override server URL
    python octopoid-dash.py --server https://... --api-key KEY   # With authentication
    python octopoid-dash.py --demo                               # Demo mode with sample data
    python octopoid-dash.py [--refresh N]                        # Custom refresh interval
"""

import argparse
import locale
locale.setlocale(locale.LC_ALL, '')

import curses
import json
import os
import random
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TAB_WORK = 0
TAB_PRS = 1
TAB_INBOX = 2
TAB_AGENTS = 3
TAB_DONE = 4
TAB_DRAFTS = 5

TAB_NAMES = ["Work", "PRs", "Inbox", "Agents", "Done", "Drafts"]
TAB_KEYS = ["W", "P", "I", "A", "D", "F"]

MAX_TURNS = 200  # default max turns for progress bar


# ---------------------------------------------------------------------------
# Color pairs
# ---------------------------------------------------------------------------

class Colors:
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
    TAB_ACTIVE = 13
    TAB_INACTIVE = 14
    DIM = 15


def init_colors():
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
    curses.init_pair(Colors.TAB_ACTIVE, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(Colors.TAB_INACTIVE, curses.COLOR_CYAN, -1)
    curses.init_pair(Colors.DIM, curses.COLOR_WHITE, -1)


# ---------------------------------------------------------------------------
# Safe drawing helpers
# ---------------------------------------------------------------------------

def safe_addstr(win, y: int, x: int, text: str, attr: int = 0, max_x: int = 0):
    """Write text to window, clipping to max_x if provided."""
    try:
        max_y_win, max_x_win = win.getmaxyx()
        if y < 0 or y >= max_y_win or x < 0 or x >= max_x_win:
            return
        limit = (max_x if max_x > 0 else max_x_win) - x
        if limit <= 0:
            return
        win.addnstr(y, x, text, limit, attr)
    except curses.error:
        pass


def safe_hline(win, y: int, x: int, ch, width: int, attr: int = 0):
    try:
        if attr:
            win.attron(attr)
        win.hline(y, x, ch, width)
        if attr:
            win.attroff(attr)
    except curses.error:
        pass


def draw_progress_bar(win, y: int, x: int, width: int, progress: float, color: int):
    """Draw a compact progress bar like [██████░░░░]."""
    if width < 3:
        return
    bar_width = width - 2
    filled = int(bar_width * min(1.0, max(0.0, progress)))
    try:
        win.addstr(y, x, "[")
        win.addstr(y, x + 1, "\u2588" * filled, curses.color_pair(color))
        win.addstr(y, x + 1 + filled, "\u2591" * (bar_width - filled),
                   curses.color_pair(Colors.DIM))
        win.addstr(y, x + width - 1, "]")
    except curses.error:
        pass


# ---------------------------------------------------------------------------
# Time formatting
# ---------------------------------------------------------------------------

def format_age(iso_str: str | None) -> str:
    """Format an ISO timestamp as a human-readable age like '2h', '15m'."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.replace(tzinfo=None)
        delta = datetime.now() - dt
        secs = delta.total_seconds()
        if secs < 0:
            return "now"
        if secs < 60:
            return f"{int(secs)}s"
        if secs < 3600:
            return f"{int(secs // 60)}m"
        if secs < 86400:
            return f"{int(secs // 3600)}h"
        return f"{int(secs // 86400)}d"
    except (ValueError, TypeError):
        return ""


# ---------------------------------------------------------------------------
# Data layer — wraps reports.py or provides demo data
# ---------------------------------------------------------------------------

def load_report(demo_mode: bool, sdk: Optional[Any] = None) -> dict[str, Any]:
    """Load the project report, either live or demo.

    Args:
        demo_mode: If True, return demo data
        sdk: OctopoidSDK instance for v2.0 API mode (required for non-demo mode)
    """
    if demo_mode:
        return _generate_demo_report()

    if not sdk:
        return {
            "work": {"incoming": [], "in_progress": [], "in_review": [], "done_today": []},
            "done_tasks": [],
            "prs": [],
            "proposals": [],
            "messages": [],
            "agents": [],
            "health": {"scheduler": "error: SDK required", "idle_agents": 0, "running_agents": 0,
                        "paused_agents": 0, "total_agents": 0, "queue_depth": 0,
                        "system_paused": False},
            "generated_at": datetime.now().isoformat(),
        }

    try:
        # Add parent of orchestrator package to sys.path if needed
        pkg_dir = Path(__file__).resolve().parent / "orchestrator"
        if pkg_dir.exists():
            parent = str(pkg_dir.parent)
            if parent not in sys.path:
                sys.path.insert(0, parent)

        from orchestrator.reports import get_project_report
        return get_project_report(sdk=sdk)
    except Exception as e:
        return {
            "work": {"incoming": [], "in_progress": [], "in_review": [], "done_today": []},
            "done_tasks": [],
            "prs": [],
            "proposals": [],
            "messages": [],
            "agents": [],
            "health": {"scheduler": f"error: {e}", "idle_agents": 0, "running_agents": 0,
                        "paused_agents": 0, "total_agents": 0, "queue_depth": 0,
                        "system_paused": False},
            "generated_at": datetime.now().isoformat(),
        }


def load_drafts(demo_mode: bool) -> list[dict[str, Any]]:
    """Load project management drafts for the Inbox tab."""
    if demo_mode:
        return _generate_demo_drafts()

    drafts = []
    drafts_dir = Path.cwd() / "project-management" / "drafts"
    if not drafts_dir.exists():
        return []
    for f in sorted(drafts_dir.glob("*.md")):
        title = f.stem.replace("-", " ").title()
        # Try to extract a better title from the file
        try:
            first_line = f.read_text().split("\n", 1)[0]
            if first_line.startswith("# "):
                title = first_line[2:].strip()
        except OSError:
            pass
        drafts.append({"filename": f.name, "title": title})
    return drafts


# ---------------------------------------------------------------------------
# Demo data generation
# ---------------------------------------------------------------------------

# Mutable demo state for animation
_demo_tick = 0


def _generate_demo_report() -> dict[str, Any]:
    global _demo_tick
    _demo_tick += 1
    now = datetime.now()

    work = {
        "incoming": [
            {"id": "a1b2c3d4", "title": "Snap system point fork unification", "role": "implement",
             "priority": "P1", "agent": None, "turns": 0, "turn_limit": 100, "commits": 0,
             "pr_number": None, "created": (now - timedelta(hours=3)).isoformat(),
             "project_id": None},
            {"id": "e5f6g7h8", "title": "Queue manipulation scripts", "role": "orchestrator_impl",
             "priority": "P2", "agent": None, "turns": 0, "turn_limit": 200, "commits": 0,
             "pr_number": None, "created": (now - timedelta(hours=1)).isoformat(),
             "project_id": None},
            {"id": "i9j0k1l2", "title": "Center line replacement", "role": "implement",
             "priority": "P2", "agent": None, "turns": 0, "turn_limit": 100, "commits": 0,
             "pr_number": None, "created": (now - timedelta(hours=5)).isoformat(),
             "project_id": None},
        ],
        "in_progress": [
            {"id": "m3n4o5p6", "title": "Gatekeeper review D1+D2", "role": "orchestrator_impl",
             "priority": "P1", "agent": "orch-impl-1", "turns": 74, "turn_limit": 200,
             "commits": 3, "pr_number": None,
             "created": (now - timedelta(hours=2)).isoformat(), "project_id": "PROJ-gate"},
            {"id": "q7r8s9t0", "title": "Void transparency fix", "role": "implement",
             "priority": "P1", "agent": "impl-agent-1", "turns": 45, "turn_limit": 100,
             "commits": 2, "pr_number": None,
             "created": (now - timedelta(hours=1)).isoformat(), "project_id": None},
        ],
        "checking": [
            {"id": "z1y2x3w4", "title": "Panel offset drag handler", "role": "implement",
             "priority": "P1", "agent": "impl-agent-2", "turns": 78, "turn_limit": 100,
             "commits": 4, "pr_number": 56, "branch": "agent/z1y2x3w4",
             "created": (now - timedelta(hours=4)).isoformat(), "project_id": None,
             "checks": ["gk-testing", "gk-geometry"],
             "check_results": {"gk-testing": {"status": "pass"}, "gk-geometry": {"status": "pending"}},
             "attempt_count": 1, "rejection_count": 0,
             "staging_url": "https://boxen-pr-56.vercel.app"},
        ],
        "in_review": [
            {"id": "u1v2w3x4", "title": "Fix z-fighting on bounding box", "role": "implement",
             "priority": "P1", "agent": "impl-agent-1", "turns": 120, "turn_limit": 100,
             "commits": 4, "pr_number": 50, "branch": "agent/u1v2w3x4",
             "created": (now - timedelta(hours=15)).isoformat(), "project_id": None,
             "attempt_count": 2, "rejection_count": 1,
             "staging_url": "https://boxen-pr-50.vercel.app"},
            {"id": "y5z6a7b8", "title": "Rename Inset tool to Offset", "role": "implement",
             "priority": "P2", "agent": "impl-agent-2", "turns": 35, "turn_limit": 100,
             "commits": 2, "pr_number": 52,
             "created": (now - timedelta(hours=15)).isoformat(), "project_id": None},
            {"id": "c9d0e1f2", "title": "Axis arrow replacement", "role": "implement",
             "priority": "P2", "agent": "impl-agent-1", "turns": 90, "turn_limit": 100,
             "commits": 5, "pr_number": 49,
             "created": (now - timedelta(hours=16)).isoformat(), "project_id": None},
            {"id": "g3h4i5j6", "title": "Void mesh transparency", "role": "implement",
             "priority": "P2", "agent": "impl-agent-2", "turns": 55, "turn_limit": 100,
             "commits": 3, "pr_number": 51,
             "created": (now - timedelta(hours=15)).isoformat(), "project_id": None},
        ],
        "done_today": [
            {"id": "k7l8m9n0", "title": "Toggle face buttons", "role": "implement",
             "priority": "P1", "agent": "impl-agent-1", "turns": 60, "turn_limit": 100,
             "commits": 3, "pr_number": 53,
             "created": (now - timedelta(hours=4)).isoformat(), "project_id": None},
            {"id": "o1p2q3r4", "title": "Task templates system", "role": "implement",
             "priority": "P2", "agent": "impl-agent-2", "turns": 40, "turn_limit": 100,
             "commits": 2, "pr_number": 47,
             "created": (now - timedelta(hours=6)).isoformat(), "project_id": None},
            {"id": "s5t6u7v8", "title": "Whats-next script", "role": "orchestrator_impl",
             "priority": "P2", "agent": "orch-impl-1", "turns": 30, "turn_limit": 200,
             "commits": 1, "pr_number": None,
             "created": (now - timedelta(hours=8)).isoformat(), "project_id": None},
            {"id": "w9x0y1z2", "title": "CLAUDE.md role definitions", "role": "implement",
             "priority": "P2", "agent": "impl-agent-1", "turns": 25, "turn_limit": 100,
             "commits": 1, "pr_number": 46,
             "created": (now - timedelta(hours=10)).isoformat(), "project_id": None},
            {"id": "a3b4c5d6", "title": "2D snap system", "role": "implement",
             "priority": "P1", "agent": "impl-agent-2", "turns": 85, "turn_limit": 100,
             "commits": 5, "pr_number": 48,
             "created": (now - timedelta(hours=12)).isoformat(), "project_id": None},
        ],
    }

    prs = [
        {"number": 55, "title": "Queue manipulation scripts", "branch": "agent/32edc31a",
         "author": "bot", "created_at": (now - timedelta(hours=1)).isoformat(),
         "url": "https://github.com/owner/repo/pull/55"},
        {"number": 54, "title": "Gatekeeper review system", "branch": "agent/06b44db0",
         "author": "bot", "created_at": (now - timedelta(hours=1)).isoformat(),
         "url": "https://github.com/owner/repo/pull/54"},
        {"number": 52, "title": "Rename Inset tool to Offset", "branch": "agent/d4063abb",
         "author": "bot", "created_at": (now - timedelta(hours=15)).isoformat(),
         "url": "https://github.com/owner/repo/pull/52"},
        {"number": 51, "title": "Fix void mesh transparency", "branch": "agent/78606c45",
         "author": "bot", "created_at": (now - timedelta(hours=15)).isoformat(),
         "url": "https://github.com/owner/repo/pull/51"},
        {"number": 50, "title": "Fix z-fighting on bounding box", "branch": "agent/f737dc48",
         "author": "bot", "created_at": (now - timedelta(hours=15)).isoformat(),
         "url": "https://github.com/owner/repo/pull/50"},
        {"number": 49, "title": "Replace axis arrow with center line", "branch": "agent/251e9f63",
         "author": "bot", "created_at": (now - timedelta(hours=16)).isoformat(),
         "url": "https://github.com/owner/repo/pull/49"},
        {"number": 48, "title": "2D View Snapping System", "branch": "agent/a6f7f4cf",
         "author": "bot", "created_at": (now - timedelta(hours=16)).isoformat(),
         "url": "https://github.com/owner/repo/pull/48"},
    ]

    proposals = [
        {"id": "PROP-001", "title": "Store-to-Engine Migration", "proposer": "architect",
         "category": "refactor", "complexity": "XL",
         "created": (now - timedelta(days=2)).isoformat()},
        {"id": "PROP-002", "title": "Fix utils-to-store dependency", "proposer": "architect",
         "category": "refactor", "complexity": "M",
         "created": (now - timedelta(days=1)).isoformat()},
        {"id": "PROP-003", "title": "Proposal Flow Redesign", "proposer": "architect",
         "category": "feature", "complexity": "L",
         "created": (now - timedelta(hours=18)).isoformat()},
        {"id": "PROP-004", "title": "Multi-Machine Coordination", "proposer": "architect",
         "category": "feature", "complexity": "XL",
         "created": (now - timedelta(hours=12)).isoformat()},
        {"id": "PROP-005", "title": "Extract useOperationPalette", "proposer": "architect",
         "category": "refactor", "complexity": "S",
         "created": (now - timedelta(hours=6)).isoformat()},
        {"id": "PROP-006", "title": "Eliminate Duplicate Model State", "proposer": "architect",
         "category": "refactor", "complexity": "L",
         "created": (now - timedelta(hours=4)).isoformat()},
        {"id": "PROP-007", "title": "Modularize SketchView2D", "proposer": "architect",
         "category": "refactor", "complexity": "M",
         "created": (now - timedelta(hours=2)).isoformat()},
    ]

    messages: list[dict[str, Any]] = []

    # Simulate running state changes
    is_running = (_demo_tick % 8) < 5  # running 5 out of 8 ticks

    agents = [
        {"name": "impl-agent-1", "role": "implementer", "status": "idle",
         "paused": False, "current_task": None,
         "last_started": (now - timedelta(minutes=2)).isoformat(),
         "last_finished": (now - timedelta(minutes=1)).isoformat(),
         "last_exit_code": 0, "consecutive_failures": 0, "total_runs": 42,
         "recent_tasks": [
             {"id": "k7l8m9n0", "title": "Toggle face buttons", "queue": "done",
              "commits": 3, "turns": 60, "pr_number": 53},
             {"id": "u1v2w3x4", "title": "Fix z-fighting", "queue": "provisional",
              "commits": 4, "turns": 120, "pr_number": 50},
             {"id": "c9d0e1f2", "title": "Axis arrow replacement", "queue": "provisional",
              "commits": 5, "turns": 90, "pr_number": 49},
         ],
         "notes": "PanelPathRenderer uses useMemo for geometry computation. "
                  "Consider extracting shared ops infrastructure for 2D/3D views."},
        {"name": "impl-agent-2", "role": "implementer", "status": "idle",
         "paused": False, "current_task": None,
         "last_started": (now - timedelta(minutes=5)).isoformat(),
         "last_finished": (now - timedelta(minutes=4)).isoformat(),
         "last_exit_code": 0, "consecutive_failures": 0, "total_runs": 38,
         "recent_tasks": [
             {"id": "y5z6a7b8", "title": "Rename Inset to Offset", "queue": "provisional",
              "commits": 2, "turns": 35, "pr_number": 52},
             {"id": "g3h4i5j6", "title": "Void mesh transparency", "queue": "provisional",
              "commits": 3, "turns": 55, "pr_number": 51},
         ],
         "notes": None},
        {"name": "orch-impl-1", "role": "orchestrator_impl",
         "status": "running" if is_running else "idle",
         "paused": False,
         "current_task": "m3n4o5p6" if is_running else None,
         "last_started": (now - timedelta(minutes=12)).isoformat() if is_running
                         else (now - timedelta(minutes=2)).isoformat(),
         "last_finished": None if is_running
                          else (now - timedelta(minutes=1)).isoformat(),
         "last_exit_code": None if is_running else 0,
         "consecutive_failures": 0, "total_runs": 15,
         "recent_tasks": [
             {"id": "s5t6u7v8", "title": "Whats-next script", "queue": "done",
              "commits": 1, "turns": 30, "pr_number": None},
         ],
         "notes": None},
        {"name": "breakdown-1", "role": "breakdown", "status": "idle",
         "paused": False, "current_task": None,
         "last_started": (now - timedelta(minutes=30)).isoformat(),
         "last_finished": (now - timedelta(minutes=29)).isoformat(),
         "last_exit_code": 0, "consecutive_failures": 0, "total_runs": 8,
         "recent_tasks": [], "notes": None},
        {"name": "inbox-poller", "role": "inbox_poller", "status": "idle",
         "paused": False, "current_task": None,
         "last_started": (now - timedelta(minutes=10)).isoformat(),
         "last_finished": (now - timedelta(minutes=9)).isoformat(),
         "last_exit_code": 0, "consecutive_failures": 0, "total_runs": 120,
         "recent_tasks": [], "notes": None},
        {"name": "recycler", "role": "recycler", "status": "idle",
         "paused": True, "current_task": None,
         "last_started": (now - timedelta(hours=2)).isoformat(),
         "last_finished": (now - timedelta(hours=2)).isoformat(),
         "last_exit_code": 0, "consecutive_failures": 0, "total_runs": 3,
         "recent_tasks": [], "notes": None},
    ]

    health = {
        "scheduler": "running",
        "system_paused": False,
        "idle_agents": sum(1 for a in agents if a["status"] == "idle" and not a["paused"]),
        "running_agents": sum(1 for a in agents if a["status"] == "running"),
        "paused_agents": sum(1 for a in agents if a["paused"]),
        "total_agents": len(agents),
        "queue_depth": len(work["incoming"]) + len(work["in_progress"]),
    }

    done_tasks = [
        {"id": "k7l8m9n0", "title": "Toggle face buttons", "role": "implement",
         "priority": "P1", "agent": "impl-agent-1", "turns": 60, "turn_limit": 100,
         "commits": 3, "pr_number": 53, "final_queue": "done",
         "completed_at": (now - timedelta(hours=4)).isoformat(),
         "accepted_by": "self-merge",
         "created": (now - timedelta(hours=4)).isoformat(), "project_id": None},
        {"id": "o1p2q3r4", "title": "Task templates system", "role": "implement",
         "priority": "P2", "agent": "impl-agent-2", "turns": 40, "turn_limit": 100,
         "commits": 2, "pr_number": 47, "final_queue": "done",
         "completed_at": (now - timedelta(hours=6)).isoformat(),
         "accepted_by": "human",
         "created": (now - timedelta(hours=6)).isoformat(), "project_id": None},
        {"id": "s5t6u7v8", "title": "Whats-next script", "role": "orchestrator_impl",
         "priority": "P2", "agent": "orch-impl-1", "turns": 30, "turn_limit": 200,
         "commits": 1, "pr_number": None, "final_queue": "done",
         "completed_at": (now - timedelta(hours=8)).isoformat(),
         "accepted_by": "self-merge",
         "created": (now - timedelta(hours=8)).isoformat(), "project_id": None},
        {"id": "w9x0y1z2", "title": "CLAUDE.md role definitions", "role": "implement",
         "priority": "P2", "agent": "impl-agent-1", "turns": 25, "turn_limit": 100,
         "commits": 1, "pr_number": 46, "final_queue": "done",
         "completed_at": (now - timedelta(hours=10)).isoformat(),
         "accepted_by": "human",
         "created": (now - timedelta(hours=10)).isoformat(), "project_id": None},
        {"id": "a3b4c5d6", "title": "2D snap system", "role": "implement",
         "priority": "P1", "agent": "impl-agent-2", "turns": 85, "turn_limit": 100,
         "commits": 5, "pr_number": 48, "final_queue": "done",
         "completed_at": (now - timedelta(hours=12)).isoformat(),
         "accepted_by": "self-merge",
         "created": (now - timedelta(hours=12)).isoformat(), "project_id": None},
        {"id": "f1g2h3i4", "title": "Fix panel alignment edge case", "role": "implement",
         "priority": "P1", "agent": "impl-agent-1", "turns": 100, "turn_limit": 100,
         "commits": 0, "pr_number": None, "final_queue": "recycled",
         "completed_at": (now - timedelta(days=1)).isoformat(),
         "accepted_by": None,
         "created": (now - timedelta(days=1)).isoformat(), "project_id": None},
        {"id": "j5k6l7m8", "title": "Broken import path", "role": "implement",
         "priority": "P2", "agent": "impl-agent-2", "turns": 15, "turn_limit": 100,
         "commits": 0, "pr_number": None, "final_queue": "failed",
         "completed_at": (now - timedelta(days=2)).isoformat(),
         "accepted_by": None,
         "created": (now - timedelta(days=2)).isoformat(), "project_id": None},
        {"id": "n9o0p1q2", "title": "Queue depth monitoring", "role": "orchestrator_impl",
         "priority": "P2", "agent": "orch-impl-1", "turns": 45, "turn_limit": 200,
         "commits": 2, "pr_number": None, "final_queue": "done",
         "completed_at": (now - timedelta(days=3)).isoformat(),
         "accepted_by": "human",
         "created": (now - timedelta(days=3)).isoformat(), "project_id": None},
        {"id": "r3s4t5u6", "title": "SVG export kerf compensation", "role": "implement",
         "priority": "P1", "agent": "impl-agent-1", "turns": 92, "turn_limit": 100,
         "commits": 6, "pr_number": 44, "final_queue": "done",
         "completed_at": (now - timedelta(days=5)).isoformat(),
         "accepted_by": "self-merge",
         "created": (now - timedelta(days=5)).isoformat(), "project_id": None},
    ]

    return {
        "work": work,
        "done_tasks": done_tasks,
        "prs": prs,
        "proposals": proposals,
        "messages": messages,
        "agents": agents,
        "health": health,
        "generated_at": now.isoformat(),
    }


def _generate_demo_drafts() -> list[dict[str, Any]]:
    return [
        {"filename": "gatekeeper-review-plan.md", "title": "Gatekeeper Review Plan"},
        {"filename": "specialist-agents.md", "title": "Specialist Agents"},
        {"filename": "local-env-config.md", "title": "Local Environment Configuration"},
        {"filename": "workflow-improvements.md", "title": "Workflow Improvements"},
        {"filename": "interactive-role-gates.md", "title": "Interactive Role & Gates"},
        {"filename": "dashboard-redesign.md", "title": "Dashboard Redesign"},
        {"filename": "operation-sourced-identity.md", "title": "Operation Sourced Identity"},
        {"filename": "visual-pr-review.md", "title": "Visual PR Review Command"},
        {"filename": "project-breakdown.md", "title": "Project Breakdown System"},
        {"filename": "fillet-max-radius.md", "title": "Fillet Max Radius Geometry"},
    ]


# ---------------------------------------------------------------------------
# Tab renderers
# ---------------------------------------------------------------------------

def _flatten_work_tasks(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten work tasks across columns into a single navigable list.

    Order: left-to-right across columns, top-to-bottom within each column.
    Returns the ordered list of task dicts.
    """
    work = report.get("work", {})
    columns = [
        work.get("incoming", []),
        work.get("in_progress", []),
        work.get("checking", []),
        work.get("in_review", []),
    ]
    flat: list[dict[str, Any]] = []
    for col_tasks in columns:
        flat.extend(col_tasks)
    return flat


def render_work_tab(win, report: dict[str, Any], state: "DashboardState"):
    """Render Tab 1: Work Board (kanban) or task detail overlay."""
    # If detail view is open, render that instead of the board
    if state.work_detail_task_id:
        _render_work_detail(win, report, state)
        return

    max_y, max_x = win.getmaxyx()
    work = report.get("work", {})

    # Build the flat task list to determine which task is highlighted
    flat = _flatten_work_tasks(report)
    highlighted_id = None
    if flat and 0 <= state.work_cursor < len(flat):
        highlighted_id = flat[state.work_cursor].get("id")

    columns = [
        ("QUEUED", work.get("incoming", []), Colors.WARNING, False),
        ("IN PROGRESS", work.get("in_progress", []), Colors.RUNNING, True),
        ("CHECKS", work.get("checking", []), Colors.PAUSED, False),
        ("IN REVIEW", work.get("in_review", []), Colors.HEADER, False),
    ]
    col_width = max(15, (max_x - 1) // len(columns))
    content_start_y = 0

    for col_idx, (col_title, tasks, title_color, show_bar) in enumerate(columns):
        col_x = col_idx * col_width
        right_edge = min(col_x + col_width - 1, max_x - 1)
        inner_width = right_edge - col_x - 1

        if inner_width < 10:
            continue

        # Column header
        header = f" {col_title} ({len(tasks)}) "
        safe_addstr(win, content_start_y, col_x, header,
                    curses.color_pair(title_color) | curses.A_BOLD, right_edge)
        safe_hline(win, content_start_y + 1, col_x, curses.ACS_HLINE, inner_width + 1,
                   curses.color_pair(Colors.BORDER))

        # Draw column separator (vertical line)
        if col_idx > 0:
            for row in range(content_start_y, max_y):
                safe_addstr(win, row, col_x - 1, "\u2502",
                            curses.color_pair(Colors.BORDER))

        # Task cards
        card_y = content_start_y + 2
        for task in tasks:
            if card_y + 3 >= max_y:
                remaining = len(tasks) - tasks.index(task)
                if remaining > 0:
                    safe_addstr(win, card_y, col_x + 1, f"+{remaining} more",
                                curses.color_pair(Colors.DIM))
                break

            is_highlighted = task.get("id") == highlighted_id
            _render_task_card(win, card_y, col_x + 1, inner_width, task,
                              show_progress_bar=show_bar, highlighted=is_highlighted)
            card_y += _card_height(task, show_agent=show_bar) + 1  # +1 for spacing

    # Action hints at bottom
    hint_y = max_y - 2
    safe_hline(win, hint_y - 1, 0, curses.ACS_HLINE, max_x - 1,
               curses.color_pair(Colors.BORDER))
    safe_addstr(win, hint_y, 2, "[j/k] select  [Enter] details  [q] quit",
                curses.color_pair(Colors.DIM))



def _card_height(task: dict[str, Any], show_agent: bool = False) -> int:
    """Calculate card height in lines."""
    h = 2  # id line + title line
    if show_agent and task.get("agent"):
        h += 1  # agent + progress
    return max(h, 2)


def _render_task_card(win, y: int, x: int, width: int, task: dict[str, Any],
                      show_progress_bar: bool = True, highlighted: bool = False):
    """Render a single task card."""
    task_id = task.get("id", "")[:8]
    title = task.get("title", "untitled")
    role = task.get("role", "")
    agent = task.get("agent")
    turns = task.get("turns", 0)
    turn_limit = task.get("turn_limit", MAX_TURNS)
    is_orch = role in ("orchestrator_impl", "breakdown", "recycler", "inbox_poller")

    # Highlighted cards use yellow text
    hl_attr = curses.color_pair(Colors.WARNING) if highlighted else 0

    # Line 1: ID with role badge
    if is_orch:
        safe_addstr(win, y, x, "ORCH",
                    hl_attr or (curses.color_pair(Colors.PAUSED) | curses.A_BOLD))
        safe_addstr(win, y, x + 5, task_id,
                    hl_attr or curses.color_pair(Colors.DIM))
    else:
        safe_addstr(win, y, x, task_id,
                    hl_attr or curses.color_pair(Colors.DIM))

    # Cursor indicator for highlighted card
    if highlighted:
        safe_addstr(win, y, x - 1, "\u25b6", curses.color_pair(Colors.WARNING))

    # Line 2: Title
    safe_addstr(win, y + 1, x, title[:width],
                hl_attr or curses.color_pair(Colors.DEFAULT))

    row = y + 2

    # Agent + progress — only shown when show_progress_bar is True (in-progress column)
    if show_progress_bar and agent:
        agent_text = agent[:12]
        safe_addstr(win, row, x, agent_text, curses.color_pair(Colors.DIM))
        turns_val = turns or 0

        turn_text = f" {turns_val}/{turn_limit}t"
        # Calculate available space for bar after agent text and turn text
        remaining = width - len(agent_text) - 1 - len(turn_text)
        bar_x = x + len(agent_text) + 1

        if turn_limit > 0 and remaining >= 5:
            bar_width = min(12, remaining)
            progress = min(1.0, turns_val / turn_limit)
            draw_progress_bar(win, row, bar_x, bar_width, progress, Colors.RUNNING)
            safe_addstr(win, row, bar_x + bar_width, turn_text[:width - len(agent_text) - 1 - bar_width],
                        curses.color_pair(Colors.DIM))
        else:
            # No room for bar, just show turn text
            safe_addstr(win, row, bar_x, turn_text[:width - len(agent_text) - 1],
                        curses.color_pair(Colors.DIM))
        row += 1


# ---------------------------------------------------------------------------
# Task detail view — content loading helpers
# ---------------------------------------------------------------------------

DETAIL_MENU_ITEMS = ["Diff", "Desc", "Result", "Logs"]

# Cache: (task_id, menu_index) -> list[str]
_detail_content_cache: dict[tuple[str, int], list[str]] = {}


def _get_base_branch_for_diff() -> str:
    """Get repo base branch from orchestrator config."""
    try:
        pkg_dir = Path(__file__).resolve().parent / "orchestrator"
        if pkg_dir.exists():
            parent = str(pkg_dir.parent)
            if parent not in sys.path:
                sys.path.insert(0, parent)
        from orchestrator.config import get_base_branch
        return get_base_branch()
    except Exception:
        pass
    return "feature/client-server-architecture"


def _fetch_detail_content(task_id: str, menu_index: int) -> list[str]:
    """Fetch content lines for the given detail view. May block briefly."""
    repo_root = Path(__file__).resolve().parent
    runtime_dir = repo_root / ".octopoid" / "runtime" / "tasks" / task_id

    if menu_index == 0:  # Diff
        worktree = runtime_dir / "worktree"
        if not worktree.exists():
            return ["(no diff available — worktree not found)"]
        try:
            base_branch = _get_base_branch_for_diff()
            result = subprocess.run(
                ["git", "diff", "--stat", f"origin/{base_branch}...HEAD"],
                capture_output=True, text=True, timeout=10,
                cwd=worktree,
            )
            output = result.stdout.strip()
            if not output:
                return ["(no diff available — no commits yet)"]
            return output.splitlines()
        except subprocess.TimeoutExpired:
            return ["(git diff timed out)"]
        except Exception as e:
            return [f"(error running git diff: {e})"]

    elif menu_index == 1:  # Desc
        task_file = repo_root / ".octopoid" / "tasks" / f"{task_id}.md"
        if task_file.exists():
            try:
                return task_file.read_text().splitlines()
            except OSError:
                pass
        # Fallback: extract ## Task Description section from prompt.md
        prompt_file = runtime_dir / "prompt.md"
        if prompt_file.exists():
            try:
                lines = prompt_file.read_text().splitlines()
                in_section = False
                section_lines: list[str] = []
                for line in lines:
                    if "## Task Description" in line:
                        in_section = True
                    elif in_section and line.startswith("## "):
                        break
                    elif in_section:
                        section_lines.append(line)
                if section_lines:
                    return section_lines
                return lines
            except OSError:
                pass
        return ["(task description not found)"]

    elif menu_index == 2:  # Result
        result_file = runtime_dir / "result.json"
        if not result_file.exists():
            return ["(no result yet)"]
        try:
            data = json.loads(result_file.read_text())
            return json.dumps(data, indent=2).splitlines()
        except Exception as e:
            return [f"(error reading result.json: {e})"]

    elif menu_index == 3:  # Logs
        for log_name in ("stdout.log", "stderr.log"):
            log_file = runtime_dir / log_name
            if log_file.exists():
                try:
                    content = log_file.read_text()
                    if content.strip():
                        return content.splitlines()
                except OSError:
                    pass
        return ["(no logs available)"]

    return []


def _get_detail_content(task_id: str, menu_index: int) -> list[str]:
    """Return cached content lines for the given detail view, fetching if needed."""
    key = (task_id, menu_index)
    if key not in _detail_content_cache:
        _detail_content_cache[key] = _fetch_detail_content(task_id, menu_index)
    return _detail_content_cache[key]


def _render_work_detail(win, report: dict[str, Any], state: "DashboardState"):
    """Render the task detail overlay with split sidebar/content layout."""
    max_y, max_x = win.getmaxyx()
    task_id = state.work_detail_task_id

    # Find the task in the report
    task = None
    queue_name = None
    work = report.get("work", {})
    queue_map = [
        ("incoming", "QUEUED"),
        ("in_progress", "IN PROGRESS"),
        ("checking", "CHECKS"),
        ("in_review", "IN REVIEW"),
        ("done_today", "DONE TODAY"),
    ]
    for key, label in queue_map:
        for t in work.get(key, []):
            if t.get("id") == task_id:
                task = t
                queue_name = label
                break
        if task:
            break

    if not task:
        safe_addstr(win, 1, 2, f"Task {task_id} not found.",
                    curses.color_pair(Colors.FAILURE))
        return

    full_id = task.get("id", "")
    title = task.get("title", "untitled")
    role = task.get("role", "?")
    priority = task.get("priority", "?")
    agent = task.get("agent")
    turns = task.get("turns", 0) or 0
    turn_limit = task.get("turn_limit", MAX_TURNS) or MAX_TURNS
    commits = task.get("commits", 0) or 0
    pr_number = task.get("pr_number")

    # ── TOP PANEL (rows 0-2): compact summary ────────────────────────────────
    row = 0
    width = max_x - 2

    # Row 0: ID + title
    id_part = f"{full_id}  "
    safe_addstr(win, row, 1, id_part, curses.color_pair(Colors.DIM))
    safe_addstr(win, row, 1 + len(id_part), title[: width - len(id_part)],
                curses.color_pair(Colors.DEFAULT) | curses.A_BOLD)
    row += 1

    # Row 1: Role / Priority / Agent / Status
    is_orch = role in ("orchestrator_impl", "breakdown", "recycler", "inbox_poller")
    role_color = Colors.PAUSED if is_orch else Colors.DEFAULT
    p_color = Colors.P0 if priority == "P0" else (
        Colors.P1 if priority == "P1" else Colors.P2)
    meta_parts = [
        ("Role: ", Colors.DIM),
        (role, role_color),
        ("  Priority: ", Colors.DIM),
        (str(priority), p_color),
        ("  Agent: ", Colors.DIM),
        (agent or "(none)", Colors.DEFAULT if agent else Colors.DIM),
        ("  Status: ", Colors.DIM),
        (queue_name or "?", Colors.WARNING),
    ]
    col = 1
    for text, color in meta_parts:
        if col >= max_x - 2:
            break
        safe_addstr(win, row, col, text[: max_x - 2 - col],
                    curses.color_pair(color))
        col += len(text)
    row += 1

    # Row 2: Turns progress / Commits / PR
    turn_text = f"Turns: {turns}/{turn_limit}"
    safe_addstr(win, row, 1, turn_text, curses.color_pair(Colors.DIM))
    bar_x = 1 + len(turn_text) + 1
    bar_width = min(20, max_x - bar_x - 20)
    if bar_width >= 5 and turn_limit > 0:
        draw_progress_bar(win, row, bar_x, bar_width,
                          min(1.0, turns / turn_limit), Colors.RUNNING)
    commits_x = bar_x + (bar_width if bar_width >= 5 else 0) + 2
    safe_addstr(win, row, commits_x, f"Commits: {commits}",
                curses.color_pair(Colors.DIM))
    if pr_number:
        pr_x = commits_x + len(f"Commits: {commits}") + 2
        safe_addstr(win, row, pr_x, f"PR: #{pr_number}",
                    curses.color_pair(Colors.P1))
    row += 1

    # Row 3: horizontal separator with sidebar divider
    SIDEBAR_W = 10  # 9 chars content + column 9 is │
    safe_hline(win, row, 0, curses.ACS_HLINE, SIDEBAR_W - 1,
               curses.color_pair(Colors.BORDER))
    try:
        win.addch(row, SIDEBAR_W - 1, curses.ACS_PLUS,
                  curses.color_pair(Colors.BORDER))
    except curses.error:
        pass
    safe_hline(win, row, SIDEBAR_W, curses.ACS_HLINE, max_x - SIDEBAR_W - 1,
               curses.color_pair(Colors.BORDER))
    row += 1

    # ── SPLIT LAYOUT ─────────────────────────────────────────────────────────
    content_top = row          # first row of split area
    hint_rows = 2              # separator + hint line at bottom
    content_bot = max_y - hint_rows  # exclusive upper bound
    visible_height = content_bot - content_top

    if visible_height < 1:
        return

    # --- Left sidebar: menu ---
    menu_focused = (state.detail_focus == "menu")
    for i, item in enumerate(DETAIL_MENU_ITEMS):
        r = content_top + i
        if r >= content_bot:
            break
        is_selected = (i == state.detail_menu_index)

        if is_selected and menu_focused:
            raw = f"[{item}]"
            label = raw.center(SIDEBAR_W - 1)
            attr = curses.color_pair(Colors.HIGHLIGHT) | curses.A_BOLD
        elif is_selected:
            raw = item
            label = raw.center(SIDEBAR_W - 1)
            attr = curses.color_pair(Colors.WARNING) | curses.A_BOLD
        else:
            raw = item
            label = raw.center(SIDEBAR_W - 1)
            attr = curses.color_pair(Colors.DIM)

        safe_addstr(win, r, 0, label[: SIDEBAR_W - 1], attr)

    # Draw vertical │ separator for all split rows
    for r in range(content_top, content_bot):
        try:
            win.addch(r, SIDEBAR_W - 1, curses.ACS_VLINE,
                      curses.color_pair(Colors.BORDER))
        except curses.error:
            pass

    # --- Right content area ---
    content_x = SIDEBAR_W
    content_width = max_x - content_x - 1
    if content_width < 4:
        content_width = 4

    content_lines = _get_detail_content(task_id, state.detail_menu_index)
    offset = state.detail_scroll_offset

    if not content_lines:
        safe_addstr(win, content_top, content_x + 1, "(no content)",
                    curses.color_pair(Colors.DIM))
    else:
        visible = content_lines[offset: offset + visible_height]
        for i, line in enumerate(visible):
            r = content_top + i
            if r >= content_bot:
                break
            safe_addstr(win, r, content_x + 1, line[: content_width],
                        curses.color_pair(Colors.DEFAULT))

        # Scroll indicator in sidebar if content is scrollable
        if len(content_lines) > visible_height:
            scroll_pct = int(offset / max(1, len(content_lines) - visible_height) * 100)
            scroll_text = f"{scroll_pct}%"
            indicator_row = content_bot - 1
            if indicator_row >= content_top:
                safe_addstr(win, indicator_row, 0,
                            scroll_text.center(SIDEBAR_W - 1),
                            curses.color_pair(Colors.DIM))

    # ── HINT LINE ────────────────────────────────────────────────────────────
    hint_y = max_y - 1
    safe_hline(win, hint_y - 1, 0, curses.ACS_HLINE, max_x - 1,
               curses.color_pair(Colors.BORDER))
    hint = "[h/l] focus  [j/k] navigate  [r] refresh  [Esc/q] back to board"
    safe_addstr(win, hint_y, 1, hint[: max_x - 2],
                curses.color_pair(Colors.DIM))


def render_prs_tab(win, report: dict[str, Any], state: "DashboardState"):
    """Render Tab 2: PRs list."""
    max_y, max_x = win.getmaxyx()
    prs = report.get("prs", [])

    # Header
    safe_addstr(win, 0, 1, f" OPEN PRs ({len(prs)}) ",
                curses.color_pair(Colors.HEADER) | curses.A_BOLD)
    safe_hline(win, 1, 1, curses.ACS_HLINE, max_x - 2,
               curses.color_pair(Colors.BORDER))

    if not prs:
        safe_addstr(win, 3, 3, "No open pull requests.",
                    curses.color_pair(Colors.DIM))
        return

    # Column headers
    y = 2
    safe_addstr(win, y, 2, "  #", curses.color_pair(Colors.DIM))
    safe_addstr(win, y, 8, "Title", curses.color_pair(Colors.DIM))
    safe_addstr(win, y, max(40, max_x - 40), "Branch", curses.color_pair(Colors.DIM))
    safe_addstr(win, y, max(60, max_x - 15), "Age", curses.color_pair(Colors.DIM))
    y += 1

    for i, pr in enumerate(prs):
        if y >= max_y - 3:
            break

        selected = i == state.pr_cursor
        attr = curses.color_pair(Colors.HIGHLIGHT) if selected else 0

        # Determine if this is an orchestrator PR
        branch = pr.get("branch", "")
        is_orch = "orch" in branch.lower()

        num = str(pr.get("number", ""))
        title = pr.get("title", "untitled")
        age = format_age(pr.get("created_at"))

        # Build the line
        safe_addstr(win, y, 2, f"#{num:<4}", attr or curses.color_pair(Colors.P1))
        title_max = max(30, max_x - 45) - 9
        safe_addstr(win, y, 8, title[:title_max], attr or curses.color_pair(Colors.DEFAULT))

        branch_x = max(40, max_x - 40)
        branch_max = max(18, max_x - 15 - branch_x)
        safe_addstr(win, y, branch_x, branch[:branch_max],
                    attr or curses.color_pair(Colors.DIM))

        age_x = max(60, max_x - 15)
        safe_addstr(win, y, age_x, age, attr or curses.color_pair(Colors.DIM))

        if is_orch:
            badge_x = age_x + len(age) + 2
            safe_addstr(win, y, badge_x, "ORCH",
                        curses.color_pair(Colors.PAUSED) | curses.A_BOLD)

        y += 1

        # Show staging URL on a second line if available
        staging_url = pr.get("staging_url")
        if staging_url and y < max_y - 3:
            safe_addstr(win, y, 8, staging_url[:max_x - 10],
                        curses.color_pair(Colors.DIM))
            y += 1

    # Action hints
    hint_y = max_y - 2
    safe_hline(win, hint_y - 1, 1, curses.ACS_HLINE, max_x - 2,
               curses.color_pair(Colors.BORDER))
    safe_addstr(win, hint_y, 2, "[Enter] preview  [a] approve  [r] reject  [d] diff",
                curses.color_pair(Colors.DIM))


def render_inbox_tab(win, report: dict[str, Any], drafts: list[dict[str, Any]],
                     state: "DashboardState"):
    """Render Tab 3: Inbox (Proposals | Messages | Drafts)."""
    max_y, max_x = win.getmaxyx()
    proposals = report.get("proposals", [])
    messages = report.get("messages", [])

    # Three sub-columns
    col_width = max(15, (max_x - 2) // 3)

    sub_cols = [
        ("PROPOSALS", proposals, 0),
        ("MESSAGES", messages, col_width),
        ("DRAFTS", drafts, col_width * 2),
    ]

    for col_title, items, col_x in sub_cols:
        right_edge = min(col_x + col_width - 1, max_x - 1)
        inner_w = right_edge - col_x - 1

        safe_addstr(win, 0, col_x + 1, f" {col_title} ({len(items)}) ",
                    curses.color_pair(Colors.HEADER) | curses.A_BOLD)
        safe_hline(win, 1, col_x, curses.ACS_HLINE, inner_w + 1,
                   curses.color_pair(Colors.BORDER))

        # Column separator
        if col_x > 0:
            for row in range(0, max_y - 2):
                safe_addstr(win, row, col_x - 1, "\u2502",
                            curses.color_pair(Colors.BORDER))

        y = 2
        if not items:
            safe_addstr(win, y, col_x + 1, "No pending items",
                        curses.color_pair(Colors.DIM))
            continue

        for item in items:
            if y >= max_y - 4:
                remaining = len(items) - items.index(item)
                safe_addstr(win, y, col_x + 2, f"...+{remaining} more",
                            curses.color_pair(Colors.DIM))
                break

            if col_title == "PROPOSALS":
                title = item.get("title", "untitled")
                safe_addstr(win, y, col_x + 2, f"\u2022 {title[:inner_w - 3]}",
                            curses.color_pair(Colors.DEFAULT))
            elif col_title == "MESSAGES":
                mtype = item.get("type", "info")
                fname = item.get("filename", "")
                color = Colors.FAILURE if mtype == "error" else (
                    Colors.WARNING if mtype == "warning" else Colors.DEFAULT)
                safe_addstr(win, y, col_x + 2, f"\u2022 [{mtype}] {fname[:inner_w - 12]}",
                            curses.color_pair(color))
            elif col_title == "DRAFTS":
                title = item.get("title", item.get("filename", "untitled"))
                safe_addstr(win, y, col_x + 2, f"\u2022 {title[:inner_w - 3]}",
                            curses.color_pair(Colors.DEFAULT))
            y += 1

    # Action hints
    hint_y = max_y - 2
    safe_hline(win, hint_y - 1, 1, curses.ACS_HLINE, max_x - 2,
               curses.color_pair(Colors.BORDER))
    safe_addstr(win, hint_y, 2, "[Enter] read  [a] approve  [x] dismiss  [e] enqueue",
                curses.color_pair(Colors.DIM))


def render_agents_tab(win, report: dict[str, Any], state: "DashboardState"):
    """Render Tab 4: Agents (master-detail)."""
    max_y, max_x = win.getmaxyx()
    agents = report.get("agents", [])
    health = report.get("health", {})

    # Master-detail split
    list_width = min(24, max_x // 3)
    detail_x = list_width + 1
    detail_width = max_x - detail_x - 1

    # -- LEFT: Agent list --
    safe_addstr(win, 0, 1, " AGENTS ",
                curses.color_pair(Colors.HEADER) | curses.A_BOLD)
    safe_hline(win, 1, 0, curses.ACS_HLINE, list_width,
               curses.color_pair(Colors.BORDER))

    # Vertical separator
    for row in range(0, max_y - 2):
        safe_addstr(win, row, list_width, "\u2502",
                    curses.color_pair(Colors.BORDER))

    for i, agent in enumerate(agents):
        y = 2 + i
        if y >= max_y - 2:
            break

        selected = i == state.agent_cursor
        name = agent.get("name", "?")
        status = agent.get("status", "idle")
        paused = agent.get("paused", False)

        # Status badge
        if paused:
            badge = "PAUSE"
            badge_color = Colors.PAUSED
        elif status == "running":
            badge = "RUN"
            badge_color = Colors.RUNNING
        elif status.startswith("idle("):
            badge = "BLOCK"
            badge_color = Colors.BLOCKED
        else:
            badge = "IDLE"
            badge_color = Colors.SUCCESS

        # Cursor indicator
        prefix = "\u25b6 " if selected else "  "
        row_attr = curses.color_pair(Colors.HIGHLIGHT) if selected else 0

        safe_addstr(win, y, 1, prefix, row_attr)
        name_max = list_width - 8 - len(prefix)
        safe_addstr(win, y, 1 + len(prefix), name[:name_max], row_attr)
        safe_addstr(win, y, list_width - len(badge) - 1, badge,
                    curses.color_pair(badge_color) | curses.A_BOLD)

    # -- RIGHT: Detail pane --
    if not agents:
        safe_addstr(win, 2, detail_x + 1, "No agents configured.",
                    curses.color_pair(Colors.DIM))
        return

    agent = agents[min(state.agent_cursor, len(agents) - 1)]
    _render_agent_detail(win, 0, detail_x + 1, detail_width, max_y - 2, agent, report)

    # Action hints
    hint_y = max_y - 2
    safe_hline(win, hint_y - 1, 0, curses.ACS_HLINE, max_x - 1,
               curses.color_pair(Colors.BORDER))
    safe_addstr(win, hint_y, 2, "[j/k] select  [p] pause/resume  [Enter] expand",
                curses.color_pair(Colors.DIM))


def _render_agent_detail(win, y: int, x: int, width: int, max_height: int,
                         agent: dict[str, Any], report: dict[str, Any]):
    """Render the agent detail pane."""
    name = agent.get("name", "?")
    role = agent.get("role", "?")
    status = agent.get("status", "idle")
    paused = agent.get("paused", False)
    current_task_id = agent.get("current_task")
    last_started = agent.get("last_started")
    recent_tasks = agent.get("recent_tasks", [])
    notes = agent.get("notes")

    # Agent name header
    safe_addstr(win, y, x, name, curses.color_pair(Colors.HEADER) | curses.A_BOLD)
    safe_hline(win, y + 1, x, curses.ACS_HLINE, min(len(name) + 2, width),
               curses.color_pair(Colors.BORDER))
    row = y + 2

    # Role
    safe_addstr(win, row, x, f"Role: ", curses.color_pair(Colors.DIM))
    safe_addstr(win, row, x + 6, role, curses.color_pair(Colors.DEFAULT))
    row += 1

    # Status
    if paused:
        status_text = "PAUSED"
        status_color = Colors.PAUSED
    elif status == "running":
        status_text = "RUNNING"
        status_color = Colors.RUNNING
        if last_started:
            age = format_age(last_started)
            status_text += f" \u00b7 {age} elapsed"
        turns = 0
        turn_limit = MAX_TURNS
        # Try to find current task turns from work data
        if current_task_id:
            for cat in report.get("work", {}).values():
                if isinstance(cat, list):
                    for t in cat:
                        if t.get("id") == current_task_id:
                            turns = t.get("turns", 0)
                            turn_limit = t.get("turn_limit", MAX_TURNS)
                            break
        if turns:
            status_text += f" \u00b7 {turns}/{turn_limit} turns"
    elif status.startswith("idle("):
        reason = status[5:-1] if status.endswith(")") else status[5:]
        status_text = f"BLOCKED \u00b7 {reason}"
        status_color = Colors.BLOCKED
    else:
        status_text = "IDLE"
        status_color = Colors.SUCCESS
        if last_started:
            age = format_age(last_started)
            status_text += f" \u00b7 last run {age} ago"

    safe_addstr(win, row, x, "Status: ", curses.color_pair(Colors.DIM))
    safe_addstr(win, row, x + 8, status_text,
                curses.color_pair(status_color))
    row += 2

    # Current task section
    safe_addstr(win, row, x, "CURRENT TASK",
                curses.color_pair(Colors.HEADER) | curses.A_BOLD)
    safe_hline(win, row + 1, x, curses.ACS_HLINE, 12,
               curses.color_pair(Colors.BORDER))
    row += 2

    if current_task_id:
        # Find task details
        task_info = None
        for cat in report.get("work", {}).values():
            if isinstance(cat, list):
                for t in cat:
                    if t.get("id") == current_task_id:
                        task_info = t
                        break

        if task_info:
            title = task_info.get("title", "untitled")
            safe_addstr(win, row, x, f"{current_task_id[:8]} {title[:width - 10]}",
                        curses.color_pair(Colors.DEFAULT))
            row += 1

            branch = task_info.get("branch", "")
            if branch:
                safe_addstr(win, row, x, f"Branch: {branch[:width - 8]}",
                            curses.color_pair(Colors.DIM))
                row += 1

            commits = task_info.get("commits", 0)
            turns = task_info.get("turns", 0)
            task_turn_limit = task_info.get("turn_limit", MAX_TURNS)
            safe_addstr(win, row, x, f"Commits: {commits}",
                        curses.color_pair(Colors.DIM))
            row += 1

            # Progress bar for turns
            if status == "running" and turns and task_turn_limit > 0:
                bar_width = min(25, width - 15)
                if bar_width >= 5:
                    draw_progress_bar(win, row, x, bar_width,
                                      min(1.0, turns / task_turn_limit),
                                      Colors.RUNNING)
                    safe_addstr(win, row, x + bar_width + 1,
                                f" {turns}/{task_turn_limit} turns",
                                curses.color_pair(Colors.DIM))
                row += 1
        else:
            safe_addstr(win, row, x, f"Task: {current_task_id[:8]}",
                        curses.color_pair(Colors.DEFAULT))
            row += 1
    else:
        safe_addstr(win, row, x, "(none)", curses.color_pair(Colors.DIM))
        row += 1

    row += 1
    if row >= y + max_height:
        return

    # Recent work section
    safe_addstr(win, row, x, "RECENT WORK",
                curses.color_pair(Colors.HEADER) | curses.A_BOLD)
    safe_hline(win, row + 1, x, curses.ACS_HLINE, 11,
               curses.color_pair(Colors.BORDER))
    row += 2

    if recent_tasks:
        for rt in recent_tasks[:5]:
            if row >= y + max_height:
                break
            tid = rt.get("id", "?")[:8]
            rtitle = rt.get("title", "untitled")
            queue = rt.get("queue", "")
            pr_num = rt.get("pr_number")

            check = "\u2713" if queue == "done" else "\u25cb"
            color = Colors.SUCCESS if queue == "done" else Colors.DIM

            line = f"{check} {tid} {rtitle[:width - 30]}"
            safe_addstr(win, row, x, line, curses.color_pair(color))

            if pr_num:
                pr_text = f"PR #{pr_num}"
                status_word = "merged" if queue == "done" else "waiting"
                extra = f" {pr_text} {status_word}"
                safe_addstr(win, row, x + min(len(line) + 1, width - len(extra) - 1),
                            extra, curses.color_pair(Colors.DIM))
            row += 1
    else:
        safe_addstr(win, row, x, "(no recent tasks)", curses.color_pair(Colors.DIM))
        row += 1

    row += 1
    if row >= y + max_height:
        return

    # Notes section
    safe_addstr(win, row, x, "NOTES",
                curses.color_pair(Colors.HEADER) | curses.A_BOLD)
    safe_hline(win, row + 1, x, curses.ACS_HLINE, 5,
               curses.color_pair(Colors.BORDER))
    row += 2

    if notes:
        # Word-wrap notes into available width
        words = notes.split()
        line = ""
        for word in words:
            if len(line) + len(word) + 1 > width - 2:
                safe_addstr(win, row, x, f'"{line}',
                            curses.color_pair(Colors.DIM))
                row += 1
                line = word
                if row >= y + max_height:
                    break
            else:
                line = f"{line} {word}" if line else word
        if line and row < y + max_height:
            safe_addstr(win, row, x, f' {line}"',
                        curses.color_pair(Colors.DIM))
            row += 1
    else:
        safe_addstr(win, row, x, "(none)", curses.color_pair(Colors.DIM))


def render_done_tab(win, report: dict[str, Any], state: "DashboardState"):
    """Render Tab 5: Done (completed, failed, recycled tasks in last 7 days)."""
    max_y, max_x = win.getmaxyx()
    done_tasks = report.get("done_tasks", [])

    # Header
    safe_addstr(win, 0, 1, f" COMPLETED WORK ({len(done_tasks)}) — last 7 days ",
                curses.color_pair(Colors.HEADER) | curses.A_BOLD)
    safe_hline(win, 1, 1, curses.ACS_HLINE, max_x - 2,
               curses.color_pair(Colors.BORDER))

    if not done_tasks:
        safe_addstr(win, 3, 3, "No completed tasks in the last 7 days.",
                    curses.color_pair(Colors.DIM))
        return

    # Column headers
    y = 2
    safe_addstr(win, y, 2, "  ID", curses.color_pair(Colors.DIM))
    safe_addstr(win, y, 16, "Title", curses.color_pair(Colors.DIM))
    title_end = max(45, max_x - 50)
    safe_addstr(win, y, title_end, "Age", curses.color_pair(Colors.DIM))
    safe_addstr(win, y, title_end + 8, "Turns", curses.color_pair(Colors.DIM))
    safe_addstr(win, y, title_end + 16, "Cmts", curses.color_pair(Colors.DIM))
    safe_addstr(win, y, title_end + 22, "Merge", curses.color_pair(Colors.DIM))
    safe_addstr(win, y, title_end + 34, "Agent", curses.color_pair(Colors.DIM))
    y += 1

    # Calculate visible range for scrolling
    visible_rows = max_y - 6  # header(2) + column_header(1) + footer(3)
    scroll_offset = max(0, state.done_cursor - visible_rows + 1)

    for i, task in enumerate(done_tasks):
        if i < scroll_offset:
            continue
        if y >= max_y - 3:
            remaining = len(done_tasks) - i
            if remaining > 0:
                safe_addstr(win, y, 2, f"+{remaining} more",
                            curses.color_pair(Colors.DIM))
            break

        selected = i == state.done_cursor
        task_id = task.get("id", "")[:8]
        title = task.get("title", "untitled")
        role = task.get("role", "")
        agent = task.get("agent", "")
        turns = task.get("turns", 0) or 0
        turn_limit = task.get("turn_limit", MAX_TURNS)
        commits = task.get("commits", 0) or 0
        final_queue = task.get("final_queue", "done")
        accepted_by = task.get("accepted_by") or ""
        completed_at = task.get("completed_at")
        is_orch = role in ("orchestrator_impl", "breakdown", "recycler", "inbox_poller")

        # Determine row color based on final queue
        if final_queue == "failed":
            row_color = Colors.FAILURE
        elif final_queue == "recycled":
            row_color = Colors.WARNING
        else:
            row_color = Colors.SUCCESS

        hl_attr = curses.color_pair(Colors.HIGHLIGHT) if selected else 0

        # Cursor indicator
        if selected:
            safe_addstr(win, y, 1, "\u25b6", curses.color_pair(Colors.WARNING))

        # Status icon
        if final_queue == "failed":
            safe_addstr(win, y, 2, "\u2717", hl_attr or curses.color_pair(Colors.FAILURE))
        elif final_queue == "recycled":
            safe_addstr(win, y, 2, "\u267b", hl_attr or curses.color_pair(Colors.WARNING))
        else:
            safe_addstr(win, y, 2, "\u2713", hl_attr or curses.color_pair(Colors.SUCCESS))

        # ID with ORCH badge
        if is_orch:
            safe_addstr(win, y, 4, "ORCH",
                        hl_attr or (curses.color_pair(Colors.PAUSED) | curses.A_BOLD))
            safe_addstr(win, y, 9, task_id,
                        hl_attr or curses.color_pair(Colors.DIM))
        else:
            safe_addstr(win, y, 4, task_id,
                        hl_attr or curses.color_pair(Colors.DIM))

        # Title
        title_max = title_end - 17
        safe_addstr(win, y, 16, title[:title_max],
                    hl_attr or curses.color_pair(Colors.DEFAULT))

        # Age
        age = format_age(completed_at)
        safe_addstr(win, y, title_end, age,
                    hl_attr or curses.color_pair(Colors.DIM))

        # Turns
        turns_text = f"{turns}/{turn_limit}"
        safe_addstr(win, y, title_end + 8, turns_text[:7],
                    hl_attr or curses.color_pair(Colors.DIM))

        # Commits
        safe_addstr(win, y, title_end + 16, str(commits),
                    hl_attr or curses.color_pair(Colors.DIM))

        # Merge method
        if accepted_by:
            merge_color = Colors.SUCCESS if accepted_by == "self-merge" else Colors.P1
            safe_addstr(win, y, title_end + 22, accepted_by[:10],
                        hl_attr or curses.color_pair(merge_color))
        elif final_queue == "failed":
            safe_addstr(win, y, title_end + 22, "failed",
                        hl_attr or curses.color_pair(Colors.FAILURE))
        elif final_queue == "recycled":
            safe_addstr(win, y, title_end + 22, "recycled",
                        hl_attr or curses.color_pair(Colors.WARNING))

        # Agent
        if agent:
            safe_addstr(win, y, title_end + 34, agent[:12],
                        hl_attr or curses.color_pair(Colors.DIM))

        y += 1

    # Footer with count and hints
    hint_y = max_y - 2
    safe_hline(win, hint_y - 1, 1, curses.ACS_HLINE, max_x - 2,
               curses.color_pair(Colors.BORDER))

    # Count summary
    done_count = sum(1 for t in done_tasks if t.get("final_queue") == "done")
    failed_count = sum(1 for t in done_tasks if t.get("final_queue") == "failed")
    recycled_count = sum(1 for t in done_tasks if t.get("final_queue") == "recycled")
    parts = [f"{done_count} done"]
    if recycled_count:
        parts.append(f"{recycled_count} recycled")
    if failed_count:
        parts.append(f"{failed_count} failed")
    summary = " \u00b7 ".join(parts)

    safe_addstr(win, hint_y, 2, f"[j/k] select  Total: {summary}",
                curses.color_pair(Colors.DIM))


def render_drafts_tab(win, drafts: list[dict[str, Any]], state: "DashboardState"):
    """Render Tab 6: Drafts (master-detail)."""
    max_y, max_x = win.getmaxyx()

    # Master-detail split: left ~30%, right ~70%
    list_width = max(20, max_x * 30 // 100)
    detail_x = list_width + 1
    detail_width = max_x - detail_x - 1

    # -- LEFT: Draft list --
    safe_addstr(win, 0, 1, " DRAFTS ",
                curses.color_pair(Colors.HEADER) | curses.A_BOLD)
    safe_hline(win, 1, 0, curses.ACS_HLINE, list_width,
               curses.color_pair(Colors.BORDER))

    # Vertical separator
    for row in range(0, max_y - 2):
        safe_addstr(win, row, list_width, "\u2502",
                    curses.color_pair(Colors.BORDER))

    if not drafts:
        safe_addstr(win, 2, 1, "No drafts found.",
                    curses.color_pair(Colors.DIM))
    else:
        for i, draft in enumerate(drafts):
            y = 2 + i
            if y >= max_y - 2:
                break

            selected = i == state.drafts_cursor
            title = draft.get("title", draft.get("filename", "?"))
            num_label = f"{i + 1}. "
            prefix = "\u25b6 " if selected else "  "
            row_attr = curses.color_pair(Colors.HIGHLIGHT) if selected else 0

            full_prefix = prefix + num_label
            title_max = list_width - len(full_prefix) - 2
            safe_addstr(win, y, 1, full_prefix + title[:title_max], row_attr)

    # -- RIGHT: Detail pane --
    safe_addstr(win, 0, detail_x + 1, " CONTENT ",
                curses.color_pair(Colors.HEADER) | curses.A_BOLD)
    safe_hline(win, 1, detail_x, curses.ACS_HLINE, detail_width,
               curses.color_pair(Colors.BORDER))

    if not drafts:
        safe_addstr(win, 2, detail_x + 1, "No draft selected.",
                    curses.color_pair(Colors.DIM))
    else:
        content = state.drafts_content
        if content is None:
            safe_addstr(win, 2, detail_x + 1, "Loading...",
                        curses.color_pair(Colors.DIM))
        else:
            lines = content.splitlines()
            for row_offset, line in enumerate(lines):
                y = 2 + row_offset
                if y >= max_y - 2:
                    break
                safe_addstr(win, y, detail_x + 1, line, 0, detail_x + detail_width)

    # Action hints
    hint_y = max_y - 2
    safe_hline(win, hint_y - 1, 0, curses.ACS_HLINE, max_x - 1,
               curses.color_pair(Colors.BORDER))
    safe_addstr(win, hint_y, 2, "[j/k] select draft",
                curses.color_pair(Colors.DIM))


# ---------------------------------------------------------------------------
# Dashboard state
# ---------------------------------------------------------------------------

@dataclass
class DashboardState:
    """Mutable dashboard UI state."""
    active_tab: int = TAB_WORK
    agent_cursor: int = 0
    pr_cursor: int = 0
    work_cursor: int = 0
    inbox_cursor: int = 0
    done_cursor: int = 0
    work_detail_task_id: Optional[str] = None  # set when detail overlay is open
    last_report: Optional[dict[str, Any]] = None
    last_drafts: Optional[list[dict[str, Any]]] = None
    demo_mode: bool = False
    drafts_cursor: int = 0
    drafts_content: Optional[str] = None
    detail_menu_index: int = 0      # which content view is selected (0-3)
    detail_scroll_offset: int = 0   # scroll position in content area
    detail_focus: str = "menu"      # "menu" or "content"


# ---------------------------------------------------------------------------
# Main Dashboard class
# ---------------------------------------------------------------------------

class Dashboard:
    def __init__(self, stdscr, refresh_interval: float = 2.0, demo_mode: bool = False, sdk: Optional[Any] = None):
        self.stdscr = stdscr
        self.stdscr.encoding = 'utf-8'
        self.refresh_interval = refresh_interval
        self.running = True
        self.state = DashboardState(demo_mode=demo_mode)
        self.sdk = sdk  # Octopoid SDK for v2.0 API mode

        # Background data thread
        self._data_lock = threading.Lock()
        self._force_refresh = threading.Event()
        self._data_thread = threading.Thread(target=self._data_loop, daemon=True)

        # Initialize curses
        curses.curs_set(0)
        self.stdscr.nodelay(True)
        self.stdscr.timeout(100)  # 100ms — responsive input
        init_colors()

    def load_data(self):
        """Load fresh data (blocking, for initial load only)."""
        report = load_report(self.state.demo_mode, sdk=self.sdk)
        drafts = load_drafts(self.state.demo_mode)
        with self._data_lock:
            self.state.last_report = report
            self.state.last_drafts = drafts
        if self.state.active_tab == TAB_DRAFTS:
            self._load_draft_content()

    def _data_loop(self):
        """Background thread: fetch data on a timer, or immediately on force-refresh signal."""
        while self.running:
            # Wait up to refresh_interval seconds, or until force-refreshed
            self._force_refresh.wait(timeout=self.refresh_interval)
            self._force_refresh.clear()
            if not self.running:
                break
            report = load_report(self.state.demo_mode, sdk=self.sdk)
            drafts = load_drafts(self.state.demo_mode)
            with self._data_lock:
                self.state.last_report = report
                self.state.last_drafts = drafts

    def handle_input(self, key: int) -> bool:
        """Handle keyboard input. Returns False to quit."""
        report = self.state.last_report or {}

        # When detail view is open, all navigation is handled there
        if self.state.work_detail_task_id:
            return self._handle_detail_input(key)

        # Global: quit
        if key == ord('q') or key == ord('Q'):
            return False

        # Global: force refresh (signal background thread to fetch immediately)
        if key == ord('r') or key == ord('R'):
            self._force_refresh.set()
            return True

        # Tab switching: W/P/I/A or 1/2/3/4
        if key == ord('w') or key == ord('1'):
            self.state.active_tab = TAB_WORK
        elif key == ord('p') or key == ord('2'):
            self.state.active_tab = TAB_PRS
        elif key == ord('i') or key == ord('3'):
            self.state.active_tab = TAB_INBOX
        elif key == ord('a') or key == ord('4'):
            self.state.active_tab = TAB_AGENTS
        elif key == ord('d') or key == ord('5'):
            self.state.active_tab = TAB_DONE
        elif key == ord('f') or key == ord('F') or key == ord('6'):
            self.state.active_tab = TAB_DRAFTS
            self._load_draft_content()

        # Navigation: j/k
        elif key == ord('j') or key == curses.KEY_DOWN:
            self._move_cursor(1, report)
        elif key == ord('k') or key == curses.KEY_UP:
            self._move_cursor(-1, report)

        # Enter: open detail view on Work tab
        elif key == ord('\n') or key == curses.KEY_ENTER:
            if self.state.active_tab == TAB_WORK and not self.state.work_detail_task_id:
                flat = _flatten_work_tasks(report)
                if flat and 0 <= self.state.work_cursor < len(flat):
                    self.state.work_detail_task_id = flat[self.state.work_cursor].get("id")

        # Esc: close detail view
        elif key == 27:
            if self.state.work_detail_task_id:
                self.state.work_detail_task_id = None

        return True

    def _handle_detail_input(self, key: int) -> bool:
        """Handle keyboard input while the task detail overlay is open."""
        task_id = self.state.work_detail_task_id

        # q / Esc: close detail, return to board
        if key == ord('q') or key == ord('Q') or key == 27:
            self.state.work_detail_task_id = None
            self.state.detail_menu_index = 0
            self.state.detail_scroll_offset = 0
            self.state.detail_focus = "menu"
            _detail_content_cache.clear()
            return True

        # r: force-refresh cached content
        if key == ord('r') or key == ord('R'):
            _detail_content_cache.clear()
            self._force_refresh.set()
            return True

        # h / left arrow: focus sidebar menu
        if key == ord('h') or key == curses.KEY_LEFT:
            self.state.detail_focus = "menu"
            return True

        # l / right arrow: focus content area
        if key == ord('l') or key == curses.KEY_RIGHT:
            self.state.detail_focus = "content"
            return True

        # j / down: navigate down in focused panel
        if key == ord('j') or key == curses.KEY_DOWN:
            if self.state.detail_focus == "menu":
                old = self.state.detail_menu_index
                self.state.detail_menu_index = min(
                    len(DETAIL_MENU_ITEMS) - 1, self.state.detail_menu_index + 1)
                if self.state.detail_menu_index != old:
                    self.state.detail_scroll_offset = 0
            else:
                content = _get_detail_content(task_id, self.state.detail_menu_index)
                self.state.detail_scroll_offset = min(
                    max(0, len(content) - 1),
                    self.state.detail_scroll_offset + 1,
                )
            return True

        # k / up: navigate up in focused panel
        if key == ord('k') or key == curses.KEY_UP:
            if self.state.detail_focus == "menu":
                old = self.state.detail_menu_index
                self.state.detail_menu_index = max(0, self.state.detail_menu_index - 1)
                if self.state.detail_menu_index != old:
                    self.state.detail_scroll_offset = 0
            else:
                self.state.detail_scroll_offset = max(
                    0, self.state.detail_scroll_offset - 1)
            return True

        return True

    def _move_cursor(self, delta: int, report: dict[str, Any]):
        """Move the active tab's cursor."""
        if self.state.active_tab == TAB_WORK:
            flat = _flatten_work_tasks(report)
            if flat:
                self.state.work_cursor = max(0, min(
                    len(flat) - 1, self.state.work_cursor + delta))
        elif self.state.active_tab == TAB_AGENTS:
            agents = report.get("agents", [])
            self.state.agent_cursor = max(0, min(
                len(agents) - 1, self.state.agent_cursor + delta))
        elif self.state.active_tab == TAB_PRS:
            prs = report.get("prs", [])
            self.state.pr_cursor = max(0, min(
                len(prs) - 1, self.state.pr_cursor + delta))
        elif self.state.active_tab == TAB_DONE:
            done_tasks = report.get("done_tasks", [])
            if done_tasks:
                self.state.done_cursor = max(0, min(
                    len(done_tasks) - 1, self.state.done_cursor + delta))
        elif self.state.active_tab == TAB_DRAFTS:
            drafts = self.state.last_drafts or []
            if drafts:
                self.state.drafts_cursor = max(0, min(
                    len(drafts) - 1, self.state.drafts_cursor + delta))
                self._load_draft_content()

    def _load_draft_content(self):
        """Load the markdown content for the currently selected draft."""
        drafts = self.state.last_drafts or []
        if not drafts:
            self.state.drafts_content = None
            return
        idx = min(self.state.drafts_cursor, len(drafts) - 1)
        draft = drafts[idx]
        filename = draft.get("filename", "")
        if not filename:
            self.state.drafts_content = None
            return
        drafts_dir = Path.cwd() / "project-management" / "drafts"
        filepath = drafts_dir / filename
        try:
            self.state.drafts_content = filepath.read_text()
        except OSError:
            self.state.drafts_content = f"(Could not read {filename})"

    def render(self):
        """Render the entire dashboard."""
        self.stdscr.clear()
        max_y, max_x = self.stdscr.getmaxyx()

        if max_y < 10 or max_x < 40:
            try:
                self.stdscr.addstr(0, 0, "Terminal too small! Need 40x10 minimum.")
            except curses.error:
                pass
            return

        report = self.state.last_report or {}
        drafts = self.state.last_drafts or []
        health = report.get("health", {})

        # -- Header line with tab bar --
        header_y = 0
        title = "OCTOPOID"
        if self.state.demo_mode:
            title += " [DEMO]"
        safe_addstr(self.stdscr, header_y, 1, title,
                    curses.color_pair(Colors.HEADER) | curses.A_BOLD)
        if self.state.demo_mode:
            # Highlight [DEMO]
            demo_start = len("OCTOPOID ")
            safe_addstr(self.stdscr, header_y, 1 + demo_start, "[DEMO]",
                        curses.color_pair(Colors.WARNING) | curses.A_BOLD)

        # Tab bar (right-aligned)
        tab_bar = ""
        total_tab_width = sum(len(f" [{k}]{n} ") for k, n in zip(TAB_KEYS, TAB_NAMES))
        tab_x = max(20, max_x - total_tab_width)
        for i, name in enumerate(TAB_NAMES):
            label = f" [{TAB_KEYS[i]}]{name} "
            if i == self.state.active_tab:
                safe_addstr(self.stdscr, header_y, tab_x, label,
                            curses.color_pair(Colors.TAB_ACTIVE) | curses.A_BOLD)
            else:
                safe_addstr(self.stdscr, header_y, tab_x, label,
                            curses.color_pair(Colors.TAB_INACTIVE))
            tab_x += len(label)

        # Separator under header
        safe_hline(self.stdscr, 1, 0, curses.ACS_HLINE, max_x,
                   curses.color_pair(Colors.BORDER))

        # -- Content area --
        content_y = 2
        content_height = max_y - 4  # header + separator + status bar + hint
        content_width = max_x

        # Create a sub-window for content
        try:
            content_win = self.stdscr.subwin(content_height, content_width,
                                              content_y, 0)
        except curses.error:
            return

        if self.state.active_tab == TAB_WORK:
            render_work_tab(content_win, report, self.state)
        elif self.state.active_tab == TAB_PRS:
            render_prs_tab(content_win, report, self.state)
        elif self.state.active_tab == TAB_INBOX:
            render_inbox_tab(content_win, report, drafts, self.state)
        elif self.state.active_tab == TAB_AGENTS:
            render_agents_tab(content_win, report, self.state)
        elif self.state.active_tab == TAB_DONE:
            render_done_tab(content_win, report, self.state)
        elif self.state.active_tab == TAB_DRAFTS:
            render_drafts_tab(content_win, drafts, self.state)

        # -- Status bar (bottom) --
        status_y = max_y - 1
        work = report.get("work", {})
        agents_list = report.get("agents", [])
        prs = report.get("prs", [])
        proposals = report.get("proposals", [])
        messages = report.get("messages", [])

        running_count = health.get("running_agents", 0)
        idle_count = health.get("idle_agents", 0)
        scheduler = health.get("scheduler", "?")

        parts = []
        parts.append(f"Sched: {scheduler}")
        parts.append(f"{health.get('total_agents', 0)} agents")
        if running_count:
            parts.append(f"{running_count} running")
        parts.append(f"{idle_count} idle")
        parts.append(f"{len(work.get('incoming', []))} queued")
        parts.append(f"{len(prs)} PRs")
        parts.append(f"{len(proposals)} proposals")
        if messages:
            parts.append(f"{len(messages)} msgs")

        status_text = " \u00b7 ".join(parts)
        safe_addstr(self.stdscr, status_y, 0, " " * max_x,
                    curses.color_pair(Colors.HIGHLIGHT))
        safe_addstr(self.stdscr, status_y, 1, status_text,
                    curses.color_pair(Colors.HIGHLIGHT))

        # Timestamp on the right
        ts = datetime.now().strftime("%H:%M:%S")
        safe_addstr(self.stdscr, status_y, max_x - len(ts) - 2, ts,
                    curses.color_pair(Colors.HIGHLIGHT))

        self.stdscr.refresh()

    def run(self):
        """Main loop."""
        self.load_data()  # initial blocking load
        self._data_thread.start()
        while self.running:
            try:
                self.render()
                key = self.stdscr.getch()
                if key == -1:
                    continue  # no input, just re-render (data updates in background)
                else:
                    self.running = self.handle_input(key)
            except KeyboardInterrupt:
                break
            except curses.error:
                pass
        self.running = False  # signal background thread to stop


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(stdscr, refresh_interval: float, demo_mode: bool, sdk: Optional[Any] = None):
    dashboard = Dashboard(stdscr, refresh_interval, demo_mode, sdk=sdk)
    dashboard.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Octopoid Project Dashboard")
    parser.add_argument("--refresh", type=float, default=2.0,
                        help="Refresh interval in seconds")
    parser.add_argument("--demo", action="store_true",
                        help="Run in demo mode with sample data")
    parser.add_argument("--server", type=str,
                        help="Octopoid server URL for v2.0 API mode (e.g., https://octopoid.example.com)")
    parser.add_argument("--api-key", type=str,
                        help="API key for server authentication")
    parser.add_argument("--local", action="store_true",
                        help="Force local mode (v1.x) even if .octopoid/config.yaml exists")
    args = parser.parse_args()

    sdk = None

    if not args.demo and not args.local:
        # Try to initialize SDK if server is specified or config exists
        server_url = args.server
        api_key = args.api_key

        # If no explicit --server, try loading from .octopoid/config.yaml
        if not server_url:
            config_path = Path.cwd() / ".octopoid" / "config.yaml"
            if config_path.exists():
                try:
                    import yaml
                    with open(config_path) as f:
                        config = yaml.safe_load(f)
                    server_config = config.get("server", {})
                    if server_config.get("enabled"):
                        server_url = server_config.get("url")
                        if not api_key:
                            api_key = server_config.get("api_key") or os.getenv("OCTOPOID_API_KEY")
                except Exception as e:
                    print(f"Warning: Failed to load config from {config_path}: {e}")

        # Initialize SDK if we have a server URL
        if server_url:
            try:
                from octopoid_sdk import OctopoidSDK
                sdk = OctopoidSDK(server_url=server_url, api_key=api_key)
                print(f"Connected to Octopoid server: {server_url}")
                time.sleep(0.5)  # Brief pause so user sees connection message
            except ImportError:
                print("Error: octopoid-sdk not installed.")
                print("Install with: pip install octopoid-sdk")
                sys.exit(1)
            except Exception as e:
                print(f"Error: Failed to connect to server {server_url}: {e}")
                sys.exit(1)

    if not args.demo and not sdk:
        # SDK is required for non-demo mode
        print("Error: Octopoid v2.0 requires an API server connection.")
        print("")
        print("Options:")
        print("  --demo               Run with demo data (no server needed)")
        print("  --server <URL>       Connect to Octopoid API server")
        print("")
        print("Example:")
        print("  python octopoid-dash.py --server http://localhost:8787")
        print("")
        print("If you have a .octopoid/config.yaml file with server settings,")
        print("the dashboard will automatically connect to that server.")
        sys.exit(1)

    try:
        curses.wrapper(lambda stdscr: main(stdscr, args.refresh, args.demo, sdk=sdk))
    except KeyboardInterrupt:
        pass
