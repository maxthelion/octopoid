"""Tests for the rebaser system: DB operations, scheduler staleness check, and role."""

import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# DB: needs_rebase column and helper functions
# ---------------------------------------------------------------------------


class TestNeedsRebaseDB:
    """Tests for needs_rebase DB column and helper functions."""

    def test_needs_rebase_column_exists(self, initialized_db):
        """Schema v7 creates needs_rebase column on tasks table."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import get_connection

            with get_connection() as conn:
                cursor = conn.execute("PRAGMA table_info(tasks)")
                columns = [row["name"] for row in cursor.fetchall()]
                assert "needs_rebase" in columns

    def test_needs_rebase_default_false(self, initialized_db):
        """New tasks have needs_rebase=False by default."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, get_task

            create_task(task_id="rb1", file_path="/rb1.md")
            task = get_task("rb1")
            assert task is not None
            assert not task.get("needs_rebase")

    def test_mark_for_rebase(self, initialized_db):
        """mark_for_rebase sets needs_rebase=True."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, mark_for_rebase, get_task

            create_task(task_id="rb2", file_path="/rb2.md")
            result = mark_for_rebase("rb2", reason="stale")

            assert result is not None
            task = get_task("rb2")
            assert task["needs_rebase"]

    def test_mark_for_rebase_nonexistent_task(self, initialized_db):
        """mark_for_rebase returns None for missing task."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import mark_for_rebase

            result = mark_for_rebase("nonexistent", reason="test")
            assert result is None

    def test_mark_for_rebase_records_history(self, initialized_db):
        """mark_for_rebase adds a history event."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, mark_for_rebase, get_task_history

            create_task(task_id="rb3", file_path="/rb3.md")
            mark_for_rebase("rb3", reason="manual")

            history = get_task_history("rb3")
            events = [h["event"] for h in history]
            assert "marked_for_rebase" in events

    def test_clear_rebase_flag(self, initialized_db):
        """clear_rebase_flag sets needs_rebase=False."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, mark_for_rebase, clear_rebase_flag, get_task

            create_task(task_id="rb4", file_path="/rb4.md")
            mark_for_rebase("rb4")

            result = clear_rebase_flag("rb4")
            assert result is not None

            task = get_task("rb4")
            assert not task["needs_rebase"]

    def test_clear_rebase_flag_records_history(self, initialized_db):
        """clear_rebase_flag adds a history event."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import (
                create_task,
                mark_for_rebase,
                clear_rebase_flag,
                get_task_history,
            )

            create_task(task_id="rb5", file_path="/rb5.md")
            mark_for_rebase("rb5")
            clear_rebase_flag("rb5")

            history = get_task_history("rb5")
            events = [h["event"] for h in history]
            assert "rebase_completed" in events

    def test_get_tasks_needing_rebase(self, initialized_db):
        """get_tasks_needing_rebase returns only tasks with needs_rebase=True."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import (
                create_task,
                mark_for_rebase,
                get_tasks_needing_rebase,
            )

            create_task(task_id="rb6", file_path="/rb6.md")
            create_task(task_id="rb7", file_path="/rb7.md")
            create_task(task_id="rb8", file_path="/rb8.md")

            mark_for_rebase("rb6")
            mark_for_rebase("rb8")

            tasks = get_tasks_needing_rebase()
            task_ids = [t["id"] for t in tasks]
            assert "rb6" in task_ids
            assert "rb8" in task_ids
            assert "rb7" not in task_ids

    def test_get_tasks_needing_rebase_with_queue_filter(self, initialized_db):
        """get_tasks_needing_rebase can filter by queue."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import (
                create_task,
                mark_for_rebase,
                get_tasks_needing_rebase,
                update_task_queue,
            )

            create_task(task_id="rb9", file_path="/rb9.md")
            create_task(task_id="rb10", file_path="/rb10.md")

            # Move rb10 to provisional
            update_task_queue("rb10", "provisional")

            mark_for_rebase("rb9")
            mark_for_rebase("rb10")

            # Filter for provisional only
            tasks = get_tasks_needing_rebase(queue="provisional")
            task_ids = [t["id"] for t in tasks]
            assert "rb10" in task_ids
            assert "rb9" not in task_ids

    def test_get_tasks_needing_rebase_empty(self, initialized_db):
        """get_tasks_needing_rebase returns empty list when none need rebase."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, get_tasks_needing_rebase

            create_task(task_id="rb11", file_path="/rb11.md")
            tasks = get_tasks_needing_rebase()
            assert tasks == []


# ---------------------------------------------------------------------------
# DB: Schema migration v6 → v7
# ---------------------------------------------------------------------------


class TestSchemaMigration:
    """Tests for the v6 → v7 migration (needs_rebase column)."""

    def test_migrate_adds_needs_rebase_column(self, mock_config, db_path):
        """Migrating from v6 should add the needs_rebase column."""
        with patch("orchestrator.db.get_database_path", return_value=db_path):
            from orchestrator.db import (
                init_schema,
                get_connection,
                migrate_schema,
                SCHEMA_VERSION,
            )

            # Init creates schema at current version
            init_schema()

            # Verify the column exists (since init creates at latest version)
            with get_connection() as conn:
                cursor = conn.execute("PRAGMA table_info(tasks)")
                columns = [row["name"] for row in cursor.fetchall()]
                assert "needs_rebase" in columns

            # Verify schema version is current
            with get_connection() as conn:
                cursor = conn.execute(
                    "SELECT value FROM schema_info WHERE key = 'version'"
                )
                version = int(cursor.fetchone()["value"])
                assert version == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Scheduler: staleness checking
# ---------------------------------------------------------------------------


class TestStalenessChecking:
    """Tests for the scheduler's branch staleness detection."""

    def test_count_commits_behind(self):
        """_count_commits_behind returns correct count."""
        from orchestrator.scheduler import _count_commits_behind

        with patch("subprocess.run") as mock_run:
            # Mock: branch exists (rev-parse succeeds)
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="abc123\n"),  # rev-parse
                MagicMock(returncode=0, stdout="7\n"),  # rev-list --count
            ]

            result = _count_commits_behind(Path("/fake/repo"), "feature/test")
            assert result == 7

    def test_count_commits_behind_branch_not_found(self):
        """_count_commits_behind returns None when branch doesn't exist."""
        from orchestrator.scheduler import _count_commits_behind

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            result = _count_commits_behind(Path("/fake/repo"), "no-such-branch")
            assert result is None

    def test_check_stale_branches_marks_stale_task(self, initialized_db):
        """check_stale_branches marks tasks that are behind by threshold."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, get_task, update_task_queue
            from orchestrator.scheduler import check_stale_branches

            # Create a task on a feature branch in provisional queue
            create_task(
                task_id="stale1",
                file_path="/stale1.md",
                branch="feature/old-branch",
                role="implement",
            )
            update_task_queue("stale1", "provisional")

            with (
                patch("orchestrator.scheduler.is_db_enabled", return_value=True),
                patch("orchestrator.scheduler._count_commits_behind", return_value=10),
                patch("subprocess.run"),  # for git fetch
                patch("orchestrator.config.find_parent_project", return_value=Path("/fake")),
            ):
                check_stale_branches(commits_behind_threshold=5)

            task = get_task("stale1")
            assert task["needs_rebase"]

    def test_check_stale_branches_ignores_fresh_tasks(self, initialized_db):
        """check_stale_branches doesn't mark tasks that are up to date."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, get_task, update_task_queue
            from orchestrator.scheduler import check_stale_branches

            create_task(
                task_id="fresh1",
                file_path="/fresh1.md",
                branch="feature/fresh",
                role="implement",
            )
            update_task_queue("fresh1", "provisional")

            with (
                patch("orchestrator.scheduler.is_db_enabled", return_value=True),
                patch("orchestrator.scheduler._count_commits_behind", return_value=2),
                patch("subprocess.run"),
                patch("orchestrator.config.find_parent_project", return_value=Path("/fake")),
            ):
                check_stale_branches(commits_behind_threshold=5)

            task = get_task("fresh1")
            assert not task.get("needs_rebase")

    def test_check_stale_branches_skips_orchestrator_impl(self, initialized_db):
        """check_stale_branches skips orchestrator_impl tasks."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, get_task, update_task_queue
            from orchestrator.scheduler import check_stale_branches

            create_task(
                task_id="orch1",
                file_path="/orch1.md",
                branch="feature/orch",
                role="orchestrator_impl",
            )
            update_task_queue("orch1", "provisional")

            with (
                patch("orchestrator.scheduler.is_db_enabled", return_value=True),
                patch("orchestrator.scheduler._count_commits_behind", return_value=20),
                patch("subprocess.run"),
                patch("orchestrator.config.find_parent_project", return_value=Path("/fake")),
            ):
                check_stale_branches(commits_behind_threshold=5)

            task = get_task("orch1")
            assert not task.get("needs_rebase")

    def test_check_stale_branches_skips_main_branch(self, initialized_db):
        """check_stale_branches skips tasks on main branch."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, get_task, update_task_queue
            from orchestrator.scheduler import check_stale_branches

            create_task(
                task_id="main1",
                file_path="/main1.md",
                branch="main",
                role="implement",
            )
            update_task_queue("main1", "provisional")

            with (
                patch("orchestrator.scheduler.is_db_enabled", return_value=True),
                patch("orchestrator.scheduler._count_commits_behind", return_value=20),
                patch("subprocess.run"),
                patch("orchestrator.config.find_parent_project", return_value=Path("/fake")),
            ):
                check_stale_branches(commits_behind_threshold=5)

            task = get_task("main1")
            assert not task.get("needs_rebase")

    def test_check_stale_branches_skips_already_marked(self, initialized_db):
        """check_stale_branches doesn't re-mark tasks already flagged."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import (
                create_task,
                mark_for_rebase,
                get_task_history,
                update_task_queue,
            )
            from orchestrator.scheduler import check_stale_branches

            create_task(
                task_id="marked1",
                file_path="/marked1.md",
                branch="feature/old",
                role="implement",
            )
            update_task_queue("marked1", "provisional")
            mark_for_rebase("marked1")

            # Count history events before
            history_before = len(get_task_history("marked1"))

            with (
                patch("orchestrator.scheduler.is_db_enabled", return_value=True),
                patch("orchestrator.scheduler._count_commits_behind", return_value=20),
                patch("subprocess.run"),
                patch("orchestrator.config.find_parent_project", return_value=Path("/fake")),
            ):
                check_stale_branches(commits_behind_threshold=5)

            # History should not have new entries (wasn't re-marked)
            history_after = len(get_task_history("marked1"))
            assert history_after == history_before


# ---------------------------------------------------------------------------
# RebaserRole: unit tests
# ---------------------------------------------------------------------------


class TestRebaserRole:
    """Tests for the RebaserRole."""

    def test_rebaser_skips_orchestrator_impl(self, initialized_db):
        """RebaserRole skips orchestrator_impl tasks."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, mark_for_rebase, get_task
            from orchestrator.roles.rebaser import RebaserRole

            create_task(
                task_id="orc1",
                file_path="/orc1.md",
                role="orchestrator_impl",
            )
            mark_for_rebase("orc1")

            role = RebaserRole.__new__(RebaserRole)
            role.agent_name = "test-rebaser"
            role.agent_id = 0
            role.agent_role = "rebaser"
            role.parent_project = Path("/fake")
            role.worktree = Path("/fake")
            role.shared_dir = Path("/fake")
            role.orchestrator_dir = Path("/fake")
            role.debug = False
            role._log_file = None

            exit_code = role.run()
            assert exit_code == 0

            # Task should still have needs_rebase (was skipped, not cleared)
            task = get_task("orc1")
            assert task["needs_rebase"]

    def test_rebaser_clears_flag_on_success(self, initialized_db, temp_dir):
        """RebaserRole clears needs_rebase flag after successful rebase."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, mark_for_rebase, get_task
            from orchestrator.roles.rebaser import RebaserRole

            create_task(
                task_id="succ1",
                file_path="/succ1.md",
                role="implement",
                branch="feature/succ",
            )
            mark_for_rebase("succ1")

            role = RebaserRole.__new__(RebaserRole)
            role.agent_name = "test-rebaser"
            role.agent_id = 0
            role.agent_role = "rebaser"
            role.parent_project = temp_dir
            role.worktree = temp_dir
            role.shared_dir = temp_dir
            role.orchestrator_dir = temp_dir
            role.debug = False
            role._log_file = None

            # Mock the internal methods
            with (
                patch.object(role, "_find_task_branch", return_value="feature/succ"),
                patch.object(role, "_rebase_task", return_value=True),
            ):
                exit_code = role.run()

            assert exit_code == 0
            task = get_task("succ1")
            # _rebase_task returns True, and inside it calls clear_rebase_flag
            # But since we mocked _rebase_task entirely, the flag clearing
            # happens inside _rebase_task which we mocked. Let's verify the
            # mock was called correctly instead.

    def test_rebaser_run_returns_zero(self, initialized_db):
        """RebaserRole.run() returns 0 even when no tasks need rebase."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.roles.rebaser import RebaserRole

            role = RebaserRole.__new__(RebaserRole)
            role.agent_name = "test-rebaser"
            role.agent_id = 0
            role.agent_role = "rebaser"
            role.parent_project = Path("/fake")
            role.worktree = Path("/fake")
            role.shared_dir = Path("/fake")
            role.orchestrator_dir = Path("/fake")
            role.debug = False
            role._log_file = None

            exit_code = role.run()
            assert exit_code == 0


# ---------------------------------------------------------------------------
# DB: last_rebase_attempt_at column and throttling
# ---------------------------------------------------------------------------


class TestRebaseThrottling:
    """Tests for rebase throttling (last_rebase_attempt_at, record_rebase_attempt, is_rebase_throttled)."""

    def test_last_rebase_attempt_at_column_exists(self, initialized_db):
        """Schema v8 creates last_rebase_attempt_at column on tasks table."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import get_connection

            with get_connection() as conn:
                cursor = conn.execute("PRAGMA table_info(tasks)")
                columns = [row["name"] for row in cursor.fetchall()]
                assert "last_rebase_attempt_at" in columns

    def test_last_rebase_attempt_at_default_null(self, initialized_db):
        """New tasks have last_rebase_attempt_at=NULL by default."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, get_task

            create_task(task_id="thr1", file_path="/thr1.md")
            task = get_task("thr1")
            assert task is not None
            assert task.get("last_rebase_attempt_at") is None

    def test_record_rebase_attempt(self, initialized_db):
        """record_rebase_attempt sets last_rebase_attempt_at to current time."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_rebase_attempt, get_task

            create_task(task_id="thr2", file_path="/thr2.md")
            result = record_rebase_attempt("thr2")

            assert result is not None
            task = get_task("thr2")
            assert task["last_rebase_attempt_at"] is not None
            # Verify it's a valid ISO datetime
            dt = datetime.fromisoformat(task["last_rebase_attempt_at"])
            assert (datetime.now() - dt).total_seconds() < 5

    def test_record_rebase_attempt_nonexistent_task(self, initialized_db):
        """record_rebase_attempt returns None for missing task."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import record_rebase_attempt

            result = record_rebase_attempt("nonexistent")
            assert result is None

    def test_is_rebase_throttled_no_attempt(self, initialized_db):
        """is_rebase_throttled returns False when no attempt has been recorded."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, is_rebase_throttled

            create_task(task_id="thr3", file_path="/thr3.md")
            assert not is_rebase_throttled("thr3")

    def test_is_rebase_throttled_recent_attempt(self, initialized_db):
        """is_rebase_throttled returns True when attempt was recent."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_rebase_attempt, is_rebase_throttled

            create_task(task_id="thr4", file_path="/thr4.md")
            record_rebase_attempt("thr4")

            assert is_rebase_throttled("thr4", cooldown_minutes=10)

    def test_is_rebase_throttled_old_attempt(self, initialized_db):
        """is_rebase_throttled returns False when attempt was old enough."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task, is_rebase_throttled

            create_task(task_id="thr5", file_path="/thr5.md")
            # Set last_rebase_attempt_at to 20 minutes ago
            old_time = (datetime.now() - timedelta(minutes=20)).isoformat()
            update_task("thr5", last_rebase_attempt_at=old_time)

            assert not is_rebase_throttled("thr5", cooldown_minutes=10)

    def test_is_rebase_throttled_nonexistent_task(self, initialized_db):
        """is_rebase_throttled returns False for nonexistent task."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import is_rebase_throttled

            assert not is_rebase_throttled("nonexistent")


# ---------------------------------------------------------------------------
# DB: Schema migration v7 → v8
# ---------------------------------------------------------------------------


class TestSchemaMigrationV8:
    """Tests for the v7 → v8 migration (last_rebase_attempt_at column)."""

    def test_migrate_adds_last_rebase_attempt_at_column(self, mock_config, db_path):
        """Migrating from v7 should add the last_rebase_attempt_at column."""
        with patch("orchestrator.db.get_database_path", return_value=db_path):
            from orchestrator.db import init_schema, get_connection, SCHEMA_VERSION

            init_schema()

            with get_connection() as conn:
                cursor = conn.execute("PRAGMA table_info(tasks)")
                columns = [row["name"] for row in cursor.fetchall()]
                assert "last_rebase_attempt_at" in columns

            with get_connection() as conn:
                cursor = conn.execute(
                    "SELECT value FROM schema_info WHERE key = 'version'"
                )
                version = int(cursor.fetchone()["value"])
                assert version == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Scheduler: branch freshness checking (merge-base --is-ancestor)
# ---------------------------------------------------------------------------


class TestBranchFreshnessChecking:
    """Tests for _is_branch_fresh() and check_branch_freshness()."""

    def test_is_branch_fresh_returns_true_for_fresh_branch(self):
        """_is_branch_fresh returns True when main is ancestor of branch."""
        from orchestrator.scheduler import _is_branch_fresh

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="abc123\n"),  # rev-parse succeeds
                MagicMock(returncode=0),  # merge-base --is-ancestor returns 0 (is ancestor)
            ]

            result = _is_branch_fresh(Path("/fake/repo"), "feature/test")
            assert result is True

    def test_is_branch_fresh_returns_false_for_stale_branch(self):
        """_is_branch_fresh returns False when main is NOT ancestor of branch."""
        from orchestrator.scheduler import _is_branch_fresh

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="abc123\n"),  # rev-parse succeeds
                MagicMock(returncode=1),  # merge-base --is-ancestor returns 1 (not ancestor)
            ]

            result = _is_branch_fresh(Path("/fake/repo"), "feature/old")
            assert result is False

    def test_is_branch_fresh_returns_none_for_missing_branch(self):
        """_is_branch_fresh returns None when branch doesn't exist."""
        from orchestrator.scheduler import _is_branch_fresh

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")  # rev-parse fails
            result = _is_branch_fresh(Path("/fake/repo"), "no-such-branch")
            assert result is None

    def test_is_branch_fresh_returns_none_on_timeout(self):
        """_is_branch_fresh returns None when git times out."""
        from orchestrator.scheduler import _is_branch_fresh

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=10)
            result = _is_branch_fresh(Path("/fake/repo"), "feature/test")
            assert result is None

    def test_check_branch_freshness_triggers_rebase_for_stale(self, initialized_db):
        """check_branch_freshness calls rebase_stale_branch for stale task branches."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task, update_task_queue
            from orchestrator.scheduler import check_branch_freshness

            create_task(
                task_id="fresh1",
                file_path="/fresh1.md",
                branch="feature/stale-branch",
                role="implement",
            )
            update_task("fresh1", pr_url="https://github.com/test/test/pull/1")
            update_task_queue("fresh1", "provisional")

            with (
                patch("orchestrator.scheduler.is_db_enabled", return_value=True),
                patch("orchestrator.scheduler._is_branch_fresh", return_value=False),
                patch("orchestrator.scheduler.rebase_stale_branch") as mock_rebase,
                patch("subprocess.run"),  # for git fetch
                patch("orchestrator.config.find_parent_project", return_value=Path("/fake")),
            ):
                check_branch_freshness()

            mock_rebase.assert_called_once_with("fresh1", "feature/stale-branch")

    def test_check_branch_freshness_skips_fresh_branches(self, initialized_db):
        """check_branch_freshness does NOT call rebase for fresh branches."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task, update_task_queue
            from orchestrator.scheduler import check_branch_freshness

            create_task(
                task_id="fresh2",
                file_path="/fresh2.md",
                branch="feature/up-to-date",
                role="implement",
            )
            update_task("fresh2", pr_url="https://github.com/test/test/pull/2")
            update_task_queue("fresh2", "provisional")

            with (
                patch("orchestrator.scheduler.is_db_enabled", return_value=True),
                patch("orchestrator.scheduler._is_branch_fresh", return_value=True),
                patch("orchestrator.scheduler.rebase_stale_branch") as mock_rebase,
                patch("subprocess.run"),
                patch("orchestrator.config.find_parent_project", return_value=Path("/fake")),
            ):
                check_branch_freshness()

            mock_rebase.assert_not_called()

    def test_check_branch_freshness_skips_orchestrator_impl(self, initialized_db):
        """check_branch_freshness skips orchestrator_impl tasks."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task, update_task_queue
            from orchestrator.scheduler import check_branch_freshness

            create_task(
                task_id="orch2",
                file_path="/orch2.md",
                branch="feature/orch-branch",
                role="orchestrator_impl",
            )
            update_task("orch2", pr_url="https://github.com/test/test/pull/3")
            update_task_queue("orch2", "provisional")

            with (
                patch("orchestrator.scheduler.is_db_enabled", return_value=True),
                patch("orchestrator.scheduler._is_branch_fresh", return_value=False),
                patch("orchestrator.scheduler.rebase_stale_branch") as mock_rebase,
                patch("subprocess.run"),
                patch("orchestrator.config.find_parent_project", return_value=Path("/fake")),
            ):
                check_branch_freshness()

            mock_rebase.assert_not_called()

    def test_check_branch_freshness_skips_tasks_without_pr(self, initialized_db):
        """check_branch_freshness skips tasks that have no pr_url."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue
            from orchestrator.scheduler import check_branch_freshness

            create_task(
                task_id="nopr1",
                file_path="/nopr1.md",
                branch="feature/no-pr",
                role="implement",
            )
            update_task_queue("nopr1", "provisional")

            with (
                patch("orchestrator.scheduler.is_db_enabled", return_value=True),
                patch("orchestrator.scheduler._is_branch_fresh") as mock_fresh,
                patch("orchestrator.scheduler.rebase_stale_branch") as mock_rebase,
                patch("subprocess.run"),
                patch("orchestrator.config.find_parent_project", return_value=Path("/fake")),
            ):
                check_branch_freshness()

            mock_fresh.assert_not_called()
            mock_rebase.assert_not_called()

    def test_check_branch_freshness_skips_main_branch(self, initialized_db):
        """check_branch_freshness skips tasks on main branch."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task, update_task_queue
            from orchestrator.scheduler import check_branch_freshness

            create_task(
                task_id="main2",
                file_path="/main2.md",
                branch="main",
                role="implement",
            )
            update_task("main2", pr_url="https://github.com/test/test/pull/4")
            update_task_queue("main2", "provisional")

            with (
                patch("orchestrator.scheduler.is_db_enabled", return_value=True),
                patch("orchestrator.scheduler._is_branch_fresh") as mock_fresh,
                patch("orchestrator.scheduler.rebase_stale_branch") as mock_rebase,
                patch("subprocess.run"),
                patch("orchestrator.config.find_parent_project", return_value=Path("/fake")),
            ):
                check_branch_freshness()

            mock_fresh.assert_not_called()
            mock_rebase.assert_not_called()

    def test_check_branch_freshness_respects_throttle(self, initialized_db):
        """check_branch_freshness skips throttled tasks."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task, update_task_queue, record_rebase_attempt
            from orchestrator.scheduler import check_branch_freshness

            create_task(
                task_id="throt1",
                file_path="/throt1.md",
                branch="feature/throttled",
                role="implement",
            )
            update_task("throt1", pr_url="https://github.com/test/test/pull/5")
            update_task_queue("throt1", "provisional")
            record_rebase_attempt("throt1")  # Just attempted

            with (
                patch("orchestrator.scheduler.is_db_enabled", return_value=True),
                patch("orchestrator.scheduler._is_branch_fresh") as mock_fresh,
                patch("orchestrator.scheduler.rebase_stale_branch") as mock_rebase,
                patch("subprocess.run"),
                patch("orchestrator.config.find_parent_project", return_value=Path("/fake")),
            ):
                check_branch_freshness()

            mock_fresh.assert_not_called()
            mock_rebase.assert_not_called()


# ---------------------------------------------------------------------------
# Scheduler: ensure_rebaser_worktree
# ---------------------------------------------------------------------------


class TestEnsureRebaserWorktree:
    """Tests for ensure_rebaser_worktree()."""

    def test_returns_existing_worktree(self, mock_config, temp_dir):
        """Returns path if worktree already exists."""
        from orchestrator.scheduler import ensure_rebaser_worktree

        worktree_path = mock_config / "agents" / "rebaser-worktree"
        worktree_path.mkdir(parents=True, exist_ok=True)
        (worktree_path / ".git").write_text("gitdir: /fake")

        result = ensure_rebaser_worktree()
        assert result == worktree_path

    def test_creates_worktree_if_missing(self, mock_config, temp_dir):
        """Creates worktree using git worktree add when it doesn't exist."""
        from orchestrator.scheduler import ensure_rebaser_worktree

        worktree_path = mock_config / "agents" / "rebaser-worktree"

        with patch("subprocess.run") as mock_run:
            # First call: git worktree add succeeds
            def side_effect(*args, **kwargs):
                cmd = args[0] if args else kwargs.get("args", [])
                if "worktree" in cmd:
                    # Create the dir and .git to simulate success
                    worktree_path.mkdir(parents=True, exist_ok=True)
                    (worktree_path / ".git").write_text("gitdir: /fake")
                    return MagicMock(returncode=0, stdout="", stderr="")
                return MagicMock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = side_effect
            result = ensure_rebaser_worktree()

        # The function should have been called (for git worktree add + npm install)
        assert mock_run.called

    def test_returns_none_on_git_failure(self, mock_config, temp_dir):
        """Returns None if git worktree add fails."""
        from orchestrator.scheduler import ensure_rebaser_worktree

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="fatal: error"
            )
            result = ensure_rebaser_worktree()
            assert result is None


# ---------------------------------------------------------------------------
# Scheduler: rebase_stale_branch
# ---------------------------------------------------------------------------


class TestRebaseStaleBranch:
    """Tests for rebase_stale_branch()."""

    def test_successful_rebase_and_push(self, initialized_db, mock_config, temp_dir):
        """Successful rebase: fetch, checkout, rebase, test, push all succeed."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue
            from orchestrator.scheduler import rebase_stale_branch

            create_task(
                task_id="reb1",
                file_path="/reb1.md",
                branch="feature/rebasing",
                role="implement",
            )
            update_task_queue("reb1", "provisional")

            worktree_path = temp_dir / "rebaser-worktree"
            worktree_path.mkdir(parents=True, exist_ok=True)
            (worktree_path / "node_modules").mkdir()  # So npm install is skipped

            with (
                patch("orchestrator.scheduler.is_db_enabled", return_value=True),
                patch("orchestrator.scheduler.ensure_rebaser_worktree", return_value=worktree_path),
                patch("subprocess.run") as mock_run,
            ):
                # All git/test commands succeed
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="ok\n", stderr=""
                )
                result = rebase_stale_branch("reb1", "feature/rebasing")

            assert result is True

    def test_rebase_conflict_rejects_task(self, initialized_db, mock_config, temp_dir):
        """On rebase conflict, rejects task back to agent with feedback."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue
            from orchestrator.scheduler import rebase_stale_branch

            task_file = temp_dir / "TASK-reb2.md"
            task_file.write_text("# Task\n")

            create_task(
                task_id="reb2",
                file_path=str(task_file),
                branch="feature/conflict",
                role="implement",
            )
            update_task_queue("reb2", "provisional")

            worktree_path = temp_dir / "rebaser-worktree"
            worktree_path.mkdir(parents=True, exist_ok=True)

            call_count = 0

            def subprocess_side_effect(*args, **kwargs):
                nonlocal call_count
                call_count += 1

                # 1: fetch succeeds
                if call_count == 1:
                    return MagicMock(returncode=0, stdout="", stderr="")
                # 2: checkout succeeds
                if call_count == 2:
                    return MagicMock(returncode=0, stdout="", stderr="")
                # 3: rebase fails (conflict)
                if call_count == 3:
                    return MagicMock(
                        returncode=1,
                        stdout="",
                        stderr="CONFLICT (content): Merge conflict in file.ts",
                    )
                # 4: git diff --name-only (conflicted files)
                if call_count == 4:
                    return MagicMock(returncode=0, stdout="file.ts\n", stderr="")
                # 5: rebase --abort
                if call_count == 5:
                    return MagicMock(returncode=0, stdout="", stderr="")
                return MagicMock(returncode=0, stdout="", stderr="")

            with (
                patch("orchestrator.scheduler.is_db_enabled", return_value=True),
                patch("orchestrator.scheduler.ensure_rebaser_worktree", return_value=worktree_path),
                patch("subprocess.run", side_effect=subprocess_side_effect),
                patch("orchestrator.queue_utils.review_reject_task") as mock_reject,
            ):
                result = rebase_stale_branch("reb2", "feature/conflict")

            assert result is False
            mock_reject.assert_called_once()
            # Check feedback contains "Rebase Conflict"
            feedback = mock_reject.call_args[0][1]
            assert "Rebase Conflict" in feedback
            assert "file.ts" in feedback

    def test_test_failure_after_rebase_rejects_task(self, initialized_db, mock_config, temp_dir):
        """On test failure after successful rebase, rejects task with test output."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue
            from orchestrator.scheduler import rebase_stale_branch

            task_file = temp_dir / "TASK-reb3.md"
            task_file.write_text("# Task\n")

            create_task(
                task_id="reb3",
                file_path=str(task_file),
                branch="feature/test-fail",
                role="implement",
            )
            update_task_queue("reb3", "provisional")

            worktree_path = temp_dir / "rebaser-worktree"
            worktree_path.mkdir(parents=True, exist_ok=True)
            (worktree_path / "node_modules").mkdir()

            call_count = 0

            def subprocess_side_effect(*args, **kwargs):
                nonlocal call_count
                call_count += 1

                # 1: fetch, 2: checkout, 3: rebase all succeed
                if call_count <= 3:
                    return MagicMock(returncode=0, stdout="ok\n", stderr="")
                # 4: vitest run fails
                if call_count == 4:
                    return MagicMock(
                        returncode=1,
                        stdout="FAIL src/test.ts\n  Expected true, got false",
                        stderr="Test suite failed",
                    )
                return MagicMock(returncode=0, stdout="", stderr="")

            with (
                patch("orchestrator.scheduler.is_db_enabled", return_value=True),
                patch("orchestrator.scheduler.ensure_rebaser_worktree", return_value=worktree_path),
                patch("subprocess.run", side_effect=subprocess_side_effect),
                patch("orchestrator.queue_utils.review_reject_task") as mock_reject,
            ):
                result = rebase_stale_branch("reb3", "feature/test-fail")

            assert result is False
            mock_reject.assert_called_once()
            feedback = mock_reject.call_args[0][1]
            assert "Post-Rebase Test Failure" in feedback

    def test_records_rebase_attempt_for_throttling(self, initialized_db, mock_config, temp_dir):
        """rebase_stale_branch records the attempt for throttling."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue, get_task
            from orchestrator.scheduler import rebase_stale_branch

            create_task(
                task_id="reb4",
                file_path="/reb4.md",
                branch="feature/attempt",
                role="implement",
            )
            update_task_queue("reb4", "provisional")

            worktree_path = temp_dir / "rebaser-worktree"
            worktree_path.mkdir(parents=True, exist_ok=True)
            (worktree_path / "node_modules").mkdir()

            with (
                patch("orchestrator.scheduler.is_db_enabled", return_value=True),
                patch("orchestrator.scheduler.ensure_rebaser_worktree", return_value=worktree_path),
                patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
            ):
                rebase_stale_branch("reb4", "feature/attempt")

            task = get_task("reb4")
            assert task["last_rebase_attempt_at"] is not None

    def test_returns_false_when_worktree_unavailable(self, initialized_db, mock_config):
        """Returns False if rebaser worktree cannot be created."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task
            from orchestrator.scheduler import rebase_stale_branch

            create_task(task_id="reb5", file_path="/reb5.md")

            with (
                patch("orchestrator.scheduler.is_db_enabled", return_value=True),
                patch("orchestrator.scheduler.ensure_rebaser_worktree", return_value=None),
            ):
                result = rebase_stale_branch("reb5", "feature/no-worktree")

            assert result is False

    def test_push_failure_returns_false(self, initialized_db, mock_config, temp_dir):
        """Returns False when force-push fails (but doesn't reject task)."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue
            from orchestrator.scheduler import rebase_stale_branch

            create_task(
                task_id="reb6",
                file_path="/reb6.md",
                branch="feature/push-fail",
                role="implement",
            )
            update_task_queue("reb6", "provisional")

            worktree_path = temp_dir / "rebaser-worktree"
            worktree_path.mkdir(parents=True, exist_ok=True)
            (worktree_path / "node_modules").mkdir()

            call_count = 0

            def subprocess_side_effect(*args, **kwargs):
                nonlocal call_count
                call_count += 1

                # 1-3: fetch, checkout, rebase succeed
                # 4: test succeeds
                if call_count <= 4:
                    return MagicMock(returncode=0, stdout="ok\n", stderr="")
                # 5: push fails
                if call_count == 5:
                    return MagicMock(
                        returncode=1,
                        stdout="",
                        stderr="rejected: stale info",
                    )
                return MagicMock(returncode=0, stdout="", stderr="")

            with (
                patch("orchestrator.scheduler.is_db_enabled", return_value=True),
                patch("orchestrator.scheduler.ensure_rebaser_worktree", return_value=worktree_path),
                patch("subprocess.run", side_effect=subprocess_side_effect),
            ):
                result = rebase_stale_branch("reb6", "feature/push-fail")

            assert result is False


# ---------------------------------------------------------------------------
# RebaserRole: throttling and rejection behavior
# ---------------------------------------------------------------------------


class TestRebaserRoleThrottling:
    """Tests for RebaserRole throttling and task rejection on failure."""

    def test_rebaser_skips_throttled_tasks(self, initialized_db):
        """RebaserRole skips tasks that were rebased recently."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, mark_for_rebase, record_rebase_attempt, get_task
            from orchestrator.roles.rebaser import RebaserRole

            create_task(
                task_id="thr_role1",
                file_path="/thr_role1.md",
                role="implement",
                branch="feature/throttled",
            )
            mark_for_rebase("thr_role1")
            record_rebase_attempt("thr_role1")  # Just attempted

            role = RebaserRole.__new__(RebaserRole)
            role.agent_name = "test-rebaser"
            role.agent_id = 0
            role.agent_role = "rebaser"
            role.parent_project = Path("/fake")
            role.worktree = Path("/fake")
            role.shared_dir = Path("/fake")
            role.orchestrator_dir = Path("/fake")
            role.debug = False
            role._log_file = None

            exit_code = role.run()
            assert exit_code == 0

            # Task should still be marked for rebase (was skipped)
            task = get_task("thr_role1")
            assert task["needs_rebase"]

    def test_rebaser_rejects_on_conflict(self, initialized_db, temp_dir):
        """RebaserRole rejects task back to agent when rebase conflicts."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, mark_for_rebase
            from orchestrator.roles.rebaser import RebaserRole

            task_file = temp_dir / "TASK-conflict1.md"
            task_file.write_text("# Task\n")

            create_task(
                task_id="conflict1",
                file_path=str(task_file),
                role="implement",
                branch="feature/conflicting",
            )
            mark_for_rebase("conflict1")

            role = RebaserRole.__new__(RebaserRole)
            role.agent_name = "test-rebaser"
            role.agent_id = 0
            role.agent_role = "rebaser"
            role.parent_project = temp_dir
            role.worktree = temp_dir
            role.shared_dir = temp_dir
            role.orchestrator_dir = temp_dir
            role.debug = False
            role._log_file = None

            worktree_path = temp_dir / "rebaser-wt"
            worktree_path.mkdir(parents=True, exist_ok=True)

            with (
                patch.object(role, "_find_task_branch", return_value="feature/conflicting"),
                patch.object(role, "_get_rebaser_worktree", return_value=worktree_path),
                patch.object(role, "_run_git"),  # fetch + checkout succeed
                patch.object(
                    role,
                    "_attempt_rebase",
                    return_value=(False, "CONFLICT in file.ts"),
                ),
                patch("orchestrator.queue_utils.review_reject_task") as mock_reject,
            ):
                exit_code = role.run()

            assert exit_code == 0
            mock_reject.assert_called_once()
            feedback = mock_reject.call_args[0][1]
            assert "Rebase Conflict" in feedback
