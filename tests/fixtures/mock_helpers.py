"""Helper functions for running mock agents in tests."""

import os
import subprocess
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent
MOCK_AGENT = FIXTURES_DIR / "mock-agent.sh"
FAKE_GH_BIN = FIXTURES_DIR / "bin"


def run_mock_agent(
    task_dir: Path,
    agent_env: dict = None,
    gh_env: dict = None,
) -> subprocess.CompletedProcess:
    """Run the mock agent script against a task directory.

    Sets up environment variables for TASK_DIR and TASK_WORKTREE, applies
    MOCK_* vars from agent_env, GH_MOCK_* vars from gh_env, and prepends
    tests/fixtures/bin/ to PATH so the fake gh CLI is used.

    Args:
        task_dir: Path to the task directory (must contain a worktree/ subdirectory).
        agent_env: Optional dict of MOCK_* vars controlling agent behaviour
                   (e.g. MOCK_OUTCOME, MOCK_CRASH, MOCK_COMMITS).
        gh_env: Optional dict of GH_MOCK_* vars controlling fake gh behaviour
                (e.g. GH_MOCK_PR_NUMBER, GH_MOCK_MERGE_FAIL).

    Returns:
        subprocess.CompletedProcess with returncode, stdout, stderr.
    """
    worktree = task_dir / "worktree"

    env = {**os.environ}

    # Required scheduler vars
    env["TASK_DIR"] = str(task_dir)
    env["TASK_WORKTREE"] = str(worktree)

    # Prepend fake gh bin dir so the real gh is never called
    env["PATH"] = f"{FAKE_GH_BIN}:{env.get('PATH', '')}"

    if agent_env:
        env.update(agent_env)

    if gh_env:
        env.update(gh_env)

    return subprocess.run(
        [str(MOCK_AGENT)],
        env=env,
        capture_output=True,
        text=True,
    )
