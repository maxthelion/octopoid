"""Result-handling and flow-transition subsystem for the orchestrator scheduler.

This module contains the functions responsible for processing agent results and
driving task state-machine transitions. It was extracted from scheduler.py to
give this well-scoped concern its own home.

Public API (also re-exported from orchestrator.scheduler for backwards compat):
    read_result_json            – Parse result.json from a task directory
    handle_agent_result_via_flow – Handle gatekeeper/review agent results via flow
    handle_agent_result          – Handle implementer agent results via outcome dispatch
"""

import json
from datetime import datetime
from pathlib import Path

from . import queue_utils


def debug_log(message: str) -> None:
    """Forward to scheduler.debug_log.

    Uses a function-level import to avoid a circular dependency at module
    initialisation time (scheduler imports result_handler; result_handler
    references scheduler's debug_log only when the function is called, by
    which point both modules are fully loaded).
    """
    from .scheduler import debug_log as _fn  # noqa: PLC0415
    _fn(message)


# ---------------------------------------------------------------------------
# Result reading helpers
# ---------------------------------------------------------------------------

def read_result_json(task_dir: Path) -> dict:
    """Read and parse result.json from a task directory.

    Args:
        task_dir: Path to the task directory

    Returns:
        Parsed result dict, or an error dict if missing/invalid
    """
    result_path = task_dir / "result.json"
    if not result_path.exists():
        return {"status": "failure", "message": "No result.json produced"}

    try:
        return json.loads(result_path.read_text())
    except json.JSONDecodeError:
        return {"status": "failure", "message": "Invalid result.json"}


def _read_or_infer_result(task_dir: Path) -> dict:
    """Read result.json from a task directory, with fallback heuristics.

    If result.json exists, parses and returns it. If it's missing or invalid,
    falls back to checking notes.md for a continuation signal, or returns an
    error result.

    Args:
        task_dir: Path to the task directory

    Returns:
        Result dict with at least an "outcome" key.
    """
    result_path = task_dir / "result.json"

    if result_path.exists():
        try:
            return json.loads(result_path.read_text())
        except json.JSONDecodeError:
            return {"outcome": "error", "reason": "Invalid result.json"}

    # No result.json — check for progress notes as a continuation signal
    notes_path = task_dir / "notes.md"
    if notes_path.exists() and notes_path.read_text().strip():
        return {"outcome": "needs_continuation"}

    return {"outcome": "error", "reason": "No result.json produced"}


# ---------------------------------------------------------------------------
# Flow-transition helpers
# ---------------------------------------------------------------------------

def _perform_transition(sdk: object, task_id: str, to_state: str) -> None:
    """Perform the actual API call to transition a task to the given state.

    The mapping is:
    - "provisional" → sdk.tasks.submit()  (standard submit after implementation)
    - "done"        → sdk.tasks.accept()  (direct accept, e.g. child tasks)
    - anything else → sdk.tasks.update(queue=to_state)  (custom queues)
    """
    if to_state == "provisional":
        sdk.tasks.submit(task_id=task_id, commits_count=0, turns_used=0)
    elif to_state == "done":
        sdk.tasks.accept(task_id=task_id, accepted_by="flow-engine")
    else:
        sdk.tasks.update(task_id, queue=to_state)
    debug_log(f"Task {task_id}: engine performed transition to {to_state}")


def _get_fail_target_from_flow(task: dict, current_queue: str) -> str:
    """Consult the flow definition for the target queue when a task fails.

    Loads the task's flow and checks for on_fail targets on conditions in the
    transition from current_queue. Returns the first on_fail state found, or
    "failed" if the flow defines no failure path for this transition.

    Falls back to "failed" gracefully if the flow cannot be loaded (e.g. in
    test environments or when the flow file is missing).

    Args:
        task: Task dict containing at least a "flow" key (defaults to "default")
        current_queue: The queue the task is currently in

    Returns:
        Target queue name for failed outcomes (e.g. "failed", "incoming")
    """
    from .flow import load_flow  # noqa: PLC0415

    try:
        flow_name = task.get("flow", "default")
        flow = load_flow(flow_name)
        transitions = flow.get_transitions_from(current_queue)
        if transitions:
            for condition in transitions[0].conditions:
                if condition.on_fail:
                    return condition.on_fail
    except Exception:
        pass  # Fall through to hardcoded default

    return "failed"


def _get_continuation_target_from_flow(task: dict, current_queue: str) -> str:
    """Consult the flow definition for the target queue when a task needs continuation.

    Currently the flow YAML has no dedicated continuation concept, so this
    always returns "needs_continuation". The function exists so the scheduler
    consults the flow for continuation routing — when flows gain a continuation
    path, this function will pick it up automatically.

    Falls back to "needs_continuation" gracefully if the flow cannot be loaded.

    Args:
        task: Task dict containing at least a "flow" key (defaults to "default")
        current_queue: The queue the task is currently in

    Returns:
        Target queue name for continuation outcomes (e.g. "needs_continuation")
    """
    # No continuation concept in flows yet — always use the standard queue.
    # When flows gain on_continuation support, look it up here.
    return "needs_continuation"


# ---------------------------------------------------------------------------
# Step failure circuit-breaker helpers
# ---------------------------------------------------------------------------

def _increment_step_failure_count(task_dir: Path) -> int:
    """Increment and return the consecutive step-failure count for a task."""
    counter_file = task_dir / "step_failure_count"
    count = 0
    if counter_file.exists():
        try:
            count = int(counter_file.read_text().strip())
        except (ValueError, OSError):
            count = 0
    count += 1
    try:
        counter_file.write_text(str(count))
    except OSError:
        pass
    return count


def _reset_step_failure_count(task_dir: Path) -> None:
    """Reset the step-failure counter for a task."""
    counter_file = task_dir / "step_failure_count"
    try:
        counter_file.unlink(missing_ok=True)
    except OSError:
        pass


# Default number of requeue cycles before the circuit breaker trips
_DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 3


def _get_circuit_breaker_threshold() -> int:
    """Return the configured requeue threshold before circuit breaker trips.

    Reads ``agents.circuit_breaker_threshold`` from .octopoid/config.yaml.
    Defaults to 3 if not set.
    """
    try:
        from .config import _load_project_config  # noqa: PLC0415
        config = _load_project_config()
        return int(config.get("agents", {}).get("circuit_breaker_threshold", _DEFAULT_CIRCUIT_BREAKER_THRESHOLD))
    except Exception:
        return _DEFAULT_CIRCUIT_BREAKER_THRESHOLD


# ---------------------------------------------------------------------------
# Outcome handlers
# ---------------------------------------------------------------------------

def _handle_done_outcome(sdk: object, task_id: str, task: dict, result: dict, task_dir: Path) -> bool:
    """Execute flow steps for a successfully-completed task, then perform the transition.

    The engine owns the transition:
    1. Look up the flow transition from "claimed"
    2. Run pre-transition steps (push_branch, run_tests, create_pr, etc.)
    3. Engine calls the right API method based on the transition's to_state

    Returns:
        True if the task was transitioned (PID safe to remove).
        False if the task was not transitioned and the PID should be kept for retry.
    """
    from .flow import load_flow  # noqa: PLC0415
    from .steps import execute_steps  # noqa: PLC0415

    current_queue = task.get("queue", "unknown")
    if current_queue != "claimed":
        # Task is not in "claimed" — do not transition. Return False so the
        # caller keeps the PID and retries next tick. This prevents orphaning
        # a task that is still claimed but was observed in a transient state.
        debug_log(f"Task {task_id}: outcome=done but queue={current_queue}, skipping")
        return False

    flow_name = task.get("flow", "default")
    flow = load_flow(flow_name)
    # Use child_flow transitions if this is a child task in a project
    if task.get("project_id") and flow.child_flow:
        transitions = flow.child_flow.get_transitions_from("claimed")
    else:
        transitions = flow.get_transitions_from("claimed")

    if not transitions:
        # Fallback: direct submit if no transition defined in flow
        sdk.tasks.submit(task_id=task_id, commits_count=0, turns_used=0)
        debug_log(f"Task {task_id}: no flow transition from claimed, used direct submit")
        return True

    transition = transitions[0]

    # Execute pre-transition steps (side effects before state change)
    if transition.runs:
        debug_log(f"Task {task_id}: executing flow steps {transition.runs}")
        execute_steps(transition.runs, task, result, task_dir)

    # Engine performs the transition — the step list no longer needs a "move" step
    _perform_transition(sdk, task_id, transition.to_state)
    print(f"[{datetime.now().isoformat()}] Task {task_id} transitioned to {transition.to_state} via flow")
    return True


def _handle_fail_outcome(sdk: object, task_id: str, task: dict, reason: str, current_queue: str) -> bool:
    """Move a failed task to the appropriate queue, consulting the flow for the target.

    Loads the task's flow to find any on_fail target defined on conditions for
    the current transition. Falls back to "failed" if the flow defines no
    failure path.

    Returns:
        True if the task was transitioned (PID safe to remove).
        False if the task was not transitioned and the PID should be kept for retry.
    """
    if current_queue == "claimed":
        fail_target = _get_fail_target_from_flow(task, current_queue)
        sdk.tasks.update(task_id, queue=fail_target)
        debug_log(f"Task {task_id}: failed (claimed → {fail_target}): {reason}")
        return True
    else:
        debug_log(f"Task {task_id}: outcome=failed but queue={current_queue}, skipping")
        return False


def _handle_continuation_outcome(sdk: object, task_id: str, task: dict, agent_name: str, current_queue: str) -> bool:
    """Move a task to the continuation queue, consulting the flow for the target.

    Loads the task's flow to find any continuation routing defined there.
    Currently flows have no dedicated continuation concept, so this always
    falls back to "needs_continuation". When flows gain on_continuation
    support, _get_continuation_target_from_flow will return that target.

    Returns:
        True if the task was transitioned (PID safe to remove).
        False if the task was not transitioned and the PID should be kept for retry.
    """
    if current_queue == "claimed":
        continuation_target = _get_continuation_target_from_flow(task, current_queue)
        sdk.tasks.update(task_id, queue=continuation_target)
        debug_log(f"Task {task_id}: needs continuation by {agent_name} (→ {continuation_target})")
        return True
    else:
        debug_log(f"Task {task_id}: outcome=needs_continuation but queue={current_queue}, skipping")
        return False


# ---------------------------------------------------------------------------
# Main result-handling entry points
# ---------------------------------------------------------------------------

def handle_agent_result_via_flow(task_id: str, agent_name: str, task_dir: Path, expected_queue: str | None = None) -> bool:
    """Handle agent result using the task's flow definition.

    Replaces the hardcoded if/else dispatch for agent roles. Reads the flow,
    finds the current transition, and executes steps accordingly.

    The gatekeeper result format:
      {"status": "success", "decision": "approve"/"reject", "comment": "<markdown>"}
    or on failure:
      {"status": "failure", "message": "<reason>"}

    Returns:
        True if the task was transitioned or is gone (PID safe to remove).
        False if the task was not transitioned and the PID should be kept for retry.

    Args:
        task_id: Task identifier
        agent_name: Name of the agent
        task_dir: Path to the task directory containing result.json
        expected_queue: Queue the agent was working from (e.g. 'provisional').
            If set and the task has moved to a different queue, the result is
            discarded as stale to prevent running wrong transition steps.
    """
    from .flow import load_flow  # noqa: PLC0415
    from .steps import execute_steps, reject_with_feedback  # noqa: PLC0415

    result = read_result_json(task_dir)

    debug_log(f"handle_agent_result_via_flow: task={task_id} agent={agent_name} status={result.get('status')} decision={result.get('decision')}")

    try:
        sdk = queue_utils.get_sdk()

        # Fetch current task state
        task = sdk.tasks.get(task_id)
        if not task:
            debug_log(f"Flow dispatch: task {task_id} not found on server, skipping")
            return True  # Nothing to track — PID safe to remove

        current_queue = task.get("queue", "unknown")

        # When expected_queue is set, the agent claimed from that queue (e.g.
        # "provisional") and the server moved the task to "claimed".  Use the
        # pre-claim queue for transition lookup so we find the right flow
        # transition (e.g. "provisional -> done", not "claimed -> provisional").
        # Only discard as stale if the task moved to something other than the
        # expected queue or "claimed" (normal claiming behaviour).
        if expected_queue and current_queue not in (expected_queue, "claimed"):
            debug_log(
                f"Flow dispatch: task {task_id} moved from expected '{expected_queue}' "
                f"to '{current_queue}', discarding stale result from {agent_name}"
            )
            return True  # Task already moved on — PID safe to remove

        lookup_queue = expected_queue if expected_queue else current_queue
        flow_name = task.get("flow", "default")

        flow = load_flow(flow_name)
        # Use child_flow transitions if this is a child task in a project
        if task.get("project_id") and flow.child_flow:
            transitions = flow.child_flow.get_transitions_from(lookup_queue)
        else:
            transitions = flow.get_transitions_from(lookup_queue)

        if not transitions:
            debug_log(f"Flow dispatch: no transition from '{current_queue}' in flow '{flow_name}' for task {task_id}")
            return True  # No transition defined — nothing to retry, PID safe to remove

        transition = transitions[0]  # Take first matching transition

        status = result.get("status")
        decision = result.get("decision")

        if status == "failure":
            # Agent couldn't complete — find on_fail state from agent condition
            message = result.get("message", "Agent could not complete review")
            debug_log(f"Flow dispatch: agent failure for {task_id}: {message}")
            for condition in transition.conditions:
                if condition.type == "agent" and condition.on_fail:
                    debug_log(f"Flow dispatch: rejecting {task_id} back to {condition.on_fail}")
                    sdk.tasks.reject(task_id, reason=message, rejected_by=agent_name)
                    return True  # Task transitioned — PID safe to remove
            # Default: reject back to incoming
            sdk.tasks.reject(task_id, reason=message, rejected_by=agent_name)
            return True  # Task transitioned — PID safe to remove

        # Agent-specific decision handling (approve/reject for gatekeeper)
        if decision == "reject":
            debug_log(f"Flow dispatch: agent rejected task {task_id}")
            reject_with_feedback(task, result, task_dir)
            print(f"[{datetime.now().isoformat()}] Agent {agent_name} rejected task {task_id}")
            return True  # Task transitioned — PID safe to remove

        if decision != "approve":
            debug_log(f"Flow dispatch: unknown decision '{decision}' for {task_id}, leaving in {current_queue} for human review")
            return True  # Cannot act — human review needed, retrying won't help

        # Execute the transition's runs (approve path — only reached on explicit "approve")
        if transition.runs:
            debug_log(f"Flow dispatch: executing steps {transition.runs} for task {task_id}")
            execute_steps(transition.runs, task, result, task_dir)
            print(f"[{datetime.now().isoformat()}] Agent {agent_name} completed task {task_id} (steps: {transition.runs})")
        else:
            # No runs defined — just log
            debug_log(f"Flow dispatch: no runs defined for transition from '{current_queue}', task {task_id}")

        return True  # Steps executed (or no steps needed) — PID safe to remove

    except Exception as e:
        import traceback  # noqa: PLC0415
        print(f"[{datetime.now().isoformat()}] ERROR: handle_agent_result_via_flow failed for {task_id}: {e}")
        debug_log(f"Error in handle_agent_result_via_flow for {task_id}: {e}")
        debug_log(traceback.format_exc())
        try:
            sdk = queue_utils.get_sdk()
            # Intentionally hardcoded: this is the emergency fallback that fires when
            # the flow system itself crashes (load_flow, execute_steps, etc.).  We
            # cannot consult the flow to find the target because the flow machinery is
            # what just failed.  "failed" is the only safe terminal state here.
            sdk.tasks.update(task_id, queue='failed', execution_notes=f'Flow dispatch error: {e}')
        except Exception as inner_e:
            print(f"[{datetime.now().isoformat()}] ERROR: move-to-failed failed for {task_id}: {inner_e}")
            debug_log(f"Failed to move {task_id} to failed queue")
        return True  # Task moved to terminal state (or already gone) — PID safe to remove


def handle_agent_result(task_id: str, agent_name: str, task_dir: Path) -> bool:
    """Handle the result of a script-based agent run.

    Reads result.json and transitions the task using flow steps:
    1. Read result.json to determine outcome
    2. Fetch current task state from server
    3. For "done" outcomes in "claimed" queue: execute the flow's steps, then the engine
       performs the transition to the target queue (submit, accept, or update)
    4. For "failed"/"error": move to failed queue
    5. For "needs_continuation": move to needs_continuation queue

    Raises on step failure so the caller (check_and_update_finished_agents) knows
    NOT to delete the PID — the next tick will retry. After 3 consecutive failures
    for the same task, the task is moved to failed and the function returns True
    (so the PID is removed).

    Returns:
        True if the task was transitioned or is gone (PID safe to remove).
        False if the task was not transitioned and the PID should be kept for retry.
        Raises on transient step failure (caller keeps PID for retry).

    Args:
        task_id: Task identifier
        agent_name: Name of the agent
        task_dir: Path to the task directory
    """
    result = _read_or_infer_result(task_dir)
    outcome = result.get("outcome", "error")
    debug_log(f"Task {task_id} result: {outcome}")

    sdk = queue_utils.get_sdk()

    task = sdk.tasks.get(task_id)
    if not task:
        debug_log(f"Task {task_id}: not found on server, skipping result handling")
        return True  # Nothing to track — PID safe to remove

    current_queue = task.get("queue", "unknown")
    debug_log(f"Task {task_id}: current queue = {current_queue}, outcome = {outcome}")

    try:
        if outcome in ("done", "submitted"):
            return _handle_done_outcome(sdk, task_id, task, result, task_dir)
        elif outcome in ("failed", "error"):
            return _handle_fail_outcome(sdk, task_id, task, result.get("reason", "Agent reported failure"), current_queue)
        elif outcome == "needs_continuation":
            return _handle_continuation_outcome(sdk, task_id, task, agent_name, current_queue)
        else:
            return _handle_fail_outcome(sdk, task_id, task, f"Unknown outcome: {outcome}", current_queue)
    except Exception as e:
        import traceback  # noqa: PLC0415
        failure_count = _increment_step_failure_count(task_dir)
        print(
            f"[{datetime.now().isoformat()}] ERROR: step failure for task {task_id} "
            f"(attempt {failure_count}/3): {e}"
        )
        debug_log(f"handle_agent_result: step failure #{failure_count} for {task_id}:\n{traceback.format_exc()}")

        if failure_count >= 3:
            # Too many consecutive failures — give up and move to failed
            print(
                f"[{datetime.now().isoformat()}] Task {task_id}: {failure_count} consecutive "
                f"step failures, moving to failed"
            )
            try:
                sdk.tasks.update(
                    task_id,
                    queue="failed",
                    execution_notes=f"Step failure after {failure_count} attempts: {e}",
                )
            except Exception as update_err:
                print(f"[{datetime.now().isoformat()}] ERROR: move-to-failed failed for {task_id}: {update_err}")
                debug_log(f"handle_agent_result: failed to update {task_id} to failed: {update_err}")
            _reset_step_failure_count(task_dir)
            return True  # Task moved to terminal state — PID safe to remove

        raise  # Re-raise so caller leaves PID in tracking for retry
