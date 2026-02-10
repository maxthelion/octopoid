"""Tests for the orchestrator task approval automation.

Tests the push-to-origin approval flow using mock git repos
to simulate the agent worktree scenario.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from orchestrator.approve_orch import (
    resolve_task_id,
    find_agent_submodule,
    find_submodule_branch,
    find_main_repo_branch,
    count_branch_commits,
    rebase_onto_origin,
    run_tests,
    run_tests_with_baseline,
    parse_test_failures,
    push_to_origin,
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
    - local: the main checkout (human's working tree)
    - agent: the agent's worktree

    Returns a dict with paths and helpers.
    """
    origin = tmp_path / "origin.git"
    local = tmp_path / "local"
    agent = tmp_path / "agent"

    # Create bare origin
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)

    # Clone to local, make initial commit on main
    subprocess.run(["git", "clone", str(origin), str(local)], check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-B", "main"], cwd=local, check=True, capture_output=True)
    (local / "base.txt").write_text("base content\n")
    subprocess.run(["git", "add", "."], cwd=local, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=local, check=True, capture_output=True,
        env=_git_env(),
    )
    subprocess.run(
        ["git", "push", "-u", "origin", "main"],
        cwd=local, check=True, capture_output=True,
    )

    # Clone to agent
    subprocess.run(["git", "clone", str(origin), str(agent)], check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=agent, check=True, capture_output=True)

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
# Test: find_submodule_branch
# ---------------------------------------------------------------------------


class TestFindSubmoduleBranch:
    def test_finds_orch_branch(self, git_repo):
        """Finds orch/<task-id> branch."""
        agent = git_repo["agent"]
        subprocess.run(
            ["git", "checkout", "-b", "orch/abc12345"],
            cwd=agent, check=True, capture_output=True,
        )
        result = find_submodule_branch(agent, "abc12345")
        assert result == "orch/abc12345"

    def test_returns_none_when_on_main(self, git_repo):
        """Returns None when only on main."""
        agent = git_repo["agent"]
        result = find_submodule_branch(agent, "nonexist")
        assert result is None

    def test_falls_back_to_current_branch(self, git_repo):
        """Falls back to current non-main branch."""
        agent = git_repo["agent"]
        subprocess.run(
            ["git", "checkout", "-b", "some-other-branch"],
            cwd=agent, check=True, capture_output=True,
        )
        result = find_submodule_branch(agent, "nonexist")
        assert result == "some-other-branch"


# ---------------------------------------------------------------------------
# Test: find_main_repo_branch
# ---------------------------------------------------------------------------


class TestFindMainRepoBranch:
    def test_finds_tooling_branch(self, git_repo):
        """Finds tooling/<task-id> branch."""
        agent = git_repo["agent"]
        subprocess.run(
            ["git", "checkout", "-b", "tooling/abc12345"],
            cwd=agent, check=True, capture_output=True,
        )
        result = find_main_repo_branch(agent, "abc12345")
        assert result == "tooling/abc12345"

    def test_finds_agent_branch(self, git_repo):
        """Finds agent/<task-id>-* branch."""
        agent = git_repo["agent"]
        subprocess.run(
            ["git", "checkout", "-b", "agent/abc12345-20260209"],
            cwd=agent, check=True, capture_output=True,
        )
        result = find_main_repo_branch(agent, "abc12345")
        assert result == "agent/abc12345-20260209"

    def test_returns_none_when_no_match(self, git_repo):
        """Returns None when no matching branch."""
        agent = git_repo["agent"]
        result = find_main_repo_branch(agent, "nonexist")
        assert result is None


# ---------------------------------------------------------------------------
# Test: rebase_onto_origin
# ---------------------------------------------------------------------------


class TestRebaseOntoOrigin:
    def test_successful_rebase(self, git_repo):
        """Rebases a feature branch onto origin/main."""
        agent = git_repo["agent"]

        # Create feature branch with a commit
        subprocess.run(
            ["git", "checkout", "-b", "orch/test1234"],
            cwd=agent, check=True, capture_output=True,
        )
        _make_commit(agent, "feature.txt", "feature", "add feature")

        result = rebase_onto_origin(agent, "orch/test1234")
        assert result is True

    def test_rebase_conflict_aborts(self, git_repo):
        """Conflicting rebase aborts cleanly."""
        agent = git_repo["agent"]
        local = git_repo["local"]

        # Push a conflicting commit from local
        _make_commit(local, "conflict.txt", "local version", "local edit")
        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=local, check=True, capture_output=True,
        )

        # Create feature branch with conflicting commit
        subprocess.run(
            ["git", "checkout", "-b", "orch/test5678"],
            cwd=agent, check=True, capture_output=True,
        )
        _make_commit(agent, "conflict.txt", "agent version", "agent edit")

        result = rebase_onto_origin(agent, "orch/test5678")
        assert result is False


# ---------------------------------------------------------------------------
# Test: run_tests
# ---------------------------------------------------------------------------


class TestRunTests:
    def test_returns_true_when_no_venv(self, tmp_path):
        """Returns True (with warning) when no venv exists."""
        with patch("orchestrator.approve_orch._repo_root", return_value=tmp_path), \
             patch("orchestrator.approve_orch._submodule_dir", return_value=tmp_path / "sub"):
            result = run_tests(tmp_path)
            assert result is True

    def test_returns_true_on_passing_tests(self, tmp_path):
        """Returns True when pytest passes."""
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        fake_python = venv_bin / "python"
        fake_python.write_text("#!/bin/bash\necho '1 passed'\nexit 0\n")
        fake_python.chmod(0o755)

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
# Test: push_to_origin
# ---------------------------------------------------------------------------


class TestPushToOrigin:
    def test_successful_push(self, git_repo):
        """Pushes rebased branch to origin/main via refspec."""
        agent = git_repo["agent"]

        # Create feature branch with a commit
        subprocess.run(
            ["git", "checkout", "-b", "orch/push1234"],
            cwd=agent, check=True, capture_output=True,
        )
        _make_commit(agent, "feature.txt", "feature", "add feature")

        result = push_to_origin(agent, "orch/push1234")
        assert result is True

        # Verify origin/main has the commit
        subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=agent, check=True, capture_output=True,
        )
        log = subprocess.run(
            ["git", "log", "--oneline", "origin/main"],
            cwd=agent, capture_output=True, text=True,
        )
        assert "add feature" in log.stdout

    def test_cleans_up_remote_branch(self, git_repo):
        """Deletes the remote branch after pushing."""
        agent = git_repo["agent"]

        subprocess.run(
            ["git", "checkout", "-b", "orch/clean1234"],
            cwd=agent, check=True, capture_output=True,
        )
        _make_commit(agent, "feature.txt", "feature", "add feature")

        push_to_origin(agent, "orch/clean1234")

        # Check that remote branch was deleted
        branches = subprocess.run(
            ["git", "branch", "-r"],
            cwd=agent, capture_output=True, text=True,
        )
        assert "orch/clean1234" not in branches.stdout


# ---------------------------------------------------------------------------
# Test: accept_in_db
# ---------------------------------------------------------------------------


class TestAcceptInDb:
    def test_accepts_task(self, initialized_db):
        """Moves task to done and clears claimed_by."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue
            create_task(
                task_id="acc12345",
                file_path="/tmp/TASK-acc12345.md",
                role="orchestrator_impl",
            )
            update_task_queue("acc12345", "provisional", claimed_by="orch-impl-1")

            result = accept_in_db("acc12345")
            assert result is True

            from orchestrator.db import get_task
            task = get_task("acc12345")
            assert task["queue"] == "done"
            assert task["claimed_by"] is None

    def test_idempotent_on_already_done(self, initialized_db):
        """Re-calling accept_in_db on a done task is a no-op."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue, get_task, get_connection

            create_task(
                task_id="idem1234",
                file_path="/tmp/TASK-idem1234.md",
                role="orchestrator_impl",
            )
            update_task_queue("idem1234", "provisional", claimed_by="orch-impl-1")

            result = accept_in_db("idem1234")
            assert result is True

            with get_connection() as conn:
                count_before = conn.execute(
                    "SELECT COUNT(*) as c FROM task_history WHERE task_id = ? AND event = 'accepted'",
                    ("idem1234",),
                ).fetchone()["c"]

            result = accept_in_db("idem1234")
            assert result is True

            with get_connection() as conn:
                count_after = conn.execute(
                    "SELECT COUNT(*) as c FROM task_history WHERE task_id = ? AND event = 'accepted'",
                    ("idem1234",),
                ).fetchone()["c"]

            assert count_after == count_before

            task = get_task("idem1234")
            assert task["queue"] == "done"
            assert task["claimed_by"] is None


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
                from orchestrator.db import update_task_queue
                update_task_queue("impl1234", "provisional")

                result = approve_orchestrator_task("impl1234")
                assert result == 1

    def test_done_task_succeeds_for_idempotency(self, initialized_db):
        """Re-running on an already-done task succeeds (idempotency)."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.approve_orch.is_db_enabled", return_value=True):
                from orchestrator.db import create_task, update_task_queue
                create_task(
                    task_id="done1234",
                    file_path="/tmp/TASK-done1234.md",
                    role="orchestrator_impl",
                )
                update_task_queue("done1234", "done")

                result = approve_orchestrator_task("done1234")
                assert result == 0

    def test_rejects_wrong_queue(self, initialized_db):
        """Returns error for tasks in non-approvable queues."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.approve_orch.is_db_enabled", return_value=True):
                from orchestrator.db import create_task, update_task_queue
                create_task(
                    task_id="inc_1234",
                    file_path="/tmp/TASK-inc_1234.md",
                    role="orchestrator_impl",
                )
                update_task_queue("inc_1234", "incoming")

                result = approve_orchestrator_task("inc_1234")
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


# ---------------------------------------------------------------------------
# Test: parse_test_failures
# ---------------------------------------------------------------------------


class TestParseTestFailures:
    def test_parses_summary_section_failures(self):
        """Extracts test IDs from FAILED lines in short summary."""
        output = """
tests/test_foo.py::TestFoo::test_one PASSED
tests/test_foo.py::TestFoo::test_two FAILED
tests/test_bar.py::test_three PASSED
= short test summary info =
FAILED tests/test_foo.py::TestFoo::test_two - AssertionError
= 1 failed, 2 passed =
"""
        failures = parse_test_failures(output)
        assert failures == {"tests/test_foo.py::TestFoo::test_two"}

    def test_parses_verbose_mode_failures(self):
        """Extracts test IDs from verbose-mode FAILED lines."""
        output = """
tests/test_foo.py::TestFoo::test_one PASSED
tests/test_foo.py::TestFoo::test_two FAILED
tests/test_bar.py::test_three FAILED
"""
        failures = parse_test_failures(output)
        assert "tests/test_foo.py::TestFoo::test_two" in failures
        assert "tests/test_bar.py::test_three" in failures

    def test_empty_on_all_passing(self):
        """Returns empty set when all tests pass."""
        output = """
tests/test_foo.py::TestFoo::test_one PASSED
tests/test_bar.py::test_two PASSED
= 2 passed =
"""
        failures = parse_test_failures(output)
        assert failures == set()

    def test_empty_on_empty_output(self):
        """Returns empty set on empty output."""
        assert parse_test_failures("") == set()

    def test_multiple_failures(self):
        """Handles multiple failures correctly."""
        output = """
= short test summary info =
FAILED tests/test_a.py::test_x - AssertionError
FAILED tests/test_b.py::TestB::test_y - TypeError
FAILED tests/test_c.py::test_z - ValueError
= 3 failed, 10 passed =
"""
        failures = parse_test_failures(output)
        assert len(failures) == 3
        assert "tests/test_a.py::test_x" in failures
        assert "tests/test_b.py::TestB::test_y" in failures
        assert "tests/test_c.py::test_z" in failures


# ---------------------------------------------------------------------------
# Test: run_tests_with_baseline
# ---------------------------------------------------------------------------


class TestRunTestsWithBaseline:
    def test_passes_when_branch_failures_match_baseline(self, git_repo):
        """Tolerates pre-existing failures that are also on main."""
        agent = git_repo["agent"]

        # Create a fake venv with a python that always fails with the same test
        venv_bin = agent / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        fake_python = venv_bin / "python"
        # Script always reports the same failure regardless of branch
        fake_python.write_text(
            "#!/bin/bash\n"
            "echo 'tests/test_known.py::test_pre_existing FAILED'\n"
            "echo 'FAILED tests/test_known.py::test_pre_existing - AssertionError'\n"
            "echo '1 failed, 5 passed'\n"
            "exit 1\n"
        )
        fake_python.chmod(0o755)

        tests_dir = agent / "tests"
        tests_dir.mkdir(exist_ok=True)

        # Create feature branch
        subprocess.run(
            ["git", "checkout", "-b", "orch/baseline1"],
            cwd=agent, check=True, capture_output=True,
        )
        _make_commit(agent, "feature.txt", "feature", "add feature")

        result = run_tests_with_baseline(agent, "orch/baseline1")
        assert result is True  # Pre-existing failure tolerated

    def test_fails_when_branch_introduces_new_failure(self, git_repo):
        """Blocks approval when branch introduces a NEW test failure."""
        agent = git_repo["agent"]

        venv_bin = agent / "venv" / "bin"
        venv_bin.mkdir(parents=True)

        # Create a script that returns different results based on git branch
        fake_python = venv_bin / "python"
        fake_python.write_text(
            "#!/bin/bash\n"
            "BRANCH=$(git branch --show-current 2>/dev/null || echo 'detached')\n"
            "HEAD=$(git rev-parse HEAD 2>/dev/null)\n"
            "MAIN=$(git rev-parse origin/main 2>/dev/null)\n"
            "if [ \"$HEAD\" = \"$MAIN\" ]; then\n"
            "  echo '6 passed'\n"
            "  exit 0\n"
            "else\n"
            "  echo 'tests/test_new.py::test_regression FAILED'\n"
            "  echo 'FAILED tests/test_new.py::test_regression - AssertionError'\n"
            "  echo '1 failed, 5 passed'\n"
            "  exit 1\n"
            "fi\n"
        )
        fake_python.chmod(0o755)

        tests_dir = agent / "tests"
        tests_dir.mkdir(exist_ok=True)

        # Create feature branch with a commit
        subprocess.run(
            ["git", "checkout", "-b", "orch/baseline2"],
            cwd=agent, check=True, capture_output=True,
        )
        _make_commit(agent, "feature.txt", "feature", "add feature")

        result = run_tests_with_baseline(agent, "orch/baseline2")
        assert result is False  # New failure should block

    def test_returns_true_when_no_venv(self, tmp_path):
        """Returns True (with warning) when no venv exists."""
        with patch("orchestrator.approve_orch._repo_root", return_value=tmp_path), \
             patch("orchestrator.approve_orch._submodule_dir", return_value=tmp_path / "sub"):
            result = run_tests_with_baseline(tmp_path, "some-branch")
            assert result is True


# ---------------------------------------------------------------------------
# Test: hardened accept_in_db
# ---------------------------------------------------------------------------


class TestAcceptInDbHardened:
    def test_handles_get_task_exception(self, initialized_db):
        """Falls back to update_task_queue when get_task throws."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue

            create_task(
                task_id="hard1234",
                file_path="/tmp/TASK-hard1234.md",
                role="orchestrator_impl",
            )
            update_task_queue("hard1234", "provisional", claimed_by="orch-impl-1")

            # Make get_task fail on the FIRST call only
            call_count = [0]
            original_get_task = __import__("orchestrator.db", fromlist=["get_task"]).get_task

            def failing_get_task(task_id):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise RuntimeError("simulated schema error")
                return original_get_task(task_id)

            with patch("orchestrator.approve_orch.get_task", side_effect=failing_get_task):
                result = accept_in_db("hard1234")
                assert result is True

            # Verify task was still accepted
            from orchestrator.db import get_task
            task = get_task("hard1234")
            assert task["queue"] == "done"

    def test_handles_accept_completion_exception(self, initialized_db):
        """Falls back to update_task_queue when accept_completion throws."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue

            create_task(
                task_id="hard5678",
                file_path="/tmp/TASK-hard5678.md",
                role="orchestrator_impl",
            )
            update_task_queue("hard5678", "provisional", claimed_by="orch-impl-1")

            with patch("orchestrator.approve_orch.accept_completion",
                       side_effect=RuntimeError("simulated column error")):
                result = accept_in_db("hard5678")
                assert result is True

            from orchestrator.db import get_task
            task = get_task("hard5678")
            assert task["queue"] == "done"
            assert task["claimed_by"] is None

    def test_returns_false_when_all_paths_fail(self, initialized_db):
        """Returns False when both accept_completion and update_task_queue fail."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue

            create_task(
                task_id="fail1234",
                file_path="/tmp/TASK-fail1234.md",
                role="orchestrator_impl",
            )
            update_task_queue("fail1234", "provisional")

            with patch("orchestrator.approve_orch.accept_completion",
                       side_effect=RuntimeError("fail1")), \
                 patch("orchestrator.approve_orch.update_task_queue",
                       side_effect=RuntimeError("fail2")):
                result = accept_in_db("fail1234")
                assert result is False
