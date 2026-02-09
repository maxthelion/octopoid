"""Tests for orchestrator_impl role — submodule commit location."""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from orchestrator.scheduler import _verify_submodule_isolation


class TestVerifySubmoduleIsolation:
    """Tests for _verify_submodule_isolation — detects when an agent's
    worktree submodule shares a git object store with the main checkout."""

    def test_correct_worktree_gitdir(self, tmp_path):
        """A correct worktree submodule .git points to a worktrees/ path."""
        sub_path = tmp_path / "orchestrator"
        sub_path.mkdir()
        git_file = sub_path / ".git"
        # Correct: points to a worktree-specific modules directory
        git_file.write_text("gitdir: ../../../../../.git/worktrees/worktree/modules/orchestrator\n")

        # Should not raise or warn — it's correct
        _verify_submodule_isolation(sub_path, "test-agent")

    def test_main_checkout_gitdir_warns(self, tmp_path, capsys):
        """A submodule pointing to the main checkout's store should warn."""
        sub_path = tmp_path / "orchestrator"
        sub_path.mkdir()
        git_file = sub_path / ".git"
        # WRONG: points to main checkout's modules (no 'worktrees' in path)
        git_file.write_text("gitdir: ../../.git/modules/orchestrator\n")

        _verify_submodule_isolation(sub_path, "test-agent")

        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "test-agent" in captured.out

    def test_missing_git_file(self, tmp_path):
        """Missing .git file should not crash."""
        sub_path = tmp_path / "orchestrator"
        sub_path.mkdir()
        # No .git file — should handle gracefully
        _verify_submodule_isolation(sub_path, "test-agent")

    def test_non_gitdir_content(self, tmp_path):
        """Non-gitdir .git content should not crash."""
        sub_path = tmp_path / "orchestrator"
        sub_path.mkdir()
        git_file = sub_path / ".git"
        git_file.write_text("not a gitdir pointer\n")

        _verify_submodule_isolation(sub_path, "test-agent")


class TestOrchestratorImplSubmodulePath:
    """Tests for OrchestratorImplRole._get_submodule_path — ensures the
    role correctly identifies the worktree's submodule directory."""

    def test_submodule_path_is_worktree_child(self):
        """The submodule path should be worktree/orchestrator, not any other location."""
        from orchestrator.roles.orchestrator_impl import OrchestratorImplRole

        with patch.dict('os.environ', {
            'AGENT_NAME': 'test-orch',
            'AGENT_ID': '1',
            'AGENT_ROLE': 'orchestrator_impl',
            'PARENT_PROJECT': '/fake/project',
            'WORKTREE': '/fake/agents/test-orch/worktree',
            'SHARED_DIR': '/fake/.orchestrator/shared',
            'ORCHESTRATOR_DIR': '/fake/.orchestrator',
        }):
            role = OrchestratorImplRole()
            sub_path = role._get_submodule_path()

            assert sub_path == Path('/fake/agents/test-orch/worktree/orchestrator')
            # Crucially, it should NOT be the main checkout's submodule
            assert '/dev/boxen/orchestrator' not in str(sub_path)

    def test_submodule_path_changes_with_worktree(self):
        """Different agents get different submodule paths."""
        from orchestrator.roles.orchestrator_impl import OrchestratorImplRole

        for name, worktree in [
            ('agent-1', '/p/agents/agent-1/worktree'),
            ('agent-2', '/p/agents/agent-2/worktree'),
        ]:
            with patch.dict('os.environ', {
                'AGENT_NAME': name,
                'AGENT_ID': '1',
                'AGENT_ROLE': 'orchestrator_impl',
                'PARENT_PROJECT': '/p',
                'WORKTREE': worktree,
                'SHARED_DIR': '/p/.orchestrator/shared',
                'ORCHESTRATOR_DIR': '/p/.orchestrator',
            }):
                role = OrchestratorImplRole()
                sub_path = role._get_submodule_path()
                assert str(sub_path) == f'{worktree}/orchestrator'


def _make_role_and_task():
    """Helper to create a role instance and mock task for tests."""
    from orchestrator.roles.orchestrator_impl import OrchestratorImplRole

    role = OrchestratorImplRole()
    task = {
        'id': 'test123',
        'title': 'Test task',
        'branch': 'main',
        'path': '/fake/path',
        'content': 'Test content',
    }
    return role, task


class TestOrchestratorImplPrompt:
    """Tests that the orchestrator_impl prompt includes explicit submodule paths."""

    def test_prompt_includes_submodule_path(self):
        """The prompt sent to Claude must include the explicit submodule path
        so the agent knows exactly where to commit."""
        with patch.dict('os.environ', {
            'AGENT_NAME': 'test-orch',
            'AGENT_ID': '1',
            'AGENT_ROLE': 'orchestrator_impl',
            'PARENT_PROJECT': '/fake/project',
            'WORKTREE': '/fake/agents/test-orch/worktree',
            'SHARED_DIR': '/fake/.orchestrator/shared',
            'ORCHESTRATOR_DIR': '/fake/.orchestrator',
        }):
            role, task = _make_role_and_task()

            captured_prompt = None

            def mock_invoke(prompt, **kwargs):
                nonlocal captured_prompt
                captured_prompt = prompt
                return (0, 'ok', '')

            with patch.object(role, 'invoke_claude', side_effect=mock_invoke), \
                 patch.object(role, 'read_instructions', return_value=''), \
                 patch.object(role, 'reset_tool_counter'), \
                 patch.object(role, 'read_tool_count', return_value=5), \
                 patch.object(role, '_run_cmd', return_value=_make_completed_result(0)), \
                 patch.object(role, '_create_submodule_branch'), \
                 patch.object(role, '_try_merge_to_main', return_value=False), \
                 patch('orchestrator.git_utils.create_feature_branch', return_value='agent/test'), \
                 patch('orchestrator.git_utils.get_head_ref', return_value='abc123'), \
                 patch('orchestrator.git_utils.get_commit_count', return_value=1), \
                 patch('orchestrator.queue_utils.submit_completion'), \
                 patch('orchestrator.queue_utils.save_task_notes'), \
                 patch('orchestrator.queue_utils.get_task_notes', return_value=None), \
                 patch('orchestrator.config.get_notes_dir', return_value=Path('/fake/notes')), \
                 patch('orchestrator.config.is_db_enabled', return_value=True), \
                 patch('orchestrator.db.get_task', return_value=None):
                role._run_with_task(task)

            assert captured_prompt is not None
            # The prompt must contain the absolute submodule path
            assert '/fake/agents/test-orch/worktree/orchestrator' in captured_prompt
            # Must warn about NOT using the main checkout path
            assert 'DIFFERENT git repo' in captured_prompt

    def test_prompt_does_not_suggest_pr_creation(self):
        """orchestrator_impl prompt should NOT tell the agent to create a PR."""
        with patch.dict('os.environ', {
            'AGENT_NAME': 'test-orch',
            'AGENT_ID': '1',
            'AGENT_ROLE': 'orchestrator_impl',
            'PARENT_PROJECT': '/fake/project',
            'WORKTREE': '/fake/agents/test-orch/worktree',
            'SHARED_DIR': '/fake/.orchestrator/shared',
            'ORCHESTRATOR_DIR': '/fake/.orchestrator',
        }):
            role, task = _make_role_and_task()

            captured_prompt = None

            def mock_invoke(prompt, **kwargs):
                nonlocal captured_prompt
                captured_prompt = prompt
                return (0, 'ok', '')

            with patch.object(role, 'invoke_claude', side_effect=mock_invoke), \
                 patch.object(role, 'read_instructions', return_value=''), \
                 patch.object(role, 'reset_tool_counter'), \
                 patch.object(role, 'read_tool_count', return_value=5), \
                 patch.object(role, '_run_cmd', return_value=_make_completed_result(0)), \
                 patch.object(role, '_create_submodule_branch'), \
                 patch.object(role, '_try_merge_to_main', return_value=False), \
                 patch('orchestrator.git_utils.create_feature_branch', return_value='agent/test'), \
                 patch('orchestrator.git_utils.get_head_ref', return_value='abc123'), \
                 patch('orchestrator.git_utils.get_commit_count', return_value=1), \
                 patch('orchestrator.queue_utils.submit_completion'), \
                 patch('orchestrator.queue_utils.save_task_notes'), \
                 patch('orchestrator.queue_utils.get_task_notes', return_value=None), \
                 patch('orchestrator.config.get_notes_dir', return_value=Path('/fake/notes')), \
                 patch('orchestrator.config.is_db_enabled', return_value=True), \
                 patch('orchestrator.db.get_task', return_value=None):
                role._run_with_task(task)

            assert captured_prompt is not None
            assert 'Do NOT create a pull request' in captured_prompt


class TestOrchestratorImplCommitCounting:
    """Tests that commits are counted from the submodule, not the main repo."""

    def test_commit_count_uses_submodule_path(self):
        """get_commit_count must be called with the submodule path for
        submodule commits AND the worktree path for main repo commits."""
        with patch.dict('os.environ', {
            'AGENT_NAME': 'test-orch',
            'AGENT_ID': '1',
            'AGENT_ROLE': 'orchestrator_impl',
            'PARENT_PROJECT': '/fake/project',
            'WORKTREE': '/fake/agents/test-orch/worktree',
            'SHARED_DIR': '/fake/.orchestrator/shared',
            'ORCHESTRATOR_DIR': '/fake/.orchestrator',
        }):
            role, task = _make_role_and_task()

            mock_commit_count = MagicMock(return_value=2)

            with patch.object(role, 'invoke_claude', return_value=(0, 'ok', '')), \
                 patch.object(role, 'read_instructions', return_value=''), \
                 patch.object(role, 'reset_tool_counter'), \
                 patch.object(role, 'read_tool_count', return_value=5), \
                 patch.object(role, '_run_cmd', return_value=_make_completed_result(0)), \
                 patch.object(role, '_create_submodule_branch'), \
                 patch.object(role, '_try_merge_to_main', return_value=False), \
                 patch('orchestrator.git_utils.create_feature_branch', return_value='agent/test'), \
                 patch('orchestrator.git_utils.get_head_ref', return_value='abc123'), \
                 patch('orchestrator.git_utils.get_commit_count', mock_commit_count), \
                 patch('orchestrator.queue_utils.submit_completion'), \
                 patch('orchestrator.queue_utils.save_task_notes'), \
                 patch('orchestrator.queue_utils.get_task_notes', return_value=None), \
                 patch('orchestrator.config.get_notes_dir', return_value=Path('/fake/notes')), \
                 patch('orchestrator.config.is_db_enabled', return_value=True), \
                 patch('orchestrator.db.get_task', return_value=None):
                role._run_with_task(task)

            # get_commit_count must be called twice: once for submodule, once for main repo
            assert mock_commit_count.call_count == 2
            # First call: submodule path with orch/<task-id> branch
            sub_call = mock_commit_count.call_args_list[0]
            assert str(sub_call[0][0]) == '/fake/agents/test-orch/worktree/orchestrator'
            assert sub_call[1]['branch'] == 'orch/test123'
            # Second call: worktree path with tooling/<task-id> branch
            main_call = mock_commit_count.call_args_list[1]
            assert str(main_call[0][0]) == '/fake/agents/test-orch/worktree'
            assert main_call[1]['branch'] == 'tooling/test123'

    def test_head_ref_uses_both_paths(self):
        """get_head_ref must be called for both main repo and submodule."""
        with patch.dict('os.environ', {
            'AGENT_NAME': 'test-orch',
            'AGENT_ID': '1',
            'AGENT_ROLE': 'orchestrator_impl',
            'PARENT_PROJECT': '/fake/project',
            'WORKTREE': '/fake/agents/test-orch/worktree',
            'SHARED_DIR': '/fake/.orchestrator/shared',
            'ORCHESTRATOR_DIR': '/fake/.orchestrator',
        }):
            role, task = _make_role_and_task()

            mock_head_ref = MagicMock(return_value='abc123def456')

            with patch.object(role, 'invoke_claude', return_value=(0, 'ok', '')), \
                 patch.object(role, 'read_instructions', return_value=''), \
                 patch.object(role, 'reset_tool_counter'), \
                 patch.object(role, 'read_tool_count', return_value=5), \
                 patch.object(role, '_run_cmd', return_value=_make_completed_result(0)), \
                 patch.object(role, '_create_submodule_branch'), \
                 patch.object(role, '_try_merge_to_main', return_value=False), \
                 patch('orchestrator.git_utils.create_feature_branch', return_value='agent/test'), \
                 patch('orchestrator.git_utils.get_head_ref', mock_head_ref), \
                 patch('orchestrator.git_utils.get_commit_count', return_value=1), \
                 patch('orchestrator.queue_utils.submit_completion'), \
                 patch('orchestrator.queue_utils.save_task_notes'), \
                 patch('orchestrator.queue_utils.get_task_notes', return_value=None), \
                 patch('orchestrator.config.get_notes_dir', return_value=Path('/fake/notes')), \
                 patch('orchestrator.config.is_db_enabled', return_value=True), \
                 patch('orchestrator.db.get_task', return_value=None):
                role._run_with_task(task)

            # get_head_ref must be called twice: main repo first, then submodule
            assert mock_head_ref.call_count == 2
            first_path = str(mock_head_ref.call_args_list[0][0][0])
            second_path = str(mock_head_ref.call_args_list[1][0][0])
            assert first_path == '/fake/agents/test-orch/worktree'
            assert second_path == '/fake/agents/test-orch/worktree/orchestrator'


class TestOrchestratorImplNoPR:
    """Tests that orchestrator_impl does NOT attempt to create PRs."""

    def test_no_pr_creation(self):
        """orchestrator_impl should not call create_pull_request at all."""
        with patch.dict('os.environ', {
            'AGENT_NAME': 'test-orch',
            'AGENT_ID': '1',
            'AGENT_ROLE': 'orchestrator_impl',
            'PARENT_PROJECT': '/fake/project',
            'WORKTREE': '/fake/agents/test-orch/worktree',
            'SHARED_DIR': '/fake/.orchestrator/shared',
            'ORCHESTRATOR_DIR': '/fake/.orchestrator',
        }):
            role, task = _make_role_and_task()

            mock_create_pr = MagicMock(return_value='https://fake.pr')

            with patch.object(role, 'invoke_claude', return_value=(0, 'ok', '')), \
                 patch.object(role, 'read_instructions', return_value=''), \
                 patch.object(role, 'reset_tool_counter'), \
                 patch.object(role, 'read_tool_count', return_value=5), \
                 patch.object(role, '_run_cmd', return_value=_make_completed_result(0)), \
                 patch.object(role, '_create_submodule_branch'), \
                 patch.object(role, '_try_merge_to_main', return_value=False), \
                 patch('orchestrator.git_utils.create_feature_branch', return_value='agent/test'), \
                 patch('orchestrator.git_utils.get_head_ref', return_value='abc123'), \
                 patch('orchestrator.git_utils.get_commit_count', return_value=1), \
                 patch('orchestrator.queue_utils.submit_completion'), \
                 patch('orchestrator.queue_utils.save_task_notes'), \
                 patch('orchestrator.queue_utils.get_task_notes', return_value=None), \
                 patch('orchestrator.config.get_notes_dir', return_value=Path('/fake/notes')), \
                 patch('orchestrator.config.is_db_enabled', return_value=True), \
                 patch('orchestrator.db.get_task', return_value=None), \
                 patch('orchestrator.git_utils.create_pull_request', mock_create_pr):
                role._run_with_task(task)

            # create_pull_request should NOT have been called
            mock_create_pr.assert_not_called()


# ---------------------------------------------------------------------------
# Helpers for self-merge tests
# ---------------------------------------------------------------------------

_ENV = {
    'AGENT_NAME': 'test-orch',
    'AGENT_ID': '1',
    'AGENT_ROLE': 'orchestrator_impl',
    'PARENT_PROJECT': '/fake/project',
    'WORKTREE': '/fake/agents/test-orch/worktree',
    'SHARED_DIR': '/fake/.orchestrator/shared',
    'ORCHESTRATOR_DIR': '/fake/.orchestrator',
}


def _make_completed_result(returncode=0, stdout='', stderr=''):
    """Create a subprocess.CompletedProcess."""
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class TestTryMergeSubmodule:
    """Tests for _try_merge_submodule -- the submodule self-merge flow."""

    def _make_role(self):
        from orchestrator.roles.orchestrator_impl import OrchestratorImplRole
        return OrchestratorImplRole()

    def test_success_path(self):
        """All steps succeed -> returns True."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            sub_path = Path('/fake/agents/test-orch/worktree/orchestrator')
            venv_python = Path('/fake/project/.orchestrator/venv/bin/python')

            calls = []

            def mock_run_cmd(cmd, cwd, timeout=120):
                calls.append((cmd, str(cwd)))
                return _make_completed_result(0, stdout='all passed')

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd), \
                 patch.object(role, '_find_venv_python', return_value=venv_python):
                result = role._try_merge_submodule(sub_path, 'abc12345')

            assert result is True

            cmds = [c[0] for c in calls]
            assert ['git', 'rebase', 'main'] in cmds
            assert any('pytest' in str(c) for c in cmds)
            assert ['git', 'checkout', 'main'] in cmds
            assert ['git', 'merge', '--ff-only', 'orch/abc12345'] in cmds

    def test_rebase_failure_returns_false(self):
        """If rebase fails, returns False and aborts rebase."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            sub_path = Path('/fake/agents/test-orch/worktree/orchestrator')

            def mock_run_cmd(cmd, cwd, timeout=120):
                if cmd == ['git', 'rebase', 'main']:
                    return _make_completed_result(1, stderr='CONFLICT')
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd):
                result = role._try_merge_submodule(sub_path, 'abc12345')

            assert result is False

    def test_new_test_failure_returns_false(self):
        """If the branch introduces NEW test failures, returns False."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            sub_path = Path('/fake/agents/test-orch/worktree/orchestrator')
            venv_python = Path('/fake/venv/bin/python')

            pytest_call_count = [0]

            def mock_run_cmd(cmd, cwd, timeout=120):
                if 'pytest' in cmd:
                    pytest_call_count[0] += 1
                    if pytest_call_count[0] == 1:
                        return _make_completed_result(0, stdout='5 passed')
                    else:
                        return _make_completed_result(
                            1, stdout='FAILED tests/test_foo.py::test_new_bug\n4 passed, 1 failed'
                        )
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd), \
                 patch.object(role, '_find_venv_python', return_value=venv_python):
                result = role._try_merge_submodule(sub_path, 'abc12345')

            assert result is False

    def test_preexisting_failure_still_merges(self):
        """If all test failures are pre-existing, merge proceeds."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            sub_path = Path('/fake/agents/test-orch/worktree/orchestrator')
            venv_python = Path('/fake/venv/bin/python')

            def mock_run_cmd(cmd, cwd, timeout=120):
                if 'pytest' in cmd:
                    return _make_completed_result(
                        1, stdout='FAILED tests/test_known.py::test_flaky\n4 passed, 1 failed'
                    )
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd), \
                 patch.object(role, '_find_venv_python', return_value=venv_python):
                result = role._try_merge_submodule(sub_path, 'abc12345')

            assert result is True

    def test_no_venv_returns_false(self):
        """If no venv is found, returns False."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            sub_path = Path('/fake/agents/test-orch/worktree/orchestrator')

            with patch.object(role, '_run_cmd', return_value=_make_completed_result(0)), \
                 patch.object(role, '_find_venv_python', return_value=None):
                result = role._try_merge_submodule(sub_path, 'abc12345')

            assert result is False

    def test_ff_merge_failure_returns_false(self):
        """If fast-forward merge fails, returns False."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            sub_path = Path('/fake/agents/test-orch/worktree/orchestrator')
            venv_python = Path('/fake/venv/bin/python')

            def mock_run_cmd(cmd, cwd, timeout=120):
                if cmd == ['git', 'merge', '--ff-only', 'orch/abc12345']:
                    return _make_completed_result(1, stderr='not a ff')
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd), \
                 patch.object(role, '_find_venv_python', return_value=venv_python):
                result = role._try_merge_submodule(sub_path, 'abc12345')

            assert result is False


class TestTryMergeMainRepo:
    """Tests for _try_merge_main_repo -- push-to-origin main repo merge flow.

    The new flow never touches the human's working tree. All operations
    happen in the agent's worktree, pushing to origin via refspec.
    """

    def _make_role(self):
        from orchestrator.roles.orchestrator_impl import OrchestratorImplRole
        return OrchestratorImplRole()

    def test_success_path(self):
        """All steps succeed -> returns True, all git ops use worktree cwd."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            calls = []

            def mock_run_cmd(cmd, cwd, timeout=120):
                calls.append((cmd, str(cwd)))
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd), \
                 patch('orchestrator.message_utils.info'):
                result = role._try_merge_main_repo('abc12345')

            assert result is True

            worktree = str(role.worktree)
            cmds = [c[0] for c in calls]

            # All git commands must use the worktree, not parent_project
            for cmd, cwd in calls:
                if cmd[0] == 'git':
                    assert cwd == worktree, f"Command {cmd} ran in {cwd}, expected {worktree}"

            # Verify key steps
            assert ['git', 'fetch', 'origin', 'main'] in cmds
            assert ['git', 'checkout', 'tooling/abc12345'] in cmds
            assert ['git', 'rebase', 'origin/main'] in cmds
            assert ['git', 'push', 'origin', 'tooling/abc12345', '--force-with-lease'] in cmds
            assert ['git', 'push', 'origin', 'tooling/abc12345:main'] in cmds
            # Remote branch cleanup
            assert ['git', 'push', 'origin', '--delete', 'tooling/abc12345'] in cmds

    def test_never_touches_parent_project(self):
        """No git command should use parent_project as cwd."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            calls = []

            def mock_run_cmd(cmd, cwd, timeout=120):
                calls.append((cmd, str(cwd)))
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd), \
                 patch('orchestrator.message_utils.info'):
                role._try_merge_main_repo('abc12345')

            parent = str(role.parent_project)
            for cmd, cwd in calls:
                assert cwd != parent, f"Command {cmd} touched parent_project {parent}"

    def test_fetch_failure_returns_false(self):
        """If fetch origin/main fails, returns False."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            def mock_run_cmd(cmd, cwd, timeout=120):
                if cmd == ['git', 'fetch', 'origin', 'main']:
                    return _make_completed_result(1, stderr='fetch failed')
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd):
                result = role._try_merge_main_repo('abc12345')

            assert result is False

    def test_rebase_failure_returns_false(self):
        """If rebase onto origin/main fails, returns False and aborts."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            calls = []

            def mock_run_cmd(cmd, cwd, timeout=120):
                calls.append((cmd, str(cwd)))
                if cmd == ['git', 'rebase', 'origin/main']:
                    return _make_completed_result(1, stderr='CONFLICT')
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd):
                result = role._try_merge_main_repo('abc12345')

            assert result is False
            # Should abort the rebase
            cmds = [c[0] for c in calls]
            assert ['git', 'rebase', '--abort'] in cmds

    def test_branch_push_failure_returns_false(self):
        """If pushing the rebased branch to origin fails, returns False."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            def mock_run_cmd(cmd, cwd, timeout=120):
                if '--force-with-lease' in cmd:
                    return _make_completed_result(1, stderr='push rejected')
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd):
                result = role._try_merge_main_repo('abc12345')

            assert result is False

    def test_ff_push_failure_triggers_retry(self):
        """If the refspec push to main fails, rebase and retry once."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            refspec_push_count = [0]

            def mock_run_cmd(cmd, cwd, timeout=120):
                if cmd == ['git', 'push', 'origin', 'tooling/abc12345:main']:
                    refspec_push_count[0] += 1
                    if refspec_push_count[0] == 1:
                        return _make_completed_result(1, stderr='not fast-forward')
                    return _make_completed_result(0)
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd), \
                 patch('orchestrator.message_utils.info'):
                result = role._try_merge_main_repo('abc12345')

            assert result is True
            assert refspec_push_count[0] == 2

    def test_ff_push_failure_after_retry_returns_false(self):
        """If the refspec push fails twice, returns False."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            def mock_run_cmd(cmd, cwd, timeout=120):
                if cmd == ['git', 'push', 'origin', 'tooling/abc12345:main']:
                    return _make_completed_result(1, stderr='not fast-forward')
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd):
                result = role._try_merge_main_repo('abc12345')

            assert result is False

    def test_retry_rebase_failure_returns_false(self):
        """If the retry rebase fails, returns False."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            rebase_count = [0]

            def mock_run_cmd(cmd, cwd, timeout=120):
                if cmd == ['git', 'push', 'origin', 'tooling/abc12345:main']:
                    return _make_completed_result(1, stderr='not fast-forward')
                if cmd == ['git', 'rebase', 'origin/main']:
                    rebase_count[0] += 1
                    if rebase_count[0] == 2:
                        return _make_completed_result(1, stderr='CONFLICT on retry')
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd):
                result = role._try_merge_main_repo('abc12345')

            assert result is False

    def test_sends_notification_on_success(self):
        """On success, sends an info message to the human inbox."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            mock_send = MagicMock()

            with patch.object(role, '_run_cmd', return_value=_make_completed_result(0)), \
                 patch('orchestrator.message_utils.info', mock_send):
                result = role._try_merge_main_repo('abc12345')

            assert result is True
            mock_send.assert_called_once()
            call_kwargs = mock_send.call_args
            assert 'abc12345' in call_kwargs.kwargs.get('title', '') or 'abc12345' in str(call_kwargs)
            assert call_kwargs.kwargs.get('agent_name') == 'test-orch'
            assert 'git pull' in call_kwargs.kwargs.get('body', '')

    def test_notification_failure_does_not_break_merge(self):
        """If notification fails, merge still returns True."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            with patch.object(role, '_run_cmd', return_value=_make_completed_result(0)), \
                 patch('orchestrator.message_utils.info', side_effect=Exception('msg fail')):
                result = role._try_merge_main_repo('abc12345')

            assert result is True


class TestTryMergeToMain:
    """Tests for the top-level _try_merge_to_main orchestrator."""

    def _make_role(self):
        from orchestrator.roles.orchestrator_impl import OrchestratorImplRole
        return OrchestratorImplRole()

    def test_submodule_only(self):
        """Submodule-only: calls _try_merge_submodule, skips main repo."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            sub_path = Path('/fake/agents/test-orch/worktree/orchestrator')

            with patch.object(role, '_try_merge_submodule', return_value=True) as mock_sub, \
                 patch.object(role, '_try_merge_main_repo') as mock_main, \
                 patch.object(role, '_run_cmd', return_value=_make_completed_result(0)):
                result = role._try_merge_to_main(
                    sub_path, 'abc12345',
                    has_sub_commits=True, has_main_commits=False,
                )

            assert result is True
            mock_sub.assert_called_once_with(sub_path, 'abc12345', target_branch='main')
            mock_main.assert_not_called()

    def test_main_repo_only(self):
        """Main-repo-only: calls _try_merge_main_repo, skips submodule."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            sub_path = Path('/fake/agents/test-orch/worktree/orchestrator')

            with patch.object(role, '_try_merge_submodule') as mock_sub, \
                 patch.object(role, '_try_merge_main_repo', return_value=True) as mock_main, \
                 patch.object(role, '_run_cmd', return_value=_make_completed_result(0)):
                result = role._try_merge_to_main(
                    sub_path, 'abc12345',
                    has_sub_commits=False, has_main_commits=True,
                )

            assert result is True
            mock_sub.assert_not_called()
            mock_main.assert_called_once_with('abc12345', target_branch='main')

    def test_both_repos(self):
        """Both repos: merges submodule first, then main repo."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            sub_path = Path('/fake/agents/test-orch/worktree/orchestrator')

            call_order = []

            def mock_sub(*a, **kw):
                call_order.append('sub')
                return True

            def mock_main(*a, **kw):
                call_order.append('main')
                return True

            with patch.object(role, '_try_merge_submodule', side_effect=mock_sub), \
                 patch.object(role, '_try_merge_main_repo', side_effect=mock_main), \
                 patch.object(role, '_run_cmd', return_value=_make_completed_result(0)):
                result = role._try_merge_to_main(
                    sub_path, 'abc12345',
                    has_sub_commits=True, has_main_commits=True,
                )

            assert result is True
            assert call_order == ['sub', 'main']

    def test_submodule_failure_skips_main_repo(self):
        """If submodule merge fails, main repo merge is NOT attempted."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            sub_path = Path('/fake/agents/test-orch/worktree/orchestrator')

            with patch.object(role, '_try_merge_submodule', return_value=False), \
                 patch.object(role, '_try_merge_main_repo') as mock_main:
                result = role._try_merge_to_main(
                    sub_path, 'abc12345',
                    has_sub_commits=True, has_main_commits=True,
                )

            assert result is False
            mock_main.assert_not_called()

    def test_main_repo_failure_after_submodule_success(self):
        """If main repo fails but submodule succeeded, returns False."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            sub_path = Path('/fake/agents/test-orch/worktree/orchestrator')

            with patch.object(role, '_try_merge_submodule', return_value=True), \
                 patch.object(role, '_try_merge_main_repo', return_value=False), \
                 patch.object(role, '_run_cmd', return_value=_make_completed_result(0)):
                result = role._try_merge_to_main(
                    sub_path, 'abc12345',
                    has_sub_commits=True, has_main_commits=True,
                )

            assert result is False


class TestSelfMergeIntegration:
    """Tests that _run_with_task calls self-merge on success and falls back on failure."""

    def _standard_patches(self, role, mock_merge_result, sub_commits=2, main_commits=0):
        """Return a context manager stack for the standard _run_with_task mocks."""
        from contextlib import ExitStack

        # get_commit_count returns different values per call
        commit_values = [sub_commits, main_commits]
        commit_iter = iter(commit_values)

        stack = ExitStack()
        stack.enter_context(patch.object(role, 'invoke_claude', return_value=(0, 'ok', '')))
        stack.enter_context(patch.object(role, 'read_instructions', return_value=''))
        stack.enter_context(patch.object(role, 'reset_tool_counter'))
        stack.enter_context(patch.object(role, 'read_tool_count', return_value=5))
        stack.enter_context(patch.object(role, '_run_cmd', return_value=_make_completed_result(0)))
        stack.enter_context(patch.object(role, '_create_submodule_branch'))
        stack.enter_context(patch.object(role, '_try_merge_to_main', return_value=mock_merge_result))
        stack.enter_context(patch('orchestrator.git_utils.create_feature_branch', return_value='agent/test'))
        stack.enter_context(patch('orchestrator.git_utils.get_head_ref', return_value='abc123'))
        stack.enter_context(patch('orchestrator.git_utils.get_commit_count', side_effect=lambda *a, **kw: next(commit_iter)))
        stack.enter_context(patch('orchestrator.queue_utils.save_task_notes'))
        stack.enter_context(patch('orchestrator.queue_utils.get_task_notes', return_value=None))
        stack.enter_context(patch('orchestrator.config.get_notes_dir', return_value=Path('/fake/notes')))
        stack.enter_context(patch('orchestrator.config.is_db_enabled', return_value=True))
        stack.enter_context(patch('orchestrator.db.get_task', return_value=None))
        return stack

    def test_successful_merge_calls_accept(self):
        """When _try_merge_to_main returns True, accept_completion is called."""
        with patch.dict('os.environ', _ENV):
            role, task = _make_role_and_task()
            mock_accept = MagicMock()
            mock_submit = MagicMock()

            with self._standard_patches(role, mock_merge_result=True, sub_commits=2), \
                 patch('orchestrator.queue_utils.accept_completion', mock_accept), \
                 patch('orchestrator.queue_utils.submit_completion', mock_submit):
                role._run_with_task(task)

            mock_accept.assert_called_once()
            assert mock_accept.call_args.kwargs.get('accepted_by') == 'self-merge'
            mock_submit.assert_not_called()

    def test_failed_merge_calls_submit(self):
        """When _try_merge_to_main returns False, submit_completion is called."""
        with patch.dict('os.environ', _ENV):
            role, task = _make_role_and_task()
            mock_accept = MagicMock()
            mock_submit = MagicMock()

            with self._standard_patches(role, mock_merge_result=False, sub_commits=2), \
                 patch('orchestrator.queue_utils.accept_completion', mock_accept), \
                 patch('orchestrator.queue_utils.submit_completion', mock_submit):
                role._run_with_task(task)

            mock_submit.assert_called_once()
            mock_accept.assert_not_called()

    def test_zero_commits_skips_merge(self):
        """When total commits is 0, _try_merge_to_main is NOT called."""
        with patch.dict('os.environ', _ENV):
            role, task = _make_role_and_task()
            mock_merge = MagicMock()
            mock_submit = MagicMock()

            with patch.object(role, 'invoke_claude', return_value=(0, 'ok', '')), \
                 patch.object(role, 'read_instructions', return_value=''), \
                 patch.object(role, 'reset_tool_counter'), \
                 patch.object(role, 'read_tool_count', return_value=5), \
                 patch.object(role, '_run_cmd', return_value=_make_completed_result(0)), \
                 patch.object(role, '_create_submodule_branch'), \
                 patch.object(role, '_try_merge_to_main', mock_merge), \
                 patch('orchestrator.git_utils.create_feature_branch', return_value='agent/test'), \
                 patch('orchestrator.git_utils.get_head_ref', return_value='abc123'), \
                 patch('orchestrator.git_utils.get_commit_count', return_value=0), \
                 patch('orchestrator.queue_utils.submit_completion', mock_submit), \
                 patch('orchestrator.queue_utils.accept_completion'), \
                 patch('orchestrator.queue_utils.save_task_notes'), \
                 patch('orchestrator.queue_utils.get_task_notes', return_value=None), \
                 patch('orchestrator.config.get_notes_dir', return_value=Path('/fake/notes')), \
                 patch('orchestrator.config.is_db_enabled', return_value=True), \
                 patch('orchestrator.db.get_task', return_value=None):
                role._run_with_task(task)

            mock_merge.assert_not_called()
            mock_submit.assert_called_once()

    def test_main_repo_only_commits_trigger_merge(self):
        """When only main repo has commits, merge is still triggered."""
        with patch.dict('os.environ', _ENV):
            role, task = _make_role_and_task()
            mock_merge = MagicMock(return_value=True)
            mock_accept = MagicMock()

            with self._standard_patches(role, mock_merge_result=True, sub_commits=0, main_commits=3), \
                 patch.object(role, '_try_merge_to_main', mock_merge), \
                 patch('orchestrator.queue_utils.accept_completion', mock_accept):
                role._run_with_task(task)

            mock_merge.assert_called_once()
            # Verify has_sub_commits=False, has_main_commits=True
            call_kwargs = mock_merge.call_args
            assert call_kwargs.kwargs.get('has_sub_commits') is False
            assert call_kwargs.kwargs.get('has_main_commits') is True
            mock_accept.assert_called_once()


class TestCreateToolingBranch:
    """Tests for _create_tooling_branch."""

    def _make_role(self):
        from orchestrator.roles.orchestrator_impl import OrchestratorImplRole
        return OrchestratorImplRole()

    def test_creates_branch_from_main(self):
        """Tooling branch should be created from main."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            calls = []

            def mock_run_cmd(cmd, cwd, timeout=120):
                calls.append((cmd, str(cwd)))
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd):
                branch = role._create_tooling_branch(Path('/fake/worktree'), 'test123')

            assert branch == 'tooling/test123'
            assert any(
                c[0] == ['git', 'branch', 'tooling/test123', 'main']
                for c in calls
            )

    def test_branch_name_uses_task_id(self):
        """Branch name should be tooling/<task-id>."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            with patch.object(role, '_run_cmd', return_value=_make_completed_result(0)):
                branch = role._create_tooling_branch(Path('/fake/worktree'), 'my-task-42')

            assert branch == 'tooling/my-task-42'


class TestPromptIncludesToolingBranch:
    """Tests that the prompt includes tooling branch instructions."""

    def test_prompt_mentions_tooling_branch(self):
        """The prompt must tell the agent about the tooling branch."""
        with patch.dict('os.environ', _ENV):
            role, task = _make_role_and_task()

            captured_prompt = None

            def mock_invoke(prompt, **kwargs):
                nonlocal captured_prompt
                captured_prompt = prompt
                return (0, 'ok', '')

            with patch.object(role, 'invoke_claude', side_effect=mock_invoke), \
                 patch.object(role, 'read_instructions', return_value=''), \
                 patch.object(role, 'reset_tool_counter'), \
                 patch.object(role, 'read_tool_count', return_value=5), \
                 patch.object(role, '_run_cmd', return_value=_make_completed_result(0)), \
                 patch.object(role, '_create_submodule_branch'), \
                 patch.object(role, '_try_merge_to_main', return_value=False), \
                 patch('orchestrator.git_utils.create_feature_branch', return_value='agent/test'), \
                 patch('orchestrator.git_utils.get_head_ref', return_value='abc123'), \
                 patch('orchestrator.git_utils.get_commit_count', return_value=1), \
                 patch('orchestrator.queue_utils.submit_completion'), \
                 patch('orchestrator.queue_utils.save_task_notes'), \
                 patch('orchestrator.queue_utils.get_task_notes', return_value=None), \
                 patch('orchestrator.config.get_notes_dir', return_value=Path('/fake/notes')), \
                 patch('orchestrator.config.is_db_enabled', return_value=True), \
                 patch('orchestrator.db.get_task', return_value=None):
                role._run_with_task(task)

            assert captured_prompt is not None
            assert 'tooling/test123' in captured_prompt
            assert 'Main Repo Tooling Files' in captured_prompt
            assert 'Do NOT commit main repo files directly on main' in captured_prompt


# ---------------------------------------------------------------------------
# Project-level branching tests
# ---------------------------------------------------------------------------


class TestEnsureBranchExists:
    """Tests for _ensure_branch_exists — creates branch if missing."""

    def _make_role(self):
        from orchestrator.roles.orchestrator_impl import OrchestratorImplRole
        return OrchestratorImplRole()

    def test_branch_exists_locally_is_noop(self):
        """If the branch already exists locally, no action taken."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            calls = []

            def mock_run_cmd(cmd, cwd, timeout=120):
                calls.append(cmd)
                if cmd == ['git', 'rev-parse', '--verify', 'proj/my-branch']:
                    return _make_completed_result(0)  # exists
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd):
                role._ensure_branch_exists(Path('/repo'), 'proj/my-branch')

            # Should only check local existence, then stop
            assert ['git', 'rev-parse', '--verify', 'proj/my-branch'] in calls
            assert ['git', 'branch', 'proj/my-branch', 'main'] not in calls

    def test_creates_from_origin_if_remote_exists(self):
        """If the branch exists on origin but not locally, create tracking branch."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            calls = []

            def mock_run_cmd(cmd, cwd, timeout=120):
                calls.append(cmd)
                if cmd == ['git', 'rev-parse', '--verify', 'proj/my-branch']:
                    return _make_completed_result(1)  # not local
                if cmd == ['git', 'rev-parse', '--verify', 'origin/proj/my-branch']:
                    return _make_completed_result(0)  # exists on origin
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd):
                role._ensure_branch_exists(Path('/repo'), 'proj/my-branch')

            assert ['git', 'branch', 'proj/my-branch', 'origin/proj/my-branch'] in calls

    def test_creates_from_base_if_no_remote(self):
        """If the branch doesn't exist anywhere, create from base."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            calls = []

            def mock_run_cmd(cmd, cwd, timeout=120):
                calls.append(cmd)
                if cmd[0:3] == ['git', 'rev-parse', '--verify']:
                    return _make_completed_result(1)  # doesn't exist
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd):
                role._ensure_branch_exists(Path('/repo'), 'proj/my-branch')

            assert ['git', 'branch', 'proj/my-branch', 'main'] in calls


class TestProjectBranchSubmoduleMerge:
    """Tests for _try_merge_submodule with a project target branch."""

    def _make_role(self):
        from orchestrator.roles.orchestrator_impl import OrchestratorImplRole
        return OrchestratorImplRole()

    def test_project_branch_rebase_target(self):
        """Submodule merge with project branch rebases onto project branch, not main."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            sub_path = Path('/fake/agents/test-orch/worktree/orchestrator')
            venv_python = Path('/fake/venv/bin/python')

            calls = []

            def mock_run_cmd(cmd, cwd, timeout=120):
                calls.append(cmd)
                return _make_completed_result(0, stdout='all passed')

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd), \
                 patch.object(role, '_find_venv_python', return_value=venv_python), \
                 patch.object(role, '_ensure_branch_exists'):
                result = role._try_merge_submodule(
                    sub_path, 'abc12345', target_branch='proj/feature-x'
                )

            assert result is True
            # Should rebase onto project branch, not main
            assert ['git', 'rebase', 'proj/feature-x'] in calls
            # Should checkout project branch for ff-merge
            assert ['git', 'checkout', 'proj/feature-x'] in calls
            # Should ff-merge to project branch
            assert ['git', 'merge', '--ff-only', 'orch/abc12345'] in calls
            # Should push project branch
            assert ['git', 'push', 'origin', 'proj/feature-x'] in calls

    def test_project_branch_calls_ensure_branch(self):
        """Project branch merge calls _ensure_branch_exists for both submodule and main checkout."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            sub_path = Path('/fake/agents/test-orch/worktree/orchestrator')
            venv_python = Path('/fake/venv/bin/python')

            with patch.object(role, '_run_cmd', return_value=_make_completed_result(0, stdout='ok')), \
                 patch.object(role, '_find_venv_python', return_value=venv_python), \
                 patch.object(role, '_ensure_branch_exists') as mock_ensure:
                role._try_merge_submodule(
                    sub_path, 'abc12345', target_branch='proj/feature-x'
                )

            # Called once for the agent's worktree submodule, once for main checkout submodule
            assert mock_ensure.call_count == 2
            assert mock_ensure.call_args_list[0] == call(sub_path, 'proj/feature-x')
            main_checkout_sub = role.parent_project / "orchestrator"
            assert mock_ensure.call_args_list[1] == call(main_checkout_sub, 'proj/feature-x')

    def test_main_branch_does_not_call_ensure(self):
        """Default main branch does NOT call _ensure_branch_exists."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            sub_path = Path('/fake/agents/test-orch/worktree/orchestrator')
            venv_python = Path('/fake/venv/bin/python')

            with patch.object(role, '_run_cmd', return_value=_make_completed_result(0, stdout='ok')), \
                 patch.object(role, '_find_venv_python', return_value=venv_python), \
                 patch.object(role, '_ensure_branch_exists') as mock_ensure:
                role._try_merge_submodule(sub_path, 'abc12345')

            mock_ensure.assert_not_called()


class TestProjectBranchMainRepoMerge:
    """Tests for _try_merge_main_repo with a project target branch."""

    def _make_role(self):
        from orchestrator.roles.orchestrator_impl import OrchestratorImplRole
        return OrchestratorImplRole()

    def test_project_branch_target(self):
        """Main repo merge with project branch targets the project branch, not main."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            calls = []

            def mock_run_cmd(cmd, cwd, timeout=120):
                calls.append(cmd)
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd), \
                 patch('orchestrator.message_utils.info'):
                result = role._try_merge_main_repo('abc12345', target_branch='proj/feature-x')

            assert result is True
            # Fetch project branch
            assert ['git', 'fetch', 'origin', 'proj/feature-x'] in calls
            # Rebase onto project branch
            assert ['git', 'rebase', 'origin/proj/feature-x'] in calls
            # Push to project branch via refspec
            assert ['git', 'push', 'origin', 'tooling/abc12345:proj/feature-x'] in calls

    def test_notification_mentions_project_branch(self):
        """Notification should mention the project branch, not main."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            mock_send = MagicMock()

            with patch.object(role, '_run_cmd', return_value=_make_completed_result(0)), \
                 patch('orchestrator.message_utils.info', mock_send):
                role._try_merge_main_repo('abc12345', target_branch='proj/feature-x')

            mock_send.assert_called_once()
            body = mock_send.call_args.kwargs.get('body', '')
            assert 'proj/feature-x' in body


class TestProjectTaskSkipsSubmoduleRef:
    """Tests that project tasks skip the submodule ref update on main."""

    def _make_role(self):
        from orchestrator.roles.orchestrator_impl import OrchestratorImplRole
        return OrchestratorImplRole()

    def test_project_task_skips_submodule_ref(self):
        """When target_branch != 'main', submodule ref update is skipped."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            calls = []

            def mock_run_cmd(cmd, cwd, timeout=120):
                calls.append((cmd, str(cwd)))
                return _make_completed_result(0)

            with patch.object(role, '_try_merge_submodule', return_value=True), \
                 patch.object(role, '_run_cmd', side_effect=mock_run_cmd):
                result = role._try_merge_to_main(
                    Path('/fake/sub'), 'abc12345',
                    has_sub_commits=True, has_main_commits=False,
                    target_branch='proj/feature-x',
                )

            assert result is True
            # Should NOT run 'git add orchestrator' on main repo
            git_add_cmds = [c for c in calls if c[0] == ['git', 'add', 'orchestrator']]
            assert len(git_add_cmds) == 0

    def test_non_project_task_updates_submodule_ref(self):
        """When target_branch == 'main' (default), submodule ref IS updated."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            calls = []

            def mock_run_cmd(cmd, cwd, timeout=120):
                calls.append((cmd, str(cwd)))
                # Return non-zero for diff --cached --quiet to simulate staged changes
                if cmd == ['git', 'diff', '--cached', '--quiet']:
                    return _make_completed_result(1)
                return _make_completed_result(0)

            with patch.object(role, '_try_merge_submodule', return_value=True), \
                 patch.object(role, '_run_cmd', side_effect=mock_run_cmd):
                result = role._try_merge_to_main(
                    Path('/fake/sub'), 'abc12345',
                    has_sub_commits=True, has_main_commits=False,
                    target_branch='main',
                )

            assert result is True
            # Should run 'git add orchestrator'
            git_add_cmds = [c[0] for c in calls if c[0] == ['git', 'add', 'orchestrator']]
            assert len(git_add_cmds) == 1

    def test_project_branch_passed_to_sub_merge(self):
        """target_branch is forwarded to _try_merge_submodule."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            with patch.object(role, '_try_merge_submodule', return_value=True) as mock_sub, \
                 patch.object(role, '_run_cmd', return_value=_make_completed_result(0)):
                role._try_merge_to_main(
                    Path('/fake/sub'), 'abc12345',
                    has_sub_commits=True, has_main_commits=False,
                    target_branch='proj/feature-x',
                )

            mock_sub.assert_called_once_with(
                Path('/fake/sub'), 'abc12345', target_branch='proj/feature-x'
            )

    def test_project_branch_passed_to_main_merge(self):
        """target_branch is forwarded to _try_merge_main_repo."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            with patch.object(role, '_try_merge_submodule', return_value=True), \
                 patch.object(role, '_try_merge_main_repo', return_value=True) as mock_main, \
                 patch.object(role, '_run_cmd', return_value=_make_completed_result(0)):
                role._try_merge_to_main(
                    Path('/fake/sub'), 'abc12345',
                    has_sub_commits=True, has_main_commits=True,
                    target_branch='proj/feature-x',
                )

            mock_main.assert_called_once_with('abc12345', target_branch='proj/feature-x')


class TestRunWithTaskProjectBranch:
    """Tests that _run_with_task looks up project_id and determines target branch."""

    def _standard_patches(self, role, mock_merge_result, sub_commits=2, main_commits=0):
        """Return a context manager stack for the standard _run_with_task mocks."""
        from contextlib import ExitStack

        commit_values = [sub_commits, main_commits]
        commit_iter = iter(commit_values)

        stack = ExitStack()
        stack.enter_context(patch.object(role, 'invoke_claude', return_value=(0, 'ok', '')))
        stack.enter_context(patch.object(role, 'read_instructions', return_value=''))
        stack.enter_context(patch.object(role, 'reset_tool_counter'))
        stack.enter_context(patch.object(role, 'read_tool_count', return_value=5))
        stack.enter_context(patch.object(role, '_run_cmd', return_value=_make_completed_result(0)))
        stack.enter_context(patch.object(role, '_create_submodule_branch'))
        stack.enter_context(patch.object(role, '_try_merge_to_main', return_value=mock_merge_result))
        stack.enter_context(patch('orchestrator.git_utils.create_feature_branch', return_value='agent/test'))
        stack.enter_context(patch('orchestrator.git_utils.get_head_ref', return_value='abc123'))
        stack.enter_context(patch('orchestrator.git_utils.get_commit_count', side_effect=lambda *a, **kw: next(commit_iter)))
        stack.enter_context(patch('orchestrator.queue_utils.save_task_notes'))
        stack.enter_context(patch('orchestrator.queue_utils.get_task_notes', return_value=None))
        stack.enter_context(patch('orchestrator.config.get_notes_dir', return_value=Path('/fake/notes')))
        stack.enter_context(patch('orchestrator.config.is_db_enabled', return_value=True))
        stack.enter_context(patch('orchestrator.db.get_task', return_value=None))
        return stack

    def test_project_task_uses_project_branch(self):
        """When task has project_id, merge targets the project branch."""
        with patch.dict('os.environ', _ENV):
            from orchestrator.roles.orchestrator_impl import OrchestratorImplRole
            role = OrchestratorImplRole()
            task = {
                'id': 'proj-task1',
                'title': 'Project task',
                'branch': 'main',
                'path': '/fake/path',
                'content': 'Test content',
            }

            mock_merge = MagicMock(return_value=True)

            # Mock db.get_task to return a task with project_id
            mock_get_task = MagicMock(return_value={
                'id': 'proj-task1',
                'project_id': 'PROJ-abc',
                'checks': [],
                'check_results': {},
            })
            # Mock db.get_project to return project with branch
            mock_get_project = MagicMock(return_value={
                'id': 'PROJ-abc',
                'branch': 'proj/feature-x',
                'base_branch': 'main',
            })

            with self._standard_patches(role, mock_merge_result=True), \
                 patch.object(role, '_try_merge_to_main', mock_merge), \
                 patch('orchestrator.queue_utils.accept_completion'), \
                 patch('orchestrator.db.get_task', mock_get_task), \
                 patch('orchestrator.db.get_project', mock_get_project):
                role._run_with_task(task)

            mock_merge.assert_called_once()
            call_kwargs = mock_merge.call_args
            assert call_kwargs.kwargs.get('target_branch') == 'proj/feature-x'

    def test_non_project_task_uses_main(self):
        """When task has no project_id, merge targets main."""
        with patch.dict('os.environ', _ENV):
            from orchestrator.roles.orchestrator_impl import OrchestratorImplRole
            role = OrchestratorImplRole()
            task = {
                'id': 'solo-task1',
                'title': 'Solo task',
                'branch': 'main',
                'path': '/fake/path',
                'content': 'Test content',
            }

            mock_merge = MagicMock(return_value=True)

            # Mock db.get_task to return a task WITHOUT project_id
            mock_get_task = MagicMock(return_value={
                'id': 'solo-task1',
                'project_id': None,
                'checks': [],
                'check_results': {},
            })

            with self._standard_patches(role, mock_merge_result=True), \
                 patch.object(role, '_try_merge_to_main', mock_merge), \
                 patch('orchestrator.queue_utils.accept_completion'), \
                 patch('orchestrator.db.get_task', mock_get_task):
                role._run_with_task(task)

            mock_merge.assert_called_once()
            call_kwargs = mock_merge.call_args
            assert call_kwargs.kwargs.get('target_branch') == 'main'


class TestMergeProjectToMain:
    """Tests for the module-level merge_project_to_main function."""

    def test_merges_project_branch_to_main(self):
        """merge_project_to_main merges the project branch and updates submodule ref."""
        from orchestrator.roles.orchestrator_impl import merge_project_to_main

        mock_project = {
            'id': 'PROJ-abc',
            'branch': 'proj/feature-x',
            'base_branch': 'main',
        }

        calls = []

        def mock_run(cmd, capture_output=True, text=True, cwd=None, timeout=120):
            calls.append((cmd, str(cwd) if cwd else None))
            # Simulate that ff-only merge succeeds
            return _make_completed_result(0)

        with patch('orchestrator.roles.orchestrator_impl.subprocess.run', side_effect=mock_run), \
             patch('orchestrator.config.is_db_enabled', return_value=True), \
             patch('orchestrator.db.get_project', return_value=mock_project), \
             patch('orchestrator.db.update_project') as mock_update:
            result = merge_project_to_main('PROJ-abc', parent_project=Path('/fake/project'))

        assert result is True
        # Should have checked out main in submodule
        cmds = [c[0] for c in calls]
        assert ['git', 'checkout', 'main'] in cmds
        # Should merge project branch
        assert ['git', 'merge', '--ff-only', 'origin/proj/feature-x'] in cmds
        # Should update project to complete
        mock_update.assert_called_once_with('PROJ-abc', status='complete')

    def test_no_branch_is_noop(self):
        """If project has no branch (or branch is 'main'), returns True immediately."""
        from orchestrator.roles.orchestrator_impl import merge_project_to_main

        mock_project = {
            'id': 'PROJ-abc',
            'branch': 'main',
            'base_branch': 'main',
        }

        with patch('orchestrator.config.is_db_enabled', return_value=True), \
             patch('orchestrator.db.get_project', return_value=mock_project), \
             patch('orchestrator.db.update_project') as mock_update:
            result = merge_project_to_main('PROJ-abc', parent_project=Path('/fake/project'))

        assert result is True
        mock_update.assert_not_called()

    def test_project_not_found_returns_false(self):
        """If project doesn't exist, returns False."""
        from orchestrator.roles.orchestrator_impl import merge_project_to_main

        with patch('orchestrator.config.is_db_enabled', return_value=True), \
             patch('orchestrator.db.get_project', return_value=None):
            result = merge_project_to_main('PROJ-missing', parent_project=Path('/fake/project'))

        assert result is False

    def test_merge_failure_falls_back_to_regular_merge(self):
        """If ff-only merge fails, tries regular merge."""
        from orchestrator.roles.orchestrator_impl import merge_project_to_main

        mock_project = {
            'id': 'PROJ-abc',
            'branch': 'proj/feature-x',
            'base_branch': 'main',
        }

        merge_count = [0]

        def mock_run(cmd, capture_output=True, text=True, cwd=None, timeout=120):
            if 'merge' in cmd and '--ff-only' in cmd:
                merge_count[0] += 1
                return _make_completed_result(1, stderr='not a fast-forward')
            if 'merge' in cmd and '--ff-only' not in cmd:
                merge_count[0] += 1
                return _make_completed_result(0)
            return _make_completed_result(0)

        with patch('orchestrator.roles.orchestrator_impl.subprocess.run', side_effect=mock_run), \
             patch('orchestrator.config.is_db_enabled', return_value=True), \
             patch('orchestrator.db.get_project', return_value=mock_project), \
             patch('orchestrator.db.update_project'):
            result = merge_project_to_main('PROJ-abc', parent_project=Path('/fake/project'))

        assert result is True
        assert merge_count[0] == 2  # ff-only + regular merge
