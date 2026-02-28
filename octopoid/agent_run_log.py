"""Run log for background agent jobs.

Appends a JSONL entry after each background agent job completes,
capturing timing and a brief summary of what the agent did.
The dashboard reads these entries to surface "last run: 3m ago, processed 2 drafts".

Log files are stored at:
  .octopoid/runtime/agent-run-logs/{job_name}.jsonl

Each entry is a JSON line:
  {"started_at": "...", "finished_at": "...", "summary": "...", "outcome": "ok|error"}
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Max JSONL entries to keep per job (older entries are trimmed on write)
_MAX_LOG_ENTRIES = 20
# Max characters to extract from stdout for summary
_STDOUT_TAIL_CHARS = 2000
# Max summary characters stored in log
_SUMMARY_MAX_CHARS = 400


def _run_log_dir() -> Path:
    """Return (and create) the agent-run-logs directory."""
    from .config import get_runtime_dir
    log_dir = get_runtime_dir() / "agent-run-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _run_log_path(job_name: str) -> Path:
    """Path to the JSONL log file for a specific job."""
    return _run_log_dir() / f"{job_name}.jsonl"


def write_run_log(
    job_name: str,
    job_dir: Path | str | None,
    started_at: str | None,
    *,
    outcome: str = "ok",
) -> None:
    """Append a run log entry for a completed background agent job.

    Args:
        job_name: Name of the job (e.g. "codebase_analyst").
        job_dir: Path to the job directory containing stdout.log.
        started_at: ISO8601 timestamp when the run started.
        outcome: "ok" or "error".
    """
    summary = _extract_summary(job_dir) if job_dir else None

    entry: dict[str, Any] = {
        "started_at": started_at,
        "finished_at": datetime.now(tz=timezone.utc).isoformat(),
        "outcome": outcome,
        "summary": summary,
    }
    if job_dir:
        entry["job_dir"] = str(job_dir)

    log_path = _run_log_path(job_name)
    try:
        # Read existing entries
        existing: list[str] = []
        if log_path.exists():
            existing = [line for line in log_path.read_text().splitlines() if line.strip()]

        # Append new entry and trim to max
        existing.append(json.dumps(entry))
        if len(existing) > _MAX_LOG_ENTRIES:
            existing = existing[-_MAX_LOG_ENTRIES:]

        log_path.write_text("\n".join(existing) + "\n")
    except OSError:
        pass  # Non-fatal — log write failure must not crash the scheduler


def read_run_logs(job_name: str, max_entries: int = 5) -> list[dict[str, Any]]:
    """Read the most recent run log entries for a job, newest first.

    Args:
        job_name: Name of the job.
        max_entries: Maximum number of entries to return.

    Returns:
        List of log entry dicts, most recent first. Empty list if no log exists.
    """
    log_path = _run_log_path(job_name)
    if not log_path.exists():
        return []

    try:
        lines = log_path.read_text().splitlines()
        entries: list[dict[str, Any]] = []
        # Walk backwards (most recent last in file)
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
            if len(entries) >= max_entries:
                break
        return entries  # already newest-first (reversed iteration)
    except OSError:
        return []


def get_last_run_summary(job_name: str) -> dict[str, Any] | None:
    """Return the most recent run log entry, or None if no log exists."""
    entries = read_run_logs(job_name, max_entries=1)
    return entries[0] if entries else None


def _extract_summary(job_dir: Path | str) -> str | None:
    """Extract a brief summary from the agent's stdout.log.

    Takes the last few lines of output, which typically contain the agent's
    final status report (e.g. "Created draft for 3 files, skipped 2").

    Args:
        job_dir: Path to the job directory.

    Returns:
        A short summary string, or None if stdout.log is missing/empty.
    """
    job_dir = Path(job_dir)
    stdout_log = job_dir / "stdout.log"
    if not stdout_log.exists():
        return None

    try:
        content = stdout_log.read_text(errors="replace")
        if not content.strip():
            return None

        # Take the tail of stdout
        tail = content[-_STDOUT_TAIL_CHARS:]

        # Filter to non-empty lines and take the last 3
        lines = [line.strip() for line in tail.splitlines() if line.strip()]
        if not lines:
            return None

        summary = " | ".join(lines[-3:])
        if len(summary) > _SUMMARY_MAX_CHARS:
            summary = "..." + summary[-(_SUMMARY_MAX_CHARS - 3):]
        return summary
    except OSError:
        return None
