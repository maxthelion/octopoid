#!/usr/bin/env python3
"""Comprehensive orchestrator status report (v2.0 — API mode).

Usage:
    .venv/bin/python scripts/octopoid-status.py [--verbose] [--logs N]
    .venv/bin/python scripts/octopoid-status.py --task <id>

One-shot overview of: queue state, agent status, worktree state,
agent notes, open PRs, scheduler health, and recent logs.
"""

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.config import (
    get_agents,
    get_agents_runtime_dir,
    get_notes_dir,
    get_orchestrator_dir,
    get_queue_limits,
    get_tasks_dir,
    is_system_paused,
)
from orchestrator.queue_utils import count_open_prs, get_sdk

VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv

LOG_LINES = 10
for i, arg in enumerate(sys.argv):
    if arg == "--logs" and i + 1 < len(sys.argv):
        LOG_LINES = int(sys.argv[i + 1])
        break

TASK_ID = None
for i, arg in enumerate(sys.argv):
    if arg == "--task" and i + 1 < len(sys.argv):
        TASK_ID = sys.argv[i + 1]
        break


# -- Helpers ---------------------------------------------------------------


def ago(iso_str: str | None) -> str:
    """Convert ISO timestamp to human-readable 'X ago' string."""
    if not iso_str:
        return "never"
    try:
        cleaned = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        delta = datetime.now() - dt
        if delta < timedelta(minutes=1):
            return f"{int(delta.total_seconds())}s ago"
        if delta < timedelta(hours=1):
            return f"{int(delta.total_seconds() / 60)}m ago"
        if delta < timedelta(days=1):
            h = delta.seconds // 3600
            return f"{h}h ago"
        return f"{delta.days}d ago"
    except (ValueError, TypeError):
        return str(iso_str)


def duration_str(start_iso: str | None, end_iso: str | None = None) -> str:
    """Human-readable duration between two timestamps (or start to now)."""
    if not start_iso:
        return "-"
    try:
        start_cleaned = start_iso.replace("Z", "+00:00")
        start_dt = datetime.fromisoformat(start_cleaned)
        if start_dt.tzinfo is not None:
            start_dt = start_dt.replace(tzinfo=None)
        if end_iso:
            end_cleaned = end_iso.replace("Z", "+00:00")
            end_dt = datetime.fromisoformat(end_cleaned)
            if end_dt.tzinfo is not None:
                end_dt = end_dt.replace(tzinfo=None)
        else:
            end_dt = datetime.now()
        delta = end_dt - start_dt
        if delta < timedelta(minutes=1):
            return f"{int(delta.total_seconds())}s"
        if delta < timedelta(hours=1):
            return f"{int(delta.total_seconds() / 60)}m"
        if delta < timedelta(days=1):
            h = int(delta.total_seconds() // 3600)
            m = int((delta.total_seconds() % 3600) // 60)
            return f"{h}h {m}m" if m > 0 else f"{h}h"
        days = delta.days
        h = delta.seconds // 3600
        return f"{days}d {h}h" if h > 0 else f"{days}d"
    except (ValueError, TypeError):
        return "-"


def run(cmd: list[str], cwd: str | None = None) -> str:
    """Run a command and return stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=cwd, timeout=10,
        )
        return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def subheader(title: str) -> None:
    print(f"\n  -- {title} --")


# -- Sections --------------------------------------------------------------


def print_scheduler_health() -> None:
    header("SCHEDULER")

    launchctl_out = run(["launchctl", "list", "com.boxen.orchestrator"])
    if "LastExitStatus" in launchctl_out:
        exit_match = re.search(r"LastExitStatus.*?=\s*(\d+)", launchctl_out)
        exit_code = exit_match.group(1) if exit_match else "?"
        print(f"  launchd:        loaded (last exit: {exit_code})")
    else:
        print("  launchd:        NOT LOADED")

    if is_system_paused():
        print("  system pause:   PAUSED (all agents stopped)")
    else:
        print("  system pause:   not paused")

    # Last scheduler tick from log
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = get_orchestrator_dir() / "logs" / f"scheduler-{today}.log"
    if log_path.exists():
        try:
            with open(log_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 4096))
                tail = f.read().decode("utf-8", errors="replace")
            for line in reversed(tail.strip().split("\n")):
                ts_match = re.search(
                    r"\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", line
                )
                if ts_match:
                    print(f"  last tick:      {ago(ts_match.group(1))}")
                    break
        except OSError:
            pass


def print_queue_status() -> None:
    header("QUEUE")

    sdk = get_sdk()
    limits = get_queue_limits()

    # Fetch all tasks in one call (no queue filter)
    try:
        all_tasks = sdk.tasks.list()
    except Exception as e:
        print(f"  API error: {e}")
        return

    # Group by queue
    by_queue: dict[str, list] = {}
    for t in all_tasks:
        q = t.get("queue", "unknown")
        by_queue.setdefault(q, []).append(t)

    # Summary counts
    queue_order = [
        "incoming", "claimed", "breakdown", "provisional",
        "done", "needs_continuation", "failed", "escalated",
    ]
    parts = []
    for q in queue_order:
        count = len(by_queue.get(q, []))
        if count > 0:
            parts.append(f"{q}: {count}")
    # Include any queues not in our ordered list
    for q, tasks in sorted(by_queue.items()):
        if q not in queue_order and tasks:
            parts.append(f"{q}: {len(tasks)}")

    print(f"  {' | '.join(parts) if parts else 'all queues empty'}")

    open_prs = count_open_prs()
    print(f"  open PRs: {open_prs} (limit: {limits.get('max_open_prs', '?')})")
    print(f"  max claimed: {limits.get('max_claimed', '?')}")

    # Task details per active queue
    for queue_name in queue_order:
        tasks = by_queue.get(queue_name, [])
        if not tasks:
            continue
        # For done queue, only show recent (last 10)
        if queue_name == "done":
            tasks = tasks[-10:]

        subheader(f"{queue_name} ({len(by_queue.get(queue_name, []))})")
        for task in tasks:
            tid = (task.get("id") or "?")[:24]
            title = (task.get("title") or task.get("id") or "untitled")[:40]
            priority = task.get("priority") or "?"
            role = task.get("role") or ""
            claimed_by = task.get("claimed_by") or ""
            claimed_at = task.get("claimed_at")
            submitted_at = task.get("submitted_at")
            created_at = task.get("created_at")
            commits = task.get("commits_count", 0)
            turns = task.get("turns_used", 0)
            blocked_by = task.get("blocked_by") or ""

            # Health markers
            health = []
            if queue_name == "claimed" and claimed_at:
                try:
                    claimed_dt = datetime.fromisoformat(
                        claimed_at.replace("Z", "+00:00")
                    )
                    if claimed_dt.tzinfo is not None:
                        claimed_dt = claimed_dt.replace(tzinfo=None)
                    hrs = (datetime.now() - claimed_dt).total_seconds() / 3600
                    if hrs > 2 and commits == 0:
                        health.append("[STUCK?]")
                except (ValueError, TypeError):
                    pass
            if queue_name == "provisional" and submitted_at:
                try:
                    prov_dt = datetime.fromisoformat(
                        submitted_at.replace("Z", "+00:00")
                    )
                    if prov_dt.tzinfo is not None:
                        prov_dt = prov_dt.replace(tzinfo=None)
                    hrs = (datetime.now() - prov_dt).total_seconds() / 3600
                    if hrs > 24:
                        health.append("[STALE?]")
                except (ValueError, TypeError):
                    pass

            line = f"    {priority} {tid}  {title}"

            extras = []
            if role and role != "implement":
                extras.append(role)
            if claimed_by:
                extras.append(f"by:{claimed_by}")
            if blocked_by:
                extras.append(f"blocked:{blocked_by}")

            time_parts = []
            if queue_name == "claimed" and claimed_at:
                time_parts.append(f"claimed {ago(claimed_at)}")
                time_parts.append(f"running {duration_str(claimed_at)}")
            elif queue_name == "provisional" and submitted_at:
                time_parts.append(f"submitted {ago(submitted_at)}")
                if turns:
                    time_parts.append(f"{turns} turns")
                if commits:
                    time_parts.append(f"{commits} commits")
            elif created_at:
                time_parts.append(f"created {ago(created_at)}")

            if extras:
                line += f"  ({', '.join(extras)})"
            if time_parts:
                line += f"  | {' | '.join(time_parts)}"
            if health:
                line += f"  {' '.join(health)}"
            print(line)


def print_agent_status() -> None:
    header("AGENTS")

    agents = get_agents()
    runtime_dir = get_agents_runtime_dir()

    fmt = "  {:<20} {:<16} {:<14} {:<12} {}"
    print(fmt.format("NAME", "ROLE", "STATUS", "LAST ACTIVE", "TASK"))
    print(fmt.format("-" * 20, "-" * 16, "-" * 14, "-" * 12, "-" * 8))

    for agent in agents:
        name = agent["name"]
        role = agent.get("role", "?")
        paused = agent.get("paused", False)

        state_path = runtime_dir / name / "state.json"
        state = {}
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        if paused:
            status_str = "paused"
        elif state.get("running"):
            pid = state.get("pid")
            # Verify PID is still alive
            if pid:
                try:
                    import os
                    os.kill(pid, 0)
                    status_str = f"RUNNING({pid})"
                except (OSError, TypeError):
                    status_str = "STALE"
            else:
                status_str = "RUNNING"
        else:
            blocked = state.get("extra", {}).get("blocked_reason", "")
            exit_code = state.get("last_exit_code")
            if blocked:
                status_str = f"idle({blocked[:10]})"
            elif exit_code and exit_code != 0:
                status_str = f"idle(exit:{exit_code})"
            else:
                status_str = "idle"

        heartbeat_path = runtime_dir / name / "heartbeat"
        heartbeat_ts = None
        if heartbeat_path.exists():
            try:
                heartbeat_ts = heartbeat_path.read_text().strip()
            except OSError:
                pass
        last_active = ago(
            heartbeat_ts
            or state.get("last_finished")
            or state.get("last_started")
        )
        current_task = (state.get("current_task") or "-")[:24]
        total_runs = state.get("total_runs", 0)

        print(fmt.format(name, role, status_str, last_active, current_task))
        if VERBOSE:
            print(f"  {'':20} runs: {total_runs}")

    paused_count = sum(1 for a in agents if a.get("paused"))
    active_count = len(agents) - paused_count
    print(f"\n  {active_count} active, {paused_count} paused of {len(agents)} total")


def print_worktree_status() -> None:
    header("WORKTREES")

    # Agent worktrees
    runtime_dir = get_agents_runtime_dir()
    agent_roles = {a["name"]: a.get("role", "?") for a in get_agents()}

    found = False
    if runtime_dir.exists():
        for agent_dir in sorted(runtime_dir.iterdir()):
            if not agent_dir.is_dir():
                continue
            worktree = agent_dir / "worktree"
            if not (worktree / ".git").exists():
                continue

            found = True
            name = agent_dir.name
            wt = str(worktree)

            branch = run(["git", "branch", "--show-current"], cwd=wt) or run(
                ["git", "rev-parse", "--short", "HEAD"], cwd=wt
            )
            commits_ahead = run(
                ["git", "rev-list", "--count", "main..HEAD"], cwd=wt
            )
            diff_shortstat = run(["git", "diff", "--shortstat"], cwd=wt)

            subheader(f"{name} [{agent_roles.get(name, '?')}]")
            print(f"    branch:   {branch}")
            print(f"    ahead:    {commits_ahead or '0'} commit(s)")
            if diff_shortstat:
                print(f"    unstaged: {diff_shortstat}")

            if VERBOSE:
                try:
                    n = min(int(commits_ahead or "0"), 5)
                except ValueError:
                    n = 0
                if n > 0:
                    log = run(["git", "log", "--oneline", f"-{n}"], cwd=wt)
                    if log:
                        print("    recent:")
                        for line in log.split("\n"):
                            print(f"      {line}")

    # Task worktrees
    tasks_dir = get_tasks_dir()
    if tasks_dir.exists():
        task_wts = [
            d for d in sorted(tasks_dir.iterdir())
            if d.is_dir() and (d / "worktree" / ".git").exists()
        ]
        if task_wts:
            subheader(f"task worktrees ({len(task_wts)})")
            for d in task_wts:
                wt = str(d / "worktree")
                branch = run(
                    ["git", "branch", "--show-current"], cwd=wt
                ) or "detached"
                print(f"    {d.name}  ({branch})")

    if not found and not (tasks_dir.exists() and any(tasks_dir.iterdir())):
        print("  No worktrees found")


def print_agent_notes() -> None:
    header("AGENT NOTES")

    notes_dir = get_notes_dir()
    notes = sorted(notes_dir.glob("*.md")) if notes_dir.exists() else []

    if not notes:
        print("  No agent notes")
        return

    print(f"  {len(notes)} note(s):\n")
    for note_path in notes:
        task_id = note_path.stem
        content = note_path.read_text().strip()
        lines = content.split("\n")
        preview = lines[0][:80] if lines else "(empty)"
        print(f"  {task_id}: {preview}")
        if VERBOSE and len(lines) > 1:
            for line in lines[1:6]:
                print(f"    {line[:80]}")
            if len(lines) > 6:
                print(f"    ... ({len(lines) - 6} more lines)")


def print_open_prs() -> None:
    header("OPEN PRs")

    pr_json = run([
        "gh", "pr", "list", "--state", "open", "--json",
        "number,title,headRefName,author,updatedAt", "--limit", "20",
    ])
    if not pr_json:
        print("  No open PRs (or gh CLI unavailable)")
        return

    try:
        prs = json.loads(pr_json)
    except json.JSONDecodeError:
        print("  Failed to parse PR list")
        return

    if not prs:
        print("  No open PRs")
        return

    print(f"  {len(prs)} open PR(s):\n")
    for pr in prs:
        number = pr.get("number", "?")
        title = pr.get("title", "untitled")[:50]
        branch = pr.get("headRefName", "?")
        author = pr.get("author", {}).get("login", "?")
        updated = ago(pr.get("updatedAt"))
        print(f"  #{number:<5} {title}")
        print(f"         {branch} (by {author}, {updated})")


def print_recent_logs() -> None:
    header(f"RECENT LOGS (last {LOG_LINES} lines)")

    logs_dir = get_orchestrator_dir() / "logs"
    today = datetime.now().strftime("%Y-%m-%d")

    # Scheduler log
    sched_log = logs_dir / f"scheduler-{today}.log"
    if sched_log.exists():
        subheader("scheduler")
        try:
            lines = sched_log.read_text().strip().split("\n")
            for line in lines[-LOG_LINES:]:
                print(f"  {line}")
        except OSError:
            pass

    # Agent logs
    runtime_dir = get_agents_runtime_dir()
    if runtime_dir.exists():
        for agent_dir in sorted(runtime_dir.iterdir()):
            if not agent_dir.is_dir():
                continue
            name = agent_dir.name
            log_file = logs_dir / f"{name}-{today}.log"
            if log_file.exists() and log_file.stat().st_size > 0:
                subheader(name)
                try:
                    lines = log_file.read_text().strip().split("\n")
                    for line in lines[-LOG_LINES:]:
                        print(f"  {line}")
                except OSError:
                    pass


def print_task_detail(task_id: str) -> None:
    """Print detailed info for a specific task."""
    header(f"TASK: {task_id}")

    sdk = get_sdk()
    task = sdk.tasks.get(task_id)
    if not task:
        print(f"  Task {task_id} not found")
        return

    # Task summary
    fields = [
        ("queue", task.get("queue")),
        ("priority", task.get("priority")),
        ("role", task.get("role")),
        ("title", task.get("title")),
        ("branch", task.get("branch")),
        ("claimed_by", task.get("claimed_by")),
        ("orchestrator_id", task.get("orchestrator_id")),
        ("commits", task.get("commits_count", 0)),
        ("turns", task.get("turns_used")),
        ("attempts", task.get("attempt_count", 0)),
        ("rejections", task.get("rejection_count", 0)),
        ("pr_url", task.get("pr_url")),
        ("project_id", task.get("project_id")),
        ("blocked_by", task.get("blocked_by")),
        ("version", task.get("version")),
    ]
    for label, value in fields:
        if value is not None and value != "" and value != 0:
            print(f"  {label:<16} {value}")

    # Lifecycle timestamps
    subheader("Lifecycle")
    timestamps = [
        ("created_at", task.get("created_at")),
        ("claimed_at", task.get("claimed_at")),
        ("submitted_at", task.get("submitted_at")),
        ("completed_at", task.get("completed_at")),
        ("updated_at", task.get("updated_at")),
    ]
    for label, ts in timestamps:
        if ts:
            print(f"  {label:<16} {ts}  ({ago(ts)})")

    # Durations
    subheader("Durations")
    created = task.get("created_at")
    claimed = task.get("claimed_at")
    submitted = task.get("submitted_at")
    completed = task.get("completed_at")
    if created and claimed:
        print(f"  wait time:     {duration_str(created, claimed)}")
    if claimed and submitted:
        print(f"  work time:     {duration_str(claimed, submitted)}")
    elif claimed and not submitted:
        print(f"  working for:   {duration_str(claimed)}")
    if submitted and completed:
        print(f"  review time:   {duration_str(submitted, completed)}")
    elif submitted and not completed:
        print(f"  in review for: {duration_str(submitted)}")
    if created and completed:
        print(f"  total time:    {duration_str(created, completed)}")


# -- Main ------------------------------------------------------------------


def main() -> int:
    if TASK_ID:
        print(
            f"\nOrchestrator Task Detail"
            f" -- {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        print("-" * 60)
        print_task_detail(TASK_ID)
        print(f"\n{'-' * 60}")
        return 0

    print(
        f"\nOrchestrator Status Report"
        f" -- {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    print("-" * 60)

    print_scheduler_health()
    print_queue_status()
    print_agent_status()
    print_worktree_status()
    print_agent_notes()
    print_open_prs()
    print_recent_logs()

    print(f"\n{'-' * 60}")
    print("Done.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
