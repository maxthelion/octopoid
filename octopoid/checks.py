"""Async checks for flow transitions.

Checks are pure functions: (task: dict) -> CheckResult.
They are polled by the scheduler on each tick before allowing a transition's
condition (e.g. gatekeeper claim) to proceed.

A transition can declare:
  checks: [check_ci]
  on_checks_fail: incoming

The scheduler evaluates all checks for unclaimed tasks sitting in a
pre-condition queue. If all pass, the task is claimable. If any fail,
the task is moved to on_checks_fail. If any are pending, the task stays
put and is re-evaluated on the next tick.
"""

import enum
import json
import logging
import subprocess
from typing import Callable

logger = logging.getLogger("octopoid.checks")

CheckFn = Callable[[dict], "CheckResult"]

CHECK_REGISTRY: dict[str, CheckFn] = {}


class CheckResult(enum.Enum):
    """Result of a check evaluation."""
    PASS = "pass"
    FAIL = "fail"
    PENDING = "pending"


def register_check(name: str) -> Callable:
    """Decorator to register a check function."""
    def decorator(fn: CheckFn) -> CheckFn:
        CHECK_REGISTRY[name] = fn
        return fn
    return decorator


def evaluate_checks(check_names: list[str], task: dict) -> tuple[CheckResult, str]:
    """Evaluate a list of checks and return the aggregate result.

    Evaluation order:
    1. If any check returns FAIL, return (FAIL, reason) immediately.
    2. If any check returns PENDING (and none failed), return (PENDING, reason).
    3. If all checks return PASS, return (PASS, "").

    Args:
        check_names: Names of checks to run, in order.
        task: Task dict passed to each check.

    Returns:
        (aggregate_result, reason_string) where reason_string is empty on PASS.
    """
    pending_reason: str | None = None

    for name in check_names:
        fn = CHECK_REGISTRY.get(name)
        if fn is None:
            logger.warning(f"evaluate_checks: unknown check '{name}', treating as FAIL")
            return (CheckResult.FAIL, f"Unknown check: {name}")
        try:
            result = fn(task)
        except Exception as e:
            logger.warning(f"evaluate_checks: check '{name}' raised unexpectedly: {e}")
            return (CheckResult.FAIL, f"Check '{name}' raised: {e}")

        if result == CheckResult.FAIL:
            return (CheckResult.FAIL, f"Check '{name}' failed")
        if result == CheckResult.PENDING and pending_reason is None:
            pending_reason = f"Check '{name}' pending"

    if pending_reason is not None:
        return (CheckResult.PENDING, pending_reason)
    return (CheckResult.PASS, "")


# =============================================================================
# Built-in checks
# =============================================================================

_PENDING_STATES = {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED"}
_FAILED_STATES = {"ERROR", "FAILURE"}
_FAILED_CONCLUSIONS = {
    "FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED",
    "STARTUP_FAILURE", "ERROR",
}


@register_check("check_ci")
def check_ci(task: dict) -> CheckResult:
    """Verify that GitHub CI has passed for the task's PR.

    Uses `gh pr checks` to inspect CI status. Returns:
    - PASS: all checks passed (or no PR / no CI configured)
    - FAIL: one or more checks failed
    - PENDING: one or more checks still in progress
    """
    pr_number = task.get("pr_number")
    if not pr_number:
        logger.debug("check_ci: no pr_number on task, returning PASS")
        return CheckResult.PASS

    try:
        proc = subprocess.run(
            ["gh", "pr", "checks", str(pr_number), "--json", "name,state,conclusion"],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        logger.debug("check_ci: gh pr checks timed out, returning PENDING")
        return CheckResult.PENDING
    except FileNotFoundError:
        logger.debug("check_ci: gh CLI not found, returning PASS")
        return CheckResult.PASS

    if proc.returncode != 0:
        if not proc.stdout.strip():
            logger.debug(f"check_ci: no CI checks found (gh exit {proc.returncode}), returning PASS")
            return CheckResult.PASS
        logger.debug(f"check_ci: failed to query CI checks: {proc.stderr.strip()}, returning PENDING")
        return CheckResult.PENDING

    try:
        checks = json.loads(proc.stdout)
    except json.JSONDecodeError:
        logger.warning("check_ci: could not parse gh output, returning PASS")
        return CheckResult.PASS

    if not checks:
        logger.debug("check_ci: no CI checks configured, returning PASS")
        return CheckResult.PASS

    failed_checks: list[str] = []
    pending_checks: list[str] = []

    for check in checks:
        name = check.get("name", "unknown")
        state = (check.get("state") or "").upper()
        conclusion = (check.get("conclusion") or "").upper()

        if state in _PENDING_STATES:
            pending_checks.append(name)
        elif state in _FAILED_STATES or conclusion in _FAILED_CONCLUSIONS:
            label = conclusion.lower() if conclusion else state.lower()
            failed_checks.append(f"{name} ({label})")

    if failed_checks:
        logger.info(f"check_ci: CI failed — {', '.join(failed_checks)}")
        return CheckResult.FAIL

    if pending_checks:
        logger.debug(f"check_ci: CI pending — {', '.join(pending_checks)}")
        return CheckResult.PENDING

    logger.info(f"check_ci: all {len(checks)} CI check(s) passed")
    return CheckResult.PASS
