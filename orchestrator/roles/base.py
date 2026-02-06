"""Base class for agent roles."""

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

    def invoke_claude(
        self,
        prompt: str,
        allowed_tools: list[str] | None = None,
        max_turns: int | None = None,
        output_format: str = "text",
    ) -> tuple[int, str, str]:
        """Invoke Claude Code CLI with a prompt.

        Args:
            prompt: The prompt to send to Claude
            allowed_tools: List of allowed tools (e.g., ["Read", "Write", "Bash"])
            max_turns: Maximum number of turns before stopping
            output_format: Output format ("text", "json", "stream-json")

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

        self.log(f"Invoking Claude: {' '.join(cmd[:5])}...")
        self.debug_log(f"Full command: {cmd}")
        self.debug_log(f"Working directory: {self.worktree}")
        self.debug_log(f"Allowed tools: {allowed_tools}")
        self.debug_log(f"Max turns: {max_turns}")
        self.debug_log(f"Prompt length: {len(prompt)} chars")

        result = subprocess.run(
            cmd,
            cwd=self.worktree,
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
            # Always write exit code for scheduler to read
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
