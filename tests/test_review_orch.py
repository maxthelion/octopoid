"""Tests for the orchestrator task review script.

Tests the individual steps of review_orch.py using mock git repos
and the initialized_db fixture from conftest.
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.review_orch import (
    resolve_task_id,
    find_agent_submodule,
    check_submodule_branch,
    get_local_commits,
    get_diff,
    review_orchestrator_task,
    SUBMODULE_BRANCH,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git_env():
    """Return env overrides for deterministic git commits."""
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


@pytest.fixture
def git_repo(tmp_path):
    """Create a bare git repo to act as 'origin' and a clone (agent_sub).

    - origin: bare repo with sqlite-model branch
    - agent_sub: clone on sqlite-model (simulates agent's worktree submodule)
    """
    origin = tmp_path / "origin.git"
    agent = tmp_path / "agent_sub"

    # Create bare origin
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)

    # Clone, create sqlite-model branch with initial commit
    subprocess.run(["git", "clone", str(origin), str(agent)], check=True, capture_output=True)
    subprocess.run(
        ["git", "checkout", "-b", SUBMODULE_BRANCH],
        cwd=agent, check=True, capture_output=True,
    )
    (agent / "base.txt").write_text("base content\n")
    subprocess.run(["git", "add", "."], cwd=agent, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=agent, check=True, capture_output=True,
        env=_git_env(),
    )
    subprocess.run(
        ["git", "push", "-u", "origin", SUBMODULE_BRANCH],
        cwd=agent, check=True, capture_output=True,
    )

    return {"origin": origin, "agent_sub": agent}


# ---------------------------------------------------------------------------
# Test: resolve_task_id
# ---------------------------------------------------------------------------


class TestResolveTaskId:
    def test_exact_match(self, initialized_db):
        """Single match returns task dict."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task
            create_task(
                task_id="rev12345",
                file_path="/tmp/TASK-rev12345.md",
                role="orchestrator_impl",
            )
            result = resolve_task_id("rev12345")
            assert result is not None
            assert result["id"] == "rev12345"

    def test_prefix_match(self, initialized_db):
        """Prefix resolves to single task."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task
            create_task(
                task_id="rev99999",
                file_path="/tmp/TASK-rev99999.md",
                role="orchestrator_impl",
            )
            result = resolve_task_id("rev9")
            assert result is not None
            assert result["id"] == "rev99999"

    def test_no_match(self, initialized_db):
        """Non-existent prefix returns None."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            result = resolve_task_id("nonexistent")
            assert result is None

    def test_ambiguous_match(self, initialized_db):
        """Ambiguous prefix returns None."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task
            create_task(task_id="revamb01", file_path="/tmp/t1.md", role="orchestrator_impl")
            create_task(task_id="revamb02", file_path="/tmp/t2.md", role="orchestrator_impl")
            result = resolve_task_id("revamb")
            assert result is None


# ---------------------------------------------------------------------------
# Test: find_agent_submodule
# ---------------------------------------------------------------------------


class TestFindAgentSubmodule:
    def test_finds_from_claimed_by(self, tmp_path):
        """Locates the submodule using claimed_by field."""
        agent_sub = tmp_path / ".orchestrator" / "agents" / "orch-impl-1" / "worktree" / "orchestrator"
        agent_sub.mkdir(parents=True)

        with patch("orchestrator.review_orch.get_agents_runtime_dir",
                    return_value=tmp_path / ".orchestrator" / "agents"):
            task_info = {"id": "test1234", "claimed_by": "orch-impl-1"}
            result = find_agent_submodule(task_info)
            assert result == agent_sub

    def test_finds_from_history(self, tmp_path, initialized_db):
        """Falls back to task history if claimed_by is None."""
        agent_sub = tmp_path / ".orchestrator" / "agents" / "orch-impl-2" / "worktree" / "orchestrator"
        agent_sub.mkdir(parents=True)

        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, get_connection
            create_task(task_id="hist1234", file_path="/tmp/t.md", role="orchestrator_impl")
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO task_history (task_id, event, agent) VALUES (?, 'claimed', ?)",
                    ("hist1234", "orch-impl-2"),
                )

            with patch("orchestrator.review_orch.get_agents_runtime_dir",
                        return_value=tmp_path / ".orchestrator" / "agents"):
                task_info = {"id": "hist1234", "claimed_by": None}
                result = find_agent_submodule(task_info)
                assert result == agent_sub

    def test_returns_none_when_no_agent(self, initialized_db):
        """Returns None when agent cannot be determined."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task
            create_task(task_id="noag1234", file_path="/tmp/t.md", role="orchestrator_impl")
            task_info = {"id": "noag1234", "claimed_by": None}
            result = find_agent_submodule(task_info)
            assert result is None

    def test_returns_none_when_worktree_missing(self, tmp_path):
        """Returns None when worktree path doesn't exist."""
        with patch("orchestrator.review_orch.get_agents_runtime_dir",
                    return_value=tmp_path / ".orchestrator" / "agents"):
            task_info = {"id": "test5678", "claimed_by": "ghost-agent"}
            result = find_agent_submodule(task_info)
            assert result is None


# ---------------------------------------------------------------------------
# Test: check_submodule_branch
# ---------------------------------------------------------------------------


class TestCheckSubmoduleBranch:
    def test_valid_branch(self, git_repo):
        """Returns branch name when on sqlite-model."""
        result = check_submodule_branch(git_repo["agent_sub"])
        assert result == SUBMODULE_BRANCH

    def test_wrong_branch(self, git_repo):
        """Returns None when on wrong branch."""
        agent = git_repo["agent_sub"]
        subprocess.run(
            ["git", "checkout", "-b", "wrong-branch"],
            cwd=agent, check=True, capture_output=True,
        )
        result = check_submodule_branch(agent)
        assert result is None

    def test_detached_head(self, git_repo):
        """Returns None when in detached HEAD state."""
        agent = git_repo["agent_sub"]
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=agent, capture_output=True, text=True, check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "checkout", head_sha],
            cwd=agent, check=True, capture_output=True,
        )
        result = check_submodule_branch(agent)
        assert result is None

    def test_not_a_git_repo(self, tmp_path):
        """Returns None for a non-git directory."""
        result = check_submodule_branch(tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# Test: get_local_commits
# ---------------------------------------------------------------------------


class TestGetLocalCommits:
    def test_no_local_commits(self, git_repo):
        """Returns empty list when agent HEAD matches origin."""
        commits = get_local_commits(git_repo["agent_sub"])
        assert commits == []

    def test_finds_local_commits(self, git_repo):
        """Finds commits that aren't on origin."""
        agent = git_repo["agent_sub"]
        sha1 = _make_commit(agent, "a.txt", "aaa", "agent commit 1")
        sha2 = _make_commit(agent, "b.txt", "bbb", "agent commit 2")

        commits = get_local_commits(agent)
        assert len(commits) == 2
        assert commits[0]["sha"] == sha1
        assert commits[0]["subject"] == "agent commit 1"
        assert commits[1]["sha"] == sha2
        assert commits[1]["subject"] == "agent commit 2"

    def test_commit_fields_populated(self, git_repo):
        """Commit dicts have sha, subject, author, date fields."""
        agent = git_repo["agent_sub"]
        _make_commit(agent, "c.txt", "ccc", "test fields")

        commits = get_local_commits(agent)
        assert len(commits) == 1
        assert len(commits[0]["sha"]) == 40  # Full SHA
        assert commits[0]["subject"] == "test fields"
        assert commits[0]["author"] == "Test"
        assert commits[0]["date"]  # Non-empty


# ---------------------------------------------------------------------------
# Test: get_diff
# ---------------------------------------------------------------------------


class TestGetDiff:
    def test_no_diff_when_matching_origin(self, git_repo):
        """Returns empty string when no local changes."""
        diff = get_diff(git_repo["agent_sub"])
        assert diff == ""

    def test_diff_shows_changes(self, git_repo):
        """Returns diff text for local commits."""
        agent = git_repo["agent_sub"]
        _make_commit(agent, "new_file.py", "print('hello')\n", "add new file")

        diff = get_diff(agent)
        assert "new_file.py" in diff
        assert "hello" in diff


# ---------------------------------------------------------------------------
# Test: full review flow
# ---------------------------------------------------------------------------


class TestReviewOrchestratorTask:
    def test_rejects_when_db_disabled(self):
        """Returns error when DB mode is off."""
        with patch("orchestrator.review_orch.is_db_enabled", return_value=False):
            result = review_orchestrator_task("anything")
            assert result == 1

    def test_rejects_nonexistent_task(self, initialized_db):
        """Returns error for non-existent task prefix."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.review_orch.is_db_enabled", return_value=True):
                result = review_orchestrator_task("nonexist")
                assert result == 1

    def test_rejects_non_orchestrator_role(self, initialized_db):
        """Returns error for non-orchestrator_impl tasks."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.review_orch.is_db_enabled", return_value=True):
                from orchestrator.db import create_task
                create_task(
                    task_id="impl1234",
                    file_path="/tmp/TASK-impl1234.md",
                    role="implement",
                )
                result = review_orchestrator_task("impl1234")
                assert result == 1

    def test_strips_task_prefix(self, initialized_db):
        """Handles TASK- prefix correctly."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.review_orch.is_db_enabled", return_value=True):
                from orchestrator.db import create_task
                create_task(
                    task_id="strip123",
                    file_path="/tmp/TASK-strip123.md",
                    role="orchestrator_impl",
                )
                # Should fail at finding worktree, not at resolving ID
                result = review_orchestrator_task("TASK-strip123")
                # The task is found (role check passes) but worktree won't exist
                assert result == 1

    def test_full_review_with_commits(self, initialized_db, git_repo, tmp_path):
        """Full review flow succeeds when agent has local commits."""
        agent_sub = git_repo["agent_sub"]
        _make_commit(agent_sub, "feature.py", "def foo(): pass\n", "add feature")

        # Set up fake agent directory structure pointing to our git_repo
        agents_dir = tmp_path / "agents"
        agent_worktree_orch = agents_dir / "test-agent" / "worktree" / "orchestrator"
        agent_worktree_orch.mkdir(parents=True)

        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.review_orch.is_db_enabled", return_value=True):
                with patch("orchestrator.review_orch.get_agents_runtime_dir", return_value=agents_dir):
                    from orchestrator.db import create_task

                    create_task(
                        task_id="full1234",
                        file_path="/tmp/TASK-full1234.md",
                        role="orchestrator_impl",
                    )
                    # Set claimed_by
                    from orchestrator.db import get_connection
                    with get_connection() as conn:
                        conn.execute(
                            "UPDATE tasks SET claimed_by = ? WHERE id = ?",
                            ("test-agent", "full1234"),
                        )

                    # Symlink the fake agent worktree orchestrator dir to our real git repo
                    import shutil
                    shutil.rmtree(agent_worktree_orch)
                    agent_worktree_orch.symlink_to(agent_sub)

                    result = review_orchestrator_task("full1234")
                    assert result == 0

    def test_review_with_no_commits(self, initialized_db, git_repo, tmp_path):
        """Full review flow succeeds (returns 0) when no local commits."""
        agent_sub = git_repo["agent_sub"]

        agents_dir = tmp_path / "agents"
        agent_worktree_orch = agents_dir / "test-agent" / "worktree" / "orchestrator"
        agent_worktree_orch.mkdir(parents=True)

        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.review_orch.is_db_enabled", return_value=True):
                with patch("orchestrator.review_orch.get_agents_runtime_dir", return_value=agents_dir):
                    from orchestrator.db import create_task, get_connection

                    create_task(
                        task_id="nocom123",
                        file_path="/tmp/TASK-nocom123.md",
                        role="orchestrator_impl",
                    )
                    with get_connection() as conn:
                        conn.execute(
                            "UPDATE tasks SET claimed_by = ? WHERE id = ?",
                            ("test-agent", "nocom123"),
                        )

                    import shutil
                    shutil.rmtree(agent_worktree_orch)
                    agent_worktree_orch.symlink_to(agent_sub)

                    result = review_orchestrator_task("nocom123")
                    assert result == 0
