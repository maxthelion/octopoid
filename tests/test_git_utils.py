"""Tests for orchestrator.git_utils module."""

import pytest
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, call


class TestEnsureWorktree:
    """Tests for ensure_worktree using origin/main as base."""

    def test_new_worktree_uses_origin_ref(self, temp_dir):
        """New worktrees are created from origin/{base_branch}, not local branch."""
        from orchestrator.git_utils import ensure_worktree

        worktree_path = temp_dir / "agents" / "test-agent" / "worktree"

        with patch('orchestrator.git_utils.find_parent_project', return_value=temp_dir), \
             patch('orchestrator.git_utils.get_worktree_path', return_value=worktree_path), \
             patch('orchestrator.git_utils.run_git') as mock_run:

            mock_run.return_value = MagicMock(returncode=0)

            ensure_worktree("test-agent", base_branch="main")

            # Find the worktree add call
            add_calls = [c for c in mock_run.call_args_list
                         if c[0][0][:2] == ["worktree", "add"]]
            assert len(add_calls) == 1
            add_args = add_calls[0][0][0]
            # Must use origin/main, not bare "main"
            assert add_args[-1] == "origin/main"

    def test_new_worktree_custom_branch_uses_origin(self, temp_dir):
        """Custom base_branch also uses origin/ prefix."""
        from orchestrator.git_utils import ensure_worktree

        worktree_path = temp_dir / "agents" / "test-agent" / "worktree"

        with patch('orchestrator.git_utils.find_parent_project', return_value=temp_dir), \
             patch('orchestrator.git_utils.get_worktree_path', return_value=worktree_path), \
             patch('orchestrator.git_utils.run_git') as mock_run:

            mock_run.return_value = MagicMock(returncode=0)

            ensure_worktree("test-agent", base_branch="feature/test")

            add_calls = [c for c in mock_run.call_args_list
                         if c[0][0][:2] == ["worktree", "add"]]
            assert len(add_calls) == 1
            assert add_calls[0][0][0][-1] == "origin/feature/test"

    def test_new_worktree_fetches_before_create(self, temp_dir):
        """Fetch happens before worktree creation."""
        from orchestrator.git_utils import ensure_worktree

        worktree_path = temp_dir / "agents" / "test-agent" / "worktree"
        call_order = []

        def tracking_run_git(args, cwd=None, check=True):
            call_order.append(args[:2])
            return MagicMock(returncode=0)

        with patch('orchestrator.git_utils.find_parent_project', return_value=temp_dir), \
             patch('orchestrator.git_utils.get_worktree_path', return_value=worktree_path), \
             patch('orchestrator.git_utils.run_git', side_effect=tracking_run_git):

            ensure_worktree("test-agent")

            fetch_idx = next(i for i, c in enumerate(call_order) if c == ["fetch", "origin"])
            add_idx = next(i for i, c in enumerate(call_order) if c == ["worktree", "add"])
            assert fetch_idx < add_idx, "fetch must happen before worktree add"

    def test_existing_worktree_resets_to_origin(self, temp_dir):
        """Existing worktrees are reset to origin/{base_branch}."""
        from orchestrator.git_utils import ensure_worktree

        worktree_path = temp_dir / "agents" / "test-agent" / "worktree"
        worktree_path.mkdir(parents=True)
        (worktree_path / ".git").write_text("gitdir: ...")

        with patch('orchestrator.git_utils.find_parent_project', return_value=temp_dir), \
             patch('orchestrator.git_utils.get_worktree_path', return_value=worktree_path), \
             patch('orchestrator.git_utils.run_git') as mock_run:

            mock_run.return_value = MagicMock(returncode=0)

            ensure_worktree("test-agent", base_branch="main")

            # Should checkout --detach origin/main
            checkout_calls = [c for c in mock_run.call_args_list
                              if len(c[0][0]) >= 3 and c[0][0][:2] == ["checkout", "--detach"]]
            assert len(checkout_calls) == 1
            assert checkout_calls[0][0][0][2] == "origin/main"

    def test_existing_worktree_fetches_first(self, temp_dir):
        """Existing worktrees fetch origin before resetting."""
        from orchestrator.git_utils import ensure_worktree

        worktree_path = temp_dir / "agents" / "test-agent" / "worktree"
        worktree_path.mkdir(parents=True)
        (worktree_path / ".git").write_text("gitdir: ...")

        call_order = []

        def tracking_run_git(args, cwd=None, check=True):
            call_order.append(args[:2])
            return MagicMock(returncode=0)

        with patch('orchestrator.git_utils.find_parent_project', return_value=temp_dir), \
             patch('orchestrator.git_utils.get_worktree_path', return_value=worktree_path), \
             patch('orchestrator.git_utils.run_git', side_effect=tracking_run_git):

            ensure_worktree("test-agent")

            # fetch must come before checkout
            fetch_idx = next(i for i, c in enumerate(call_order) if c == ["fetch", "origin"])
            checkout_idx = next(i for i, c in enumerate(call_order) if c == ["checkout", "--detach"])
            assert fetch_idx < checkout_idx

    def test_new_worktree_fallback_to_local_on_missing_origin(self, temp_dir):
        """Falls back to local branch if origin ref doesn't exist."""
        from orchestrator.git_utils import ensure_worktree

        worktree_path = temp_dir / "agents" / "test-agent" / "worktree"

        call_count = [0]

        def mock_run_git(args, cwd=None, check=True):
            if args[:2] == ["worktree", "add"]:
                call_count[0] += 1
                if call_count[0] == 1:
                    # First call with origin/main fails
                    err = subprocess.CalledProcessError(128, "git")
                    err.stderr = "fatal: invalid reference: origin/main"
                    raise err
                # Second call with local main succeeds
            return MagicMock(returncode=0)

        with patch('orchestrator.git_utils.find_parent_project', return_value=temp_dir), \
             patch('orchestrator.git_utils.get_worktree_path', return_value=worktree_path), \
             patch('orchestrator.git_utils.run_git', side_effect=mock_run_git):

            ensure_worktree("test-agent")

            assert call_count[0] == 2  # Tried origin/main then fell back to main


class TestCreateFeatureBranch:
    """Tests for create_feature_branch using origin/main as base."""

    def test_branch_based_on_origin(self, temp_dir):
        """Feature branch is created from origin/{base_branch}."""
        from orchestrator.git_utils import create_feature_branch

        with patch('orchestrator.git_utils.run_git') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            branch = create_feature_branch(temp_dir, "test-task", base_branch="main")

            # Should checkout --detach origin/main (not local main)
            checkout_calls = [c for c in mock_run.call_args_list
                              if len(c[0][0]) >= 3 and c[0][0][:2] == ["checkout", "--detach"]]
            assert len(checkout_calls) == 1
            assert checkout_calls[0][0][0][2] == "origin/main"

    def test_branch_based_on_origin_custom_base(self, temp_dir):
        """Custom base_branch uses origin/ prefix."""
        from orchestrator.git_utils import create_feature_branch

        with patch('orchestrator.git_utils.run_git') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            create_feature_branch(temp_dir, "test-task", base_branch="feature/xyz")

            checkout_calls = [c for c in mock_run.call_args_list
                              if len(c[0][0]) >= 3 and c[0][0][:2] == ["checkout", "--detach"]]
            assert len(checkout_calls) == 1
            assert checkout_calls[0][0][0][2] == "origin/feature/xyz"

    def test_fetches_before_checkout(self, temp_dir):
        """Fetch happens before creating the branch."""
        from orchestrator.git_utils import create_feature_branch

        call_order = []

        def tracking_run_git(args, cwd=None, check=True):
            call_order.append(args[:2])
            return MagicMock(returncode=0)

        with patch('orchestrator.git_utils.run_git', side_effect=tracking_run_git):
            create_feature_branch(temp_dir, "test-task")

            fetch_idx = next(i for i, c in enumerate(call_order) if c == ["fetch", "origin"])
            checkout_idx = next(i for i, c in enumerate(call_order) if c == ["checkout", "--detach"])
            assert fetch_idx < checkout_idx

    def test_does_not_checkout_local_main(self, temp_dir):
        """Does NOT check out bare 'main' (local) as the base."""
        from orchestrator.git_utils import create_feature_branch

        with patch('orchestrator.git_utils.run_git') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            create_feature_branch(temp_dir, "test-task", base_branch="main")

            # Should NOT have a call to checkout bare "main"
            for c in mock_run.call_args_list:
                args = c[0][0]
                if args[:1] == ["checkout"] and args != ["checkout", "-b", mock_run.call_args_list[-1][0][0][-1]]:
                    # Any checkout call should use origin/ prefix or -b
                    if len(args) == 2 and args[1] == "main":
                        pytest.fail("Should not checkout local 'main' directly")

    def test_fallback_to_local_on_origin_failure(self, temp_dir):
        """Falls back to local branch if origin ref fails."""
        from orchestrator.git_utils import create_feature_branch

        call_count = [0]

        def mock_run_git(args, cwd=None, check=True):
            if args[:2] == ["checkout", "--detach"]:
                raise subprocess.CalledProcessError(1, "git")
            return MagicMock(returncode=0)

        with patch('orchestrator.git_utils.run_git', side_effect=mock_run_git):
            branch = create_feature_branch(temp_dir, "test-task")

            assert branch.startswith("agent/test-task-")

    def test_branch_name_format(self, temp_dir):
        """Branch name follows agent/{task_id}-{timestamp} format."""
        from orchestrator.git_utils import create_feature_branch

        with patch('orchestrator.git_utils.run_git') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            branch = create_feature_branch(temp_dir, "abc12345")

            assert branch.startswith("agent/abc12345-")
            # Timestamp portion: YYYYMMDD-HHMMSS
            import re
            assert re.match(r"agent/abc12345-\d{8}-\d{6}$", branch)


class TestGetCommitCount:
    """Tests for get_commit_count function."""

    def test_get_commit_count_with_commits(self, temp_dir):
        """Test counting commits when there are commits."""
        from orchestrator.git_utils import get_commit_count

        # Mock the git commands
        with patch('orchestrator.git_utils.run_git') as mock_run:
            # Mock merge-base finding common ancestor
            merge_base_result = MagicMock()
            merge_base_result.returncode = 0
            merge_base_result.stdout = "abc123\n"

            # Mock rev-list counting commits
            count_result = MagicMock()
            count_result.returncode = 0
            count_result.stdout = "5\n"

            mock_run.side_effect = [merge_base_result, count_result]

            count = get_commit_count(temp_dir)

            assert count == 5

    def test_get_commit_count_no_common_ancestor(self, temp_dir):
        """Test counting commits when there's no common ancestor with main."""
        from orchestrator.git_utils import get_commit_count

        with patch('orchestrator.git_utils.run_git') as mock_run:
            # Mock merge-base failing (no common ancestor)
            merge_base_result = MagicMock()
            merge_base_result.returncode = 1

            # Mock rev-list counting all commits
            count_result = MagicMock()
            count_result.returncode = 0
            count_result.stdout = "10\n"

            mock_run.side_effect = [merge_base_result, count_result]

            count = get_commit_count(temp_dir)

            assert count == 10

    def test_get_commit_count_with_since_ref(self, temp_dir):
        """Test counting commits since a specific ref."""
        from orchestrator.git_utils import get_commit_count

        with patch('orchestrator.git_utils.run_git') as mock_run:
            count_result = MagicMock()
            count_result.returncode = 0
            count_result.stdout = "3\n"

            mock_run.return_value = count_result

            count = get_commit_count(temp_dir, since_ref="HEAD~5")

            assert count == 3
            # Should have called with the since_ref
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert "HEAD~5..HEAD" in call_args

    def test_get_commit_count_zero(self, temp_dir):
        """Test counting commits when there are none."""
        from orchestrator.git_utils import get_commit_count

        with patch('orchestrator.git_utils.run_git') as mock_run:
            merge_base_result = MagicMock()
            merge_base_result.returncode = 0
            merge_base_result.stdout = "abc123\n"

            count_result = MagicMock()
            count_result.returncode = 0
            count_result.stdout = "0\n"

            mock_run.side_effect = [merge_base_result, count_result]

            count = get_commit_count(temp_dir)

            assert count == 0

    def test_get_commit_count_error(self, temp_dir):
        """Test that errors return 0."""
        from orchestrator.git_utils import get_commit_count

        with patch('orchestrator.git_utils.run_git') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "git")

            count = get_commit_count(temp_dir)

            assert count == 0

    def test_get_commit_count_invalid_output(self, temp_dir):
        """Test handling invalid git output."""
        from orchestrator.git_utils import get_commit_count

        with patch('orchestrator.git_utils.run_git') as mock_run:
            merge_base_result = MagicMock()
            merge_base_result.returncode = 0
            merge_base_result.stdout = "abc123\n"

            count_result = MagicMock()
            count_result.returncode = 0
            count_result.stdout = "not a number\n"

            mock_run.side_effect = [merge_base_result, count_result]

            count = get_commit_count(temp_dir)

            assert count == 0


class TestRunGit:
    """Tests for run_git helper function."""

    def test_run_git_success(self, temp_dir):
        """Test successful git command."""
        from orchestrator.git_utils import run_git

        with patch('subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "output\n"
            mock_run.return_value = mock_result

            result = run_git(["status"], cwd=temp_dir)

            assert result.returncode == 0
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args[0][0] == ["git", "status"]

    def test_run_git_with_check(self, temp_dir):
        """Test that check=True raises on non-zero exit."""
        from orchestrator.git_utils import run_git

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "git status")

            with pytest.raises(subprocess.CalledProcessError):
                run_git(["status"], cwd=temp_dir, check=True)

    def test_run_git_without_check(self, temp_dir):
        """Test that check=False doesn't raise."""
        from orchestrator.git_utils import run_git

        with patch('subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_run.return_value = mock_result

            result = run_git(["status"], cwd=temp_dir, check=False)

            assert result.returncode == 1


class TestHasUncommittedChanges:
    """Tests for has_uncommitted_changes function."""

    def test_has_uncommitted_changes_clean(self, temp_dir):
        """Test clean working directory."""
        from orchestrator.git_utils import has_uncommitted_changes

        with patch('orchestrator.git_utils.run_git') as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = ""
            mock_run.return_value = mock_result

            has_changes = has_uncommitted_changes(temp_dir)

            assert has_changes is False

    def test_has_uncommitted_changes_dirty(self, temp_dir):
        """Test dirty working directory."""
        from orchestrator.git_utils import has_uncommitted_changes

        with patch('orchestrator.git_utils.run_git') as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = "M file.py\n"
            mock_run.return_value = mock_result

            has_changes = has_uncommitted_changes(temp_dir)

            assert has_changes is True


class TestGetSubmoduleStatus:
    """Tests for get_submodule_status function."""

    def test_nonexistent_submodule(self, temp_dir):
        """Returns exists=False when submodule directory doesn't exist."""
        from orchestrator.git_utils import get_submodule_status

        result = get_submodule_status(temp_dir)
        assert result["exists"] is False
        assert result["branch"] == ""
        assert result["commits_ahead"] == 0

    def test_submodule_without_git(self, temp_dir):
        """Returns exists=False when submodule dir exists but has no .git."""
        from orchestrator.git_utils import get_submodule_status

        (temp_dir / "orchestrator").mkdir()
        result = get_submodule_status(temp_dir)
        assert result["exists"] is False

    def test_submodule_on_main_branch(self, temp_dir):
        """Reports branch correctly when on main."""
        from orchestrator.git_utils import get_submodule_status

        sub = temp_dir / "orchestrator"
        sub.mkdir()
        (sub / ".git").write_text("gitdir: ...")

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.returncode = 0
            if args[:2] == ["rev-parse", "--abbrev-ref"]:
                result.stdout = "main\n"
            elif args[:2] == ["rev-list", "--count"]:
                result.stdout = "3\n"
            elif args[:2] == ["log", "--oneline"]:
                result.stdout = "abc1234 fix something\ndef5678 add feature\nghi9012 init\n"
            elif args[:2] == ["diff", "--shortstat"]:
                result.stdout = ""
            elif args[:3] == ["diff", "--cached", "--shortstat"]:
                result.stdout = ""
            elif args[:2] == ["ls-files", "--others"]:
                result.stdout = ""
            else:
                result.stdout = ""
            return result

        with patch('orchestrator.git_utils.run_git', side_effect=mock_run_git):
            result = get_submodule_status(temp_dir)

        assert result["exists"] is True
        assert result["branch"] == "main"
        assert result["commits_ahead"] == 3
        assert len(result["recent_commits"]) == 3
        assert result["warnings"] == []

    def test_submodule_detached_head(self, temp_dir):
        """Reports detached HEAD with warning."""
        from orchestrator.git_utils import get_submodule_status

        sub = temp_dir / "orchestrator"
        sub.mkdir()
        (sub / ".git").write_text("gitdir: ...")

        call_count = [0]

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.returncode = 0
            if args[:2] == ["rev-parse", "--abbrev-ref"]:
                result.stdout = "HEAD\n"  # detached
            elif args[:2] == ["rev-parse", "--short"]:
                result.stdout = "abc1234\n"
            elif args[:2] == ["rev-list", "--count"]:
                result.stdout = "0\n"
            else:
                result.stdout = ""
            return result

        with patch('orchestrator.git_utils.run_git', side_effect=mock_run_git):
            result = get_submodule_status(temp_dir)

        assert result["exists"] is True
        assert "DETACHED" in result["branch"]
        assert "abc1234" in result["branch"]
        assert "submodule HEAD is detached" in result["warnings"]

    def test_submodule_wrong_branch(self, temp_dir):
        """Warns when submodule is on unexpected branch."""
        from orchestrator.git_utils import get_submodule_status

        sub = temp_dir / "orchestrator"
        sub.mkdir()
        (sub / ".git").write_text("gitdir: ...")

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.returncode = 0
            if args[:2] == ["rev-parse", "--abbrev-ref"]:
                result.stdout = "some-other-branch\n"
            elif args[:2] == ["rev-list", "--count"]:
                result.stdout = "0\n"
            else:
                result.stdout = ""
            return result

        with patch('orchestrator.git_utils.run_git', side_effect=mock_run_git):
            result = get_submodule_status(temp_dir)

        assert result["branch"] == "some-other-branch"
        assert any("unexpected branch" in w for w in result["warnings"])

    def test_submodule_with_unstaged_changes(self, temp_dir):
        """Reports unstaged changes in submodule."""
        from orchestrator.git_utils import get_submodule_status

        sub = temp_dir / "orchestrator"
        sub.mkdir()
        (sub / ".git").write_text("gitdir: ...")

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.returncode = 0
            if args[:2] == ["rev-parse", "--abbrev-ref"]:
                result.stdout = "main\n"
            elif args[:2] == ["rev-list", "--count"]:
                result.stdout = "0\n"
            elif args == ["diff", "--shortstat"]:
                result.stdout = " 2 files changed, 15 insertions(+), 3 deletions(-)\n"
            elif args[:3] == ["diff", "--cached", "--shortstat"]:
                result.stdout = ""
            elif args[:2] == ["ls-files", "--others"]:
                result.stdout = "new_file.py\nanother.py\n"
            else:
                result.stdout = ""
            return result

        with patch('orchestrator.git_utils.run_git', side_effect=mock_run_git):
            result = get_submodule_status(temp_dir)

        assert result["diff_shortstat"] == "2 files changed, 15 insertions(+), 3 deletions(-)"
        assert result["untracked_count"] == 2

    def test_submodule_custom_name(self, temp_dir):
        """Respects custom submodule name."""
        from orchestrator.git_utils import get_submodule_status

        sub = temp_dir / "custom-sub"
        sub.mkdir()
        (sub / ".git").write_text("gitdir: ...")

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.returncode = 0
            if args[:2] == ["rev-parse", "--abbrev-ref"]:
                result.stdout = "main\n"
            elif args[:2] == ["rev-list", "--count"]:
                result.stdout = "0\n"
            else:
                result.stdout = ""
            return result

        with patch('orchestrator.git_utils.run_git', side_effect=mock_run_git):
            result = get_submodule_status(temp_dir, submodule_name="custom-sub")

        assert result["exists"] is True
        assert result["branch"] == "main"

    def test_submodule_git_failures_graceful(self, temp_dir):
        """Handles git command failures gracefully."""
        from orchestrator.git_utils import get_submodule_status

        sub = temp_dir / "orchestrator"
        sub.mkdir()
        (sub / ".git").write_text("gitdir: ...")

        def mock_run_git(args, cwd=None, check=True):
            raise subprocess.SubprocessError("git not available")

        with patch('orchestrator.git_utils.run_git', side_effect=mock_run_git):
            result = get_submodule_status(temp_dir)

        # Should not raise, return safe defaults
        assert result["exists"] is True
        assert result["branch"] == ""
        assert result["commits_ahead"] == 0


class TestCreateTaskWorktree:
    """Tests for create_task_worktree branch mismatch detection."""

    def _make_worktree(self, path: Path) -> None:
        """Create a fake valid worktree directory."""
        path.mkdir(parents=True)
        (path / ".git").write_text("gitdir: ...")

    def test_reuses_existing_worktree_on_correct_branch(self, temp_dir):
        """Existing worktree is reused when it's based on the correct branch."""
        from orchestrator.git_utils import create_task_worktree

        task = {"id": "abc12345", "branch": "main"}
        worktree_path = temp_dir / "tasks" / "abc12345" / "worktree"
        self._make_worktree(worktree_path)

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "abc123def456\n"
            return result

        with patch('orchestrator.git_utils.find_parent_project', return_value=temp_dir), \
             patch('orchestrator.git_utils.get_task_worktree_path', return_value=worktree_path), \
             patch('orchestrator.git_utils.run_git', side_effect=mock_run_git) as mock_git:

            result = create_task_worktree(task)

            assert result == worktree_path
            # Should NOT have called worktree add (reused existing)
            add_calls = [c for c in mock_git.call_args_list if c[0][0][:2] == ["worktree", "add"]]
            assert len(add_calls) == 0

    def test_recreates_worktree_on_branch_mismatch(self, temp_dir):
        """Existing worktree is deleted and recreated when based on wrong branch."""
        from orchestrator.git_utils import create_task_worktree

        task = {"id": "abc12345", "branch": "feature/new-branch"}
        worktree_path = temp_dir / "tasks" / "abc12345" / "worktree"
        self._make_worktree(worktree_path)

        call_log = []

        def mock_run_git(args, cwd=None, check=True):
            call_log.append(args[:])
            result = MagicMock()
            result.stdout = "abc123def456\n"

            if args[0] == "rev-parse" and "--verify" in args:
                # Both verify calls succeed
                result.returncode = 0
            elif args == ["rev-parse", "HEAD"]:
                result.returncode = 0
                result.stdout = "oldcommit111\n"
            elif args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                # Detached HEAD check — return "HEAD" to pass the assertion
                result.returncode = 0
                result.stdout = "HEAD\n"
            elif args[0] == "merge-base" and "--is-ancestor" in args:
                # origin/feature/new-branch is NOT ancestor of old worktree HEAD
                result.returncode = 1
            elif args[0] == "worktree" and args[1] == "remove":
                # Remove the worktree directory to simulate actual removal
                import shutil
                if worktree_path.exists():
                    shutil.rmtree(worktree_path)
                result.returncode = 0
            elif args[0] == "worktree" and args[1] == "prune":
                result.returncode = 0
            elif args == ["fetch", "origin"]:
                result.returncode = 0
            elif args[0] == "worktree" and args[1] == "add":
                # Simulate creating the worktree directory
                worktree_path.mkdir(parents=True, exist_ok=True)
                result.returncode = 0
            else:
                result.returncode = 0
            return result

        with patch('orchestrator.git_utils.find_parent_project', return_value=temp_dir), \
             patch('orchestrator.git_utils.get_task_worktree_path', return_value=worktree_path), \
             patch('orchestrator.git_utils.run_git', side_effect=mock_run_git):

            result = create_task_worktree(task)

            assert result == worktree_path
            # Worktree remove must have been called at least once (to remove the mismatched worktree)
            remove_calls = [c for c in call_log if c[:2] == ["worktree", "remove"]]
            assert len(remove_calls) >= 1
            # Worktree add must have been called to recreate
            add_calls = [c for c in call_log if c[:2] == ["worktree", "add"]]
            assert len(add_calls) == 1

    def test_logs_mismatch_message(self, temp_dir, capsys):
        """Branch mismatch logs a clear debug message."""
        from orchestrator.git_utils import create_task_worktree

        task = {"id": "abc12345", "branch": "feature/correct-branch"}
        worktree_path = temp_dir / "tasks" / "abc12345" / "worktree"
        self._make_worktree(worktree_path)

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.stdout = "somecommit\n"
            if args[0] == "merge-base" and "--is-ancestor" in args:
                result.returncode = 1  # mismatch
            elif args[0] == "worktree" and args[1] == "remove":
                import shutil
                if worktree_path.exists():
                    shutil.rmtree(worktree_path)
                result.returncode = 0
            elif args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                # Detached HEAD check — return "HEAD" to pass the assertion
                result.returncode = 0
                result.stdout = "HEAD\n"
            else:
                result.returncode = 0
            return result

        with patch('orchestrator.git_utils.find_parent_project', return_value=temp_dir), \
             patch('orchestrator.git_utils.get_task_worktree_path', return_value=worktree_path), \
             patch('orchestrator.git_utils.run_git', side_effect=mock_run_git):

            create_task_worktree(task)

        captured = capsys.readouterr()
        assert "branch mismatch" in captured.out.lower()
        assert "abc12345" in captured.out
        assert "feature/correct-branch" in captured.out

    def test_does_not_check_branch_for_new_worktree(self, temp_dir):
        """Branch check is skipped when no worktree exists yet."""
        from orchestrator.git_utils import create_task_worktree

        task = {"id": "newtask1", "branch": "main"}
        worktree_path = temp_dir / "tasks" / "newtask1" / "worktree"
        # Don't create worktree — it doesn't exist yet

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "abc123\n"
            if args[:2] == ["worktree", "add"]:
                worktree_path.mkdir(parents=True, exist_ok=True)
            elif args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                # Detached HEAD check — return "HEAD" to pass the assertion
                result.stdout = "HEAD\n"
            return result

        with patch('orchestrator.git_utils.find_parent_project', return_value=temp_dir), \
             patch('orchestrator.git_utils.get_task_worktree_path', return_value=worktree_path), \
             patch('orchestrator.git_utils.run_git', side_effect=mock_run_git) as mock_git:

            result = create_task_worktree(task)

            assert result == worktree_path
            # merge-base should NOT have been called (no existing worktree to check)
            merge_base_calls = [
                c for c in mock_git.call_args_list
                if c[0][0][:2] == ["merge-base"]
            ]
            assert len(merge_base_calls) == 0

    def test_treats_missing_origin_branch_as_match(self, temp_dir):
        """If origin/<branch> doesn't exist, existing worktree is kept."""
        from orchestrator.git_utils import create_task_worktree

        task = {"id": "abc12345", "branch": "nonexistent-branch"}
        worktree_path = temp_dir / "tasks" / "abc12345" / "worktree"
        self._make_worktree(worktree_path)

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.stdout = "abc123\n"
            if args[:2] == ["rev-parse", "--verify"]:
                # origin/nonexistent-branch doesn't exist
                result.returncode = 1
            else:
                result.returncode = 0
            return result

        with patch('orchestrator.git_utils.find_parent_project', return_value=temp_dir), \
             patch('orchestrator.git_utils.get_task_worktree_path', return_value=worktree_path), \
             patch('orchestrator.git_utils.run_git', side_effect=mock_run_git) as mock_git:

            result = create_task_worktree(task)

            assert result == worktree_path
            # Should not recreate the worktree
            add_calls = [c for c in mock_git.call_args_list if c[0][0][:2] == ["worktree", "add"]]
            assert len(add_calls) == 0
