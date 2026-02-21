"""Octopoid CLI — manage tasks and worktrees from the command line."""

import argparse
import sys
from pathlib import Path

from . import __version__
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


def cmd_install_commands(args: argparse.Namespace) -> None:
    """Install octopoid slash commands to .claude/commands/."""
    import shutil
    from .init import find_parent_project, get_orchestrator_submodule

    parent = find_parent_project()
    submodule = get_orchestrator_submodule()
    source_dir = submodule / "commands" / "management"

    if not source_dir.exists():
        print(f"Source commands not found at {source_dir}", file=sys.stderr)
        sys.exit(1)

    dest_dir = parent / ".claude" / "commands"
    dest_dir.mkdir(parents=True, exist_ok=True)

    source_files = sorted(source_dir.glob("*.md"))
    if not source_files:
        print("No command files found.")
        return

    installed = []
    skipped = []
    for src in source_files:
        dest = dest_dir / src.name
        if dest.exists() and not args.force:
            # Check if content differs
            if dest.read_text() == src.read_text():
                skipped.append(src.stem)
                continue
        shutil.copy2(src, dest)
        installed.append(src.stem)

    # Remove outdated commands that are no longer in source
    outdated = {"proposal-status", "retry-failed", "tune-backpressure", "octopoid-status"}
    removed = []
    for name in outdated:
        old = dest_dir / f"{name}.md"
        if old.exists():
            old.unlink()
            removed.append(name)

    if installed:
        print(f"Installed {len(installed)} command(s) to .claude/commands/:")
        for name in installed:
            print(f"  /{name}")
    if skipped:
        print(f"Unchanged: {len(skipped)} command(s)")
    if removed:
        print(f"Removed {len(removed)} outdated command(s):")
        for name in removed:
            print(f"  /{name}")
    if not installed and not removed:
        print("All commands up to date.")


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


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="octopoid",
        description="Octopoid task management CLI",
    )
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"%(prog)s {__version__}",
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

    # install-commands
    p_ic = sub.add_parser("install-commands", help="Install slash commands to .claude/commands/")
    p_ic.add_argument("--force", "-f", action="store_true", help="Overwrite even if unchanged")
    p_ic.set_defaults(func=cmd_install_commands)

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
