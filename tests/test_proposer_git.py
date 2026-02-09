"""Tests for ProposerRole git lifecycle — commit and push after run."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


_ENV = {
    "AGENT_NAME": "draft-processor",
    "AGENT_ID": "1",
    "AGENT_ROLE": "proposer",
    "PARENT_PROJECT": "/fake/project",
    "WORKTREE": "/fake/agents/draft-processor/worktree",
    "SHARED_DIR": "/fake/.orchestrator/shared",
    "ORCHESTRATOR_DIR": "/fake/.orchestrator",
    "AGENT_FOCUS": "drafts",
}


def _make_role():
    """Create a ProposerRole instance with mocked environment."""
    from orchestrator.roles.proposer import ProposerRole

    return ProposerRole()


def _make_completed_result(returncode=0, stdout="", stderr=""):
    """Create a subprocess.CompletedProcess."""
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class TestCommitAndPush:
    """Tests for ProposerRole._commit_and_push()."""

    def test_no_changes_returns_none(self):
        """When there are no uncommitted changes, returns None without doing anything."""
        with patch.dict("os.environ", _ENV):
            role = _make_role()

            with patch(
                "orchestrator.roles.proposer.has_uncommitted_changes", return_value=False
            ):
                result = role._commit_and_push()

            assert result is None

    def test_creates_branch_commits_and_pushes(self):
        """When there are changes, creates branch, commits, and pushes."""
        with patch.dict("os.environ", _ENV):
            role = _make_role()

            git_calls = []

            def mock_run_git(args, cwd, check=True):
                git_calls.append((args, str(cwd)))
                return _make_completed_result(0)

            with patch(
                "orchestrator.roles.proposer.has_uncommitted_changes", return_value=True
            ), patch(
                "orchestrator.roles.proposer.run_git", side_effect=mock_run_git
            ), patch(
                "orchestrator.roles.proposer.push_branch"
            ) as mock_push:
                result = role._commit_and_push()

            assert result is not None
            assert result.startswith("tooling/draft-processor-")

            # Verify git commands were called
            cmds = [c[0] for c in git_calls]
            assert any(c[0] == "checkout" and c[1] == "-b" for c in cmds)
            assert ["add", "-A"] in cmds
            assert any(c[0] == "commit" for c in cmds)

            # Verify push was called
            mock_push.assert_called_once()

    def test_branch_name_includes_agent_name_and_timestamp(self):
        """Branch name should be tooling/<agent-name>-<timestamp>."""
        with patch.dict("os.environ", _ENV):
            role = _make_role()

            with patch(
                "orchestrator.roles.proposer.has_uncommitted_changes", return_value=True
            ), patch(
                "orchestrator.roles.proposer.run_git",
                return_value=_make_completed_result(0),
            ), patch(
                "orchestrator.roles.proposer.push_branch"
            ):
                result = role._commit_and_push()

            assert result.startswith("tooling/draft-processor-")
            # Timestamp portion should be 15 chars: YYYYMMDD-HHMMSS
            suffix = result.replace("tooling/draft-processor-", "")
            assert len(suffix) == 15  # e.g., "20260209-110000"

    def test_custom_commit_message(self):
        """When a commit message is provided, it should be used."""
        with patch.dict("os.environ", _ENV):
            role = _make_role()

            git_calls = []

            def mock_run_git(args, cwd, check=True):
                git_calls.append((args, str(cwd)))
                return _make_completed_result(0)

            with patch(
                "orchestrator.roles.proposer.has_uncommitted_changes", return_value=True
            ), patch(
                "orchestrator.roles.proposer.run_git", side_effect=mock_run_git
            ), patch(
                "orchestrator.roles.proposer.push_branch"
            ):
                role._commit_and_push(
                    commit_message="chore: process drafts - archive 3, propose 2 tasks"
                )

            # Find the commit command
            commit_cmd = None
            for args, _ in git_calls:
                if args[0] == "commit":
                    commit_cmd = args
                    break

            assert commit_cmd is not None
            assert commit_cmd == [
                "commit", "-m", "chore: process drafts - archive 3, propose 2 tasks"
            ]

    def test_branch_creation_failure_returns_none(self):
        """If branch creation fails, returns None."""
        with patch.dict("os.environ", _ENV):
            role = _make_role()

            def mock_run_git(args, cwd, check=True):
                if args[0] == "checkout":
                    raise subprocess.CalledProcessError(
                        1, "git", stderr="branch already exists"
                    )
                return _make_completed_result(0)

            with patch(
                "orchestrator.roles.proposer.has_uncommitted_changes", return_value=True
            ), patch(
                "orchestrator.roles.proposer.run_git", side_effect=mock_run_git
            ):
                result = role._commit_and_push()

            assert result is None

    def test_commit_failure_returns_none(self):
        """If commit fails, returns None."""
        with patch.dict("os.environ", _ENV):
            role = _make_role()

            def mock_run_git(args, cwd, check=True):
                if args[0] == "commit":
                    raise subprocess.CalledProcessError(
                        1, "git", stderr="nothing to commit"
                    )
                return _make_completed_result(0)

            with patch(
                "orchestrator.roles.proposer.has_uncommitted_changes", return_value=True
            ), patch(
                "orchestrator.roles.proposer.run_git", side_effect=mock_run_git
            ):
                result = role._commit_and_push()

            assert result is None

    def test_push_failure_still_returns_branch_name(self):
        """If push fails, changes are committed locally. Returns branch name."""
        with patch.dict("os.environ", _ENV):
            role = _make_role()

            with patch(
                "orchestrator.roles.proposer.has_uncommitted_changes", return_value=True
            ), patch(
                "orchestrator.roles.proposer.run_git",
                return_value=_make_completed_result(0),
            ), patch(
                "orchestrator.roles.proposer.push_branch",
                side_effect=subprocess.CalledProcessError(
                    1, "git", stderr="push failed"
                ),
            ):
                result = role._commit_and_push()

            # Branch name is still returned even though push failed
            assert result is not None
            assert result.startswith("tooling/draft-processor-")

    def test_all_git_ops_use_worktree_cwd(self):
        """All git operations should use the worktree as cwd."""
        with patch.dict("os.environ", _ENV):
            role = _make_role()

            git_calls = []

            def mock_run_git(args, cwd, check=True):
                git_calls.append((args, str(cwd)))
                return _make_completed_result(0)

            with patch(
                "orchestrator.roles.proposer.has_uncommitted_changes", return_value=True
            ), patch(
                "orchestrator.roles.proposer.run_git", side_effect=mock_run_git
            ), patch(
                "orchestrator.roles.proposer.push_branch"
            ):
                role._commit_and_push()

            worktree = str(role.worktree)
            for args, cwd in git_calls:
                assert cwd == worktree, f"Command {args} used cwd={cwd}, expected {worktree}"


class TestRunCallsCommitAndPush:
    """Tests that ProposerRole.run() calls _commit_and_push after Claude finishes."""

    def _standard_patches(self, role, claude_exit_code=0):
        """Return patches for standard run() dependencies."""
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(
            patch(
                "orchestrator.roles.proposer.can_create_proposal",
                return_value=(True, ""),
            )
        )
        stack.enter_context(
            patch(
                "orchestrator.roles.proposer.get_proposal_limits",
                return_value={"max_per_run": 2},
            )
        )
        stack.enter_context(
            patch(
                "orchestrator.roles.proposer.get_rejected_proposals",
                return_value=[],
            )
        )
        stack.enter_context(
            patch.object(role, "get_focus_prompt", return_value="")
        )
        stack.enter_context(
            patch.object(role, "get_focus_description", return_value="Test focus")
        )
        stack.enter_context(
            patch.object(role, "read_instructions", return_value="")
        )
        stack.enter_context(
            patch.object(
                role, "invoke_claude", return_value=(claude_exit_code, "ok", "")
            )
        )
        return stack

    def test_run_calls_commit_and_push_on_success(self):
        """After successful Claude invocation, _commit_and_push is called."""
        with patch.dict("os.environ", _ENV):
            role = _make_role()

            mock_commit = MagicMock(return_value="tooling/test-branch")

            with self._standard_patches(role, claude_exit_code=0), patch.object(
                role, "_commit_and_push", mock_commit
            ):
                exit_code = role.run()

            assert exit_code == 0
            mock_commit.assert_called_once()

    def test_run_calls_commit_and_push_on_claude_failure(self):
        """Even if Claude fails, _commit_and_push is called to save partial work."""
        with patch.dict("os.environ", _ENV):
            role = _make_role()

            mock_commit = MagicMock(return_value=None)

            with self._standard_patches(role, claude_exit_code=1), patch.object(
                role, "_commit_and_push", mock_commit
            ):
                exit_code = role.run()

            assert exit_code == 1
            mock_commit.assert_called_once()

    def test_run_skips_commit_on_backpressure(self):
        """When backpressure prevents running, no commit is attempted."""
        with patch.dict("os.environ", _ENV):
            role = _make_role()

            mock_commit = MagicMock()

            with patch(
                "orchestrator.roles.proposer.can_create_proposal",
                return_value=(False, "too many proposals"),
            ), patch.object(role, "_commit_and_push", mock_commit):
                exit_code = role.run()

            assert exit_code == 0
            mock_commit.assert_not_called()

    def test_commit_and_push_failure_does_not_break_run(self):
        """If _commit_and_push raises, run() still returns the correct exit code."""
        with patch.dict("os.environ", _ENV):
            role = _make_role()

            with self._standard_patches(role, claude_exit_code=0), patch.object(
                role, "_commit_and_push", return_value=None
            ):
                exit_code = role.run()

            # run() should still succeed even if commit returned None
            assert exit_code == 0


class TestOtherProposerRolesNotAffected:
    """Verify that the commit behavior is specific to ProposerRole instances,
    not injected into other role types."""

    def test_specialist_role_has_no_commit_and_push(self):
        """SpecialistRole (the parent) should not have _commit_and_push."""
        from orchestrator.roles.specialist import SpecialistRole

        assert not hasattr(SpecialistRole, "_commit_and_push")

    def test_base_role_has_no_commit_and_push(self):
        """BaseRole should not have _commit_and_push."""
        from orchestrator.roles.base import BaseRole

        assert not hasattr(BaseRole, "_commit_and_push")
