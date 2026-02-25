"""Housekeeping jobs for the scheduler.

All periodic maintenance tasks that run on each scheduler tick are defined here.
These functions are logically independent of agent evaluation/spawning and share
no internal state with the spawning logic. They are invoked through the single
run_housekeeping() entry point.

Functions are also individually registered in jobs.py for declarative scheduling
via .octopoid/jobs.yaml.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    find_parent_project,
    get_agents,
    get_agents_runtime_dir,
    get_logs_dir,
    get_tasks_dir,
)
from .git_utils import run_git
from . import queue_utils
from .pool import load_blueprint_pids, save_blueprint_pids
from .state_utils import is_process_running
from .result_handler import (
    _get_circuit_breaker_threshold,
    handle_agent_result,
    handle_agent_result_via_flow,
)


def debug_log(message: str) -> None:
    """Proxy to scheduler.debug_log to avoid circular imports."""
    from . import scheduler
    scheduler.debug_log(message)


def _log_pid_snapshot(agents_dir: Path) -> None:
    """Log a snapshot of all tracked PIDs to a JSONL file for diagnostics.

    Called at the start of each check_and_update_finished_agents run. Produces
    one JSON line per tick with all blueprint PIDs, their alive/dead status,
    and associated task IDs.
    """
    import json as _json

    snapshot: dict[str, list] = {}
    for agent_dir in agents_dir.iterdir():
        if not agent_dir.is_dir():
            continue
        pids_path = agent_dir / "running_pids.json"
        if not pids_path.exists():
            continue
        try:
            pids = load_blueprint_pids(agent_dir.name)
        except Exception:
            continue
        if not pids:
            continue
        snapshot[agent_dir.name] = [
            {
                "pid": pid,
                "task_id": info.get("task_id", ""),
                "instance": info.get("instance_name", ""),
                "alive": is_process_running(pid),
            }
            for pid, info in pids.items()
        ]

    if not snapshot:
        return

    log_path = get_logs_dir() / "pid_snapshot.jsonl"
    try:
        entry = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "pids": snapshot,
        }
        with open(log_path, "a") as f:
            f.write(_json.dumps(entry) + "\n")
    except OSError:
        pass


def check_and_update_finished_agents() -> None:
    """Check for agents that have finished and update their state.

    Iterates blueprints via running_pids.json. For each dead PID, processes
    the agent result and removes the PID from pool tracking.
    """
    agents_dir = get_agents_runtime_dir()
    if not agents_dir.exists():
        return

    # Snapshot current PIDs for diagnostics — helps trace orphan creation
    _log_pid_snapshot(agents_dir)

    # Pre-fetch agent configs to look up claim_from per blueprint
    try:
        agents_list = get_agents()
        blueprint_configs: dict[str, dict] = {
            a.get("blueprint_name", a.get("name", "")): a
            for a in agents_list
        }
    except Exception:
        blueprint_configs = {}

    for agent_dir in agents_dir.iterdir():
        if not agent_dir.is_dir():
            continue

        blueprint_name = agent_dir.name
        pids_path = agent_dir / "running_pids.json"
        if not pids_path.exists():
            continue

        pids = load_blueprint_pids(blueprint_name)
        if not pids:
            continue

        dead_pids = {
            pid: info
            for pid, info in pids.items()
            if not is_process_running(pid)
        }
        if not dead_pids:
            continue

        blueprint_config = blueprint_configs.get(blueprint_name, {})
        claim_from = blueprint_config.get("claim_from", "incoming")

        for pid, info in dead_pids.items():
            instance_name = info.get("instance_name", blueprint_name)
            task_id = info.get("task_id", "")
            debug_log(f"Instance {instance_name} (PID {pid}) has finished")

            if task_id:
                task_dir = get_tasks_dir() / task_id
                if task_dir.exists():
                    try:
                        if claim_from != "incoming":
                            # Review agents (claim from provisional, etc.) use flow dispatch
                            transitioned = handle_agent_result_via_flow(task_id, instance_name, task_dir, expected_queue=claim_from)
                        else:
                            # Implementers (claim from incoming) use outcome dispatch
                            transitioned = handle_agent_result(task_id, instance_name, task_dir)
                        # Only remove PID when the handler confirmed a state transition
                        # (or the task is gone). If transitioned=False, the task was not
                        # moved — keep the PID so the next tick retries.
                        if transitioned:
                            del pids[pid]
                            print(f"[{datetime.now().isoformat()}] Instance {instance_name} (PID {pid}) finished")
                        else:
                            debug_log(
                                f"Instance {instance_name} (PID {pid}): handler returned False "
                                f"(task not transitioned), keeping PID for retry"
                            )
                    except Exception as e:
                        print(
                            f"[{datetime.now().isoformat()}] Instance {instance_name} (PID {pid}) "
                            f"result handling failed, will retry next tick: {e}"
                        )
                        # PID intentionally left in tracking for retry
                else:
                    # Task dir missing — clean up the PID
                    del pids[pid]
                    print(f"[{datetime.now().isoformat()}] Instance {instance_name} (PID {pid}) finished (no task dir)")
            else:
                # No task ID — clean up the PID
                del pids[pid]
                print(f"[{datetime.now().isoformat()}] Instance {instance_name} (PID {pid}) finished (no task id)")

        save_blueprint_pids(blueprint_name, pids)


# =============================================================================
# Queue Health Detection (for queue-manager agent)
# =============================================================================

# Track last queue health check time (global state)
_last_queue_health_check: datetime | None = None
QUEUE_HEALTH_CHECK_INTERVAL_SECONDS = 1800  # 30 minutes


def _check_queue_health_throttled() -> None:
    """Check queue health with throttling to avoid running too frequently."""
    global _last_queue_health_check

    now = datetime.now()

    # Check if enough time has passed since last check
    if _last_queue_health_check is not None:
        elapsed = (now - _last_queue_health_check).total_seconds()
        if elapsed < QUEUE_HEALTH_CHECK_INTERVAL_SECONDS:
            return  # Not time yet

    # Update last check time
    _last_queue_health_check = now

    # Run the actual check
    check_queue_health()


def check_queue_health() -> None:
    """Check queue health and invoke queue-manager agent if issues found.

    Runs the diagnostic script and spawns queue-manager agent if any issues
    are detected. This is called periodically from the scheduler (every 30 minutes).
    """
    # Path to diagnostic script
    parent_project = find_parent_project()
    script_path = parent_project / ".octopoid" / "scripts" / "diagnose_queue_health.py"

    if not script_path.exists():
        debug_log("Queue health diagnostic script not found, skipping")
        return

    # Run diagnostic script with JSON output
    try:
        result = subprocess.run(
            [sys.executable, str(script_path), "--json"],
            cwd=parent_project,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            # No issues found
            debug_log("Queue health check: no issues found")
            return

        # Parse diagnostic output
        import json
        try:
            diagnostic_data = json.loads(result.stdout)
        except json.JSONDecodeError:
            debug_log(f"Failed to parse diagnostic output: {result.stdout[:200]}")
            return

        # Count issues
        mismatches = len(diagnostic_data.get("file_db_mismatches", []))
        orphans = len(diagnostic_data.get("orphan_files", []))
        zombies = len(diagnostic_data.get("zombie_claims", []))

        total_issues = mismatches + orphans + zombies

        if total_issues == 0:
            debug_log("Queue health check: no issues found")
            return

        # Issues found - log summary
        print(f"[{datetime.now().isoformat()}] Queue health issues detected:")
        print(f"  File-DB mismatches: {mismatches}")
        print(f"  Orphan files: {orphans}")
        print(f"  Zombie claims: {zombies}")
        debug_log(f"Queue health issues: {mismatches} mismatches, {orphans} orphans, {zombies} zombies")

        # Check if queue-manager agent is configured and ready to run
        agents = get_agents()
        queue_manager = next((a for a in agents if a.get("role") == "queue_manager"), None)

        if not queue_manager:
            debug_log("No queue-manager agent configured")
            return

        if queue_manager.get("paused", False):
            debug_log("Queue-manager agent is paused, not invoking")
            print(f"  (queue-manager agent is paused - issues not auto-reported)")
            return

        # Trigger queue-manager agent by setting environment variable
        # The agent's prompt will check this variable to know why it was triggered
        agent_name = queue_manager.get("name", "queue-manager")
        print(f"  Triggering {agent_name} to diagnose and report issues")
        debug_log(f"Triggering {agent_name} with {total_issues} issues")

        # Write diagnostic data to a temp file for the agent to read
        from .config import get_notes_dir as _get_notes_dir
        notes_dir = _get_notes_dir()
        notes_dir.mkdir(parents=True, exist_ok=True)
        diagnostic_file = notes_dir / f"queue-health-diagnostic-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        diagnostic_file.write_text(json.dumps(diagnostic_data, indent=2))

        debug_log(f"Wrote diagnostic data to {diagnostic_file}")

        # The queue-manager agent will read this file and generate a report
        # For now, we just log that issues were found. In a future phase, we
        # could automatically spawn the agent here.

    except subprocess.TimeoutExpired:
        debug_log("Queue health diagnostic timed out")
    except Exception as e:
        debug_log(f"Queue health check failed: {e}")


def _evaluate_project_script_condition(condition: object, project_dir: Path, project_id: str) -> bool:
    """Evaluate a script-type condition for a project.

    Runs the condition's script in the project directory.
    Returns True if the script exits with code 0 (passes), False otherwise.

    The special script name 'run-tests' is mapped to auto-detected test runner
    commands (pytest, npm test, make test) based on project files present.
    """
    script_name = getattr(condition, "script", None)
    if not script_name:
        debug_log(f"Project {project_id}: condition '{condition.name}' has no script, passing by default")
        return True

    if script_name == "run-tests":
        # Auto-detect test runner based on project files
        test_cmd: list[str] | None = None
        if (project_dir / "pytest.ini").exists() or (project_dir / "pyproject.toml").exists():
            test_cmd = ["python", "-m", "pytest", "--tb=short", "-q"]
        elif (project_dir / "package.json").exists():
            test_cmd = ["npm", "test"]
        elif (project_dir / "Makefile").exists():
            test_cmd = ["make", "test"]

        if test_cmd is None:
            debug_log(f"Project {project_id}: no test runner detected, condition '{condition.name}' passes")
            return True
        cmd = test_cmd
    else:
        cmd = [script_name]

    try:
        proc = subprocess.run(
            cmd,
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode == 0:
            debug_log(f"Project {project_id}: condition '{condition.name}' passed")
            return True
        else:
            output = (proc.stdout + "\n" + proc.stderr)[-1000:]
            debug_log(
                f"Project {project_id}: condition '{condition.name}' failed "
                f"(exit {proc.returncode}):\n{output}"
            )
            print(
                f"[{datetime.now().isoformat()}] Project {project_id}: "
                f"condition '{condition.name}' failed (exit {proc.returncode})"
            )
            return False
    except subprocess.TimeoutExpired:
        debug_log(f"Project {project_id}: condition '{condition.name}' timed out")
        print(f"[{datetime.now().isoformat()}] Project {project_id}: condition '{condition.name}' timed out")
        return False
    except Exception as e:
        debug_log(f"Project {project_id}: condition '{condition.name}' error: {e}")
        return False


def _execute_project_flow_transition(sdk: object, project: dict, from_state: str) -> bool:
    """Execute a project flow transition from the given state via the flow engine.

    Loads the project's flow, finds the transition from from_state, evaluates
    script conditions, executes the transition's step list, then updates the
    project status to the transition's target state.

    Returns True if the transition was executed, False if skipped (no transition
    defined or a condition failed).
    """
    from .flow import load_flow
    from .steps import execute_steps

    project_id = project["id"]
    flow_name = project.get("flow", "project")

    try:
        flow = load_flow(flow_name)
    except FileNotFoundError:
        if flow_name != "project":
            debug_log(
                f"Project {project_id}: flow '{flow_name}' not found, falling back to 'project'"
            )
            try:
                flow = load_flow("project")
            except FileNotFoundError:
                debug_log(f"Project {project_id}: 'project' flow not found, skipping")
                return False
        else:
            debug_log(f"Project {project_id}: 'project' flow not found, skipping")
            return False

    transitions = flow.get_transitions_from(from_state)
    if not transitions:
        debug_log(
            f"Project {project_id}: no transition from '{from_state}' in flow '{flow_name}'"
        )
        return False

    transition = transitions[0]
    parent_project_dir = find_parent_project()

    # Evaluate conditions — script conditions run synchronously; manual conditions
    # are skipped here (they require explicit human action via approve_project_via_flow)
    for condition in transition.conditions:
        if condition.type == "script":
            if not _evaluate_project_script_condition(condition, parent_project_dir, project_id):
                debug_log(
                    f"Project {project_id}: condition '{condition.name}' failed, "
                    f"not transitioning to '{transition.to_state}'"
                )
                return False
        elif condition.type == "manual":
            # Manual conditions block automatic transitions — they require an explicit
            # human approval call (approve_project_via_flow). Skip silently here.
            debug_log(
                f"Project {project_id}: transition '{from_state} -> {transition.to_state}' "
                f"requires manual condition '{condition.name}' — skipping automatic transition"
            )
            return False
        # Agent conditions on project flows are not currently supported

    # Execute pre-transition steps
    if transition.runs:
        debug_log(f"Project {project_id}: executing steps {transition.runs}")
        execute_steps(transition.runs, project, {}, parent_project_dir)

    # Re-fetch project to pick up PR metadata stored by steps (e.g. create_project_pr)
    updated_project = sdk.projects.get(project_id) or project
    pr_url = updated_project.get("pr_url")

    # Perform the transition
    sdk.projects.update(project_id, status=transition.to_state)
    print(
        f"[{datetime.now().isoformat()}] Project {project_id} moved to '{transition.to_state}' "
        f"via flow (PR: {pr_url})"
    )
    debug_log(f"Project {project_id}: transitioned '{from_state}' -> '{transition.to_state}'")
    return True


def check_project_completion() -> None:
    """Check active projects and run the children_complete -> provisional flow transition.

    For each active project where every child task is in the 'done' queue:
    1. Load the project's flow (defaults to 'project')
    2. Find the 'children_complete -> provisional' transition
    3. Evaluate flow conditions (e.g. all_tests_pass) before transitioning
    4. Execute transition steps (e.g. create_project_pr) via the flow engine
    5. Update project status to the transition's target state

    Runs as a housekeeping job every 60 seconds. Skips projects that are
    already past 'active' status.
    """
    try:
        sdk = queue_utils.get_sdk()
        projects = sdk.projects.list(status="active")

        if not projects:
            return

        for project in projects:
            project_id = project.get("id", "")
            project_status = project.get("status", "")

            # Extra safety check — skip non-active projects if list returns stale data
            if project_status in ("review", "provisional", "completed", "done"):
                debug_log(f"check_project_completion: skipping {project_id} (status={project_status})")
                continue

            tasks = sdk.projects.get_tasks(project_id)

            if not tasks:
                continue

            all_done = all(t.get("queue") == "done" for t in tasks)
            if not all_done:
                continue

            # All children done — project has no branch check kept for safety
            if not project.get("branch"):
                debug_log(f"check_project_completion: project {project_id} has no branch, posting warning")
                try:
                    sdk.messages.create(
                        task_id=f"project-{project_id}",
                        from_actor="scheduler",
                        to_actor="human",
                        type="warning",
                        content=(
                            f"Project {project_id} has all tasks done but no branch set. "
                            f"Cannot create PR. Please set a branch on the project."
                        ),
                    )
                except Exception as warn_e:
                    debug_log(f"check_project_completion: failed to post warning for {project_id}: {warn_e}")
                continue

            debug_log(f"check_project_completion: all children done for {project_id}, running flow transition")
            _execute_project_flow_transition(sdk, project, "children_complete")

    except Exception as e:
        debug_log(f"check_project_completion failed: {e}")


def check_and_requeue_expired_leases() -> None:
    """Requeue tasks whose lease has expired (orchestrator-side fallback).

    Handles two cases:
    - Tasks in 'claimed' queue (claimed from 'incoming' via the implementer):
      returned to 'incoming'.
    - Tasks in 'provisional' queue with an active claim (claimed in-place by the
      gatekeeper via claim_for_review): claim fields are cleared, task stays in
      'provisional'.
    """
    try:
        sdk = queue_utils.get_sdk()
        now = datetime.now(timezone.utc)

        # Map: queue_name -> target queue after expiry
        # 'claimed' tasks came from 'incoming'; 'provisional' tasks stay in 'provisional'.
        queues_to_check = {
            "claimed": "incoming",
            "provisional": "provisional",
        }

        threshold = _get_circuit_breaker_threshold()

        for queue_name, target_queue in queues_to_check.items():
            tasks = sdk.tasks.list(queue=queue_name)
            for task in tasks or []:
                # For provisional queue, only process tasks actively claimed
                # (claimed_by set) — unclaimed provisional tasks need no action.
                if queue_name == "provisional" and not task.get("claimed_by"):
                    continue

                lease_expires = task.get("lease_expires_at")
                if not lease_expires:
                    continue

                try:
                    expires_at = datetime.fromisoformat(lease_expires.replace('Z', '+00:00'))
                    if expires_at < now:
                        task_id = task["id"]

                        # Circuit breaker: only apply to claimed→incoming transitions,
                        # not provisional→provisional (which just clears the claim).
                        if queue_name == "claimed":
                            current_attempt_count = task.get("attempt_count", 0)
                            new_attempt_count = current_attempt_count + 1

                            if new_attempt_count >= threshold:
                                reason = (
                                    f"Circuit breaker: lease expired {new_attempt_count} time(s) "
                                    f"(threshold={threshold}). Task failed to complete within the lease window."
                                )
                                sdk.tasks.update(
                                    task_id,
                                    queue="failed",
                                    claimed_by=None,
                                    lease_expires_at=None,
                                    attempt_count=new_attempt_count,
                                    execution_notes=reason,
                                )
                                print(
                                    f"[{datetime.now().isoformat()}] CIRCUIT BREAKER: {task_id} moved to failed "
                                    f"after {new_attempt_count} lease expiries (threshold={threshold})"
                                )
                                debug_log(f"Circuit breaker tripped for {task_id}: {reason}")
                                continue

                            sdk.tasks.update(
                                task_id,
                                queue=target_queue,
                                claimed_by=None,
                                lease_expires_at=None,
                                attempt_count=new_attempt_count,
                            )
                        else:
                            # Provisional: just clear the claim, no attempt_count increment
                            sdk.tasks.update(task_id, queue=target_queue, claimed_by=None, lease_expires_at=None)

                        debug_log(f"Requeued expired lease: {task_id} → {target_queue} (expired {lease_expires})")
                        print(f"[{datetime.now().isoformat()}] Requeued expired lease: {task_id} → {target_queue}")
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        debug_log(f"Lease expiry check failed: {e}")


def _register_orchestrator(orchestrator_registered: bool = False) -> None:
    """Register this orchestrator with the API server (idempotent).

    Skips the POST if the poll response already reports orchestrator_registered: true.
    Only sends the registration request on the first tick or when the server
    reports the orchestrator is not registered.

    Args:
        orchestrator_registered: Whether the poll endpoint reported this
            orchestrator as already registered. If True, skip the POST.
    """
    if orchestrator_registered:
        debug_log("Orchestrator already registered (poll confirmed), skipping registration POST")
        return
    try:
        from .queue_utils import get_sdk, get_orchestrator_id
        from .config import _load_project_config as load_config, save_api_key
        from .sdk import reset_sdk
        sdk = get_sdk()
        orch_id = get_orchestrator_id()
        parts = orch_id.split("-", 1)
        cluster = parts[0] if len(parts) > 1 else "default"
        machine_id = parts[1] if len(parts) > 1 else orch_id
        config = load_config()
        repo_url = config.get("repo", {}).get("url") or None
        payload: dict = {
            "id": orch_id,
            "cluster": cluster,
            "machine_id": machine_id,
            "version": "2.0.0",
            "max_agents": config.get("agents", {}).get("max_concurrent", 3),
        }
        if repo_url:
            payload["repo_url"] = repo_url
        response = sdk._request("POST", "/api/v1/orchestrators/register", json=payload)
        debug_log(f"Registered orchestrator: {orch_id}")
        # If the server issued a new API key (first registration for this scope),
        # persist it and reset the SDK so subsequent calls use it.
        if isinstance(response, dict) and response.get("api_key"):
            save_api_key(response["api_key"])
            reset_sdk()
            debug_log("Stored new API key from registration response")
    except Exception as e:
        debug_log(f"Orchestrator registration failed (non-fatal): {e}")

    # Sync flow definitions to server so it can validate queue names at runtime.
    # Reads directly from local YAML files (source of truth for sync).
    # Non-fatal: errors are logged but never block registration.
    try:
        from .flow import Flow
        from .config import get_orchestrator_dir
        flows_dir = get_orchestrator_dir() / "flows"
        if flows_dir.exists():
            from .queue_utils import get_sdk as _get_sdk
            _sdk = _get_sdk()
            for flow_path in sorted(flows_dir.glob("*.yaml")):
                flow_name = flow_path.stem
                try:
                    flow = Flow.from_yaml_file(flow_path)
                    states = sorted(flow.get_all_states())
                    transitions = [
                        {"from": t.from_state, "to": t.to_state}
                        for t in flow.transitions
                    ]
                    _sdk._request("PUT", f"/api/v1/flows/{flow_name}", json={
                        "states": states,
                        "transitions": transitions,
                    })
                    debug_log(f"Synced flow '{flow_name}' to server")
                except Exception as flow_err:
                    debug_log(f"Flow sync failed for '{flow_name}' (non-fatal): {flow_err}")
    except Exception as e:
        debug_log(f"Flow sync failed (non-fatal): {e}")


def send_heartbeat() -> None:
    """Send a heartbeat to the API server to update last_heartbeat.

    POSTs to /api/v1/orchestrators/{orchestrator_id}/heartbeat.
    Failures are non-fatal: errors are logged but never crash the scheduler.
    """
    try:
        from .queue_utils import get_sdk, get_orchestrator_id
        sdk = get_sdk()
        orch_id = get_orchestrator_id()
        sdk._request("POST", f"/api/v1/orchestrators/{orch_id}/heartbeat")
        debug_log(f"Heartbeat sent for orchestrator: {orch_id}")
    except Exception as e:
        debug_log(f"Heartbeat failed (non-fatal): {e}")


def sweep_stale_resources() -> None:
    """Archive logs and delete worktrees for old done/failed tasks.

    For each task in the 'done' queue (1 hour grace) or 'failed' queue
    (24 hour grace — longer to allow investigation):
    - Archives stdout.log, stderr.log, result.json, prompt.md to
      .octopoid/runtime/logs/<task-id>/
    - Deletes the worktree at .octopoid/runtime/tasks/<task-id>/worktree
    - Deletes the remote branch agent/<task-id> for done tasks only
    - Runs git worktree prune after deletions

    Idempotent: safe to run multiple times.
    Failed individual cleanups are logged but do not abort the sweep.
    """
    import shutil

    DONE_GRACE_SECONDS = 3600       # 1 hour — work is merged, safe to clean
    FAILED_GRACE_SECONDS = 86400    # 24 hours — need time to investigate

    try:
        sdk = queue_utils.get_sdk()
        done_tasks = sdk.tasks.list(queue="done") or []
        failed_tasks = sdk.tasks.list(queue="failed") or []
    except Exception as e:
        debug_log(f"sweep_stale_resources: failed to fetch tasks: {e}")
        return

    try:
        parent_repo = find_parent_project()
    except Exception as e:
        debug_log(f"sweep_stale_resources: could not find parent repo: {e}")
        return

    tasks_dir = get_tasks_dir()
    logs_dir = get_logs_dir()
    now = datetime.now(timezone.utc)
    pruned_any = False

    for task in done_tasks + failed_tasks:
        task_id = task.get("id")
        queue = task.get("queue", "")
        if not task_id:
            continue

        # Check age: skip if within grace period
        ts_str = task.get("updated_at") or task.get("completed_at")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            elapsed = (now - ts).total_seconds()
        except (ValueError, TypeError) as e:
            debug_log(f"sweep_stale_resources: could not parse timestamp {ts_str!r} for task {task_id}: {e}")
            continue

        grace = FAILED_GRACE_SECONDS if queue == "failed" else DONE_GRACE_SECONDS
        if elapsed < grace:
            continue

        task_dir = tasks_dir / task_id
        worktree_path = task_dir / "worktree"

        # Archive logs and delete worktree if it exists
        if worktree_path.exists():
            # Archive log files before deleting
            try:
                archive_dir = logs_dir / task_id
                archive_dir.mkdir(parents=True, exist_ok=True)
                for filename in ("stdout.log", "stderr.log", "result.json", "prompt.md"):
                    src = task_dir / filename
                    if src.exists():
                        shutil.copy2(src, archive_dir / filename)
            except Exception as e:
                debug_log(f"sweep_stale_resources: failed to archive logs for {task_id}: {e}")

            # Remove worktree from git tracking and filesystem
            try:
                run_git(
                    ["worktree", "remove", "--force", str(worktree_path)],
                    cwd=parent_repo,
                    check=False,
                )
                if worktree_path.exists():
                    shutil.rmtree(worktree_path)
                pruned_any = True
                debug_log(f"sweep_stale_resources: deleted worktree for {task_id} ({queue})")
                print(f"[{datetime.now().isoformat()}] Swept worktree for task {task_id} ({queue})")
            except Exception as e:
                debug_log(f"sweep_stale_resources: failed to delete worktree for {task_id}: {e}")

        # Delete remote branch for done (merged) tasks only — not failed
        if queue == "done":
            branch = f"agent/{task_id}"
            try:
                result = run_git(
                    ["push", "origin", "--delete", branch],
                    cwd=parent_repo,
                    check=False,
                )
                if result.returncode == 0:
                    debug_log(f"sweep_stale_resources: deleted remote branch {branch}")
                    print(f"[{datetime.now().isoformat()}] Deleted remote branch {branch}")
                else:
                    # Already gone or no permissions — non-fatal
                    debug_log(
                        f"sweep_stale_resources: remote branch {branch} deletion skipped: "
                        f"{result.stderr.strip()}"
                    )
            except Exception as e:
                debug_log(f"sweep_stale_resources: failed to delete remote branch {branch}: {e}")

    # Run git worktree prune once after all deletions
    if pruned_any:
        try:
            run_git(["worktree", "prune"], cwd=parent_repo, check=False)
            debug_log("sweep_stale_resources: ran git worktree prune")
        except Exception as e:
            debug_log(f"sweep_stale_resources: git worktree prune failed: {e}")


HOUSEKEEPING_JOBS = [
    _register_orchestrator,
    check_and_requeue_expired_leases,
    check_and_update_finished_agents,
    _check_queue_health_throttled,
    check_project_completion,
    sweep_stale_resources,
]


def run_housekeeping() -> None:
    """Run all housekeeping jobs. Each is independent and fault-isolated."""
    for job in HOUSEKEEPING_JOBS:
        try:
            job()
        except Exception as e:
            debug_log(f"Housekeeping job {job.__name__} failed: {e}")
