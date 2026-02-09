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
                 patch('orchestrator.config.is_db_enabled', return_value=True):
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
                 patch('orchestrator.config.is_db_enabled', return_value=True):
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
                 patch('orchestrator.config.is_db_enabled', return_value=True):
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
                 patch('orchestrator.config.is_db_enabled', return_value=True):
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
    """Tests for _try_merge_main_repo -- main repo tooling merge flow."""

    def _make_role(self):
        from orchestrator.roles.orchestrator_impl import OrchestratorImplRole
        return OrchestratorImplRole()

    def test_success_path(self):
        """All steps succeed -> returns True."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            calls = []

            def mock_run_cmd(cmd, cwd, timeout=120):
                calls.append((cmd, str(cwd)))
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd):
                result = role._try_merge_main_repo('abc12345')

            assert result is True

            cmds = [c[0] for c in calls]
            assert any('fetch' in c[0] and 'tooling/abc12345' in str(c[0]) for c in calls)
            assert ['git', 'rebase', 'main'] in cmds
            assert ['git', 'merge', '--ff-only', 'tooling/abc12345'] in cmds
            assert ['git', 'branch', '-d', 'tooling/abc12345'] in cmds

    def test_fetch_failure_returns_false(self):
        """If fetch fails, returns False."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            def mock_run_cmd(cmd, cwd, timeout=120):
                if 'fetch' in cmd:
                    return _make_completed_result(1, stderr='fetch failed')
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd):
                result = role._try_merge_main_repo('abc12345')

            assert result is False

    def test_rebase_failure_returns_false(self):
        """If rebase fails, returns False and cleans up."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            def mock_run_cmd(cmd, cwd, timeout=120):
                if cmd == ['git', 'rebase', 'main']:
                    return _make_completed_result(1, stderr='CONFLICT')
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd):
                result = role._try_merge_main_repo('abc12345')

            assert result is False

    def test_ff_merge_failure_returns_false(self):
        """If ff-merge fails, returns False."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()

            def mock_run_cmd(cmd, cwd, timeout=120):
                if cmd == ['git', 'merge', '--ff-only', 'tooling/abc12345']:
                    return _make_completed_result(1, stderr='not ff')
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd):
                result = role._try_merge_main_repo('abc12345')

            assert result is False


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
            mock_sub.assert_called_once_with(sub_path, 'abc12345')
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
            mock_main.assert_called_once_with('abc12345')

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
                 patch('orchestrator.config.is_db_enabled', return_value=True):
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
                 patch('orchestrator.config.is_db_enabled', return_value=True):
                role._run_with_task(task)

            assert captured_prompt is not None
            assert 'tooling/test123' in captured_prompt
            assert 'Main Repo Tooling Files' in captured_prompt
            assert 'Do NOT commit main repo files directly on main' in captured_prompt
