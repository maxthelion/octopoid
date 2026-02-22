"""Message dispatcher — polls action_command messages and spawns action agents.

Each scheduler tick, this module polls for unprocessed action_command messages
addressed to the "agent" actor, spawns a lightweight Claude agent to handle each
one, and posts the result (success or failure) back to the human inbox.

Processes one message per tick (serial) to keep things simple.
Local state tracks processed messages since the server messages API does not
support per-message status updates.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import queue_utils
from .config import find_parent_project, get_global_instructions_path, get_orchestrator_dir


# How long before a "processing" message is considered stuck (agent crash recovery)
STUCK_THRESHOLD_SECONDS = 300  # 5 minutes

# Maximum time to wait for an action agent to complete
AGENT_TIMEOUT_SECONDS = 180  # 3 minutes


def _get_state_path() -> Path:
    """Return path to the message dispatch state file."""
    return get_orchestrator_dir() / "runtime" / "message_dispatch_state.json"


def _load_state() -> dict:
    """Load message dispatch state from disk.

    Returns:
        Dict with keys:
          - done: list of processed message IDs
          - failed: list of failed message IDs
          - processing: {msg_id: {"started_at": iso_str, "content": str}}
    """
    path = _get_state_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"done": [], "failed": [], "processing": {}}


def _save_state(state: dict) -> None:
    """Persist message dispatch state to disk."""
    path = _get_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def _build_agent_prompt(message: dict) -> str:
    """Build the full prompt for an action agent from a message.

    Prepends global instructions and action-agent constraints to the message
    content so the agent knows its execution environment and limitations.

    Args:
        message: Message dict from sdk.messages.list()

    Returns:
        Full prompt string to pass to claude -p
    """
    content = message.get("content", "")
    task_id = message.get("task_id", "")
    msg_id = message.get("id", "")

    # Load global instructions (CLAUDE.md conventions etc.)
    global_instructions = ""
    gi_path = get_global_instructions_path()
    if gi_path.exists():
        global_instructions = gi_path.read_text()

    return f"""{global_instructions}

---

# Action Agent

You are a lightweight action agent for the Octopoid orchestration system.
You receive a single command and execute it, then you are done.

**Message ID:** {msg_id}
**Task ID:** {task_id}

**Execution constraints:**
- Allowed: Read any file, SDK calls (server API), write files under `project-management/` only
- Not allowed: Git operations (no git add/commit/push/checkout), writes outside `project-management/`
- No long-running work — complete within a few tool calls

**Available skills:** /enqueue, /process-draft, /draft-idea, /approve-task, /queue-status

**Command to execute:**
{content}

---

Execute the command above. When done, output a brief summary of what you did."""


def _run_action_agent(prompt: str, timeout: int = AGENT_TIMEOUT_SECONDS) -> tuple[bool, str]:
    """Run a lightweight action agent synchronously.

    Spawns `claude -p` in the main repo working directory and waits for it
    to complete. The agent is constrained to --max-turns 10 and Read/Write/
    Edit/Glob/Grep/Bash/Skill tools.

    Args:
        prompt: Full agent prompt (instructions + command)
        timeout: Maximum seconds to wait before treating as failure

    Returns:
        (success, output_or_error_text)
    """
    parent_project = find_parent_project()

    cmd = [
        "claude",
        "-p", prompt,
        "--allowedTools", "Read,Write,Edit,Glob,Grep,Bash,Skill",
        "--max-turns", "10",
    ]

    env = os.environ.copy()
    # Unset CLAUDECODE so the spawned claude doesn't think it's nested
    env.pop("CLAUDECODE", None)

    try:
        result = subprocess.run(
            cmd,
            cwd=parent_project,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        output = result.stdout.strip()
        if result.returncode == 0:
            return True, output
        else:
            error = result.stderr.strip() or f"Exit code {result.returncode}"
            return False, f"{error}\n{output}"[:1000]

    except subprocess.TimeoutExpired:
        return False, f"Agent timed out after {timeout}s"
    except Exception as e:
        return False, str(e)


def dispatch_action_messages() -> None:
    """Poll for action_command messages and dispatch one action agent per tick.

    Algorithm:
    1. Fetch all action_command messages addressed to "agent"
    2. Reset any stuck messages (in processing for > STUCK_THRESHOLD_SECONDS)
    3. Find the first unprocessed message (not in done/failed/processing)
    4. Mark it as processing, run the action agent synchronously
    5. Post worker_result to human inbox; mark message done or failed
    6. Process at most one message per tick

    Local state (message_dispatch_state.json) tracks processed messages because
    the server messages API does not support per-message status updates.
    """
    from .scheduler import debug_log

    try:
        sdk = queue_utils.get_sdk()
        messages = sdk.messages.list(to_actor="agent", type="action_command")
    except Exception as e:
        debug_log(f"dispatch_action_messages: failed to list messages: {e}")
        return

    if not messages:
        debug_log("dispatch_action_messages: no action_command messages")
        return

    state = _load_state()
    now = datetime.now(timezone.utc)

    # --- Stuck message detection ---
    # If an agent crashed while processing a message, it stays in "processing"
    # indefinitely. Reset stuck messages to failed so they don't block forever.
    processing = state.get("processing", {})
    for msg_id, info in list(processing.items()):
        started_at_str = info.get("started_at", "")
        try:
            started_at = datetime.fromisoformat(started_at_str)
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            elapsed = (now - started_at).total_seconds()
        except (ValueError, TypeError):
            elapsed = STUCK_THRESHOLD_SECONDS + 1  # treat unparseable as stuck

        if elapsed > STUCK_THRESHOLD_SECONDS:
            debug_log(
                f"dispatch_action_messages: message {msg_id} stuck for "
                f"{elapsed:.0f}s (threshold={STUCK_THRESHOLD_SECONDS}s), marking failed"
            )
            print(
                f"[{datetime.now().isoformat()}] Action message {msg_id} stuck, marking failed"
            )
            state.setdefault("failed", []).append(msg_id)
            del state["processing"][msg_id]

            # Post error notification to human inbox
            orig = next((m for m in messages if m.get("id") == msg_id), None)
            if orig:
                try:
                    sdk.messages.create(
                        task_id=orig.get("task_id", ""),
                        from_actor="agent",
                        to_actor="human",
                        type="worker_result",
                        content=(
                            f"Action failed (stuck/timeout after {elapsed:.0f}s): "
                            f"{orig.get('content', '')[:200]}"
                        ),
                    )
                except Exception as e:
                    debug_log(f"dispatch_action_messages: failed to post stuck error: {e}")

    _save_state(state)

    # Recompute sets after stuck-message cleanup
    done_ids = set(state.get("done", []))
    failed_ids = set(state.get("failed", []))
    processing_ids = set(state.get("processing", {}).keys())

    # --- Dispatch one message per tick ---
    for message in messages:
        msg_id = message.get("id")
        if not msg_id:
            continue

        # Skip already processed or currently in-progress messages
        if msg_id in done_ids or msg_id in failed_ids or msg_id in processing_ids:
            continue

        content = message.get("content", "")
        print(
            f"[{datetime.now().isoformat()}] Dispatching action agent for "
            f"message {msg_id}: {content[:80]}"
        )
        debug_log(f"dispatch_action_messages: processing message {msg_id}: {content[:80]}")

        # Mark as processing before spawning (crash recovery)
        state.setdefault("processing", {})[msg_id] = {
            "started_at": now.isoformat(),
            "content": content[:200],
        }
        _save_state(state)

        # Build prompt and run agent synchronously
        prompt = _build_agent_prompt(message)
        success, result_text = _run_action_agent(prompt, timeout=AGENT_TIMEOUT_SECONDS)

        # Remove from processing regardless of outcome
        state.get("processing", {}).pop(msg_id, None)

        if success:
            state.setdefault("done", []).append(msg_id)
            print(f"[{datetime.now().isoformat()}] Action message {msg_id} completed")
            debug_log(f"dispatch_action_messages: message {msg_id} done")

            # Post worker_result to human inbox
            try:
                sdk.messages.create(
                    task_id=message.get("task_id", ""),
                    from_actor="agent",
                    to_actor="human",
                    type="worker_result",
                    content=result_text or f"Action completed: {content[:100]}",
                )
            except Exception as e:
                debug_log(f"dispatch_action_messages: failed to post worker_result: {e}")

        else:
            state.setdefault("failed", []).append(msg_id)
            print(
                f"[{datetime.now().isoformat()}] Action message {msg_id} failed: "
                f"{result_text[:100]}"
            )
            debug_log(f"dispatch_action_messages: message {msg_id} failed: {result_text}")

            # Post error notification to human inbox
            try:
                sdk.messages.create(
                    task_id=message.get("task_id", ""),
                    from_actor="agent",
                    to_actor="human",
                    type="worker_result",
                    content=f"Action failed: {result_text[:500]}",
                )
            except Exception as e:
                debug_log(f"dispatch_action_messages: failed to post error message: {e}")

        _save_state(state)
        break  # Serial: one message per tick
