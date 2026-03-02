"""Result-handling and flow-transition subsystem for the orchestrator scheduler.

This module contains the functions responsible for processing agent results and
driving task state-machine transitions. It was extracted from scheduler.py to
give this well-scoped concern its own home.

Public API (also re-exported from octopoid.scheduler for backwards compat):
    infer_result_from_stdout     – Infer agent outcome from stdout.log using haiku
    handle_agent_result_via_flow – Handle gatekeeper/review agent results via flow
    handle_agent_result          – Handle implementer agent results via outcome dispatch
"""

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path

from . import queue_utils
from .tasks import fail_task, request_intervention

logger = logging.getLogger("octopoid.result_handler")


# ---------------------------------------------------------------------------
# Stdout inference helpers
# ---------------------------------------------------------------------------

_IMPLEMENTER_PROMPT = """\
You are classifying the outcome of an AI software implementer agent. The agent \
wrote code, made git commits, ran tests, and submitted a PR.

The agent output below is the last 2000 characters of the agent's session. \
Classify it as one of:
- "done": The agent successfully completed their implementation. Look for: \
"All tasks complete", "implementation done", "task is complete", \
"outcome: done", work summary, tests passing.
- "failed": The agent could not complete the task. Look for: explicit failure, \
unresolved errors, "cannot complete", very short output with only an error.
- "needs_continuation": The agent ran out of turns before completing the task. \
Look for: "ran out of turns", "needs continuation", "outcome: needs_continuation", \
"hit the turn limit", "more turns needed", output that cuts off mid-task without \
a clear success or failure statement.

Note: Agents are verbose. They describe obstacles they overcame. This does NOT \
mean they failed. Short output that says "Done." is sufficient for "done". \
"outcome: failed" or "could not complete the task" means failed, not done.

AGENT OUTPUT:
{tail}

Respond with exactly one word: done, failed, or needs_continuation"""

_GATEKEEPER_PROMPT = """\
You are classifying the decision of an AI code review agent (gatekeeper). \
The agent reviewed a pull request and decided whether to approve or reject it.

The agent output below is the last 2000 characters of the agent's session. \
Classify it as:
- "approve": The agent approved the PR. Look for: "APPROVED", "Approved", \
"approve", decision to accept, tests passing, all criteria met.
- "reject": The agent rejected the PR. Look for: "REJECTED", "Rejected", \
"reject", decision to reject, failing tests, criteria not met.

AGENT OUTPUT:
{tail}

Respond with exactly one word: approve or reject"""

_FIXER_PROMPT = """\
You are classifying the outcome of an AI fixer agent. The agent was given a \
task that entered the requires-intervention queue and asked to diagnose and \
fix the issue.

The agent output below is the last 2000 characters of the agent's session. \
Classify it as:
- "fixed": The agent successfully diagnosed and applied a fix.
- "failed": The agent could not fix the issue or needs human intervention.

AGENT OUTPUT:
{tail}

Respond with exactly one word: fixed or failed"""


def _call_haiku(prompt: str) -> str:
    """Call haiku with the given prompt and return the text response.

    Uses ``claude -p`` subprocess to match how agents are spawned, so it
    works with OAuth credentials stored in ~/.claude/ rather than requiring
    ANTHROPIC_API_KEY (which is not available in the launchd scheduler env).
    """
    result = subprocess.run(
        ["claude", "-p", prompt, "--model", "claude-haiku-4-5-20251001"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude -p exited with {result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip().lower()


def _infer_implementer(tail: str) -> dict:
    """Infer implementer outcome from stdout tail using haiku."""
    try:
        word = _call_haiku(_IMPLEMENTER_PROMPT.format(tail=tail))
        if "needs_continuation" in word or word == "continuation":
            return {"outcome": "needs_continuation"}
        elif "done" in word:
            return {"outcome": "done"}
        elif "fail" in word:
            return {"outcome": "failed", "reason": "Inferred from stdout: agent did not complete task"}
        else:
            logger.warning(f"Haiku returned unexpected word for implementer: {word!r}")
            return {"outcome": "unknown", "reason": f"Haiku returned unexpected word: {word!r}"}
    except Exception as e:
        logger.warning(f"Haiku inference failed for implementer: {e}")
        return {"outcome": "unknown", "reason": f"Inference error: {e}"}


def _infer_gatekeeper(tail: str) -> dict:
    """Infer gatekeeper decision from stdout tail using haiku."""
    try:
        word = _call_haiku(_GATEKEEPER_PROMPT.format(tail=tail))
        if "approve" in word:
            return {"status": "success", "decision": "approve", "comment": tail}
        elif "reject" in word:
            return {"status": "success", "decision": "reject", "comment": tail}
        else:
            logger.warning(f"Haiku returned unexpected word for gatekeeper: {word!r}")
            return {"status": "failure", "message": f"Could not infer gatekeeper decision (haiku: {word!r})"}
    except Exception as e:
        logger.warning(f"Haiku inference failed for gatekeeper: {e}")
        return {"status": "failure", "message": f"Inference error: {e}"}


def _infer_fixer(tail: str) -> dict:
    """Infer fixer outcome from stdout tail using haiku."""
    try:
        word = _call_haiku(_FIXER_PROMPT.format(tail=tail))
        if "fix" in word:
            return {"outcome": "fixed", "diagnosis": "Inferred from stdout", "fix_applied": tail[:500]}
        elif "fail" in word:
            return {"outcome": "failed", "diagnosis": "Inferred from stdout: could not fix"}
        else:
            logger.warning(f"Haiku returned unexpected word for fixer: {word!r}")
            return {"outcome": "unknown", "reason": f"Haiku returned unexpected word: {word!r}"}
    except Exception as e:
        logger.warning(f"Haiku inference failed for fixer: {e}")
        return {"outcome": "unknown", "reason": f"Inference error: {e}"}


def infer_result_from_stdout(stdout_path: Path, agent_role: str) -> dict:
    """Infer agent outcome from stdout.log using a role-specific haiku call.

    Reads the last 2000 characters of stdout.log and uses a role-specific
    prompt to classify the agent's outcome. This replaces result.json as the
    mechanism for agents to communicate their outcome to the scheduler.

    Args:
        stdout_path: Path to the stdout.log file
        agent_role: Role of the agent ("implement", "gatekeeper",
                    "sanity-check-gatekeeper", or "fixer")

    Returns:
        Result dict in the format expected by the relevant handler:
        - Implementer: {"outcome": "done"} or {"outcome": "failed", ...}
        - Gatekeeper: {"status": "success", "decision": "approve"/"reject", ...}
                   or {"status": "failure", "message": ...}
        - Fixer: {"outcome": "fixed", ...} or {"outcome": "failed", ...}
        - Unknown: {"outcome": "unknown"} or gatekeeper-style failure
    """
    if not stdout_path.exists():
        logger.warning(f"stdout.log not found at {stdout_path}")
        if agent_role in ("gatekeeper", "sanity-check-gatekeeper"):
            return {"status": "failure", "message": "No stdout.log produced"}
        return {"outcome": "unknown", "reason": "No stdout.log produced"}

    try:
        stdout = stdout_path.read_text(errors="replace")
    except OSError as e:
        logger.warning(f"Could not read stdout.log at {stdout_path}: {e}")
        if agent_role in ("gatekeeper", "sanity-check-gatekeeper"):
            return {"status": "failure", "message": f"Could not read stdout.log: {e}"}
        return {"outcome": "unknown", "reason": f"Could not read stdout.log: {e}"}

    if not stdout.strip():
        logger.warning(f"stdout.log is empty at {stdout_path}")
        if agent_role in ("gatekeeper", "sanity-check-gatekeeper"):
            return {"status": "failure", "message": "Empty stdout — agent may have crashed"}
        return {"outcome": "unknown", "reason": "Empty stdout — agent may have crashed"}

    tail = stdout[-2000:]

    if agent_role in ("gatekeeper", "sanity-check-gatekeeper"):
        result = _infer_gatekeeper(tail)
    elif agent_role == "fixer":
        result = _infer_fixer(tail)
    else:
        # implementer and any other role
        result = _infer_implementer(tail)

    logger.debug(f"infer_result_from_stdout: role={agent_role} result={result}")
    return result


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
    logger.debug(f"Task {task_id}: engine performed transition to {to_state}")


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

# Maximum number of continuation cycles before escalating to intervention
_MAX_CONTINUATION_CYCLES = 3


def _get_continuation_count(task_dir: Path) -> int:
    """Return the number of continuation cycles completed for a task."""
    counter_file = task_dir / "continuation_count"
    if counter_file.exists():
        try:
            return int(counter_file.read_text().strip())
        except (ValueError, OSError):
            pass
    return 0


def _increment_continuation_count(task_dir: Path) -> int:
    """Increment and return the continuation cycle count for a task."""
    count = _get_continuation_count(task_dir) + 1
    try:
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "continuation_count").write_text(str(count))
    except OSError:
        pass
    return count


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
# Agent result message posting
# ---------------------------------------------------------------------------

def _post_agent_result_message(
    sdk: object,
    task_id: str,
    agent_name: str,
    agent_role: str,
    result: dict,
) -> None:
    """Post inferred agent result as an agent_result message on the task thread.

    Creates a durable audit trail of what haiku classified for each agent run.
    Errors are logged as warnings but never propagate — message posting is
    best-effort and must not interrupt the result handling flow.

    Args:
        sdk: SDK client
        task_id: Task identifier
        agent_name: Name of the agent instance (e.g. "implementer-1")
        agent_role: Role string used for inference ("implement", "gatekeeper", "fixer")
        result: Classification dict returned by infer_result_from_stdout()
    """
    try:
        content_lines = [
            f"**Agent:** {agent_name}",
            f"**Role:** {agent_role}",
        ]
        if "outcome" in result:
            content_lines.append(f"**Outcome:** {result['outcome']}")
        if "decision" in result:
            content_lines.append(f"**Decision:** {result['decision']}")
        if "status" in result:
            content_lines.append(f"**Status:** {result['status']}")
        if "reason" in result:
            content_lines.append(f"**Reason:** {result['reason']}")
        if "diagnosis" in result:
            content_lines.append(f"**Diagnosis:** {result['diagnosis']}")
        if "message" in result:
            content_lines.append(f"**Message:** {result['message']}")

        # Include JSON summary (exclude 'comment' which is a 2000-char stdout tail)
        summary = {k: v for k, v in result.items() if k != "comment"}
        content_lines.extend(["", "```json", json.dumps(summary, indent=2), "```"])

        sdk.messages.create(
            task_id=task_id,
            from_actor="agent",
            to_actor="human",
            type="agent_result",
            content="\n".join(content_lines),
        )
        logger.debug(f"Posted agent_result message for task {task_id} agent {agent_name}")
    except Exception as e:
        logger.warning(f"Failed to post agent_result message for {task_id}: {e}")


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
        # Task is not in "claimed" — decide based on whether the queue is terminal.
        _TERMINAL_QUEUES = {"done", "failed"}
        if current_queue in _TERMINAL_QUEUES:
            # Task already reached a terminal state (e.g. lease expiry, manual
            # intervention, or a 409 race). The process is dead and the task
            # will never return to "claimed". Return True so the PID is removed.
            logger.debug(f"Task {task_id}: outcome=done but queue={current_queue} (terminal), removing stale PID")
            return True
        # Non-terminal, non-claimed (e.g. "incoming", "provisional") — the task
        # may be mid-transition. Keep the PID and retry next tick.
        logger.debug(f"Task {task_id}: outcome=done but queue={current_queue}, skipping")
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
        logger.debug(f"Task {task_id}: no flow transition from claimed, used direct submit")
        return True

    transition = transitions[0]

    # Execute pre-transition steps (side effects before state change)
    if transition.runs:
        logger.debug(f"Task {task_id}: executing flow steps {transition.runs}")
        execute_steps(transition.runs, task, result, task_dir)

    # Engine performs the transition — the step list no longer needs a "move" step
    _perform_transition(sdk, task_id, transition.to_state)
    logger.info(f"Task {task_id} transitioned to {transition.to_state} via flow")
    return True


def _handle_fail_outcome(sdk: object, task_id: str, task: dict, reason: str, current_queue: str) -> bool:
    """Move a failed task through intervention before it can reach the failed queue.

    Routes all agent failure outcomes (failed, error) through fail_task(), which
    routes to requires-intervention on first failure and only to failed if the
    fixer also fails. This satisfies the self-correcting-failure invariant.

    Returns:
        True if the task was transitioned (PID safe to remove).
        False if the task was not transitioned and the PID should be kept for retry.
    """
    if current_queue == "claimed":
        from .tasks import fail_task  # noqa: PLC0415
        fail_task(task_id, reason=reason, source="agent-outcome-failed")
        logger.debug(f"Task {task_id}: failed (claimed → intervention): {reason}")
        return True
    else:
        _TERMINAL_QUEUES = {"done", "failed"}
        if current_queue in _TERMINAL_QUEUES:
            # Task already in a terminal state — process is dead, nothing to retry.
            # Return True so the stale PID is removed from the pool.
            logger.debug(f"Task {task_id}: outcome=failed but queue={current_queue} (terminal), removing stale PID")
            return True
        # Non-terminal, non-claimed (e.g. "incoming", "provisional") — keep PID for retry.
        logger.debug(f"Task {task_id}: outcome=failed but queue={current_queue}, skipping")
        return False


def _handle_continuation_outcome(sdk: object, task_id: str, task: dict, agent_name: str, current_queue: str) -> bool:
    """Move a task to the continuation queue, consulting the flow for the target.

    Loads the task's flow to find any continuation routing defined there.
    Currently flows have no dedicated continuation concept, so this always
    falls back to "needs_continuation". When flows gain on_continuation
    support, _get_continuation_target_from_flow will return that target.

    Enforces a maximum of _MAX_CONTINUATION_CYCLES cycles. If the limit is
    reached, the task is escalated to intervention instead of re-queued.

    Returns:
        True if the task was transitioned (PID safe to remove).
        False if the task was not transitioned and the PID should be kept for retry.
    """
    from .config import get_tasks_dir  # noqa: PLC0415

    if current_queue == "claimed":
        task_dir = get_tasks_dir() / task_id
        continuation_count = _increment_continuation_count(task_dir)

        if continuation_count >= _MAX_CONTINUATION_CYCLES:
            # Too many continuation cycles — escalate to human intervention.
            reason = (
                f"Task exceeded maximum continuation cycles "
                f"({continuation_count}/{_MAX_CONTINUATION_CYCLES}) — "
                f"may need to be scoped smaller or broken into subtasks"
            )
            logger.warning(f"Task {task_id}: {reason}")
            request_intervention(
                task_id,
                reason=reason,
                source="continuation-cycle-limit",
                previous_queue=current_queue,
            )
            return True

        continuation_target = _get_continuation_target_from_flow(task, current_queue)
        sdk.tasks.update(task_id, queue=continuation_target)
        logger.debug(
            f"Task {task_id}: needs continuation by {agent_name} "
            f"(cycle {continuation_count}/{_MAX_CONTINUATION_CYCLES} → {continuation_target})"
        )
        return True
    else:
        _TERMINAL_QUEUES = {"done", "failed"}
        if current_queue in _TERMINAL_QUEUES:
            # Task already in a terminal state — process is dead, nothing to retry.
            # Return True so the stale PID is removed from the pool.
            logger.debug(f"Task {task_id}: outcome=needs_continuation but queue={current_queue} (terminal), removing stale PID")
            return True
        # Non-terminal, non-claimed (e.g. "incoming", "provisional") — keep PID for retry.
        logger.debug(f"Task {task_id}: outcome=needs_continuation but queue={current_queue}, skipping")
        return False


# ---------------------------------------------------------------------------
# Main result-handling entry points
# ---------------------------------------------------------------------------

def _resolve_task_and_transition(
    sdk: object,
    task_id: str,
    agent_name: str,
    expected_queue: str | None,
) -> tuple[dict, object, str] | tuple[None, None, None]:
    """Fetch task, check staleness, load flow, and resolve the first transition.

    Returns ``(task, transition, lookup_queue)`` when a transition was found
    and processing should continue.  Returns ``(None, None, None)`` when the
    caller should immediately return ``True`` (task not found, stale result,
    or no transition defined for the current queue).

    Args:
        sdk: SDK client
        task_id: Task identifier
        agent_name: Name of the calling agent (used in staleness log message)
        expected_queue: Queue the agent was working from, if known
    """
    from .flow import load_flow  # noqa: PLC0415

    task = sdk.tasks.get(task_id)
    if not task:
        logger.debug(f"Flow dispatch: task {task_id} not found on server, skipping")
        return None, None, None

    current_queue = task.get("queue", "unknown")

    # When expected_queue is set, the agent claimed from that queue (e.g.
    # "provisional") and the server moved the task to "claimed".  Use the
    # pre-claim queue for transition lookup so we find the right flow
    # transition (e.g. "provisional -> done", not "claimed -> provisional").
    # Only discard as stale if the task moved to something other than the
    # expected queue or "claimed" (normal claiming behaviour).
    if expected_queue and current_queue not in (expected_queue, "claimed"):
        logger.debug(
            f"Flow dispatch: task {task_id} moved from expected '{expected_queue}' "
            f"to '{current_queue}', discarding stale result from {agent_name}"
        )
        return None, None, None

    lookup_queue = expected_queue if expected_queue else current_queue
    flow_name = task.get("flow", "default")
    flow = load_flow(flow_name)

    # Use child_flow transitions if this is a child task in a project
    if task.get("project_id") and flow.child_flow:
        transitions = flow.child_flow.get_transitions_from(lookup_queue)
    else:
        transitions = flow.get_transitions_from(lookup_queue)

    if not transitions:
        logger.debug(f"Flow dispatch: no transition from '{current_queue}' in flow '{flow_name}' for task {task_id}")
        return None, None, None

    return task, transitions[0], lookup_queue


def _handle_agent_failure(
    sdk: object,
    task_id: str,
    agent_name: str,
    transition: object,
    result: dict,
) -> bool:
    """Handle a result with status=failure.

    Finds the on_fail target from the transition's agent condition and rejects
    the task back to that queue (defaulting to 'incoming').

    Returns:
        True — task was transitioned, PID safe to remove.
    """
    message = result.get("message", "Agent could not complete review")
    logger.debug(f"Flow dispatch: agent failure for {task_id}: {message}")
    for condition in transition.conditions:
        if condition.type == "agent" and condition.on_fail:
            logger.debug(f"Flow dispatch: rejecting {task_id} back to {condition.on_fail}")
            sdk.tasks.reject(task_id, reason=message, rejected_by=agent_name)
            return True
    # Default: reject back to incoming
    sdk.tasks.reject(task_id, reason=message, rejected_by=agent_name)
    return True


def _handle_gatekeeper_reject(
    task: dict,
    result: dict,
    task_dir: Path,
    task_id: str,
    agent_name: str,
) -> bool:
    """Handle a gatekeeper result with decision=reject.

    Posts rejection feedback and transitions the task back.

    Returns:
        True — task was transitioned, PID safe to remove.
    """
    from .steps import reject_with_feedback  # noqa: PLC0415

    logger.debug(f"Flow dispatch: agent rejected task {task_id}")
    reject_with_feedback(task, result, task_dir)
    logger.info(f"Agent {agent_name} rejected task {task_id}")
    return True


def _handle_approve_and_run_steps(
    sdk: object,
    task_id: str,
    agent_name: str,
    task: dict,
    transition: object,
    result: dict,
    task_dir: Path,
    current_queue: str,
) -> bool:
    """Execute the transition steps for an approved result.

    Runs the steps defined in ``transition.runs``.  Rebase and merge failures
    are recoverable — the task is rejected back to incoming so the implementer
    can re-implement on a fresh base.  All other RuntimeErrors are re-raised to
    the caller's catch-all handler.

    Returns:
        True — steps executed (or no steps needed), PID safe to remove.
    Raises:
        RuntimeError: For non-rebase/merge step failures (propagates to caller).
        RetryableStepError: Propagated from execute_steps for CI polling.
    """
    from .steps import execute_steps  # noqa: PLC0415

    if not transition.runs:
        # No runs defined — just log
        logger.debug(f"Flow dispatch: no runs defined for transition from '{current_queue}', task {task_id}")
        return True

    logger.debug(f"Flow dispatch: executing steps {transition.runs} for task {task_id}")
    try:
        execute_steps(transition.runs, task, result, task_dir)
    except RuntimeError as step_err:
        err_msg = str(step_err)
        # Rebase and merge failures are recoverable: reject back to incoming
        # so the implementer can re-implement on a fresh base.
        is_merge_fail = any(
            kw in err_msg for kw in ("rebase_on_base", "merge_pr", "git rebase failed")
        )
        if not is_merge_fail:
            raise  # Non-recoverable — let the outer except Exception handle it

        logger.warning(f"Rebase/merge failed for {task_id}: {step_err}")

        try:
            from .task_thread import post_message  # noqa: PLC0415
            post_message(
                task_id,
                role="rejection",
                content=(
                    f"## Rebase/merge failed — resolve conflicts in existing worktree\n\n"
                    f"{err_msg}\n\n"
                    f"The task will be requeued to incoming. Your previous work "
                    f"is preserved in the existing worktree. Rebase onto the "
                    f"latest base branch, resolve any conflicts, and continue — "
                    f"do NOT re-implement from scratch."
                ),
                author="scheduler-merge",
            )
        except Exception as post_e:
            logger.warning(f"Failed to post rejection message for {task_id}: {post_e}")

        # Find on_fail target from transition conditions (default: incoming)
        on_fail = "incoming"
        for condition in transition.conditions:
            if hasattr(condition, "on_fail") and condition.on_fail:
                on_fail = condition.on_fail
                break

        sdk.tasks.reject(task_id, reason=err_msg, rejected_by="scheduler-merge")
        logger.info(f"Task {task_id} rejected back to {on_fail} after rebase/merge failure")
        return True

    _perform_transition(sdk, task_id, transition.to_state)
    logger.info(f"Agent {agent_name} completed task {task_id} (steps: {transition.runs})")
    return True


def _dispatch_result(
    sdk: object,
    task_id: str,
    agent_name: str,
    task: dict,
    transition: object,
    result: dict,
    task_dir: Path,
    current_queue: str,
) -> bool:
    """Route the agent result to the appropriate handler.

    Dispatches based on result status and decision:
    - status=failure  → _handle_agent_failure
    - decision=reject → _handle_gatekeeper_reject
    - decision=approve → _handle_approve_and_run_steps
    - anything else   → log warning and return True (human review needed)

    Returns:
        True if the task was transitioned or no action was needed.
        False if the task was not transitioned and the PID should be kept for retry.
    """
    status = result.get("status")
    decision = result.get("decision")

    if status == "failure":
        return _handle_agent_failure(sdk, task_id, agent_name, transition, result)

    if decision == "reject":
        return _handle_gatekeeper_reject(task, result, task_dir, task_id, agent_name)

    if decision != "approve":
        # Unknown decision (e.g. haiku auth failure) — escalate to human review
        # rather than leaving the task stuck in its current queue.
        reason = result.get("message", f"Could not infer agent decision: {decision!r}")
        logger.warning(
            f"Flow dispatch: unknown decision '{decision}' for {task_id}, "
            f"routing to requires-intervention: {reason}"
        )
        request_intervention(task_id, reason=reason, source="unknown-decision", previous_queue=current_queue)
        return True  # Cannot act — moved to requires-intervention for human review

    return _handle_approve_and_run_steps(
        sdk, task_id, agent_name, task, transition, result, task_dir, current_queue
    )


def handle_agent_result_via_flow(task_id: str, agent_name: str, task_dir: Path, expected_queue: str | None = None) -> bool:
    """Handle agent result using the task's flow definition.

    Replaces the hardcoded if/else dispatch for agent roles. Reads the flow,
    finds the current transition, and executes steps accordingly.

    The gatekeeper result format (inferred from stdout):
      {"status": "success", "decision": "approve"/"reject", "comment": "<stdout tail>"}
    or on failure:
      {"status": "failure", "message": "<reason>"}

    Returns:
        True if the task was transitioned or is gone (PID safe to remove).
        False if the task was not transitioned and the PID should be kept for retry.

    Args:
        task_id: Task identifier
        agent_name: Name of the agent
        task_dir: Path to the task directory containing stdout.log
        expected_queue: Queue the agent was working from (e.g. 'provisional').
            If set and the task has moved to a different queue, the result is
            discarded as stale to prevent running wrong transition steps.
    """
    from .steps import RetryableStepError  # noqa: PLC0415

    result = infer_result_from_stdout(task_dir / "stdout.log", "gatekeeper")

    logger.debug(f"handle_agent_result_via_flow: task={task_id} agent={agent_name} status={result.get('status')} decision={result.get('decision')}")

    # Initialize before the try block so the except clause can reference it even
    # if the exception is raised before current_queue is assigned inside the try.
    current_queue = "unknown"

    try:
        sdk = queue_utils.get_sdk()
        _post_agent_result_message(sdk, task_id, agent_name, "gatekeeper", result)

        task, transition, _ = _resolve_task_and_transition(sdk, task_id, agent_name, expected_queue)
        if task is None:
            return True  # Task not found, stale, or no transition — PID safe to remove

        current_queue = task.get("queue", "unknown")
        return _dispatch_result(sdk, task_id, agent_name, task, transition, result, task_dir, current_queue)

    except RetryableStepError as e:
        logger.info(f"check_ci pending for {task_id}: {e}, leaving in {current_queue}")
        return False  # Don't remove PID — scheduler will retry on next tick

    except Exception as e:
        logger.error(f"handle_agent_result_via_flow failed for {task_id}: {e}", exc_info=True)
        try:
            # Before moving to failed, check if the task is already done.
            # Post-merge flow steps (e.g. update_changelog) can raise after the PR
            # is merged and the task accepted — we must not overwrite done with failed.
            _sdk = queue_utils.get_sdk()
            _current_task = _sdk.tasks.get(task_id)
            _current_q = (_current_task or {}).get("queue")
            if _current_q == "done":
                logger.warning(
                    f"Task {task_id}: catch-all exception after task reached done — "
                    f"not moving to failed (error: {e})"
                )
                return True  # Task is done — PID safe to remove
            # Intentionally hardcoded source: this is the emergency fallback that fires
            # when the flow system itself crashes (load_flow, execute_steps, etc.).  We
            # cannot consult the flow to find the target because the flow machinery is
            # what just failed.  "failed" is the only safe terminal state here.
            fail_task(task_id, reason=f'Flow dispatch error: {e}', source='flow-dispatch-error')
        except Exception as inner_e:
            logger.error(f"move-to-failed failed for {task_id}: {inner_e}")
        return True  # Task moved to terminal state (or already gone) — PID safe to remove


def handle_agent_result(task_id: str, agent_name: str, task_dir: Path) -> bool:
    """Handle the result of a script-based agent run.

    Infers the outcome from stdout.log and transitions the task using flow steps:
    1. Infer outcome from stdout.log using haiku
    2. Fetch current task state from server
    3. For "done" outcomes in "claimed" queue: execute the flow's steps, then the engine
       performs the transition to the target queue (submit, accept, or update)
    4. For "failed"/"error"/"unknown": move to failed queue (unknown routes to
       requires-intervention via fail_task if available)
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
    from .steps import RetryableStepError  # noqa: PLC0415

    result = infer_result_from_stdout(task_dir / "stdout.log", "implement")
    outcome = result.get("outcome", "error")
    logger.debug(f"Task {task_id} result: {outcome}")

    sdk = queue_utils.get_sdk()
    _post_agent_result_message(sdk, task_id, agent_name, "implement", result)

    task = sdk.tasks.get(task_id)
    if not task:
        logger.debug(f"Task {task_id}: not found on server, skipping result handling")
        return True  # Nothing to track — PID safe to remove

    current_queue = task.get("queue", "unknown")
    logger.debug(f"Task {task_id}: current queue = {current_queue}, outcome = {outcome}")

    try:
        if outcome in ("done", "submitted"):
            return _handle_done_outcome(sdk, task_id, task, result, task_dir)
        elif outcome in ("failed", "error"):
            return _handle_fail_outcome(sdk, task_id, task, result.get("reason", "Agent reported failure"), current_queue)
        elif outcome == "needs_continuation":
            return _handle_continuation_outcome(sdk, task_id, task, agent_name, current_queue)
        else:
            # Unknown outcome (e.g. haiku auth failure) — we cannot determine what
            # happened, so escalate to human review rather than failing outright.
            reason = result.get("reason", f"Unknown outcome: {outcome!r}")
            logger.warning(f"Task {task_id}: unknown outcome '{outcome}', routing to requires-intervention: {reason}")
            request_intervention(task_id, reason=reason, source="unknown-outcome", previous_queue=current_queue)
            return True
    except RetryableStepError as e:
        logger.info(f"Retryable step error for task {task_id}: {e}, will retry")
        return False  # Don't remove PID — scheduler will retry on next tick
    except Exception as e:
        failure_count = _increment_step_failure_count(task_dir)
        logger.error(
            f"Step failure for task {task_id} (attempt {failure_count}/3): {e}",
            exc_info=True,
        )

        if failure_count >= 3:
            # Too many consecutive failures — give up and move to failed
            logger.error(
                f"Task {task_id}: {failure_count} consecutive step failures, moving to failed"
            )
            try:
                fail_task(
                    task_id,
                    reason=f"Step failure after {failure_count} attempts: {e}",
                    source='step-failure-circuit-breaker',
                )
            except Exception as update_err:
                logger.error(f"move-to-failed failed for {task_id}: {update_err}")
            _reset_step_failure_count(task_dir)
            return True  # Task moved to terminal state — PID safe to remove

        raise  # Re-raise so caller leaves PID in tracking for retry


# ---------------------------------------------------------------------------
# Fixer agent result handler
# ---------------------------------------------------------------------------

def _load_intervention_context(task_id: str, task_dir: Path) -> tuple[dict, str | None]:
    """Load intervention context for a task.

    Tries the messages API first (intervention_request message to fixer),
    falling back to intervention_context.json in the task directory.

    Returns:
        Tuple of (context_dict, message_id_or_None). The message_id is set when
        context was loaded from a message (used to post the reply).
    """
    import re as _re

    # Try messages API first — primary intervention context delivery
    try:
        sdk = queue_utils.get_sdk()
        messages = sdk.messages.list(task_id=task_id, to_actor="fixer", type="intervention_request")
        if messages:
            msg = messages[-1]
            content = msg.get("content", "")
            match = _re.search(r"```json\s*(.*?)\s*```", content, _re.DOTALL)
            if match:
                ctx = json.loads(match.group(1))
                return ctx, msg.get("id")
    except Exception as e:
        logger.debug(f"_load_intervention_context: messages query failed for {task_id}: {e}")

    # Fallback: read from file
    ctx_path = task_dir / "intervention_context.json"
    if ctx_path.exists():
        try:
            return json.loads(ctx_path.read_text()), None
        except (json.JSONDecodeError, OSError):
            pass
    return {}, None


def _resume_flow(
    sdk: object,
    task_id: str,
    task: dict,
    previous_queue: str,
    steps_completed: list[str],
    step_that_failed: str,
    task_dir: Path,
    fixer_result: dict,
) -> None:
    """Resume the flow transition that was interrupted before the fixer intervened.

    Finds the transition from previous_queue in the task's flow, computes the
    remaining steps (starting from step_that_failed), executes them, and
    performs the final transition to the target state.

    Args:
        sdk: SDK client
        task_id: Task identifier
        task: Task dict (freshly fetched from server)
        previous_queue: Queue the task was in when the error occurred
        steps_completed: Steps that had already run before the failure
        step_that_failed: The step that raised the exception
        task_dir: Task directory (for step execution)
        fixer_result: The fixer's result dict (passed to step functions)
    """
    from .flow import load_flow  # noqa: PLC0415
    from .steps import execute_steps  # noqa: PLC0415

    flow_name = task.get("flow", "default")
    flow = load_flow(flow_name)
    transitions = flow.get_transitions_from(previous_queue)
    if not transitions:
        logger.debug(f"Fixer resume: no transitions from '{previous_queue}' in flow '{flow_name}' for {task_id}")
        return

    transition = transitions[0]

    # Determine remaining steps: from step_that_failed onwards (inclusive).
    # If step_that_failed isn't found in runs, run all steps to be safe.
    remaining_steps = transition.runs
    if step_that_failed and step_that_failed in transition.runs:
        idx = transition.runs.index(step_that_failed)
        remaining_steps = transition.runs[idx:]

    logger.debug(
        f"Fixer resume: task {task_id} running steps {remaining_steps} "
        f"(from '{previous_queue}' to '{transition.to_state}')"
    )

    if remaining_steps:
        execute_steps(remaining_steps, task, fixer_result, task_dir)

    _perform_transition(sdk, task_id, transition.to_state)
    print(
        f"[{datetime.now().isoformat()}] Task {task_id} resumed by fixer: "
        f"{previous_queue} -> {transition.to_state} (steps: {remaining_steps})"
    )


def handle_fixer_result(task_id: str, agent_name: str, task_dir: Path) -> bool:
    """Handle the result of a fixer agent run.

    Infers the fixer outcome from stdout.log and takes the appropriate action:
    - outcome=fixed  → clear needs_intervention, resume interrupted flow,
                        skip already-completed steps, post a reply message.
    - anything else  → move to TRUE failed (terminal), post a failure message.

    The fixer communicates through stdout — the scheduler infers the outcome.

    Returns:
        True if the task was transitioned (PID safe to remove).
        False if the task was not transitioned and the PID should be kept for retry.

    Args:
        task_id: Task identifier
        agent_name: Name of the fixer agent instance
        task_dir: Path to the task directory containing stdout.log
    """
    result = infer_result_from_stdout(task_dir / "stdout.log", "fixer")
    outcome = result.get("outcome", "error")
    logger.debug(f"Fixer result for task {task_id}: outcome={outcome}")

    # Load intervention context from messages (primary) or file (fallback)
    intervention_context, request_message_id = _load_intervention_context(task_id, task_dir)
    previous_queue = intervention_context.get("previous_queue", "incoming")
    steps_completed = intervention_context.get("steps_completed") or []
    step_that_failed = intervention_context.get("step_that_failed") or ""

    sdk = queue_utils.get_sdk()
    _post_agent_result_message(sdk, task_id, agent_name, "fixer", result)

    task = sdk.tasks.get(task_id)
    if not task:
        logger.debug(f"Fixer result: task {task_id} not found on server, removing stale PID")
        return True

    if outcome == "fixed":
        diagnosis = result.get("diagnosis", "")
        fix_applied = result.get("fix_applied", "")

        # Post reply message to the intervention request (threaded reply)
        try:
            content_parts = ["## Fixer resolved the issue"]
            if diagnosis:
                content_parts.append(f"\n**Diagnosis:** {diagnosis}")
            if fix_applied:
                content_parts.append(f"\n**Fix applied:** {fix_applied}")
            if step_that_failed:
                content_parts.append(f"\nResuming flow from step: `{step_that_failed}`")
            content_parts.append(f"\nTask will resume from `{previous_queue}`.")
            sdk.messages.create(
                task_id=task_id,
                from_actor="fixer",
                to_actor="scheduler",
                type="intervention_reply",
                content="\n".join(content_parts),
                parent_message_id=request_message_id,
            )
        except Exception as msg_e:
            print(f"[{datetime.now().isoformat()}] WARN: Failed to post fixer reply message for {task_id}: {msg_e}")

        # Resume the interrupted flow transition, then clear needs_intervention.
        # IMPORTANT: clear needs_intervention AFTER resume succeeds, not before.
        # If we clear it first and resume fails, fail_task sees needs_intervention=False
        # and treats it as a first failure — re-entering intervention and creating a loop.
        try:
            _resume_flow(
                sdk, task_id, task, previous_queue, steps_completed, step_that_failed,
                task_dir, result,
            )
            # Resume succeeded — now safe to clear the flag
            try:
                sdk.tasks.update(task_id, needs_intervention=False)
            except Exception as clear_e:
                print(f"[{datetime.now().isoformat()}] WARN: Failed to clear needs_intervention for {task_id}: {clear_e}")
        except Exception as resume_err:
            # Flow resume failed — needs_intervention is still True, so fail_task
            # will correctly treat this as a second failure and go to terminal failed.
            print(f"[{datetime.now().isoformat()}] ERROR: Flow resume failed for {task_id} after fix: {resume_err}")
            logger.debug(f"Fixer: flow resume error for {task_id}: {resume_err}")
            try:
                from .tasks import fail_task  # noqa: PLC0415
                fail_task(task_id, reason=str(resume_err)[:500], source="fixer-resume-error")
            except Exception as terminal_e:
                print(f"[{datetime.now().isoformat()}] ERROR: Failed to move {task_id} to failed: {terminal_e}")

        return True

    else:
        # Fixer could not fix the issue — true terminal failure
        reason = (
            result.get("reason")
            or result.get("diagnosis")
            or "Fixer could not resolve the issue"
        )
        reason_truncated = str(reason)[:500]

        # Post reply message
        try:
            content = "## Fixer could not resolve the issue. Moving to failed.\n\n"
            diagnosis = result.get("diagnosis", "")
            if diagnosis:
                content += f"**Diagnosis:** {diagnosis}\n"
            content += "\nThis task requires human attention."
            sdk.messages.create(
                task_id=task_id,
                from_actor="fixer",
                to_actor="scheduler",
                type="intervention_reply",
                content=content,
                parent_message_id=request_message_id,
            )
        except Exception as msg_e:
            print(f"[{datetime.now().isoformat()}] WARN: Failed to post fixer failure message for {task_id}: {msg_e}")

        # True terminal failure — fail_task() detects needs_intervention=True and
        # moves to failed (second failure path).
        try:
            from .tasks import fail_task  # noqa: PLC0415
            fail_task(task_id, reason=reason_truncated, source="fixer-failed", claimed_by=None)
        except Exception as terminal_e:
            print(f"[{datetime.now().isoformat()}] ERROR: Failed to move {task_id} to failed: {terminal_e}")

        print(
            f"[{datetime.now().isoformat()}] FAILED task={task_id} (fixer-failed): {reason_truncated[:200]}"
        )
        return True
