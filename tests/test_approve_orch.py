"""Tests for the orchestrator task approval automation.

Tests the individual steps of approve_orch.py using mock git repos
to simulate the agent worktree submodule scenario.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from orchestrator.approve_orch import (
    resolve_task_id,
    find_agent_submodule,
    find_agent_commits,
    cherry_pick_commits,
    run_tests,
    push_submodule,
    accept_in_db,
    approve_orchestrator_task,
    SUBMODULE_BRANCH,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def git_repo(tmp_path):
    """Create a bare git repo to act as 'origin' and two clones:
    - local_sub: the main submodule (on sqlite-model)
    - agent_sub: the agent's worktree submodule (on sqlite-model)

    Returns a dict with paths and helpers.
    """
    origin = tmp_path / "origin.git"
    local = tmp_path / "local"
    agent = tmp_path / "agent"

    # Create bare origin
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)

    # Clone to local, create sqlite-model branch with initial commit
    subprocess.run(["git", "clone", str(origin), str(local)], check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", SUBMODULE_BRANCH], cwd=local, check=True, capture_output=True)
    (local / "base.txt").write_text("base content\n")
    subprocess.run(["git", "add", "."], cwd=local, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=local, check=True, capture_output=True,
        env=_git_env(),
    )
    subprocess.run(
        ["git", "push", "-u", "origin", SUBMODULE_BRANCH],
        cwd=local, check=True, capture_output=True,
    )

    # Clone to agent
    subprocess.run(["git", "clone", str(origin), str(agent)], check=True, capture_output=True)
    subprocess.run(["git", "checkout", SUBMODULE_BRANCH], cwd=agent, check=True, capture_output=True)

    return {
        "origin": origin,
        "local": local,
        "agent": agent,
    }


def _git_env():
    """Return env overrides for deterministic git commits."""
    import os
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = "test@test.com"
    env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_COMMITTER_EMAIL"] = "test@test.com"
    return env


def _make_commit(repo: Path, filename: str, content: str, message: str) -> str:
    """Create a file, stage, commit, return the SHA."""
    (repo / filename).write_text(content)
    subprocess.run(["git", "add", filename], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo, check=True, capture_output=True,
        env=_git_env(),
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Test: resolve_task_id
# ---------------------------------------------------------------------------


class TestResolveTaskId:
    def test_exact_match(self, initialized_db):
        """Single match returns task dict."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task
            create_task(
                task_id="abc12345",
                file_path="/tmp/TASK-abc12345.md",
                role="orchestrator_impl",
            )
            result = resolve_task_id("abc12345")
            assert result is not None
            assert result["id"] == "abc12345"
            assert result["role"] == "orchestrator_impl"

    def test_prefix_match(self, initialized_db):
        """Prefix resolves to single task."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task
            create_task(
                task_id="xyz99999",
                file_path="/tmp/TASK-xyz99999.md",
                role="orchestrator_impl",
            )
            result = resolve_task_id("xyz9")
            assert result is not None
            assert result["id"] == "xyz99999"

    def test_no_match(self, initialized_db):
        """Non-existent prefix returns None."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            result = resolve_task_id("nonexistent")
            assert result is None

    def test_ambiguous_match(self, initialized_db):
        """Ambiguous prefix returns None."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task
            create_task(task_id="amb00001", file_path="/tmp/TASK-amb00001.md", role="orchestrator_impl")
            create_task(task_id="amb00002", file_path="/tmp/TASK-amb00002.md", role="orchestrator_impl")
            result = resolve_task_id("amb0")
            assert result is None


# ---------------------------------------------------------------------------
# Test: find_agent_submodule
# ---------------------------------------------------------------------------


class TestFindAgentSubmodule:
    def test_finds_from_claimed_by(self, tmp_path):
        """Locates the submodule using claimed_by field."""
        # Create a fake agent worktree structure
        agent_sub = tmp_path / ".orchestrator" / "agents" / "orch-impl-1" / "worktree" / "orchestrator"
        agent_sub.mkdir(parents=True)

        with patch("orchestrator.approve_orch.get_agents_runtime_dir", return_value=tmp_path / ".orchestrator" / "agents"):
            task_info = {"id": "test1234", "claimed_by": "orch-impl-1"}
            result = find_agent_submodule(task_info)
            assert result == agent_sub

    def test_finds_from_history_when_claimed_by_empty(self, tmp_path, initialized_db):
        """Falls back to task history if claimed_by is None."""
        agent_sub = tmp_path / ".orchestrator" / "agents" / "orch-impl-2" / "worktree" / "orchestrator"
        agent_sub.mkdir(parents=True)

        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, get_connection
            create_task(task_id="hist1234", file_path="/tmp/TASK-hist1234.md", role="orchestrator_impl")
            # Manually add history event
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO task_history (task_id, event, agent) VALUES (?, 'claimed', ?)",
                    ("hist1234", "orch-impl-2"),
                )

            with patch("orchestrator.approve_orch.get_agents_runtime_dir", return_value=tmp_path / ".orchestrator" / "agents"):
                task_info = {"id": "hist1234", "claimed_by": None}
                result = find_agent_submodule(task_info)
                assert result == agent_sub

    def test_returns_none_when_no_agent(self, initialized_db):
        """Returns None when agent cannot be determined."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task
            create_task(task_id="noag1234", file_path="/tmp/TASK-noag1234.md", role="orchestrator_impl")
            task_info = {"id": "noag1234", "claimed_by": None}
            result = find_agent_submodule(task_info)
            assert result is None

    def test_returns_none_when_worktree_missing(self, tmp_path):
        """Returns None when worktree path doesn't exist."""
        with patch("orchestrator.approve_orch.get_agents_runtime_dir", return_value=tmp_path / ".orchestrator" / "agents"):
            task_info = {"id": "test5678", "claimed_by": "ghost-agent"}
            result = find_agent_submodule(task_info)
            assert result is None


# ---------------------------------------------------------------------------
# Test: find_agent_commits
# ---------------------------------------------------------------------------


class TestFindAgentCommits:
    def test_finds_new_commits(self, git_repo):
        """Detects commits in agent that are not in local."""
        agent = git_repo["agent"]
        local = git_repo["local"]

        # Make two commits in agent
        sha1 = _make_commit(agent, "a.txt", "aaa", "agent commit 1")
        sha2 = _make_commit(agent, "b.txt", "bbb", "agent commit 2")

        commits = find_agent_commits(agent, local)
        assert len(commits) == 2
        assert sha1 in commits
        assert sha2 in commits

    def test_no_new_commits(self, git_repo):
        """Returns empty list when agent HEAD equals local HEAD."""
        agent = git_repo["agent"]
        local = git_repo["local"]

        commits = find_agent_commits(agent, local)
        assert commits == []

    def test_handles_diverged_histories(self, git_repo):
        """Finds agent commits even when local has advanced too."""
        agent = git_repo["agent"]
        local = git_repo["local"]

        # Local advances
        _make_commit(local, "local.txt", "local work", "local commit")

        # Agent advances (based on the older state)
        agent_sha = _make_commit(agent, "agent.txt", "agent work", "agent commit")

        commits = find_agent_commits(agent, local)
        assert len(commits) == 1
        assert agent_sha in commits


# ---------------------------------------------------------------------------
# Test: cherry_pick_commits
# ---------------------------------------------------------------------------


class TestCherryPickCommits:
    def test_successful_cherry_pick(self, git_repo):
        """Cherry-picks non-conflicting commits."""
        agent = git_repo["agent"]
        local = git_repo["local"]

        sha = _make_commit(agent, "feature.txt", "new feature", "add feature")

        # Fetch from agent so commits are available
        subprocess.run(
            ["git", "fetch", str(agent), SUBMODULE_BRANCH],
            cwd=local, check=True, capture_output=True,
        )

        result = cherry_pick_commits([sha], local)
        assert result is True

        # Verify the file exists in local
        assert (local / "feature.txt").read_text() == "new feature"

    def test_conflict_aborts_cleanly(self, git_repo):
        """Conflicting cherry-pick aborts and returns False."""
        agent = git_repo["agent"]
        local = git_repo["local"]

        # Both repos modify the same file
        _make_commit(local, "conflict.txt", "local version", "local edit")
        sha = _make_commit(agent, "conflict.txt", "agent version", "agent edit")

        # Fetch from agent
        subprocess.run(
            ["git", "fetch", str(agent), SUBMODULE_BRANCH],
            cwd=local, check=True, capture_output=True,
        )

        result = cherry_pick_commits([sha], local)
        assert result is False

        # Verify working tree is clean (cherry-pick aborted)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=local, capture_output=True, text=True,
        )
        assert status.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Test: run_tests
# ---------------------------------------------------------------------------


class TestRunTests:
    def test_returns_true_when_no_venv(self, tmp_path):
        """Returns True (with warning) when no venv exists."""
        with patch("orchestrator.approve_orch._repo_root", return_value=tmp_path):
            result = run_tests(tmp_path)
            assert result is True

    def test_returns_true_on_passing_tests(self, tmp_path):
        """Returns True when pytest passes."""
        # Create a fake venv with a mock python
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        fake_python = venv_bin / "python"
        fake_python.write_text("#!/bin/bash\necho '1 passed'\nexit 0\n")
        fake_python.chmod(0o755)

        # Create a tests dir
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        result = run_tests(tmp_path)
        assert result is True

    def test_returns_false_on_failing_tests(self, tmp_path):
        """Returns False when pytest fails."""
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        fake_python = venv_bin / "python"
        fake_python.write_text("#!/bin/bash\necho 'FAILED test_foo'\nexit 1\n")
        fake_python.chmod(0o755)

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        result = run_tests(tmp_path)
        assert result is False


# ---------------------------------------------------------------------------
# Test: push_submodule
# ---------------------------------------------------------------------------


class TestPushSubmodule:
    def test_successful_push(self, git_repo):
        """Push succeeds when local is ahead."""
        local = git_repo["local"]
        _make_commit(local, "new.txt", "content", "new commit")

        result = push_submodule(local)
        assert result is True

    def test_push_up_to_date(self, git_repo):
        """Push succeeds when already up to date."""
        local = git_repo["local"]
        result = push_submodule(local)
        assert result is True


# ---------------------------------------------------------------------------
# Test: accept_in_db
# ---------------------------------------------------------------------------


class TestAcceptInDb:
    def test_accepts_task(self, initialized_db):
        """Moves task to done and clears claimed_by."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task
            create_task(
                task_id="acc12345",
                file_path="/tmp/TASK-acc12345.md",
                role="orchestrator_impl",
            )
            update_task("acc12345", queue="provisional", claimed_by="orch-impl-1")

            result = accept_in_db("acc12345")
            assert result is True

            from orchestrator.db import get_task
            task = get_task("acc12345")
            assert task["queue"] == "done"
            assert task["claimed_by"] is None

    def test_fixes_incorrect_state(self, initialized_db):
        """Corrects queue state if accept_completion didn't fully work."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task
            create_task(
                task_id="fix12345",
                file_path="/tmp/TASK-fix12345.md",
                role="orchestrator_impl",
            )
            update_task("fix12345", queue="claimed", claimed_by="orch-impl-1")

            result = accept_in_db("fix12345")
            assert result is True


# ---------------------------------------------------------------------------
# Test: full flow (mocked)
# ---------------------------------------------------------------------------


class TestApproveOrchestratorTask:
    def test_rejects_non_orchestrator_role(self, initialized_db):
        """Returns error for non-orchestrator_impl tasks."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.approve_orch.is_db_enabled", return_value=True):
                from orchestrator.db import create_task
                create_task(
                    task_id="impl1234",
                    file_path="/tmp/TASK-impl1234.md",
                    role="implement",
                )
                update_mock = patch("orchestrator.db.update_task")
                with update_mock:
                    from orchestrator.db import update_task
                    update_task("impl1234", queue="provisional")

                result = approve_orchestrator_task("impl1234")
                assert result == 1

    def test_rejects_wrong_queue(self, initialized_db):
        """Returns error for tasks not in approvable queues."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.approve_orch.is_db_enabled", return_value=True):
                from orchestrator.db import create_task
                create_task(
                    task_id="done1234",
                    file_path="/tmp/TASK-done1234.md",
                    role="orchestrator_impl",
                )
                from orchestrator.db import update_task
                update_task("done1234", queue="done")

                result = approve_orchestrator_task("done1234")
                assert result == 1

    def test_rejects_when_db_disabled(self):
        """Returns error when DB mode is off."""
        with patch("orchestrator.approve_orch.is_db_enabled", return_value=False):
            result = approve_orchestrator_task("anything")
            assert result == 1

    def test_rejects_nonexistent_task(self, initialized_db):
        """Returns error for non-existent task prefix."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.approve_orch.is_db_enabled", return_value=True):
                result = approve_orchestrator_task("nonexist")
                assert result == 1
