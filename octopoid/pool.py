"""PID tracking per blueprint for the agent pool model.

Each blueprint (e.g. "implementer") can have multiple concurrent instances.
This module tracks their PIDs in a per-blueprint running_pids.json file.

Every mutation (add/remove) is logged to a JSONL audit trail at
.octopoid/runtime/logs/pid_audit.jsonl for post-incident forensics.
"""

import json
import os
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .config import get_agents_runtime_dir


# ---------------------------------------------------------------------------
# PID audit log
# ---------------------------------------------------------------------------

def _pid_audit(
    action: str,
    blueprint: str,
    pid: int,
    *,
    task_id: str = "",
    instance_name: str = "",
    reason: str = "",
    pids_before: dict | None = None,
    pids_after: dict | None = None,
) -> None:
    """Append a structured entry to the PID audit log.

    Every PID state change (register, remove, cleanup) is recorded so we can
    reconstruct exactly what happened during incidents.
    """
    try:
        from .config import get_logs_dir
        log_path = get_logs_dir() / "pid_audit.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "action": action,
            "blueprint": blueprint,
            "pid": pid,
            "task_id": task_id,
            "instance_name": instance_name,
            "reason": reason,
            "caller": _caller_info(),
            "pids_before": _summarise_pids(pids_before),
            "pids_after": _summarise_pids(pids_after),
        }
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # Audit logging must never crash the scheduler


def _summarise_pids(pids: dict | None) -> list[int] | None:
    """Return sorted list of tracked PIDs (compact representation for audit)."""
    if pids is None:
        return None
    return sorted(int(p) for p in pids)


def _caller_info() -> str:
    """Return file:line of the caller outside pool.py, for audit context."""
    for frame in traceback.extract_stack():
        if "pool.py" not in frame.filename and "importlib" not in frame.filename:
            last_external = f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
    return last_external


def get_blueprint_pids_path(blueprint_name: str) -> Path:
    """Path to running_pids.json for a blueprint."""
    return get_agents_runtime_dir() / blueprint_name / "running_pids.json"


def load_blueprint_pids(blueprint_name: str) -> dict[int, dict]:
    """Load {pid: {task_id, started_at, instance_name}} for a blueprint.

    Returns an empty dict if the file does not exist or cannot be parsed.
    Keys are integers (PIDs).
    """
    path = get_blueprint_pids_path(blueprint_name)
    if not path.exists():
        return {}

    try:
        with open(path) as f:
            raw = json.load(f)
        # JSON keys are strings; convert to int
        return {int(pid_str): info for pid_str, info in raw.items()}
    except (json.JSONDecodeError, IOError, ValueError):
        return {}


def save_blueprint_pids(blueprint_name: str, pids: dict[int, dict]) -> None:
    """Save blueprint PIDs atomically (write to temp file, then rename).

    Args:
        blueprint_name: Name of the blueprint (e.g. "implementer").
        pids: Mapping of PID (int) to info dict.
    """
    path = get_blueprint_pids_path(blueprint_name)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Snapshot what was on disk before this write, for the audit trail
    old_pids = load_blueprint_pids(blueprint_name) if path.exists() else {}
    removed = set(old_pids) - set(pids)
    added = set(pids) - set(old_pids)

    # JSON requires string keys
    serialisable = {str(pid): info for pid, info in pids.items()}

    fd, temp_path = tempfile.mkstemp(
        dir=path.parent, prefix=".running_pids_", suffix=".json"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(serialisable, f, indent=2)
        os.rename(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise

    # Log a save event when PIDs were added or removed.
    # Individual add/remove events are already logged by register_instance_pid
    # and check_and_update_finished_agents; this catches anything unexpected.
    if removed or added:
        _pid_audit(
            "save", blueprint_name, 0,
            reason=f"added={sorted(added)},removed={sorted(removed)}",
            pids_before=old_pids, pids_after=pids,
        )


def count_running_instances(blueprint_name: str) -> int:
    """Count how many PIDs are actually alive for this blueprint.

    Dead PIDs are ignored (but not removed from the file here).
    """
    pids = load_blueprint_pids(blueprint_name)
    return sum(1 for pid in pids if _is_pid_alive(pid))


def register_instance_pid(
    blueprint_name: str,
    pid: int,
    task_id: str,
    instance_name: str,
) -> None:
    """Add a new PID to the blueprint tracking file.

    Args:
        blueprint_name: Name of the blueprint (e.g. "implementer").
        pid: Process ID of the new instance.
        task_id: Task being worked on by this instance.
        instance_name: Unique name for this instance (e.g. "implementer-1").
    """
    pids = load_blueprint_pids(blueprint_name)
    pids_before = dict(pids)
    pids[pid] = {
        "task_id": task_id,
        "started_at": datetime.now(tz=timezone.utc).isoformat(),
        "instance_name": instance_name,
    }
    save_blueprint_pids(blueprint_name, pids)
    _pid_audit(
        "register", blueprint_name, pid,
        task_id=task_id, instance_name=instance_name,
        reason="agent_spawned",
        pids_before=pids_before, pids_after=pids,
    )


def get_active_task_ids(blueprint_name: str) -> set[str]:
    """Return the set of task IDs currently being worked on by running instances.

    Only considers alive PIDs.

    Args:
        blueprint_name: Name of the blueprint (e.g. "implementer").

    Returns:
        Set of task_id strings for all running instances.
    """
    pids = load_blueprint_pids(blueprint_name)
    return {
        info["task_id"]
        for pid, info in pids.items()
        if info.get("task_id") and _is_pid_alive(pid)
    }


def cleanup_dead_pids(blueprint_name: str) -> int:
    """Remove dead PIDs from tracking.

    WARNING: This function should NOT be called from production code paths.
    Only check_and_update_finished_agents() should remove PIDs, because it
    processes agent results first. Calling this directly creates orphaned tasks.

    Args:
        blueprint_name: Name of the blueprint.

    Returns:
        Number of dead PIDs removed.
    """
    pids = load_blueprint_pids(blueprint_name)
    pids_before = dict(pids)
    dead = [pid for pid in pids if not _is_pid_alive(pid)]
    if dead:
        for pid in dead:
            task_id = pids[pid].get("task_id", "")
            instance_name = pids[pid].get("instance_name", "")
            _pid_audit(
                "cleanup_dead", blueprint_name, pid,
                task_id=task_id, instance_name=instance_name,
                reason="dead_pid_cleanup (DEPRECATED PATH)",
                pids_before=pids_before, pids_after=None,
            )
            del pids[pid]
        save_blueprint_pids(blueprint_name, pids)
        # Log final state
        for pid in dead:
            _pid_audit(
                "cleanup_dead_saved", blueprint_name, pid,
                reason=f"removed {len(dead)} dead PIDs",
                pids_before=pids_before, pids_after=pids,
            )
    return len(dead)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_pid_alive(pid: int) -> bool:
    """Return True if the process with the given PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
