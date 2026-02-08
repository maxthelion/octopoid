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
        """get_commit_count must be called with the submodule path,
        not self.worktree (the main repo worktree root)."""
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

            # get_commit_count must be called with the submodule path
            mock_commit_count.assert_called_once()
            called_path = mock_commit_count.call_args[0][0]
            assert str(called_path) == '/fake/agents/test-orch/worktree/orchestrator'
            # NOT the worktree root
            assert str(called_path) != '/fake/agents/test-orch/worktree'

    def test_head_ref_uses_submodule_path(self):
        """get_head_ref must be called with the submodule path."""
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

            # get_head_ref must be called with the submodule path
            mock_head_ref.assert_called_once()
            called_path = mock_head_ref.call_args[0][0]
            assert str(called_path) == '/fake/agents/test-orch/worktree/orchestrator'


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


class TestTryMergeToMain:
    """Tests for _try_merge_to_main — the self-merge flow."""

    def _make_role(self):
        from orchestrator.roles.orchestrator_impl import OrchestratorImplRole
        return OrchestratorImplRole()

    def test_success_path(self):
        """All steps succeed → returns True."""
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
                result = role._try_merge_to_main(sub_path, 'abc12345')

            assert result is True

            # Verify the key commands were issued in order
            cmds = [c[0] for c in calls]
            # 1. rebase
            assert ['git', 'rebase', 'main'] in cmds
            # 2. pytest
            assert any('pytest' in str(c) for c in cmds)
            # 3. checkout main
            assert ['git', 'checkout', 'main'] in cmds
            # 4. ff-merge in agent submodule
            assert ['git', 'merge', '--ff-only', 'orch/abc12345'] in cmds
            # 5. fetch into main checkout submodule
            assert any(
                c[0] == ['git', 'fetch', str(sub_path), 'main']
                and c[1] == '/fake/project/orchestrator'
                for c in calls
            )
            # 6. ff-merge in main checkout submodule
            assert any(
                c[0] == ['git', 'merge', '--ff-only', 'FETCH_HEAD']
                and c[1] == '/fake/project/orchestrator'
                for c in calls
            )

    def test_rebase_failure_returns_false(self):
        """If rebase fails, returns False and aborts rebase."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            sub_path = Path('/fake/agents/test-orch/worktree/orchestrator')

            call_count = [0]

            def mock_run_cmd(cmd, cwd, timeout=120):
                call_count[0] += 1
                if cmd == ['git', 'rebase', 'main']:
                    return _make_completed_result(1, stderr='CONFLICT')
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd):
                result = role._try_merge_to_main(sub_path, 'abc12345')

            assert result is False

    def test_test_failure_returns_false(self):
        """If pytest fails, returns False."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            sub_path = Path('/fake/agents/test-orch/worktree/orchestrator')
            venv_python = Path('/fake/venv/bin/python')

            def mock_run_cmd(cmd, cwd, timeout=120):
                if 'pytest' in cmd:
                    return _make_completed_result(1, stdout='FAILED test_foo')
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd), \
                 patch.object(role, '_find_venv_python', return_value=venv_python):
                result = role._try_merge_to_main(sub_path, 'abc12345')

            assert result is False

    def test_no_venv_returns_false(self):
        """If no venv is found, returns False (can't verify)."""
        with patch.dict('os.environ', _ENV):
            role = self._make_role()
            sub_path = Path('/fake/agents/test-orch/worktree/orchestrator')

            def mock_run_cmd(cmd, cwd, timeout=120):
                return _make_completed_result(0)

            with patch.object(role, '_run_cmd', side_effect=mock_run_cmd), \
                 patch.object(role, '_find_venv_python', return_value=None):
                result = role._try_merge_to_main(sub_path, 'abc12345')

            assert result is False

    def test_ff_merge_failure_returns_false(self):
        """If fast-forward merge fails, returns False and restores branch."""
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
                result = role._try_merge_to_main(sub_path, 'abc12345')

            assert result is False


class TestSelfMergeIntegration:
    """Tests that _run_with_task calls self-merge on success and falls back on failure."""

    def _standard_patches(self, role, mock_merge_result, commits=2):
        """Return a context manager stack for the standard _run_with_task mocks."""
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(patch.object(role, 'invoke_claude', return_value=(0, 'ok', '')))
        stack.enter_context(patch.object(role, 'read_instructions', return_value=''))
        stack.enter_context(patch.object(role, 'reset_tool_counter'))
        stack.enter_context(patch.object(role, 'read_tool_count', return_value=5))
        stack.enter_context(patch.object(role, '_create_submodule_branch'))
        stack.enter_context(patch.object(role, '_try_merge_to_main', return_value=mock_merge_result))
        stack.enter_context(patch('orchestrator.git_utils.create_feature_branch', return_value='agent/test'))
        stack.enter_context(patch('orchestrator.git_utils.get_head_ref', return_value='abc123'))
        stack.enter_context(patch('orchestrator.git_utils.get_commit_count', return_value=commits))
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

            with self._standard_patches(role, mock_merge_result=True, commits=2), \
                 patch('orchestrator.queue_utils.accept_completion', mock_accept), \
                 patch('orchestrator.queue_utils.submit_completion', mock_submit):
                role._run_with_task(task)

            mock_accept.assert_called_once()
            assert mock_accept.call_args[1].get('accepted_by') == 'self-merge' or \
                   mock_accept.call_args.kwargs.get('accepted_by') == 'self-merge'
            mock_submit.assert_not_called()

    def test_failed_merge_calls_submit(self):
        """When _try_merge_to_main returns False, submit_completion is called."""
        with patch.dict('os.environ', _ENV):
            role, task = _make_role_and_task()
            mock_accept = MagicMock()
            mock_submit = MagicMock()

            with self._standard_patches(role, mock_merge_result=False, commits=2), \
                 patch('orchestrator.queue_utils.accept_completion', mock_accept), \
                 patch('orchestrator.queue_utils.submit_completion', mock_submit):
                role._run_with_task(task)

            mock_submit.assert_called_once()
            mock_accept.assert_not_called()

    def test_zero_commits_skips_merge(self):
        """When commits_made is 0, _try_merge_to_main is NOT called."""
        with patch.dict('os.environ', _ENV):
            role, task = _make_role_and_task()
            mock_merge = MagicMock()
            mock_submit = MagicMock()

            with patch.object(role, 'invoke_claude', return_value=(0, 'ok', '')), \
                 patch.object(role, 'read_instructions', return_value=''), \
                 patch.object(role, 'reset_tool_counter'), \
                 patch.object(role, 'read_tool_count', return_value=5), \
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
            # Verify commits_count=0 was passed
            assert mock_submit.call_args.kwargs.get('commits_count') == 0 or \
                   (len(mock_submit.call_args.args) > 0 and mock_submit.call_args[1].get('commits_count') == 0)
