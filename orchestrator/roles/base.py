"""Base class for agent roles."""

import os
import subprocess
import sys
from abc import ABC, abstractmethod
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

    def log(self, message: str) -> None:
        """Log a message with agent prefix."""
        print(f"[{self.agent_name}] {message}", file=sys.stderr)

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

        result = subprocess.run(
            cmd,
            cwd=self.worktree,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
        )

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

    def execute(self) -> int:
        """Execute the role with error handling.

        Returns:
            Exit code
        """
        try:
            self.log(f"Starting {self.agent_role} role")
            exit_code = self.run()
            self.log(f"Completed with exit code {exit_code}")
            return exit_code
        except KeyboardInterrupt:
            self.log("Interrupted")
            return 130
        except Exception as e:
            self.log(f"Error: {e}")
            import traceback

            traceback.print_exc()
            return 1


def main_entry(role_class: type[BaseRole]) -> None:
    """Common entry point for role modules.

    Args:
        role_class: The role class to instantiate and run
    """
    role = role_class()
    exit_code = role.execute()
    sys.exit(exit_code)
