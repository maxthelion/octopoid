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
