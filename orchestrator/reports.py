"""Structured project report API for Octopoid v2.0.

Provides a single get_project_report() function that aggregates data from
the Octopoid API server via SDK into a structured dict suitable for dashboards,
TUIs, and other consumers.

V2.0 API-only mode - requires OctopoidSDK instance.
"""

import json
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

# Type hint for SDK
try:
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        from octopoid_sdk import OctopoidSDK
except ImportError:
    pass


def get_project_report(sdk: "OctopoidSDK") -> dict[str, Any]:
    """Generate a comprehensive structured project report from API server.

    Aggregates data from: API tasks, agent configs/state, open PRs,
    inbox proposals, agent messages, and agent notes.

    Args:
        sdk: Octopoid SDK instance for v2.0 API access (required).

    Returns:
        Structured dict with keys: work, flows, prs, proposals, messages,
        agents, health, drafts.
    """
    return {
        "work": _gather_work(sdk),
        "flows": _gather_flows(sdk),
        "done_tasks": _gather_done_tasks(sdk),
        "prs": [],  # Disabled — _gather_prs was burning 22k+ gh API calls/hour
        "proposals": _gather_proposals(),
        "messages": _gather_messages(),
        "agents": _gather_agents(),
        "health": _gather_health(sdk),
        "drafts": _gather_drafts(sdk),
        "generated_at": datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# Work items
# ---------------------------------------------------------------------------


def _read_live_turns() -> dict[str, int]:
    """Read live turn counts from tool_counter files in task runtime directories.

    Each running agent writes to .octopoid/runtime/tasks/<task-id>/tool_counter
    via a PostToolUse hook. File size in bytes = number of tool calls = turns used.

    Returns:
        Mapping of {task_id: turn_count} for tasks with a counter file.
    """
    try:
        from .config import get_tasks_dir

        tasks_dir = get_tasks_dir()
        if not tasks_dir.exists():
            return {}

        result: dict[str, int] = {}
        for task_dir in tasks_dir.iterdir():
            if not task_dir.is_dir():
                continue
            counter_path = task_dir / "tool_counter"
            try:
                result[task_dir.name] = counter_path.stat().st_size
            except FileNotFoundError:
                pass
        return result
    except Exception:
        return {}


def _gather_work(sdk: "OctopoidSDK") -> dict[str, list[dict[str, Any]]]:
    """Gather task work items from all relevant queues via API."""
    # Fetch tasks from API server
    incoming = [_format_task(t) for t in sdk.tasks.list(queue='incoming')]

    # For in-progress tasks, overlay live turn counts from tool_counter files
    live_turns = _read_live_turns()
    claimed = []
    for t in sdk.tasks.list(queue='claimed'):
        formatted = _format_task(t)
        task_id = formatted.get("id")
        if task_id and task_id in live_turns:
            formatted["turns"] = live_turns[task_id]
        claimed.append(formatted)
    provisional = [_format_task(t) for t in sdk.tasks.list(queue='provisional')]

    # Split provisional into "checking" (has pending checks) and "in_review" (ready for human)
    checking = []
    in_review = []
    for t in provisional:
        checks = t.get("checks", [])
        if not checks:
            # No checks defined — ready for human review
            in_review.append(t)
        else:
            # Has checks — see if all have passed
            check_results = t.get("check_results", {})
            all_passed = all(
                check_results.get(c, {}).get("status") == "pass"
                for c in checks
            )
            if all_passed:
                in_review.append(t)
            else:
                checking.append(t)

    # "done_today" — tasks completed in the last 24 hours
    done_all = sdk.tasks.list(queue='done')
    cutoff = datetime.now() - timedelta(hours=24)
    done_today = [_format_task(t) for t in done_all if _is_recent(t, cutoff)]

    return {
        "incoming": incoming,
        "in_progress": claimed,
        "checking": checking,
        "in_review": in_review,
        "done_today": done_today,
    }


def _gather_done_tasks(sdk: Optional["OctopoidSDK"] = None) -> list[dict[str, Any]]:
    """Gather completed tasks from the last 7 days for the Done tab.

    Includes merge method derived from task_history 'accepted' events.
    Also includes failed and recycled tasks.
    """
    cutoff = datetime.now() - timedelta(days=7)

    if not sdk:
        return []

    # Done tasks
    done_all = sdk.tasks.list(queue='done')
    done_recent = [t for t in done_all if _is_recent(t, cutoff)]

    # Failed tasks
    try:
        failed_all = sdk.tasks.list(queue='failed')
        failed_recent = [t for t in failed_all if _is_recent(t, cutoff)]
    except Exception:
        failed_recent = []

    # Recycled tasks
    try:
        recycled_all = sdk.tasks.list(queue='recycled')
        recycled_recent = [t for t in recycled_all if _is_recent(t, cutoff)]
    except Exception:
        recycled_recent = []

    result = []
    for t in done_recent:
        item = _format_task(t)
        item["final_queue"] = "done"
        # Use actual completion time (completed_at), fall back to updated_at, then created_at
        item["completed_at"] = t.get("completed_at") or t.get("updated_at") or t.get("created_at") or t.get("created")
        item["accepted_by"] = None
        result.append(item)

    for t in failed_recent:
        item = _format_task(t)
        item["final_queue"] = "failed"
        # Use actual completion time (completed_at), fall back to updated_at, then created_at
        item["completed_at"] = t.get("completed_at") or t.get("updated_at") or t.get("created_at") or t.get("created")
        item["accepted_by"] = None
        result.append(item)

    for t in recycled_recent:
        item = _format_task(t)
        item["final_queue"] = "recycled"
        # Use actual completion time (completed_at), fall back to updated_at, then created_at
        item["completed_at"] = t.get("completed_at") or t.get("updated_at") or t.get("created_at") or t.get("created")
        item["accepted_by"] = None
        result.append(item)

    # Resolved tasks (manually resolved by humans)
    try:
        resolved_all = sdk.tasks.list(queue='resolved')
        resolved_recent = [t for t in resolved_all if _is_recent(t, cutoff)]
    except Exception:
        resolved_recent = []

    for t in resolved_recent:
        item = _format_task(t)
        item["final_queue"] = "resolved"
        item["completed_at"] = t.get("completed_at") or t.get("updated_at") or t.get("created_at") or t.get("created")
        item["resolved_by"] = t.get("resolved_by")
        item["resolution_note"] = t.get("resolution_note")
        item["accepted_by"] = None
        result.append(item)

    # Sort by most recent first (using completed_at/created)
    result.sort(key=lambda t: t.get("completed_at") or "", reverse=True)
    return result


def _format_task(task: dict[str, Any]) -> dict[str, Any]:
    """Format a task dict into a card-renderable summary."""
    title = task.get("title")
    # If title is missing or looks like a raw ID, try extracting from file
    if not title or (len(title) < 20 and " " not in title):
        file_path = task.get("path") or task.get("file_path")
        extracted = _extract_title_from_file(str(file_path) if file_path else None)
        if extracted and extracted != "untitled":
            title = extracted
    return {
        "id": task.get("id"),
        "title": title,
        "role": task.get("role"),
        "priority": task.get("priority"),
        "branch": task.get("branch"),
        "created": task.get("created"),
        "agent": task.get("claimed_by"),
        "turns": task.get("turns_used", 0),
        "turn_limit": _turn_limit_for_role(task.get("role")),
        "commits": task.get("commits_count", 0),
        "pr_number": task.get("pr_number"),
        "blocked_by": task.get("blocked_by"),
        "project_id": task.get("project_id"),
        "attempt_count": task.get("attempt_count", 0),
        "rejection_count": task.get("rejection_count", 0),
        "checks": task.get("checks", []),
        "check_results": task.get("check_results", {}),
        "staging_url": task.get("staging_url"),
        "claimed_at": task.get("claimed_at"),
        "queue": task.get("queue"),
        "flow": task.get("flow") or "default",
        "resolved_by": task.get("resolved_by"),
        "resolution_note": task.get("resolution_note"),
    }


def _is_recent(task: dict[str, Any], cutoff: datetime) -> bool:
    """Check if a task's completed/updated timestamp is after cutoff.

    For completed/failed/recycled tasks, we want to check when they were
    completed, not when they were created. A task created 30 days ago but
    completed today should appear in the Done tab.
    """
    # Check completed_at first (most accurate), then updated_at, then fall back to created
    ts_str = task.get("completed_at") or task.get("updated_at") or task.get("created_at") or task.get("created")
    if not ts_str:
        return False
    try:
        cleaned = str(ts_str).replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt >= cutoff
    except (ValueError, TypeError):
        return False


# Role → max turns mapping (mirrors values in roles/*.py)
_ROLE_TURN_LIMITS: dict[str, int] = {
    "implement": 100,
    "orchestrator_impl": 200,
    "breakdown": 50,
    "decomposition": 10,
    "reviewer": 20,
    "gatekeeper": 15,
    "curator": 30,
    "tester": 30,
    "product_manager": 20,
    "proposer": 20,
    "inbox_poller": 10,
    "recycler": 10,
}


def _turn_limit_for_role(role: str | None) -> int:
    """Return the max turn limit for a given role."""
    return _ROLE_TURN_LIMITS.get(role or "", 100)


def _extract_staging_url(pr_number: int) -> str | None:
    """Extract Cloudflare Pages branch preview URL from a PR's comments.

    Looks for the Cloudflare bot comment containing a Branch Preview URL
    in a table row like: | Branch Preview | https://xxx.pages.dev |

    Args:
        pr_number: PR number to check

    Returns:
        Branch preview URL or None if not found
    """
    try:
        result = subprocess.run(
            [
                "gh", "pr", "view", str(pr_number),
                "--json", "comments",
                "--jq", ".comments[].body",
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None

        # Look for the Cloudflare Pages branch preview URL
        # Format: | Branch Preview | https://xxx.pages.dev |
        # or: | **Branch Preview** | [Visit Preview](https://xxx.pages.dev) |
        for line in result.stdout.splitlines():
            match = re.search(
                r"Branch Preview.*?(https://[a-zA-Z0-9._-]+\.pages\.dev)",
                line, re.IGNORECASE,
            )
            if match:
                return match.group(1)

        return None
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def _store_staging_url(pr_number: int, staging_url: str, *, branch_name: str | None = None, sdk: Optional["OctopoidSDK"] = None) -> None:
    """Store a staging URL on the task associated with a PR number.

    Looks up the task by pr_number first. If that fails and a branch_name
    is provided, falls back to matching by branch pattern (agent/<task-id>-*).
    When multiple tasks match, uses the most recently updated one.

    Args:
        pr_number: PR number to look up
        staging_url: URL to store
        branch_name: Optional branch name for fallback lookup
        sdk: Optional SDK for v2.0 API mode
    """
    try:
        if sdk:
            # v2.0 API mode
            # Find task by pr_number
            tasks = sdk.tasks.list(pr_number=pr_number)
            if tasks:
                task = tasks[0]
                # Update via PATCH endpoint (SDK doesn't expose update yet, skip for now)
                # TODO: Add update method to SDK
                return

            # Fallback: match by branch name pattern
            if branch_name:
                import re
                m = re.match(r"agent/([a-f0-9]{8})", branch_name)
                if m:
                    task_id = m.group(1)
                    task = sdk.tasks.get(task_id)
                    if task:
                        # TODO: Update staging_url via SDK
                        pass
        else:
            # v1.x local mode — DB removed in v2.0, nothing to do
            pass
    except Exception:
        pass  # Best-effort — don't break PR gathering


# ---------------------------------------------------------------------------
# Flows
# ---------------------------------------------------------------------------


def _gather_flows(sdk: "OctopoidSDK") -> list[dict[str, Any]]:
    """Fetch flow definitions from the API server.

    Returns a list of flow dicts with 'name', 'states', and 'transitions'.
    States and transitions may be JSON-encoded strings on the server and are
    parsed here into Python lists.
    """
    try:
        flows_raw = sdk.flows.list()
        result = []
        for f in flows_raw:
            states = f.get("states", [])
            if isinstance(states, str):
                try:
                    states = json.loads(states)
                except (json.JSONDecodeError, ValueError):
                    states = []
            transitions = f.get("transitions", [])
            if isinstance(transitions, str):
                try:
                    transitions = json.loads(transitions)
                except (json.JSONDecodeError, ValueError):
                    transitions = []
            result.append({
                "name": f.get("name"),
                "states": states,
                "transitions": transitions,
            })
        return result
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------

# Default actions injected client-side when no proposer has created actions
# for a draft. These are synthetic — not stored on the server.
_DEFAULT_DRAFT_ACTIONS: list[dict[str, Any]] = [
    {"id": "default-enqueue", "label": "Enqueue", "action_type": "enqueue_draft"},
    {"id": "default-process", "label": "Process", "action_type": "process_draft"},
    {"id": "default-archive", "label": "Archive", "action_type": "archive_draft"},
]


def _gather_drafts(sdk: "OctopoidSDK") -> list[dict[str, Any]]:
    """Gather drafts from the API server, including pending actions for each draft.

    Actions are fetched in a single bulk call (not one per draft) and grouped
    client-side by entity_id to avoid N+1 API requests.

    When a draft has no server-side actions, the three default actions
    (Enqueue, Process, Archive) are injected client-side.
    """
    try:
        drafts = sdk.drafts.list()
    except Exception:
        return []

    # Fetch all pending draft actions in a single call, then group by entity_id.
    # This replaces N individual GET /api/v1/actions calls (one per draft) with 1.
    try:
        all_actions = sdk.actions.list(entity_type="draft", status="pending")
        actions_by_draft: dict[str, list[dict[str, Any]]] = {}
        for action in all_actions:
            eid = str(action.get("entity_id", ""))
            if eid:
                actions_by_draft.setdefault(eid, []).append(action)
    except Exception:
        actions_by_draft = {}

    result = []
    for d in drafts:
        draft_id = d.get("id")
        server_actions = actions_by_draft.get(str(draft_id) if draft_id is not None else "", [])
        # Use server-provided actions if present; otherwise fall back to defaults.
        actions = server_actions if server_actions else list(_DEFAULT_DRAFT_ACTIONS)
        result.append({
            "id": draft_id,
            "title": d.get("title"),
            "status": d.get("status", "idea"),
            "file_path": d.get("file_path"),
            "created_at": d.get("created_at"),
            "actions": actions,
        })
    return result


# ---------------------------------------------------------------------------
# Proposals (inbox items)
# ---------------------------------------------------------------------------


def _gather_proposals() -> list[dict[str, Any]]:
    """Gather active proposals from the inbox."""
    try:
        from .proposal_utils import list_proposals

        active = list_proposals("active")
        return [
            {
                "id": p.get("id"),
                "title": p.get("title"),
                "proposer": p.get("proposer"),
                "category": p.get("category"),
                "complexity": p.get("complexity"),
                "created": p.get("created"),
            }
            for p in active
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def _gather_messages() -> list[dict[str, Any]]:
    """Gather pending messages from agents."""
    try:
        from .message_utils import list_messages

        messages = list_messages()
        return [
            {
                "filename": m.get("filename"),
                "type": m.get("type"),
                "created": m.get("created"),
            }
            for m in messages
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Agent status
# ---------------------------------------------------------------------------


def _gather_agents() -> list[dict[str, Any]]:
    """Gather agent blueprint status using the pool model."""
    try:
        from .config import get_agents, get_notes_dir
        from .pool import count_running_instances, get_active_task_ids, load_blueprint_pids

        agents = get_agents()
    except FileNotFoundError:
        # Agents config not found - return empty list (API-only mode)
        return []
    except Exception:
        # Other errors - return empty list gracefully
        return []
    notes_dir = get_notes_dir()

    result = []
    for agent in agents:
        blueprint_name = agent.get("blueprint_name", agent["name"])
        name = agent["name"]
        role = agent.get("role", "unknown")
        paused = agent.get("paused", False)
        max_instances = agent.get("max_instances", 1)

        # Count alive PIDs only — do NOT call cleanup_dead_pids() here.
        # Removing dead PIDs must only happen in check_and_update_finished_agents,
        # which processes the agent result first. Calling cleanup here races
        # with result processing and causes orphaned tasks.
        running_instances = count_running_instances(blueprint_name)
        idle_capacity = max(0, max_instances - running_instances)

        # Aggregate current tasks from live PIDs only
        current_tasks = list(get_active_task_ids(blueprint_name))

        # Determine overall status
        if paused:
            status = "paused"
        elif running_instances > 0:
            status = "running"
        else:
            status = "idle"

        # Notes: look for task notes for first current task
        current_task = current_tasks[0] if current_tasks else None
        agent_notes = _get_agent_notes(notes_dir, current_task)

        result.append({
            "name": name,
            "blueprint_name": blueprint_name,
            "role": role,
            "status": status,
            "paused": paused,
            "max_instances": max_instances,
            "running_instances": running_instances,
            "idle_capacity": idle_capacity,
            "current_tasks": current_tasks,
            "notes": agent_notes,
        })

    return result


def _load_agent_state(state_path: Path) -> dict[str, Any]:
    """Load agent state from state.json, returning empty dict on failure."""
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _extract_title_from_file(file_path: str | None) -> str:
    """Extract task title from the task file on disk."""
    if not file_path:
        return "untitled"
    try:
        path = Path(file_path)
        # file_path in DB may be stale (points to incoming/ but file moved)
        if not path.exists():
            # Search across queue directories for the same filename
            queue_root = path.parent.parent
            if queue_root.exists():
                for subdir in queue_root.iterdir():
                    if subdir.is_dir():
                        candidate = subdir / path.name
                        if candidate.exists():
                            path = candidate
                            break
        if path.exists():
            content = path.read_text()
            match = re.search(r"^#\s*\[TASK-[^\]]+\]\s*(.+)$", content, re.MULTILINE)
            if match:
                return match.group(1).strip()
        # Fall back to extracting from filename
        stem = path.stem
        if stem.startswith("TASK-"):
            return stem[5:]
        return stem
    except (OSError, ValueError):
        return "untitled"


def _get_agent_notes(notes_dir: Path, current_task: str | None) -> str | None:
    """Get notes for an agent's current task."""
    if not current_task:
        return None
    notes_path = notes_dir / f"TASK-{current_task}.md"
    if notes_path.exists():
        try:
            content = notes_path.read_text().strip()
            # Return first 500 chars as preview
            return content[:500] if len(content) > 500 else content
        except OSError:
            pass
    return None


# ---------------------------------------------------------------------------
# System health
# ---------------------------------------------------------------------------


def _gather_health(sdk: Optional["OctopoidSDK"] = None) -> dict[str, Any]:
    """Gather system health information."""
    # Try to load agent config, but gracefully handle API-only mode
    try:
        from .config import get_agents, is_system_paused
        from .pool import count_running_instances
        agents = get_agents()
        scheduler_status = _get_scheduler_status()
        system_paused = is_system_paused()
    except (FileNotFoundError, Exception):
        # API-only mode or config not found - use defaults
        agents = []
        scheduler_status = "api-only"
        system_paused = False

    # Count capacity across all blueprints using pool model
    idle_count = 0
    running_count = 0
    paused_count = 0

    for agent in agents:
        if agent.get("paused"):
            paused_count += 1
            continue
        blueprint_name = agent.get("blueprint_name", agent["name"])
        max_instances = agent.get("max_instances", 1)
        running = count_running_instances(blueprint_name)
        running_count += running
        idle_count += max(0, max_instances - running)

    # Queue depth = incoming + claimed + breakdown
    if sdk:
        # v2.0 API mode
        incoming = len(sdk.tasks.list(queue='incoming'))
        claimed = len(sdk.tasks.list(queue='claimed'))
        try:
            breakdown = len(sdk.tasks.list(queue='breakdown'))
        except Exception:
            breakdown = 0
        queue_depth = incoming + claimed + breakdown
    else:
        # v1.x local mode
        from .queue_utils import count_queue
        queue_depth = count_queue("incoming") + count_queue("claimed")
        try:
            queue_depth += count_queue("breakdown")
        except Exception:
            pass

    return {
        "scheduler": scheduler_status,
        "system_paused": system_paused,
        "idle_agents": idle_count,
        "running_agents": running_count,
        "paused_agents": paused_count,
        "total_agents": len(agents),
        "queue_depth": queue_depth,
    }


def _get_scheduler_status() -> str:
    """Determine if the scheduler is running via launchctl."""
    try:
        result = subprocess.run(
            ["launchctl", "list", "com.boxen.orchestrator"],
            capture_output=True, text=True, timeout=5,
        )
        if "LastExitStatus" in result.stdout:
            return "running"
        return "not_loaded"
    except (subprocess.SubprocessError, FileNotFoundError):
        return "unknown"
