"""System health tracking, auto-pause, and spawn-failure recovery."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import get_agents_base_dir, get_logs_dir, get_orchestrator_dir
from . import queue_utils
from .result_handler import _get_circuit_breaker_threshold
from .pool import count_running_instances, register_instance_pid

logger = logging.getLogger("octopoid.scheduler")

SYSTEMIC_FAILURE_THRESHOLD = 2


# =============================================================================
# System Health State
# =============================================================================

def _get_system_health_path() -> Path:
    """Get path to system_health.json in the runtime directory."""
    from .config import get_runtime_dir
    return get_runtime_dir() / "system_health.json"


def _load_system_health() -> dict:
    """Load system health state, returning defaults if file doesn't exist."""
    path = _get_system_health_path()
    if not path.exists():
        return {
            "consecutive_systemic_failures": 0,
            "last_systemic_failure": None,
            "auto_paused": False,
            "auto_paused_at": None,
            "auto_pause_reason": None,
        }
    try:
        return json.loads(path.read_text())
    except Exception:
        return {
            "consecutive_systemic_failures": 0,
            "last_systemic_failure": None,
            "auto_paused": False,
            "auto_paused_at": None,
            "auto_pause_reason": None,
        }


def _save_system_health(data: dict) -> None:
    """Persist system health state to disk."""
    path = _get_system_health_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _record_systemic_failure(reason: str) -> int:
    """Increment the consecutive systemic failure counter.

    Args:
        reason: Human-readable description of the failure.

    Returns:
        The new consecutive failure count.
    """
    health = _load_system_health()
    health["consecutive_systemic_failures"] = health.get("consecutive_systemic_failures", 0) + 1
    health["last_failure_time"] = datetime.now(tz=timezone.utc).isoformat()
    health["last_failure_reason"] = reason
    _save_system_health(health)
    count = health["consecutive_systemic_failures"]
    logger.warning(f"Systemic failure #{count}: {reason}")
    return count


def reset_systemic_failures() -> None:
    """Reset the systemic failure counter (called by the diagnostic agent after fixing)."""
    health = _load_system_health()
    health["consecutive_systemic_failures"] = 0
    health["last_failure_time"] = None
    health["last_failure_reason"] = None
    _save_system_health(health)
    logger.info("Systemic failure counter reset")


# =============================================================================
# Diagnostic Agent and Auto-Pause
# =============================================================================

def _spawn_diagnostic_agent(reason: str) -> None:
    """Spawn the diagnostic agent to investigate a systemic pause.

    Prepares a job directory for the diagnostic agent with the failure
    context written to context.json, then invokes claude directly.

    Args:
        reason: The failure reason that triggered the auto-pause.
    """
    import yaml as _yaml

    agents_base = get_agents_base_dir()
    diag_agent_dir = agents_base / "diagnostic"
    agent_yaml = diag_agent_dir / "agent.yaml"

    if not agent_yaml.exists():
        logger.error("Diagnostic agent config not found at %s, cannot spawn", agent_yaml)
        return

    with open(agent_yaml) as f:
        agent_config = _yaml.safe_load(f) or {}

    agent_config["agent_dir"] = str(diag_agent_dir)
    agent_config.setdefault("name", "diagnostic")
    agent_config.setdefault("blueprint_name", "diagnostic")

    # Don't spawn if already running
    if count_running_instances("diagnostic") > 0:
        logger.info("Diagnostic agent already running, skipping spawn")
        return

    # Lazy import to avoid circular dependency with scheduler
    from .scheduler import prepare_job_directory, invoke_claude
    try:
        job_dir = prepare_job_directory("diagnostic", agent_config)
    except Exception as e:
        logger.error(f"Failed to prepare diagnostic job directory: {e}")
        return

    # Read last N lines of scheduler log for context
    log_tail_lines: list[str] = []
    log_file = get_logs_dir() / "octopoid.log"
    if log_file.exists():
        try:
            lines = log_file.read_text().splitlines()
            log_tail_lines = lines[-100:]
        except OSError:
            pass

    # Write diagnostic context to job_dir/context.json
    health = _load_system_health()
    context = {
        "trigger_reason": reason,
        "consecutive_failures": health.get("consecutive_systemic_failures", 0),
        "last_failure_time": health.get("last_failure_time"),
        "orchestrator_dir": str(get_orchestrator_dir()),
        "pause_file": str(get_orchestrator_dir() / "PAUSE"),
        "health_file": str(_get_system_health_path()),
        "log_file": str(log_file),
        "log_tail": log_tail_lines,
        "queue_counts": None,
    }

    # Try to get queue counts from the server
    try:
        orch_id = queue_utils.get_orchestrator_id()
        sdk = queue_utils.get_sdk()
        poll_data = sdk.poll(orch_id)
        context["queue_counts"] = poll_data.get("queue_counts")
    except Exception:
        pass  # Server may be unreachable — that could be the problem

    (job_dir / "context.json").write_text(json.dumps(context, indent=2))

    try:
        pid = invoke_claude(job_dir, agent_config)
        register_instance_pid("diagnostic", pid, "", "diagnostic-1")
        health["last_diagnostic_spawned"] = datetime.now(tz=timezone.utc).isoformat()
        _save_system_health(health)
        logger.info(f"Diagnostic agent spawned with PID {pid}")
    except Exception as e:
        logger.error(f"Failed to spawn diagnostic agent: {e}")


def _auto_pause_and_diagnose(reason: str) -> None:
    """Write the PAUSE file and spawn the diagnostic agent.

    Called when the systemic failure counter reaches the threshold.

    Args:
        reason: The failure reason that triggered the auto-pause.
    """
    pause_file = get_orchestrator_dir() / "PAUSE"
    pause_file.write_text(f"Auto-paused: {reason}\n")
    logger.warning(
        "System auto-paused due to %d consecutive systemic failures. "
        "Remove .octopoid/PAUSE when resolved.",
        SYSTEMIC_FAILURE_THRESHOLD,
    )
    _spawn_diagnostic_agent(reason)


def _handle_systemic_failure(reason: str) -> None:
    """Record a systemic failure and auto-pause if the threshold is reached.

    Called from spawn failure handlers and other systemic error paths.

    Args:
        reason: Human-readable description of what failed.
    """
    count = _record_systemic_failure(reason)
    if count >= SYSTEMIC_FAILURE_THRESHOLD:
        _auto_pause_and_diagnose(reason)


# =============================================================================
# Task Requeue Helpers
# =============================================================================

def _requeue_task(task_id: str, source_queue: str = "incoming", task: dict | None = None) -> None:
    """Requeue a task back to its source queue after spawn failure.

    Increments attempt_count on the server. If attempt_count reaches the
    circuit breaker threshold, moves the task to failed instead of requeuing.

    Args:
        task_id: Task to requeue.
        source_queue: Queue the task should return to. For tasks claimed from
            'incoming' (implementer), this is 'incoming'. For tasks claimed
            in-place from 'provisional' (gatekeeper), this is 'provisional'.
        task: Optional task dict with current state (used to read attempt_count
            without an extra API call).
    """
    try:
        from .queue_utils import get_sdk
        sdk = get_sdk()

        # Circuit breaker only applies to claimed→incoming transitions
        if source_queue == "incoming":
            if task is None:
                task = sdk.tasks.get(task_id) or {}
            current_attempt_count = task.get("attempt_count", 0)
            new_attempt_count = current_attempt_count + 1
            threshold = _get_circuit_breaker_threshold()

            if new_attempt_count >= threshold:
                reason = (
                    f"Circuit breaker: spawn failed {new_attempt_count} time(s) "
                    f"(threshold={threshold}). Task could not be started."
                )
                queue_utils.fail_task(
                    task_id,
                    reason=reason,
                    source='spawn-failure-circuit-breaker',
                    claimed_by=None,
                    lease_expires_at=None,
                    attempt_count=new_attempt_count,
                )
                logger.debug(f"Circuit breaker tripped for {task_id}: {reason}")
                return

            sdk.tasks.update(
                task_id,
                queue=source_queue,
                claimed_by=None,
                lease_expires_at=None,
                attempt_count=new_attempt_count,
            )
        else:
            # Provisional: just clear the claim, no attempt_count increment
            sdk.tasks.update(task_id, queue=source_queue, claimed_by=None, lease_expires_at=None)

        logger.debug(f"Requeued task {task_id} back to {source_queue}")
    except Exception as e:
        logger.error(f"Requeue failed for {task_id}: {e}")


def _requeue_task_blameless(task_id: str, source_queue: str = "incoming") -> None:
    """Requeue a task without incrementing attempt_count.

    Used for systemic failures (spawn failure, infrastructure error) where the
    task itself is not at fault. The task is returned to its source queue with
    no penalty.

    Args:
        task_id: Task to requeue.
        source_queue: Queue the task should return to.
    """
    try:
        from .queue_utils import get_sdk
        sdk = get_sdk()
        sdk.tasks.update(task_id, queue=source_queue, claimed_by=None, lease_expires_at=None)
        logger.debug(f"Blameless requeue of task {task_id} back to {source_queue} (no attempt_count increment)")
    except Exception as e:
        logger.error(f"Blameless requeue failed for {task_id}: {e}")


def _reset_systemic_failure_counter() -> None:
    """Reset the systemic failure counter after a successful spawn."""
    health = _load_system_health()
    if health.get("consecutive_systemic_failures", 0) > 0:
        health["consecutive_systemic_failures"] = 0
        _save_system_health(health)
        logger.debug("Systemic failure counter reset after successful spawn")
