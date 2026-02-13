"""Base class for agent roles."""

import json
import os
import subprocess
import sys
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import message_utils


class BaseRole(ABC):
    """Abstract base class for agent roles."""

    def __init__(self):
        """Initialize role from environment variables."""
        self.agent_name = os.environ.get("AGENT_NAME", "unknown")
        self.agent_id = int(os.environ.get("AGENT_ID", "0"))
        self.agent_role = os.environ.get("AGENT_ROLE", "unknown")
        self.parent_project = Path(os.environ.get("PARENT_PROJECT", "."))
        self.worktree = Path(os.environ.get("WORKTREE", "."))
        self.shared_dir = Path(os.environ.get("SHARED_DIR", "."))
        self.orchestrator_dir = Path(os.environ.get("ORCHESTRATOR_DIR", "."))

        # Port configuration
        self.dev_port = int(os.environ.get("AGENT_DEV_PORT", "41000"))
        self.mcp_port = int(os.environ.get("AGENT_MCP_PORT", "41001"))
        self.pw_ws_port = int(os.environ.get("AGENT_PW_WS_PORT", "41002"))

        # Current task (set by subclasses when working on a task)
        self.current_task_id: str | None = None

        # Debug mode
        self.debug = os.environ.get("ORCHESTRATOR_DEBUG", "").lower() in ("1", "true", "yes")
        self._log_file: Path | None = None
        if self.debug:
            self._setup_debug_logging()

    def _setup_debug_logging(self) -> None:
        """Set up debug logging to a file."""
        logs_dir = self.orchestrator_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Log file per agent per day
        date_str = datetime.now().strftime("%Y-%m-%d")
        self._log_file = logs_dir / f"{self.agent_name}-{date_str}.log"

    def _write_debug(self, level: str, message: str) -> None:
        """Write a debug message to the log file.

        Args:
            level: Log level (DEBUG, INFO, WARN, ERROR)
            message: Message to log
        """
        if not self._log_file:
            return

        timestamp = datetime.now().isoformat()
        log_line = f"[{timestamp}] [{level}] [{self.agent_name}] {message}\n"

        try:
            with open(self._log_file, "a") as f:
                f.write(log_line)
        except OSError:
            pass  # Don't fail if we can't write logs

    def debug_log(self, message: str) -> None:
        """Log a debug message (only when debug mode is enabled).

        Args:
            message: Message to log
        """
        if self.debug:
            self._write_debug("DEBUG", message)

    def log(self, message: str) -> None:
        """Log a message with agent prefix."""
        print(f"[{self.agent_name}] {message}", file=sys.stderr)
        if self.debug:
            self._write_debug("INFO", message)

    # Message helpers for agent-to-human communication
    def send_info(self, title: str, body: str) -> Path:
        """Send an info message to the user."""
        return message_utils.info(title, body, self.agent_name, self.current_task_id)

    def send_warning(self, title: str, body: str) -> Path:
        """Send a warning message to the user."""
        return message_utils.warning(title, body, self.agent_name, self.current_task_id)

    def send_error(self, title: str, body: str) -> Path:
        """Send an error message to the user."""
        return message_utils.error(title, body, self.agent_name, self.current_task_id)

    def send_question(self, title: str, body: str) -> Path:
        """Send a question to the user (agent needs human input)."""
        return message_utils.question(title, body, self.agent_name, self.current_task_id)

    def _build_claude_env(self) -> dict[str, str]:
        """Build environment dict for Claude Code subprocess.

        Inherits the current process environment and ensures agent-specific
        variables are set. This makes AGENT_NAME (and related vars) available
        to Claude Code hooks, which run as shell commands spawned by Claude.

        Returns:
            Environment dict for subprocess calls
        """
        env = os.environ.copy()
        env["AGENT_NAME"] = self.agent_name
        env["AGENT_ID"] = str(self.agent_id)
        env["AGENT_ROLE"] = self.agent_role
        env["PARENT_PROJECT"] = str(self.parent_project)
        env["WORKTREE"] = str(self.worktree)
        env["SHARED_DIR"] = str(self.shared_dir)
        env["ORCHESTRATOR_DIR"] = str(self.orchestrator_dir)
        if self.current_task_id:
            env["CURRENT_TASK_ID"] = self.current_task_id
        return env

    def invoke_claude(
        self,
        prompt: str,
        allowed_tools: list[str] | None = None,
        max_turns: int | None = None,
        output_format: str = "text",
        stdout_log: Path | None = None,
    ) -> tuple[int, str, str]:
        """Invoke Claude Code CLI with a prompt.

        Args:
            prompt: The prompt to send to Claude
            allowed_tools: List of allowed tools (e.g., ["Read", "Write", "Bash"])
            max_turns: Maximum number of turns before stopping
            output_format: Output format ("text", "json", "stream-json")
            stdout_log: If provided, stream stdout to this file in real-time.
                        The file captures output incrementally so it survives
                        timeouts and crashes.

        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        cmd = ["claude", "-p", prompt]

        if allowed_tools:
            cmd.extend(["--allowedTools", ",".join(allowed_tools)])

        if max_turns:
            cmd.extend(["--max-turns", str(max_turns)])

        if output_format != "text":
            cmd.extend(["--output-format", output_format])

        env = self._build_claude_env()

        self.log(f"Invoking Claude: {' '.join(cmd[:5])}...")
        self.debug_log(f"Full command: {cmd}")
        self.debug_log(f"Working directory: {self.worktree}")
        self.debug_log(f"Allowed tools: {allowed_tools}")
        self.debug_log(f"Max turns: {max_turns}")
        self.debug_log(f"Prompt length: {len(prompt)} chars")

        if stdout_log:
            # Stream stdout to file in real-time, so output survives crashes
            stdout_log.parent.mkdir(parents=True, exist_ok=True)
            return self._invoke_with_streaming(cmd, stdout_log, env)
        else:
            result = subprocess.run(
                cmd,
                cwd=self.worktree,
                env=env,
                capture_output=True,
                text=True,
                timeout=3600,  # 60 minute timeout
            )

            self.debug_log(f"Claude exit code: {result.returncode}")
            self.debug_log(f"Stdout length: {len(result.stdout)} chars")
            self.debug_log(f"Stderr length: {len(result.stderr)} chars")
            if result.returncode != 0:
                self.debug_log(f"Stderr: {result.stderr[:1000]}")

            return result.returncode, result.stdout, result.stderr

    def _invoke_with_streaming(
        self, cmd: list[str], stdout_log: Path, env: dict[str, str] | None = None
    ) -> tuple[int, str, str]:
        """Invoke Claude with stdout streamed to a log file.

        Uses Popen so output is written incrementally. If the process
        is killed or times out, whatever was written so far is preserved.

        Args:
            cmd: Command to run
            stdout_log: Path to write stdout to

        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        import threading

        proc = subprocess.Popen(
            cmd,
            cwd=self.worktree,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def read_stdout():
            with open(stdout_log, "w") as log:
                for line in proc.stdout:
                    log.write(line)
                    log.flush()
                    stdout_chunks.append(line)

        def read_stderr():
            for line in proc.stderr:
                stderr_chunks.append(line)

        t_out = threading.Thread(target=read_stdout, daemon=True)
        t_err = threading.Thread(target=read_stderr, daemon=True)
        t_out.start()
        t_err.start()

        try:
            proc.wait(timeout=3600)  # 60 minute timeout
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            self.log("Claude process timed out after 60 minutes")

        t_out.join(timeout=5)
        t_err.join(timeout=5)

        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)

        self.debug_log(f"Claude exit code: {proc.returncode}")
        self.debug_log(f"Stdout length: {len(stdout)} chars")
        self.debug_log(f"Stderr length: {len(stderr)} chars")
        if proc.returncode != 0:
            self.debug_log(f"Stderr: {stderr[:1000]}")

        return proc.returncode, stdout, stderr

    def get_tool_counter_path(self) -> Path:
        """Get path to the tool counter file for this agent.

        Returns:
            Path to .orchestrator/agents/<name>/tool_counter
        """
        return self.orchestrator_dir / "agents" / self.agent_name / "tool_counter"

    def read_tool_count(self) -> int | None:
        """Read the tool call count from the counter file.

        The PostToolUse hook appends one byte per tool call.
        File size in bytes = number of tool calls.

        Returns:
            Number of tool calls, or None if counter file doesn't exist
        """
        counter_path = self.get_tool_counter_path()
        try:
            return counter_path.stat().st_size
        except FileNotFoundError:
            return None

    def reset_tool_counter(self) -> None:
        """Reset the tool counter file by truncating it.

        Called when claiming a new task to start counting from zero.
        """
        counter_path = self.get_tool_counter_path()
        try:
            counter_path.parent.mkdir(parents=True, exist_ok=True)
            counter_path.write_bytes(b"")
            self.debug_log(f"Reset tool counter: {counter_path}")
        except OSError as e:
            self.debug_log(f"Failed to reset tool counter: {e}")

    def read_instructions(self) -> str:
        """Read the agent instructions file.

        Returns:
            Contents of .agent-instructions.md or empty string
        """
        instructions_path = self.worktree / ".agent-instructions.md"
        if instructions_path.exists():
            return instructions_path.read_text()
        return ""

    def get_queue_dir(self, subdir: str) -> Path:
        """Get a queue subdirectory path.

        Args:
            subdir: One of 'incoming', 'claimed', 'done', 'failed'

        Returns:
            Path to the queue subdirectory
        """
        return self.shared_dir / "queue" / subdir

    def _generate_execution_notes(self, **kwargs) -> str:
        """Generate a concise execution summary for the task.

        This is a base implementation that subclasses can override.
        Subclasses should extract relevant information from kwargs.

        Args:
            **kwargs: Role-specific information (e.g., commits_made, turns_used, stdout)

        Returns:
            Concise summary string for execution_notes field
        """
        parts = []

        # Extract common fields
        turns_used = kwargs.get("turns_used")
        if turns_used is not None:
            parts.append(f"Used {turns_used} turns")

        # Try to extract summary from stdout if provided
        stdout = kwargs.get("stdout", "")
        if stdout:
            # Get last 300 chars for summary extraction
            tail = stdout[-300:] if len(stdout) > 300 else stdout
            lines = tail.strip().split('\n')

            # Look for the last non-empty line as a summary
            for line in reversed(lines):
                line = line.strip()
                if line and len(line) > 15 and len(line) < 150:
                    parts.append(line)
                    break

        # If we have something, join it
        if parts:
            notes = ". ".join(parts) + "."
            # Truncate if too long
            if len(notes) > 450:
                notes = notes[:450] + "..."
            return notes

        # Default fallback
        return f"{self.agent_role.capitalize()} task completed"

    @abstractmethod
    def run(self) -> int:
        """Execute the role's main logic.

        Returns:
            Exit code (0 for success, non-zero for failure)
        """
        pass

    def _write_exit_code(self, exit_code: int) -> None:
        """Write exit code to a file for the scheduler to read.

        Args:
            exit_code: The exit code to write
        """
        exit_code_path = self.orchestrator_dir / "agents" / self.agent_name / "exit_code"
        try:
            exit_code_path.parent.mkdir(parents=True, exist_ok=True)
            exit_code_path.write_text(str(exit_code))
            self.debug_log(f"Wrote exit code {exit_code} to {exit_code_path}")
        except OSError as e:
            self.debug_log(f"Failed to write exit code: {e}")

    def _get_state_file_path(self) -> Path:
        """Get path to this agent's state file."""
        return self.orchestrator_dir / "agents" / self.agent_name / "state.json"

    def _update_state(self, **kwargs) -> None:
        """Update agent state file with given values.

        This allows the agent to update its own state, rather than relying
        solely on the scheduler. Useful for setting running=false on exit.

        Args:
            **kwargs: Fields to update in state.json
        """
        state_path = self._get_state_file_path()
        try:
            # Read existing state
            if state_path.exists():
                state = json.loads(state_path.read_text())
            else:
                state = {}

            # Update with new values
            state.update(kwargs)

            # Write back
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state, indent=2))
            self.debug_log(f"Updated state: {kwargs}")
        except (IOError, json.JSONDecodeError) as e:
            self.debug_log(f"Failed to update state: {e}")

    def _mark_stopped(self, exit_code: int) -> None:
        """Mark agent as stopped in state file.

        Called on exit to ensure state.json shows running=false,
        preventing stale state issues.

        Args:
            exit_code: The exit code of the agent
        """
        self._update_state(
            running=False,
            pid=None,
            last_finished=datetime.now().isoformat(),
            last_exit_code=exit_code,
        )

    def execute(self) -> int:
        """Execute the role with error handling.

        Returns:
            Exit code
        """
        exit_code = 1  # Default to failure
        try:
            self.log(f"Starting {self.agent_role} role")
            self.debug_log(f"Agent ID: {self.agent_id}")
            self.debug_log(f"Parent project: {self.parent_project}")
            self.debug_log(f"Worktree: {self.worktree}")
            self.debug_log(f"Shared dir: {self.shared_dir}")
            self.debug_log(f"Ports: dev={self.dev_port}, mcp={self.mcp_port}, pw={self.pw_ws_port}")

            exit_code = self.run()

            self.log(f"Completed with exit code {exit_code}")
            self.debug_log(f"Role execution finished: exit_code={exit_code}")
        except KeyboardInterrupt:
            self.log("Interrupted")
            self.debug_log("Role interrupted by keyboard")
            exit_code = 130
        except Exception as e:
            self.log(f"Error: {e}")
            self.debug_log(f"Role exception: {type(e).__name__}: {e}")
            import traceback

            tb_str = traceback.format_exc()
            self.debug_log(f"Traceback:\n{tb_str}")
            traceback.print_exc()
            exit_code = 1
        finally:
            # Agent-owned cleanup: mark ourselves as stopped
            # This ensures state.json is accurate even if scheduler doesn't tick
            self._mark_stopped(exit_code)

            # Also write exit code for backward compatibility
            self._write_exit_code(exit_code)

        return exit_code


def main_entry(role_class: type[BaseRole]) -> None:
    """Common entry point for role modules.

    Args:
        role_class: The role class to instantiate and run
    """
    role = role_class()
    exit_code = role.execute()
    sys.exit(exit_code)
