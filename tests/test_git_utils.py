"""Tests for orchestrator.git_utils module."""

import pytest
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, call


class TestEnsureWorktree:
    """Tests for ensure_worktree using origin/main as base."""

    def test_new_worktree_uses_origin_ref(self, temp_dir):
        """New worktrees are created from origin/{base_branch}, not local branch."""
        from octopoid.git_utils import ensure_worktree

        worktree_path = temp_dir / "agents" / "test-agent" / "worktree"

        with patch('octopoid.git_utils.find_parent_project', return_value=temp_dir), \
             patch('octopoid.git_utils.get_worktree_path', return_value=worktree_path), \
             patch('octopoid.git_utils.run_git') as mock_run:

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
        from octopoid.git_utils import ensure_worktree

        worktree_path = temp_dir / "agents" / "test-agent" / "worktree"

        with patch('octopoid.git_utils.find_parent_project', return_value=temp_dir), \
             patch('octopoid.git_utils.get_worktree_path', return_value=worktree_path), \
             patch('octopoid.git_utils.run_git') as mock_run:

            mock_run.return_value = MagicMock(returncode=0)

            ensure_worktree("test-agent", base_branch="feature/test")

            add_calls = [c for c in mock_run.call_args_list
                         if c[0][0][:2] == ["worktree", "add"]]
            assert len(add_calls) == 1
            assert add_calls[0][0][0][-1] == "origin/feature/test"

    def test_new_worktree_fetches_before_create(self, temp_dir):
        """Fetch happens before worktree creation."""
        from octopoid.git_utils import ensure_worktree

        worktree_path = temp_dir / "agents" / "test-agent" / "worktree"
        call_order = []

        def tracking_run_git(args, cwd=None, check=True):
            call_order.append(args[:2])
            return MagicMock(returncode=0)

        with patch('octopoid.git_utils.find_parent_project', return_value=temp_dir), \
             patch('octopoid.git_utils.get_worktree_path', return_value=worktree_path), \
             patch('octopoid.git_utils.run_git', side_effect=tracking_run_git):

            ensure_worktree("test-agent")

            fetch_idx = next(i for i, c in enumerate(call_order) if c == ["fetch", "origin"])
            add_idx = next(i for i, c in enumerate(call_order) if c == ["worktree", "add"])
            assert fetch_idx < add_idx, "fetch must happen before worktree add"

    def test_existing_worktree_resets_to_origin(self, temp_dir):
        """Existing worktrees are reset to origin/{base_branch}."""
        from octopoid.git_utils import ensure_worktree

        worktree_path = temp_dir / "agents" / "test-agent" / "worktree"
        worktree_path.mkdir(parents=True)
        (worktree_path / ".git").write_text("gitdir: ...")

        with patch('octopoid.git_utils.find_parent_project', return_value=temp_dir), \
             patch('octopoid.git_utils.get_worktree_path', return_value=worktree_path), \
             patch('octopoid.git_utils.run_git') as mock_run:

            mock_run.return_value = MagicMock(returncode=0)

            ensure_worktree("test-agent", base_branch="main")

            # Should checkout --detach origin/main
            checkout_calls = [c for c in mock_run.call_args_list
                              if len(c[0][0]) >= 3 and c[0][0][:2] == ["checkout", "--detach"]]
            assert len(checkout_calls) == 1
            assert checkout_calls[0][0][0][2] == "origin/main"

    def test_existing_worktree_fetches_first(self, temp_dir):
        """Existing worktrees fetch origin before resetting."""
        from octopoid.git_utils import ensure_worktree

        worktree_path = temp_dir / "agents" / "test-agent" / "worktree"
        worktree_path.mkdir(parents=True)
        (worktree_path / ".git").write_text("gitdir: ...")

        call_order = []

        def tracking_run_git(args, cwd=None, check=True):
            call_order.append(args[:2])
            return MagicMock(returncode=0)

        with patch('octopoid.git_utils.find_parent_project', return_value=temp_dir), \
             patch('octopoid.git_utils.get_worktree_path', return_value=worktree_path), \
             patch('octopoid.git_utils.run_git', side_effect=tracking_run_git):

            ensure_worktree("test-agent")

            # fetch must come before checkout
            fetch_idx = next(i for i, c in enumerate(call_order) if c == ["fetch", "origin"])
            checkout_idx = next(i for i, c in enumerate(call_order) if c == ["checkout", "--detach"])
            assert fetch_idx < checkout_idx

    def test_new_worktree_fallback_to_local_on_missing_origin(self, temp_dir):
        """Falls back to local branch if origin ref doesn't exist."""
        from octopoid.git_utils import ensure_worktree

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

        with patch('octopoid.git_utils.find_parent_project', return_value=temp_dir), \
             patch('octopoid.git_utils.get_worktree_path', return_value=worktree_path), \
             patch('octopoid.git_utils.run_git', side_effect=mock_run_git):

            ensure_worktree("test-agent")

            assert call_count[0] == 2  # Tried origin/main then fell back to main


class TestCreateFeatureBranch:
    """Tests for create_feature_branch using origin/main as base."""

    def test_branch_based_on_origin(self, temp_dir):
        """Feature branch is created from origin/{base_branch}."""
        from octopoid.git_utils import create_feature_branch

        with patch('octopoid.git_utils.run_git') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            branch = create_feature_branch(temp_dir, "test-task", base_branch="main")

            # Should checkout --detach origin/main (not local main)
            checkout_calls = [c for c in mock_run.call_args_list
                              if len(c[0][0]) >= 3 and c[0][0][:2] == ["checkout", "--detach"]]
            assert len(checkout_calls) == 1
            assert checkout_calls[0][0][0][2] == "origin/main"

    def test_branch_based_on_origin_custom_base(self, temp_dir):
        """Custom base_branch uses origin/ prefix."""
        from octopoid.git_utils import create_feature_branch

        with patch('octopoid.git_utils.run_git') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            create_feature_branch(temp_dir, "test-task", base_branch="feature/xyz")

            checkout_calls = [c for c in mock_run.call_args_list
                              if len(c[0][0]) >= 3 and c[0][0][:2] == ["checkout", "--detach"]]
            assert len(checkout_calls) == 1
            assert checkout_calls[0][0][0][2] == "origin/feature/xyz"

    def test_fetches_before_checkout(self, temp_dir):
        """Fetch happens before creating the branch."""
        from octopoid.git_utils import create_feature_branch

        call_order = []

        def tracking_run_git(args, cwd=None, check=True):
            call_order.append(args[:2])
            return MagicMock(returncode=0)

        with patch('octopoid.git_utils.run_git', side_effect=tracking_run_git):
            create_feature_branch(temp_dir, "test-task")

            fetch_idx = next(i for i, c in enumerate(call_order) if c == ["fetch", "origin"])
            checkout_idx = next(i for i, c in enumerate(call_order) if c == ["checkout", "--detach"])
            assert fetch_idx < checkout_idx

    def test_does_not_checkout_local_main(self, temp_dir):
        """Does NOT check out bare 'main' (local) as the base."""
        from octopoid.git_utils import create_feature_branch

        with patch('octopoid.git_utils.run_git') as mock_run:
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
        from octopoid.git_utils import create_feature_branch

        call_count = [0]

        def mock_run_git(args, cwd=None, check=True):
            if args[:2] == ["checkout", "--detach"]:
                raise subprocess.CalledProcessError(1, "git")
            return MagicMock(returncode=0)

        with patch('octopoid.git_utils.run_git', side_effect=mock_run_git):
            branch = create_feature_branch(temp_dir, "test-task")

            assert branch.startswith("agent/test-task-")

    def test_branch_name_format(self, temp_dir):
        """Branch name follows agent/{task_id}-{timestamp} format."""
        from octopoid.git_utils import create_feature_branch

        with patch('octopoid.git_utils.run_git') as mock_run:
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
        from octopoid.git_utils import get_commit_count

        # Mock the git commands
        with patch('octopoid.git_utils.run_git') as mock_run:
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
        from octopoid.git_utils import get_commit_count

        with patch('octopoid.git_utils.run_git') as mock_run:
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
        from octopoid.git_utils import get_commit_count

        with patch('octopoid.git_utils.run_git') as mock_run:
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
        from octopoid.git_utils import get_commit_count

        with patch('octopoid.git_utils.run_git') as mock_run:
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
        from octopoid.git_utils import get_commit_count

        with patch('octopoid.git_utils.run_git') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "git")

            count = get_commit_count(temp_dir)

            assert count == 0

    def test_get_commit_count_invalid_output(self, temp_dir):
        """Test handling invalid git output."""
        from octopoid.git_utils import get_commit_count

        with patch('octopoid.git_utils.run_git') as mock_run:
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
        from octopoid.git_utils import run_git

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
        from octopoid.git_utils import run_git

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "git status")

            with pytest.raises(subprocess.CalledProcessError):
                run_git(["status"], cwd=temp_dir, check=True)

    def test_run_git_without_check(self, temp_dir):
        """Test that check=False doesn't raise."""
        from octopoid.git_utils import run_git

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
        from octopoid.git_utils import has_uncommitted_changes

        with patch('octopoid.git_utils.run_git') as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = ""
            mock_run.return_value = mock_result

            has_changes = has_uncommitted_changes(temp_dir)

            assert has_changes is False

    def test_has_uncommitted_changes_dirty(self, temp_dir):
        """Test dirty working directory."""
        from octopoid.git_utils import has_uncommitted_changes

        with patch('octopoid.git_utils.run_git') as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = "M file.py\n"
            mock_run.return_value = mock_result

            has_changes = has_uncommitted_changes(temp_dir)

            assert has_changes is True


class TestGetSubmoduleStatus:
    """Tests for get_submodule_status function."""

    def test_nonexistent_submodule(self, temp_dir):
        """Returns exists=False when submodule directory doesn't exist."""
        from octopoid.git_utils import get_submodule_status

        result = get_submodule_status(temp_dir)
        assert result["exists"] is False
        assert result["branch"] == ""
        assert result["commits_ahead"] == 0

    def test_submodule_without_git(self, temp_dir):
        """Returns exists=False when submodule dir exists but has no .git."""
        from octopoid.git_utils import get_submodule_status

        (temp_dir / "orchestrator").mkdir()
        result = get_submodule_status(temp_dir)
        assert result["exists"] is False

    def test_submodule_on_main_branch(self, temp_dir):
        """Reports branch correctly when on main."""
        from octopoid.git_utils import get_submodule_status

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

        with patch('octopoid.git_utils.run_git', side_effect=mock_run_git):
            result = get_submodule_status(temp_dir)

        assert result["exists"] is True
        assert result["branch"] == "main"
        assert result["commits_ahead"] == 3
        assert len(result["recent_commits"]) == 3
        assert result["warnings"] == []

    def test_submodule_detached_head(self, temp_dir):
        """Reports detached HEAD with warning."""
        from octopoid.git_utils import get_submodule_status

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

        with patch('octopoid.git_utils.run_git', side_effect=mock_run_git):
            result = get_submodule_status(temp_dir)

        assert result["exists"] is True
        assert "DETACHED" in result["branch"]
        assert "abc1234" in result["branch"]
        assert "submodule HEAD is detached" in result["warnings"]

    def test_submodule_wrong_branch(self, temp_dir):
        """Warns when submodule is on unexpected branch."""
        from octopoid.git_utils import get_submodule_status

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

        with patch('octopoid.git_utils.run_git', side_effect=mock_run_git):
            result = get_submodule_status(temp_dir)

        assert result["branch"] == "some-other-branch"
        assert any("unexpected branch" in w for w in result["warnings"])

    def test_submodule_with_unstaged_changes(self, temp_dir):
        """Reports unstaged changes in submodule."""
        from octopoid.git_utils import get_submodule_status

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

        with patch('octopoid.git_utils.run_git', side_effect=mock_run_git):
            result = get_submodule_status(temp_dir)

        assert result["diff_shortstat"] == "2 files changed, 15 insertions(+), 3 deletions(-)"
        assert result["untracked_count"] == 2

    def test_submodule_custom_name(self, temp_dir):
        """Respects custom submodule name."""
        from octopoid.git_utils import get_submodule_status

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

        with patch('octopoid.git_utils.run_git', side_effect=mock_run_git):
            result = get_submodule_status(temp_dir, submodule_name="custom-sub")

        assert result["exists"] is True
        assert result["branch"] == "main"

    def test_submodule_git_failures_graceful(self, temp_dir):
        """Handles git command failures gracefully."""
        from octopoid.git_utils import get_submodule_status

        sub = temp_dir / "orchestrator"
        sub.mkdir()
        (sub / ".git").write_text("gitdir: ...")

        def mock_run_git(args, cwd=None, check=True):
            raise subprocess.SubprocessError("git not available")

        with patch('octopoid.git_utils.run_git', side_effect=mock_run_git):
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
        from octopoid.git_utils import create_task_worktree

        task = {"id": "abc12345", "branch": "main"}
        worktree_path = temp_dir / "tasks" / "abc12345" / "worktree"
        self._make_worktree(worktree_path)
        # Write matching base_branch file — worktree was created for "main"
        (worktree_path.parent / "base_branch").write_text("main")

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "abc123def456\n"
            return result

        with patch('octopoid.git_utils.find_parent_project', return_value=temp_dir), \
             patch('octopoid.git_utils.get_task_worktree_path', return_value=worktree_path), \
             patch('octopoid.git_utils.run_git', side_effect=mock_run_git) as mock_git:

            result = create_task_worktree(task)

            assert result == worktree_path
            # Should NOT have called worktree add (reused existing)
            add_calls = [c for c in mock_git.call_args_list if c[0][0][:2] == ["worktree", "add"]]
            assert len(add_calls) == 0

    def test_recreates_worktree_on_branch_mismatch(self, temp_dir):
        """Existing worktree is deleted and recreated when based on wrong branch."""
        from octopoid.git_utils import create_task_worktree

        task = {"id": "abc12345", "branch": "feature/new-branch"}
        worktree_path = temp_dir / "tasks" / "abc12345" / "worktree"
        self._make_worktree(worktree_path)
        # Write old base_branch — worktree was created from "main", task now wants "feature/new-branch"
        (worktree_path.parent / "base_branch").write_text("main")

        call_log = []

        def mock_run_git(args, cwd=None, check=True):
            call_log.append(args[:])
            result = MagicMock()
            result.stdout = "abc123def456\n"

            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                # Detached HEAD check — return "HEAD" to pass the assertion
                result.returncode = 0
                result.stdout = "HEAD\n"
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

        with patch('octopoid.git_utils.find_parent_project', return_value=temp_dir), \
             patch('octopoid.git_utils.get_task_worktree_path', return_value=worktree_path), \
             patch('octopoid.git_utils.run_git', side_effect=mock_run_git):

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
        from octopoid.git_utils import create_task_worktree

        task = {"id": "abc12345", "branch": "feature/correct-branch"}
        worktree_path = temp_dir / "tasks" / "abc12345" / "worktree"
        self._make_worktree(worktree_path)
        # Write old base_branch — worktree was from "main", task now wants "feature/correct-branch"
        (worktree_path.parent / "base_branch").write_text("main")

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.stdout = "somecommit\n"
            if args[0] == "worktree" and args[1] == "remove":
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

        with patch('octopoid.git_utils.find_parent_project', return_value=temp_dir), \
             patch('octopoid.git_utils.get_task_worktree_path', return_value=worktree_path), \
             patch('octopoid.git_utils.run_git', side_effect=mock_run_git):

            create_task_worktree(task)

        captured = capsys.readouterr()
        assert "branch mismatch" in captured.out.lower()
        assert "abc12345" in captured.out
        assert "feature/correct-branch" in captured.out

    def test_does_not_check_branch_for_new_worktree(self, temp_dir):
        """Branch check is skipped when no worktree exists yet."""
        from octopoid.git_utils import create_task_worktree

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

        with patch('octopoid.git_utils.find_parent_project', return_value=temp_dir), \
             patch('octopoid.git_utils.get_task_worktree_path', return_value=worktree_path), \
             patch('octopoid.git_utils.run_git', side_effect=mock_run_git) as mock_git:

            result = create_task_worktree(task)

            assert result == worktree_path
            # merge-base should NOT have been called (no existing worktree to check)
            merge_base_calls = [
                c for c in mock_git.call_args_list
                if c[0][0][:2] == ["merge-base"]
            ]
            assert len(merge_base_calls) == 0

    def test_treats_missing_origin_branch_as_match(self, temp_dir):
        """If no base_branch file exists, existing worktree is kept (safe fallback)."""
        from octopoid.git_utils import create_task_worktree

        task = {"id": "abc12345", "branch": "nonexistent-branch"}
        worktree_path = temp_dir / "tasks" / "abc12345" / "worktree"
        self._make_worktree(worktree_path)
        # No base_branch file — fallback to True (keep worktree)

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.stdout = "abc123\n"
            result.returncode = 0
            return result

        with patch('octopoid.git_utils.find_parent_project', return_value=temp_dir), \
             patch('octopoid.git_utils.get_task_worktree_path', return_value=worktree_path), \
             patch('octopoid.git_utils.run_git', side_effect=mock_run_git) as mock_git:

            result = create_task_worktree(task)

            assert result == worktree_path
            # Should not recreate the worktree
            add_calls = [c for c in mock_git.call_args_list if c[0][0][:2] == ["worktree", "add"]]
            assert len(add_calls) == 0

    def test_base_branch_file_written_on_create(self, temp_dir):
        """base_branch file is written when a new worktree is created."""
        from octopoid.git_utils import create_task_worktree

        task = {"id": "newtask9", "branch": "feature/cool"}
        worktree_path = temp_dir / "tasks" / "newtask9" / "worktree"

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "HEAD\n" if args == ["rev-parse", "--abbrev-ref", "HEAD"] else "abc\n"
            if args[:2] == ["worktree", "add"]:
                worktree_path.mkdir(parents=True, exist_ok=True)
            return result

        with patch('octopoid.git_utils.find_parent_project', return_value=temp_dir), \
             patch('octopoid.git_utils.get_task_worktree_path', return_value=worktree_path), \
             patch('octopoid.git_utils.run_git', side_effect=mock_run_git):

            create_task_worktree(task)

        base_branch_file = worktree_path.parent / "base_branch"
        assert base_branch_file.exists(), "base_branch file should be written after creation"
        assert base_branch_file.read_text().strip() == "feature/cool"

    def test_main_advancing_does_not_destroy_worktree(self, temp_dir):
        """When main advances past the worktree base, the worktree is preserved.

        This is the key regression test: the old code used git ancestry to check
        branch match, which failed whenever main advanced (adding new commits on
        main makes origin/main no longer an ancestor of the worktree HEAD).
        The new code uses the stored base_branch file, so advancing main has no
        effect on the branch-match check.
        """
        from octopoid.git_utils import create_task_worktree

        task = {"id": "abc12345", "branch": "main"}
        worktree_path = temp_dir / "tasks" / "abc12345" / "worktree"
        self._make_worktree(worktree_path)
        # Write matching base_branch — worktree was created from "main"
        (worktree_path.parent / "base_branch").write_text("main")

        # Simulate: old origin/main was A, agent made commits B+C (worktree HEAD=C),
        # now origin/main has advanced to D+E. origin/main is no longer an ancestor
        # of worktree HEAD C. The old git ancestry check would return False here.
        # The new file-based check should return True (main == main).
        git_call_log = []

        def mock_run_git(args, cwd=None, check=True):
            git_call_log.append(args[:])
            result = MagicMock()
            result.returncode = 0
            result.stdout = "abc123\n"
            return result

        with patch('octopoid.git_utils.find_parent_project', return_value=temp_dir), \
             patch('octopoid.git_utils.get_task_worktree_path', return_value=worktree_path), \
             patch('octopoid.git_utils.run_git', side_effect=mock_run_git) as mock_git:

            result = create_task_worktree(task)

        # Worktree should be reused, not recreated
        assert result == worktree_path
        add_calls = [c for c in git_call_log if c[:2] == ["worktree", "add"]]
        assert len(add_calls) == 0, "Worktree should not have been recreated"
        remove_calls = [c for c in git_call_log if c[:2] == ["worktree", "remove"]]
        assert len(remove_calls) == 0, "Worktree should not have been removed"

    def test_previous_commits_preserved_when_reusing(self, temp_dir):
        """Reused worktree still has its prior commits (git history preserved)."""
        from octopoid.git_utils import create_task_worktree

        task = {"id": "abc12345", "branch": "main"}
        worktree_path = temp_dir / "tasks" / "abc12345" / "worktree"
        self._make_worktree(worktree_path)
        (worktree_path.parent / "base_branch").write_text("main")

        # Simulate a file that the "previous agent" created
        prior_work_file = worktree_path / "prior_work.py"
        prior_work_file.write_text("# Previous agent's work")

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "abc123\n"
            return result

        with patch('octopoid.git_utils.find_parent_project', return_value=temp_dir), \
             patch('octopoid.git_utils.get_task_worktree_path', return_value=worktree_path), \
             patch('octopoid.git_utils.run_git', side_effect=mock_run_git):

            result = create_task_worktree(task)

        # The prior work file should still exist
        assert prior_work_file.exists(), "Previous agent's work files should be preserved"
        assert prior_work_file.read_text() == "# Previous agent's work"

    def test_legitimate_branch_mismatch_destroys_worktree(self, temp_dir):
        """Worktree is recreated when task targets a genuinely different branch.

        Example: worktree was created from feature/foo, task now targets main.
        """
        from octopoid.git_utils import create_task_worktree

        # Task was originally on feature/foo branch, now retargeted to main
        task = {"id": "abc12345", "branch": "main"}
        worktree_path = temp_dir / "tasks" / "abc12345" / "worktree"
        self._make_worktree(worktree_path)
        # base_branch file shows the old branch
        (worktree_path.parent / "base_branch").write_text("feature/foo")

        call_log = []

        def mock_run_git(args, cwd=None, check=True):
            call_log.append(args[:])
            result = MagicMock()
            result.returncode = 0
            result.stdout = "HEAD\n" if args == ["rev-parse", "--abbrev-ref", "HEAD"] else "abc\n"
            if args[:2] == ["worktree", "remove"]:
                import shutil
                if worktree_path.exists():
                    shutil.rmtree(worktree_path)
            elif args[:2] == ["worktree", "add"]:
                worktree_path.mkdir(parents=True, exist_ok=True)
            return result

        with patch('octopoid.git_utils.find_parent_project', return_value=temp_dir), \
             patch('octopoid.git_utils.get_task_worktree_path', return_value=worktree_path), \
             patch('octopoid.git_utils.run_git', side_effect=mock_run_git):

            result = create_task_worktree(task)

        assert result == worktree_path
        # Worktree should have been removed and recreated
        remove_calls = [c for c in call_log if c[:2] == ["worktree", "remove"]]
        assert len(remove_calls) >= 1, "Old mismatched worktree should have been removed"
        add_calls = [c for c in call_log if c[:2] == ["worktree", "add"]]
        assert len(add_calls) == 1, "New worktree should have been created"
        # New base_branch file should reflect the new branch
        assert (worktree_path.parent / "base_branch").read_text().strip() == "main"


class TestWorktreeBranchMatches:
    """Tests for the _worktree_branch_matches branch detection logic."""

    def test_matches_when_stored_branch_equals_requested(self, temp_dir):
        """Returns True when stored base_branch matches the requested branch."""
        from octopoid.git_utils import _worktree_branch_matches

        worktree_path = temp_dir / "worktree"
        worktree_path.mkdir(parents=True)
        (worktree_path.parent / "base_branch").write_text("main")

        result = _worktree_branch_matches(temp_dir, worktree_path, "main")
        assert result is True

    def test_mismatch_when_stored_branch_differs(self, temp_dir):
        """Returns False when stored base_branch does not match the requested branch."""
        from octopoid.git_utils import _worktree_branch_matches

        worktree_path = temp_dir / "worktree"
        worktree_path.mkdir(parents=True)
        (worktree_path.parent / "base_branch").write_text("feature/foo")

        result = _worktree_branch_matches(temp_dir, worktree_path, "main")
        assert result is False

    def test_fallback_true_when_no_base_branch_file(self, temp_dir):
        """Returns True (keep worktree) when no base_branch file exists."""
        from octopoid.git_utils import _worktree_branch_matches

        worktree_path = temp_dir / "worktree"
        worktree_path.mkdir(parents=True)
        # No base_branch file written

        result = _worktree_branch_matches(temp_dir, worktree_path, "main")
        assert result is True

    def test_matches_custom_branch(self, temp_dir):
        """Works correctly for non-main branch names."""
        from octopoid.git_utils import _worktree_branch_matches

        worktree_path = temp_dir / "worktree"
        worktree_path.mkdir(parents=True)
        (worktree_path.parent / "base_branch").write_text("feature/my-feature")

        assert _worktree_branch_matches(temp_dir, worktree_path, "feature/my-feature") is True
        assert _worktree_branch_matches(temp_dir, worktree_path, "main") is False

    def test_handles_whitespace_in_stored_branch(self, temp_dir):
        """Strips trailing whitespace/newlines from stored branch name."""
        from octopoid.git_utils import _worktree_branch_matches

        worktree_path = temp_dir / "worktree"
        worktree_path.mkdir(parents=True)
        (worktree_path.parent / "base_branch").write_text("main\n")

        result = _worktree_branch_matches(temp_dir, worktree_path, "main")
        assert result is True


class TestReuseExistingWorktree:
    """Tests for the _reuse_existing_worktree helper."""

    def test_resets_stdout_log(self, temp_dir):
        """stdout.log is deleted when reusing a worktree."""
        from octopoid.git_utils import _reuse_existing_worktree

        worktree_path = temp_dir / "worktree"
        worktree_path.mkdir(parents=True)
        task_dir = worktree_path.parent

        stdout_log = task_dir / "stdout.log"
        stdout_log.write_text("previous run output")

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "HEAD\n"
            return result

        with patch('octopoid.git_utils.run_git', side_effect=mock_run_git):
            _reuse_existing_worktree(temp_dir, worktree_path, task_dir, "main", "task123")

        assert not stdout_log.exists(), "stdout.log should be deleted on reuse"

    def test_resets_stderr_log(self, temp_dir):
        """stderr.log is deleted when reusing a worktree."""
        from octopoid.git_utils import _reuse_existing_worktree

        worktree_path = temp_dir / "worktree"
        worktree_path.mkdir(parents=True)
        task_dir = worktree_path.parent

        stderr_log = task_dir / "stderr.log"
        stderr_log.write_text("previous errors")

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "HEAD\n"
            return result

        with patch('octopoid.git_utils.run_git', side_effect=mock_run_git):
            _reuse_existing_worktree(temp_dir, worktree_path, task_dir, "main", "task123")

        assert not stderr_log.exists(), "stderr.log should be deleted on reuse"

    def test_resets_tool_counter(self, temp_dir):
        """tool_counter is deleted when reusing a worktree."""
        from octopoid.git_utils import _reuse_existing_worktree

        worktree_path = temp_dir / "worktree"
        worktree_path.mkdir(parents=True)
        task_dir = worktree_path.parent

        tool_counter = task_dir / "tool_counter"
        tool_counter.write_text("x" * 42)  # 42 tool calls in previous run

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "HEAD\n"
            return result

        with patch('octopoid.git_utils.run_git', side_effect=mock_run_git):
            _reuse_existing_worktree(temp_dir, worktree_path, task_dir, "main", "task123")

        assert not tool_counter.exists(), "tool_counter should be deleted on reuse"

    def test_tolerates_missing_log_files(self, temp_dir):
        """No error when log files don't exist yet (first reuse of fresh worktree)."""
        from octopoid.git_utils import _reuse_existing_worktree

        worktree_path = temp_dir / "worktree"
        worktree_path.mkdir(parents=True)
        task_dir = worktree_path.parent
        # No log files

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "HEAD\n"
            return result

        # Should not raise
        with patch('octopoid.git_utils.run_git', side_effect=mock_run_git):
            _reuse_existing_worktree(temp_dir, worktree_path, task_dir, "main", "task123")

    def test_rebases_named_branch_onto_origin(self, temp_dir):
        """When worktree is on a named branch, rebase is attempted."""
        from octopoid.git_utils import _reuse_existing_worktree

        worktree_path = temp_dir / "worktree"
        worktree_path.mkdir(parents=True)
        task_dir = worktree_path.parent
        git_calls = []

        def mock_run_git(args, cwd=None, check=True):
            git_calls.append(args[:])
            result = MagicMock()
            result.returncode = 0
            # Return a named branch (not detached HEAD)
            result.stdout = "agent/task123-20260101-120000\n"
            return result

        with patch('octopoid.git_utils.run_git', side_effect=mock_run_git):
            _reuse_existing_worktree(temp_dir, worktree_path, task_dir, "main", "task123")

        rebase_calls = [c for c in git_calls if c[0] == "rebase"]
        assert len(rebase_calls) == 1, "Should attempt rebase for named branch"
        assert "origin/main" in rebase_calls[0], "Should rebase onto origin/main"

    def test_aborts_rebase_on_conflict(self, temp_dir, capsys):
        """When rebase has conflicts, aborts and leaves worktree as-is."""
        from octopoid.git_utils import _reuse_existing_worktree

        worktree_path = temp_dir / "worktree"
        worktree_path.mkdir(parents=True)
        task_dir = worktree_path.parent
        git_calls = []

        def mock_run_git(args, cwd=None, check=True):
            git_calls.append(args[:])
            result = MagicMock()
            if args[0] == "rev-parse" and "--abbrev-ref" in args:
                result.returncode = 0
                result.stdout = "agent/task123-20260101-120000\n"
            elif args[0] == "rebase" and args[0] != "rebase --abort":
                result.returncode = 1  # Conflict!
                result.stdout = ""
            elif args == ["rebase", "--abort"]:
                result.returncode = 0
                result.stdout = ""
            else:
                result.returncode = 0
                result.stdout = ""
            return result

        with patch('octopoid.git_utils.run_git', side_effect=mock_run_git):
            _reuse_existing_worktree(temp_dir, worktree_path, task_dir, "main", "task123")

        abort_calls = [c for c in git_calls if c == ["rebase", "--abort"]]
        assert len(abort_calls) == 1, "Should abort the conflicting rebase"
        captured = capsys.readouterr()
        assert "conflict" in captured.out.lower() or "leaving" in captured.out.lower()

    def test_updates_detached_head_when_no_commits_ahead(self, temp_dir):
        """When detached HEAD has no agent commits, updates to current origin/main."""
        from octopoid.git_utils import _reuse_existing_worktree

        worktree_path = temp_dir / "worktree"
        worktree_path.mkdir(parents=True)
        task_dir = worktree_path.parent
        git_calls = []

        def mock_run_git(args, cwd=None, check=True):
            git_calls.append(args[:])
            result = MagicMock()
            result.returncode = 0
            if args[:2] == ["rev-parse", "--abbrev-ref"]:
                result.stdout = "HEAD\n"  # detached HEAD
            elif args[:2] == ["rev-list", "--count"]:
                result.stdout = "0\n"  # no commits ahead
            else:
                result.stdout = ""
            return result

        with patch('octopoid.git_utils.run_git', side_effect=mock_run_git):
            _reuse_existing_worktree(temp_dir, worktree_path, task_dir, "main", "task123")

        checkout_calls = [c for c in git_calls if c[:2] == ["checkout", "--detach"]]
        assert len(checkout_calls) == 1, "Should update detached HEAD to current origin/main"
        assert "origin/main" in checkout_calls[0]

    def test_leaves_detached_head_with_commits_alone(self, temp_dir):
        """When detached HEAD has agent commits, leaves it untouched."""
        from octopoid.git_utils import _reuse_existing_worktree

        worktree_path = temp_dir / "worktree"
        worktree_path.mkdir(parents=True)
        task_dir = worktree_path.parent
        git_calls = []

        def mock_run_git(args, cwd=None, check=True):
            git_calls.append(args[:])
            result = MagicMock()
            result.returncode = 0
            if args[:2] == ["rev-parse", "--abbrev-ref"]:
                result.stdout = "HEAD\n"  # detached HEAD
            elif args[:2] == ["rev-list", "--count"]:
                result.stdout = "3\n"  # 3 commits ahead — agent did work
            else:
                result.stdout = ""
            return result

        with patch('octopoid.git_utils.run_git', side_effect=mock_run_git):
            _reuse_existing_worktree(temp_dir, worktree_path, task_dir, "main", "task123")

        checkout_calls = [c for c in git_calls if c[:2] == ["checkout", "--detach"]]
        assert len(checkout_calls) == 0, "Should NOT update detached HEAD when agent has commits"
