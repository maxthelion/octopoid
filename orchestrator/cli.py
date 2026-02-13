"""Octopoid CLI — manage tasks and worktrees from the command line."""

import argparse
import sys
from pathlib import Path

from .config import get_tasks_dir
from .queue_utils import get_sdk


def _fmt_table(rows: list[list[str]], headers: list[str]) -> str:
    """Format rows as a simple aligned table."""
    all_rows = [headers] + rows
    widths = [max(len(r[i]) for r in all_rows) for i in range(len(headers))]
    lines = []
    header_line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    lines.append(header_line)
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(c.ljust(w) for c, w in zip(row, widths)))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_tasks(args: argparse.Namespace) -> None:
    """List tasks in a table."""
    sdk = get_sdk()
    params = {}
    if args.queue:
        params["queue"] = args.queue
    tasks = sdk.tasks.list(**params)

    if not tasks:
        print("No tasks found.")
        return

    headers = ["ID", "QUEUE", "PRI", "ROLE", "TITLE", "CLAIMED_BY"]
    rows = []
    for t in tasks:
        rows.append([
            t.get("id", ""),
            t.get("queue", ""),
            t.get("priority", ""),
            t.get("role", "") or "",
            (t.get("title", "") or "")[:50],
            t.get("claimed_by", "") or "",
        ])

    print(_fmt_table(rows, headers))
    print(f"\n{len(tasks)} task(s)")


def cmd_task(args: argparse.Namespace) -> None:
    """Show full detail for a single task."""
    sdk = get_sdk()
    task = sdk.tasks.get(args.id)

    if not task:
        print(f"Task not found: {args.id}", file=sys.stderr)
        sys.exit(1)

    skip = {"file_path"}
    for key, value in sorted(task.items()):
        if key in skip and not args.verbose:
            continue
        if value is None or value == "":
            continue
        print(f"  {key:25s}  {value}")


def cmd_requeue(args: argparse.Namespace) -> None:
    """Requeue a claimed task back to incoming."""
    sdk = get_sdk()
    try:
        task = sdk.tasks.requeue(args.id)
        print(f"Requeued {args.id} -> {task.get('queue', 'incoming')}")
    except Exception as e:
        print(f"Failed to requeue {args.id}: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_cancel(args: argparse.Namespace) -> None:
    """Delete / cancel a task."""
    sdk = get_sdk()

    if not args.force:
        task = sdk.tasks.get(args.id)
        if not task:
            print(f"Task not found: {args.id}", file=sys.stderr)
            sys.exit(1)
        answer = input(
            f"Delete task {args.id} ({task.get('queue','?')})? [y/N] "
        )
        if answer.lower() != "y":
            print("Cancelled.")
            return

    try:
        sdk.tasks.delete(args.id)
        print(f"Deleted {args.id}")
    except Exception as e:
        print(f"Failed to delete {args.id}: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_worktrees(args: argparse.Namespace) -> None:
    """List orchestrator task worktrees."""
    tasks_dir = get_tasks_dir()
    if not tasks_dir.exists():
        print("No task worktrees found.")
        return

    entries = sorted(tasks_dir.iterdir())
    if not entries:
        print("No task worktrees found.")
        return

    for entry in entries:
        wt = entry / "worktree"
        marker = "  (worktree)" if wt.exists() else ""
        print(f"  {entry.name}{marker}")
    print(f"\n{len(entries)} task dir(s)")


def cmd_worktrees_clean(args: argparse.Namespace) -> None:
    """Prune task worktrees for done/deleted tasks."""
    from .git_utils import cleanup_task_worktree

    sdk = get_sdk()
    tasks_dir = get_tasks_dir()

    if not tasks_dir.exists():
        print("No task worktrees to clean.")
        return

    entries = sorted(tasks_dir.iterdir())
    if not entries:
        print("No task worktrees to clean.")
        return

    cleaned = 0
    for entry in entries:
        task_id = entry.name
        wt = entry / "worktree"
        if not wt.exists():
            continue

        # Check task state on server
        task = sdk.tasks.get(task_id)
        removable = task is None or task.get("queue") in ("done", "deleted")

        if not removable:
            continue

        status = "deleted" if task is None else task.get("queue", "?")
        if args.dry_run:
            print(f"  [dry-run] would remove {task_id} ({status})")
        else:
            cleanup_task_worktree(task_id)
            print(f"  removed {task_id} ({status})")
        cleaned += 1

    if cleaned == 0:
        print("Nothing to clean.")
    elif args.dry_run:
        print(f"\n{cleaned} worktree(s) would be removed. Run without --dry-run to remove.")
    else:
        print(f"\n{cleaned} worktree(s) removed.")


def cmd_debug_task(args: argparse.Namespace) -> None:
    """Show debug information for a specific task."""
    sdk = get_sdk()
    try:
        debug_info = sdk.debug.task(args.id)
    except Exception as e:
        print(f"Failed to get debug info for {args.id}: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Task: {debug_info.get('task_id', args.id)}")
    print(f"  State:              {debug_info.get('state', 'unknown')}")

    if debug_info.get('lease_expires_at'):
        print(f"  Lease Expires:      {debug_info.get('lease_expires_in', 'N/A')} ({debug_info.get('lease_expires_at', 'N/A')})")

    blocking = debug_info.get('blocking', {})
    print(f"\nBlocking:")
    print(f"  Is Blocked:         {blocking.get('is_blocked', False)}")
    print(f"  Blocked By:         {blocking.get('blocked_by') or 'none'}")
    blocks = blocking.get('blocks', [])
    print(f"  Blocks:             {', '.join(blocks) if blocks else 'none'}")

    burnout = debug_info.get('burnout', {})
    print(f"\nBurnout:")
    print(f"  Is Burned Out:      {burnout.get('is_burned_out', False)}")
    print(f"  Turns Used:         {burnout.get('turns_used', 0)}")
    print(f"  Commits Count:      {burnout.get('commits_count', 0)}")
    print(f"  Threshold:          {burnout.get('threshold', 0)}")

    gatekeeper = debug_info.get('gatekeeper', {})
    print(f"\nGatekeeper:")
    print(f"  Review Round:       {gatekeeper.get('review_round', 0)}")
    print(f"  Max Rounds:         {gatekeeper.get('max_rounds', 3)}")
    print(f"  Rejection Count:    {gatekeeper.get('rejection_count', 0)}")

    attempts = debug_info.get('attempts', {})
    print(f"\nAttempts:")
    print(f"  Attempt Count:      {attempts.get('attempt_count', 0)}")
    if attempts.get('last_claimed_at'):
        print(f"  Last Claimed:       {attempts.get('last_claimed_at')}")
    if attempts.get('last_submitted_at'):
        print(f"  Last Submitted:     {attempts.get('last_submitted_at')}")


def cmd_debug_queues(args: argparse.Namespace) -> None:
    """Show debug information for all queues."""
    sdk = get_sdk()
    try:
        debug_info = sdk.debug.queues()
    except Exception as e:
        print(f"Failed to get queue debug info: {e}", file=sys.stderr)
        sys.exit(1)

    queues = debug_info.get('queues', {})
    print("Queue Status:")
    print()

    for queue_name in ['incoming', 'claimed', 'provisional', 'done', 'failed', 'rejected', 'blocked']:
        queue_info = queues.get(queue_name)
        if not queue_info:
            continue

        count = queue_info.get('count', 0)
        print(f"  {queue_name:15s}  {count:4d} task(s)", end='')

        oldest = queue_info.get('oldest_task')
        if oldest:
            print(f"  (oldest: {oldest.get('id', 'unknown')}, age: {oldest.get('age', 'unknown')})")
        else:
            print()

    claimed = debug_info.get('claimed', {})
    claimed_tasks = claimed.get('tasks', [])
    if claimed_tasks:
        print(f"\nClaimed Tasks ({len(claimed_tasks)}):")
        for task in claimed_tasks:
            print(f"  {task.get('id', 'unknown'):30s}  claimed by: {task.get('claimed_by', 'unknown'):20s}  "
                  f"for: {task.get('claimed_for', 'unknown'):8s}  expires in: {task.get('lease_expires_in', 'unknown')}")


def cmd_debug_agents(args: argparse.Namespace) -> None:
    """Show debug information for all agents and orchestrators."""
    sdk = get_sdk()
    try:
        debug_info = sdk.debug.agents()
    except Exception as e:
        print(f"Failed to get agent debug info: {e}", file=sys.stderr)
        sys.exit(1)

    summary = debug_info.get('summary', {})
    print("Agent Summary:")
    print(f"  Total Orchestrators:    {summary.get('total_orchestrators', 0)}")
    print(f"  Active Orchestrators:   {summary.get('active_orchestrators', 0)}")
    print(f"  Total Agents:           {summary.get('total_agents', 0)}")
    print(f"  Total Claimed Tasks:    {summary.get('total_claimed_tasks', 0)}")
    print()

    orchestrators = debug_info.get('orchestrators', [])
    if orchestrators:
        print("Orchestrators:")
        for orch in orchestrators:
            print(f"  {orch.get('orchestrator_id', 'unknown'):30s}  "
                  f"status: {orch.get('status', 'unknown'):8s}  "
                  f"cluster: {orch.get('cluster', 'unknown'):10s}  "
                  f"tasks: {orch.get('current_tasks', 0)}/{orch.get('total_completed', 0)}")
            if orch.get('last_heartbeat_at'):
                print(f"    Last heartbeat: {orch.get('heartbeat_age', 'unknown')} ago ({orch.get('last_heartbeat_at')})")
        print()

    agents = debug_info.get('agents', [])
    if agents:
        print("Agents:")
        for agent in agents:
            stats = agent.get('stats', {})
            success_rate = stats.get('success_rate', 0.0) * 100
            print(f"  {agent.get('agent_name', 'unknown'):20s}  "
                  f"role: {agent.get('role', 'unknown'):10s}  "
                  f"success: {success_rate:.1f}%  "
                  f"({stats.get('tasks_completed', 0)}/{stats.get('tasks_claimed', 0)} claimed)")

            current = agent.get('current_task')
            if current:
                print(f"    Current: {current.get('id', 'unknown')} (claimed at {current.get('claimed_at', 'unknown')})")


def cmd_debug_status(args: argparse.Namespace) -> None:
    """Show comprehensive system status overview."""
    sdk = get_sdk()
    try:
        status = sdk.debug.status()
    except Exception as e:
        print(f"Failed to get system status: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"System Status - {status.get('timestamp', 'unknown')}")
    print("=" * 80)
    print()

    # Queue summary
    queues = status.get('queues', {}).get('queues', {})
    print("Queues:")
    for queue_name in ['incoming', 'claimed', 'provisional', 'done', 'failed']:
        queue_info = queues.get(queue_name, {})
        count = queue_info.get('count', 0)
        print(f"  {queue_name:15s}  {count:4d} task(s)")
    print()

    # Agent summary
    agents = status.get('agents', {}).get('summary', {})
    print("Agents:")
    print(f"  Active Orchestrators:   {agents.get('active_orchestrators', 0)}")
    print(f"  Total Agents:           {agents.get('total_agents', 0)}")
    print(f"  Claimed Tasks:          {agents.get('total_claimed_tasks', 0)}")
    print()

    # Health metrics
    health = status.get('health', {})
    oldest = health.get('oldest_incoming_task')
    if oldest:
        print("Health:")
        print(f"  Oldest Incoming Task:   {oldest.get('id', 'unknown')} (age: {oldest.get('age', 'unknown')})")

    stuck = health.get('stuck_tasks', [])
    if stuck:
        print(f"  Stuck Tasks:            {len(stuck)}")
        for task in stuck[:5]:  # Show first 5
            print(f"    - {task.get('id', 'unknown'):30s}  {task.get('issue', 'unknown')}")

    zombies = health.get('zombie_claims', [])
    if zombies:
        print(f"  Zombie Claims:          {len(zombies)}")
        for task in zombies[:5]:  # Show first 5
            print(f"    - {task.get('id', 'unknown'):30s}  claimed by {task.get('claimed_by', 'unknown')}")

    if oldest or stuck or zombies:
        print()

    # Performance metrics
    metrics = status.get('metrics', {})
    print("Performance (24h):")
    print(f"  Avg Time to Claim:      {metrics.get('avg_time_to_claim', 'N/A')}")
    print(f"  Avg Time to Complete:   {metrics.get('avg_time_to_complete', 'N/A')}")
    print(f"  Tasks Created:          {metrics.get('tasks_created_24h', 0)}")
    print(f"  Tasks Completed:        {metrics.get('tasks_completed_24h', 0)}")
    print(f"  Tasks Failed:           {metrics.get('tasks_failed_24h', 0)}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="octopoid",
        description="Octopoid task management CLI",
    )
    sub = parser.add_subparsers(dest="command")

    # tasks
    p_tasks = sub.add_parser("tasks", help="List tasks")
    p_tasks.add_argument("--queue", "-q", help="Filter by queue (e.g. incoming,claimed)")
    p_tasks.set_defaults(func=cmd_tasks)

    # task <id>
    p_task = sub.add_parser("task", help="Show task detail")
    p_task.add_argument("id", help="Task ID")
    p_task.add_argument("--verbose", "-v", action="store_true", help="Show all fields")
    p_task.set_defaults(func=cmd_task)

    # requeue <id>
    p_requeue = sub.add_parser("requeue", help="Requeue a claimed task")
    p_requeue.add_argument("id", help="Task ID")
    p_requeue.set_defaults(func=cmd_requeue)

    # cancel <id>
    p_cancel = sub.add_parser("cancel", help="Delete a task")
    p_cancel.add_argument("id", help="Task ID")
    p_cancel.add_argument("--force", "-f", action="store_true", help="Skip confirmation")
    p_cancel.set_defaults(func=cmd_cancel)

    # worktrees
    p_wt = sub.add_parser("worktrees", help="List task worktrees")
    p_wt.set_defaults(func=cmd_worktrees)

    # worktrees clean
    p_wtc = sub.add_parser("worktrees-clean", help="Prune stale task worktrees")
    p_wtc.add_argument("--dry-run", action="store_true", help="Show what would be removed")
    p_wtc.set_defaults(func=cmd_worktrees_clean)

    # debug task <id>
    p_debug_task = sub.add_parser("debug-task", help="Show debug info for a task")
    p_debug_task.add_argument("id", help="Task ID")
    p_debug_task.set_defaults(func=cmd_debug_task)

    # debug queues
    p_debug_queues = sub.add_parser("debug-queues", help="Show debug info for all queues")
    p_debug_queues.set_defaults(func=cmd_debug_queues)

    # debug agents
    p_debug_agents = sub.add_parser("debug-agents", help="Show debug info for agents")
    p_debug_agents.set_defaults(func=cmd_debug_agents)

    # debug status
    p_debug_status = sub.add_parser("debug-status", help="Show comprehensive system status")
    p_debug_status.set_defaults(func=cmd_debug_status)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
