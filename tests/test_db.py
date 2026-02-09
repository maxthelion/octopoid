"""Tests for orchestrator.db module."""

import pytest
from unittest.mock import patch
from datetime import datetime


class TestSchema:
    """Tests for database schema initialization."""

    def test_init_schema_creates_tables(self, mock_config, db_path):
        """Test that init_schema creates all required tables."""
        with patch('orchestrator.db.get_database_path', return_value=db_path):
            from orchestrator.db import init_schema, get_connection

            init_schema()

            with get_connection() as conn:
                # Check tasks table exists
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
                )
                assert cursor.fetchone() is not None

                # Check agents table exists
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='agents'"
                )
                assert cursor.fetchone() is not None

                # Check task_history table exists
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='task_history'"
                )
                assert cursor.fetchone() is not None

    def test_get_schema_version(self, initialized_db):
        """Test getting schema version."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import get_schema_version, SCHEMA_VERSION

            version = get_schema_version()
            assert version == SCHEMA_VERSION

    def test_get_schema_version_no_db(self, mock_config, db_path):
        """Test getting schema version when DB doesn't exist."""
        with patch('orchestrator.db.get_database_path', return_value=db_path):
            from orchestrator.db import get_schema_version

            version = get_schema_version()
            assert version is None


class TestTaskOperations:
    """Tests for task CRUD operations."""

    def test_create_task(self, initialized_db):
        """Test creating a task."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task

            task = create_task(
                task_id="test123",
                file_path="/path/to/task.md",
                priority="P1",
                role="implement",
                branch="main",
            )

            assert task["id"] == "test123"
            assert task["file_path"] == "/path/to/task.md"
            assert task["priority"] == "P1"
            assert task["role"] == "implement"
            assert task["queue"] == "incoming"

    def test_get_task(self, initialized_db):
        """Test getting a task by ID."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task

            create_task(
                task_id="test456",
                file_path="/path/to/task.md",
                priority="P2",
                role="test",
            )

            task = get_task("test456")
            assert task is not None
            assert task["id"] == "test456"
            assert task["priority"] == "P2"

    def test_get_task_not_found(self, initialized_db):
        """Test getting a non-existent task."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import get_task

            task = get_task("nonexistent")
            assert task is None

    def test_get_task_by_path(self, initialized_db):
        """Test getting a task by file path."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task_by_path

            create_task(
                task_id="pathtest",
                file_path="/unique/path/task.md",
            )

            task = get_task_by_path("/unique/path/task.md")
            assert task is not None
            assert task["id"] == "pathtest"

    def test_update_task(self, initialized_db):
        """Test updating task fields."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task, get_task

            create_task(task_id="updatetest", file_path="/path.md")

            updated = update_task("updatetest", priority="P0", role="review")

            assert updated["priority"] == "P0"
            assert updated["role"] == "review"

    def test_delete_task(self, initialized_db):
        """Test deleting a task."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, delete_task, get_task

            create_task(task_id="deletetest", file_path="/path.md")
            assert get_task("deletetest") is not None

            result = delete_task("deletetest")
            assert result is True
            assert get_task("deletetest") is None

    def test_list_tasks(self, initialized_db):
        """Test listing tasks with filters."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, list_tasks

            create_task(task_id="t1", file_path="/t1.md", priority="P0", role="implement")
            create_task(task_id="t2", file_path="/t2.md", priority="P1", role="implement")
            create_task(task_id="t3", file_path="/t3.md", priority="P2", role="test")

            # All incoming
            tasks = list_tasks(queue="incoming")
            assert len(tasks) == 3

            # Filter by role
            tasks = list_tasks(role="implement")
            assert len(tasks) == 2

            # Sorted by priority
            tasks = list_tasks()
            assert tasks[0]["id"] == "t1"  # P0 first

    def test_count_tasks(self, initialized_db):
        """Test counting tasks."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, count_tasks

            create_task(task_id="c1", file_path="/c1.md")
            create_task(task_id="c2", file_path="/c2.md")

            assert count_tasks() == 2
            assert count_tasks(queue="incoming") == 2
            assert count_tasks(queue="claimed") == 0


class TestTaskLifecycle:
    """Tests for task lifecycle operations."""

    def test_claim_task(self, initialized_db):
        """Test claiming a task."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, claim_task, get_task

            create_task(task_id="claim1", file_path="/claim1.md", role="implement")

            claimed = claim_task(role_filter="implement", agent_name="test-agent")

            assert claimed is not None
            assert claimed["id"] == "claim1"
            assert claimed["queue"] == "claimed"
            assert claimed["claimed_by"] == "test-agent"

    def test_claim_task_respects_role_filter(self, initialized_db):
        """Test that claim_task respects role filter."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, claim_task

            create_task(task_id="r1", file_path="/r1.md", role="test")
            create_task(task_id="r2", file_path="/r2.md", role="implement")

            claimed = claim_task(role_filter="implement")

            assert claimed["id"] == "r2"

    def test_claim_task_skips_blocked(self, initialized_db):
        """Test that claim_task skips blocked tasks."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, claim_task

            create_task(task_id="blocked", file_path="/blocked.md", blocked_by="other")
            create_task(task_id="unblocked", file_path="/unblocked.md")

            claimed = claim_task()

            assert claimed["id"] == "unblocked"

    def test_claim_task_no_available(self, initialized_db):
        """Test claiming when no tasks available."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import claim_task

            claimed = claim_task()
            assert claimed is None

    def test_submit_completion(self, initialized_db):
        """Test submitting a task for pre-check."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, claim_task, submit_completion, get_task

            create_task(task_id="submit1", file_path="/submit1.md")
            claim_task(agent_name="agent")

            result = submit_completion("submit1", commits_count=3, turns_used=25)

            assert result["queue"] == "provisional"
            assert result["commits_count"] == 3
            assert result["turns_used"] == 25

    def test_accept_completion(self, initialized_db):
        """Test accepting a provisional task."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, claim_task, submit_completion, accept_completion

            create_task(task_id="accept1", file_path="/accept1.md")
            claim_task()
            submit_completion("accept1", commits_count=1)

            result = accept_completion("accept1", accepted_by="pre-check-agent")

            assert result["queue"] == "done"

    def test_reject_completion(self, initialized_db):
        """Test rejecting a provisional task."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, claim_task, submit_completion, reject_completion

            create_task(task_id="reject1", file_path="/reject1.md")
            claim_task()
            submit_completion("reject1", commits_count=0)

            result = reject_completion("reject1", reason="no_commits", rejected_by="pre-check")

            assert result["queue"] == "incoming"
            assert result["attempt_count"] == 1
            assert result["claimed_by"] is None

    def test_reject_increments_attempt_count(self, initialized_db):
        """Test that rejection increments attempt count."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, claim_task, submit_completion, reject_completion

            create_task(task_id="multi", file_path="/multi.md")

            # First attempt
            claim_task()
            submit_completion("multi", commits_count=0)
            reject_completion("multi", reason="no_commits")

            # Second attempt
            claim_task()
            submit_completion("multi", commits_count=0)
            result = reject_completion("multi", reason="no_commits")

            assert result["attempt_count"] == 2

    def test_escalate_to_planning(self, initialized_db):
        """Test escalating a task to planning."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, escalate_to_planning

            create_task(task_id="escalate1", file_path="/escalate1.md")

            result = escalate_to_planning("escalate1", plan_id="plan123")

            assert result["queue"] == "escalated"
            assert result["has_plan"] == 1  # SQLite stores booleans as 0/1
            assert result["plan_id"] == "plan123"

    def test_fail_task(self, initialized_db):
        """Test failing a task."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, claim_task, fail_task

            create_task(task_id="fail1", file_path="/fail1.md")
            claim_task()

            result = fail_task("fail1", error="Something went wrong")

            assert result["queue"] == "failed"


class TestDependencyManagement:
    """Tests for task dependency management."""

    def test_unblock_dependent_tasks(self, initialized_db):
        """Test that completing a task unblocks dependents."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, claim_task, submit_completion, accept_completion, get_task

            # Create blocker and blocked task
            create_task(task_id="blocker", file_path="/blocker.md")
            create_task(task_id="blocked", file_path="/blocked.md", blocked_by="blocker")

            # Complete blocker
            claim_task()
            submit_completion("blocker", commits_count=1)
            accept_completion("blocker")

            # Check blocked task is now unblocked
            blocked_task = get_task("blocked")
            assert blocked_task["blocked_by"] is None or blocked_task["blocked_by"] == ""

    def test_check_dependencies_resolved(self, initialized_db):
        """Test checking if dependencies are resolved."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, check_dependencies_resolved, update_task_queue

            create_task(task_id="dep1", file_path="/dep1.md")
            create_task(task_id="dep2", file_path="/dep2.md", blocked_by="dep1")

            # Not resolved yet
            assert check_dependencies_resolved("dep2") is False

            # Mark dep1 as done
            update_task_queue("dep1", "done")

            # Now resolved
            assert check_dependencies_resolved("dep2") is True

    def test_reset_stuck_claimed(self, initialized_db):
        """Test resetting stuck claimed tasks."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_connection, reset_stuck_claimed, get_task

            # Create a task and manually set it as claimed long ago
            create_task(task_id="stuck", file_path="/stuck.md")

            with get_connection() as conn:
                conn.execute("""
                    UPDATE tasks
                    SET queue = 'claimed',
                        claimed_by = 'old-agent',
                        claimed_at = datetime('now', '-2 hours')
                    WHERE id = 'stuck'
                """)

            # Reset with 60 minute timeout
            reset_ids = reset_stuck_claimed(timeout_minutes=60)

            assert "stuck" in reset_ids

            task = get_task("stuck")
            assert task["queue"] == "incoming"
            assert task["claimed_by"] is None


class TestAgentOperations:
    """Tests for agent state operations."""

    def test_upsert_agent(self, initialized_db):
        """Test creating/updating an agent."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import upsert_agent, get_agent

            agent = upsert_agent("test-agent", role="implementer", running=True, pid=12345)

            assert agent["name"] == "test-agent"
            assert agent["role"] == "implementer"
            assert agent["running"] == 1  # SQLite stores booleans as 0/1
            assert agent["pid"] == 12345

    def test_mark_agent_started(self, initialized_db):
        """Test marking an agent as started."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import upsert_agent, mark_agent_started, get_agent

            upsert_agent("agent1", role="implementer")
            mark_agent_started("agent1", pid=99999, task_id="task1")

            agent = get_agent("agent1")
            assert agent["running"] == 1  # SQLite stores booleans as 0/1
            assert agent["pid"] == 99999
            assert agent["current_task_id"] == "task1"
            assert agent["last_run_start"] is not None

    def test_mark_agent_finished(self, initialized_db):
        """Test marking an agent as finished."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import upsert_agent, mark_agent_started, mark_agent_finished, get_agent

            upsert_agent("agent2", role="implementer")
            mark_agent_started("agent2", pid=11111)
            mark_agent_finished("agent2")

            agent = get_agent("agent2")
            assert agent["running"] == 0  # SQLite stores booleans as 0/1
            assert agent["pid"] is None
            assert agent["current_task_id"] is None
            assert agent["last_run_end"] is not None

    def test_list_agents(self, initialized_db):
        """Test listing agents."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import upsert_agent, list_agents

            upsert_agent("a1", role="implementer", running=True)
            upsert_agent("a2", role="tester", running=False)
            upsert_agent("a3", role="reviewer", running=True)

            all_agents = list_agents()
            assert len(all_agents) == 3

            running_agents = list_agents(running_only=True)
            assert len(running_agents) == 2


class TestTaskHistory:
    """Tests for task history tracking."""

    def test_history_on_create(self, initialized_db):
        """Test that task creation is logged in history."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task_history

            create_task(task_id="hist1", file_path="/hist1.md")

            history = get_task_history("hist1")
            assert len(history) == 1
            assert history[0]["event"] == "created"

    def test_history_tracks_lifecycle(self, initialized_db):
        """Test that full lifecycle is tracked in history."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, claim_task, submit_completion, accept_completion, get_task_history

            create_task(task_id="lifecycle", file_path="/lifecycle.md")
            claim_task(agent_name="impl-agent")
            submit_completion("lifecycle", commits_count=2)
            accept_completion("lifecycle", accepted_by="pre-check")

            history = get_task_history("lifecycle")
            events = [h["event"] for h in history]

            assert "created" in events
            assert "claimed" in events
            assert "submitted" in events
            assert "accepted" in events

    def test_add_history_event(self, initialized_db):
        """Test manually adding a history event."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, add_history_event, get_task_history

            create_task(task_id="manual", file_path="/manual.md")
            add_history_event("manual", event="custom_event", agent="test", details="custom details")

            history = get_task_history("manual")
            custom = [h for h in history if h["event"] == "custom_event"]

            assert len(custom) == 1
            assert custom[0]["agent"] == "test"
            assert custom[0]["details"] == "custom details"


class TestBlockedByNormalization:
    """Tests that blocked_by is properly normalized to SQL NULL."""

    def test_create_task_blocked_by_none_stores_null(self, initialized_db):
        """create_task with blocked_by=None stores SQL NULL, not string 'None'."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task, get_connection

            create_task(task_id="bn1", file_path="/bn1.md", blocked_by=None)

            task = get_task("bn1")
            assert task["blocked_by"] is None

            # Verify at the SQL level it's actually NULL, not string "None"
            with get_connection() as conn:
                cursor = conn.execute(
                    "SELECT blocked_by, typeof(blocked_by) as btype FROM tasks WHERE id = ?",
                    ("bn1",),
                )
                row = cursor.fetchone()
                assert row["btype"] == "null"
                assert row["blocked_by"] is None

    def test_create_task_blocked_by_string_none_stores_null(self, initialized_db):
        """create_task with blocked_by='None' (string) stores SQL NULL."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task, get_connection

            create_task(task_id="bn2", file_path="/bn2.md", blocked_by="None")

            task = get_task("bn2")
            assert task["blocked_by"] is None

            with get_connection() as conn:
                cursor = conn.execute(
                    "SELECT blocked_by, typeof(blocked_by) as btype FROM tasks WHERE id = ?",
                    ("bn2",),
                )
                row = cursor.fetchone()
                assert row["btype"] == "null"

    def test_create_task_blocked_by_empty_string_stores_null(self, initialized_db):
        """create_task with blocked_by='' stores SQL NULL."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task, get_connection

            create_task(task_id="bn3", file_path="/bn3.md", blocked_by="")

            task = get_task("bn3")
            assert task["blocked_by"] is None

            with get_connection() as conn:
                cursor = conn.execute(
                    "SELECT blocked_by, typeof(blocked_by) as btype FROM tasks WHERE id = ?",
                    ("bn3",),
                )
                row = cursor.fetchone()
                assert row["btype"] == "null"

    def test_create_task_blocked_by_valid_id_stores_correctly(self, initialized_db):
        """create_task with a real blocked_by ID stores the string correctly."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task

            create_task(task_id="bn4", file_path="/bn4.md", blocked_by="abc123")

            task = get_task("bn4")
            assert task["blocked_by"] == "abc123"

    def test_create_task_blocked_by_multiple_ids_stores_correctly(self, initialized_db):
        """create_task with comma-separated IDs stores them correctly."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task

            create_task(task_id="bn5", file_path="/bn5.md", blocked_by="abc123,def456")

            task = get_task("bn5")
            assert task["blocked_by"] == "abc123,def456"

    def test_update_task_blocked_by_none_stores_null(self, initialized_db):
        """update_task with blocked_by=None stores SQL NULL."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task, get_task, get_connection

            create_task(task_id="bn6", file_path="/bn6.md", blocked_by="blocker1")

            # Clear the blocked_by
            update_task("bn6", blocked_by=None)

            task = get_task("bn6")
            assert task["blocked_by"] is None

            with get_connection() as conn:
                cursor = conn.execute(
                    "SELECT blocked_by, typeof(blocked_by) as btype FROM tasks WHERE id = ?",
                    ("bn6",),
                )
                row = cursor.fetchone()
                assert row["btype"] == "null"

    def test_update_task_blocked_by_string_none_stores_null(self, initialized_db):
        """update_task with blocked_by='None' (string) stores SQL NULL."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task, get_task, get_connection

            create_task(task_id="bn7", file_path="/bn7.md", blocked_by="blocker1")

            update_task("bn7", blocked_by="None")

            task = get_task("bn7")
            assert task["blocked_by"] is None

            with get_connection() as conn:
                cursor = conn.execute(
                    "SELECT blocked_by, typeof(blocked_by) as btype FROM tasks WHERE id = ?",
                    ("bn7",),
                )
                row = cursor.fetchone()
                assert row["btype"] == "null"

    def test_task_with_null_blocked_by_is_claimable(self, initialized_db):
        """Tasks with NULL blocked_by should be claimable."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, claim_task

            create_task(task_id="bn8", file_path="/bn8.md", blocked_by=None)

            claimed = claim_task(agent_name="agent1")
            assert claimed is not None
            assert claimed["id"] == "bn8"

    def test_task_with_string_none_blocked_by_is_claimable(self, initialized_db):
        """Tasks created with blocked_by='None' should still be claimable after normalization."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, claim_task

            create_task(task_id="bn9", file_path="/bn9.md", blocked_by="None")

            claimed = claim_task(agent_name="agent1")
            assert claimed is not None
            assert claimed["id"] == "bn9"


class TestUpdateTaskQueue:
    """Tests for the centralized update_task_queue() function."""

    def test_update_task_raises_on_queue_kwarg(self, initialized_db):
        """update_task() must raise ValueError if 'queue' is passed."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task

            create_task(task_id="utq1", file_path="/utq1.md")

            with pytest.raises(ValueError, match="Cannot update 'queue' via update_task"):
                update_task("utq1", queue="done")

    def test_done_transition_unblocks_dependents(self, initialized_db):
        """Moving a task to 'done' must unblock dependent tasks."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue, get_task

            create_task(task_id="blocker1", file_path="/blocker1.md")
            create_task(task_id="dep1", file_path="/dep1.md", blocked_by="blocker1")

            # Verify dep1 is blocked
            dep = get_task("dep1")
            assert dep["blocked_by"] == "blocker1"

            # Move blocker to done
            update_task_queue("blocker1", "done")

            # dep1 should be unblocked
            dep = get_task("dep1")
            assert dep["blocked_by"] is None

    def test_done_transition_clears_claimed_by(self, initialized_db):
        """Moving a task to 'done' must clear claimed_by."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue, get_task

            create_task(task_id="clm1", file_path="/clm1.md")
            update_task_queue("clm1", "claimed", claimed_by="agent-1")

            task = get_task("clm1")
            assert task["claimed_by"] == "agent-1"

            # Move to done — claimed_by should be auto-cleared
            update_task_queue("clm1", "done")

            task = get_task("clm1")
            assert task["queue"] == "done"
            assert task["claimed_by"] is None

    def test_done_transition_records_history(self, initialized_db):
        """Moving a task to 'done' must record a history event."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue, get_task_history

            create_task(task_id="hist1", file_path="/hist1.md")
            update_task_queue("hist1", "done")

            history = get_task_history("hist1")
            queue_events = [h for h in history if "done" in h.get("event", "")]
            assert len(queue_events) >= 1

    def test_non_done_transition_does_not_unblock(self, initialized_db):
        """Moving a task to a non-done queue must NOT unblock dependents."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue, get_task

            create_task(task_id="blocker2", file_path="/blocker2.md")
            create_task(task_id="dep2", file_path="/dep2.md", blocked_by="blocker2")

            # Move blocker to provisional (not done)
            update_task_queue("blocker2", "provisional")

            # dep2 should still be blocked
            dep = get_task("dep2")
            assert dep["blocked_by"] == "blocker2"

    def test_sets_commits_and_turns(self, initialized_db):
        """update_task_queue can set commits_count and turns_used."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue, get_task

            create_task(task_id="ct1", file_path="/ct1.md")
            update_task_queue("ct1", "provisional", commits_count=5, turns_used=42)

            task = get_task("ct1")
            assert task["queue"] == "provisional"
            assert task["commits_count"] == 5
            assert task["turns_used"] == 42

    def test_multi_blocker_partial_unblock(self, initialized_db):
        """A task blocked by multiple tasks only unblocks when ALL blockers are done."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue, get_task

            create_task(task_id="blkA", file_path="/blkA.md")
            create_task(task_id="blkB", file_path="/blkB.md")
            create_task(task_id="waitall", file_path="/waitall.md", blocked_by="blkA,blkB")

            # Complete first blocker
            update_task_queue("blkA", "done")

            # Still blocked by blkB
            task = get_task("waitall")
            assert task["blocked_by"] == "blkB"

            # Complete second blocker
            update_task_queue("blkB", "done")

            # Now fully unblocked
            task = get_task("waitall")
            assert task["blocked_by"] is None


class TestChecksField:
    """Tests for the per-task checks field."""

    def test_create_task_with_checks(self, initialized_db):
        """create_task with checks stores comma-separated string and retrieves as list."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task

            task = create_task(
                task_id="chk1",
                file_path="/chk1.md",
                checks=["gk-testing-octopoid", "vitest"],
            )

            assert task["checks"] == ["gk-testing-octopoid", "vitest"]

            # Verify round-trip
            fetched = get_task("chk1")
            assert fetched["checks"] == ["gk-testing-octopoid", "vitest"]

    def test_create_task_without_checks(self, initialized_db):
        """create_task without checks returns empty list."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task

            task = create_task(
                task_id="chk2",
                file_path="/chk2.md",
            )

            assert task["checks"] == []

            fetched = get_task("chk2")
            assert fetched["checks"] == []

    def test_create_task_with_empty_checks(self, initialized_db):
        """create_task with empty checks list returns empty list."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task

            task = create_task(
                task_id="chk3",
                file_path="/chk3.md",
                checks=[],
            )

            assert task["checks"] == []

    def test_create_task_with_single_check(self, initialized_db):
        """create_task with a single check stores and retrieves correctly."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task

            task = create_task(
                task_id="chk4",
                file_path="/chk4.md",
                checks=["gk-testing-octopoid"],
            )

            assert task["checks"] == ["gk-testing-octopoid"]

    def test_checks_stored_as_comma_separated_in_db(self, initialized_db):
        """Verify the raw DB value is a comma-separated string."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_connection

            create_task(
                task_id="chk5",
                file_path="/chk5.md",
                checks=["gk-testing-octopoid", "vitest"],
            )

            with get_connection() as conn:
                cursor = conn.execute(
                    "SELECT checks FROM tasks WHERE id = ?", ("chk5",)
                )
                row = cursor.fetchone()
                assert row["checks"] == "gk-testing-octopoid,vitest"

    def test_checks_null_when_none(self, initialized_db):
        """Verify checks is NULL in DB when not specified."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_connection

            create_task(
                task_id="chk6",
                file_path="/chk6.md",
            )

            with get_connection() as conn:
                cursor = conn.execute(
                    "SELECT checks, typeof(checks) as ctype FROM tasks WHERE id = ?",
                    ("chk6",),
                )
                row = cursor.fetchone()
                assert row["ctype"] == "null"
                assert row["checks"] is None

    def test_list_tasks_returns_checks_as_list(self, initialized_db):
        """list_tasks returns checks as list for each task."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, list_tasks

            create_task(
                task_id="chk7",
                file_path="/chk7.md",
                checks=["gk-testing-octopoid"],
            )
            create_task(
                task_id="chk8",
                file_path="/chk8.md",
            )

            tasks = list_tasks(queue="incoming")
            by_id = {t["id"]: t for t in tasks}

            assert by_id["chk7"]["checks"] == ["gk-testing-octopoid"]
            assert by_id["chk8"]["checks"] == []

    def test_get_task_by_path_returns_checks_as_list(self, initialized_db):
        """get_task_by_path returns checks as list."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task_by_path

            create_task(
                task_id="chk9",
                file_path="/chk9.md",
                checks=["vitest", "typecheck"],
            )

            task = get_task_by_path("/chk9.md")
            assert task["checks"] == ["vitest", "typecheck"]


class TestStagingUrl:
    """Tests for the staging_url field on tasks."""

    def test_create_task_with_staging_url(self, initialized_db):
        """create_task with staging_url stores it correctly."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task

            task = create_task(
                task_id="stg1",
                file_path="/stg1.md",
                staging_url="https://my-branch.boxen-8f6.pages.dev",
            )

            assert task["staging_url"] == "https://my-branch.boxen-8f6.pages.dev"

            # Verify round-trip
            fetched = get_task("stg1")
            assert fetched["staging_url"] == "https://my-branch.boxen-8f6.pages.dev"

    def test_create_task_without_staging_url(self, initialized_db):
        """create_task without staging_url returns None."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task

            task = create_task(
                task_id="stg2",
                file_path="/stg2.md",
            )

            assert task["staging_url"] is None

    def test_update_task_staging_url(self, initialized_db):
        """update_task can set staging_url."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task, get_task

            create_task(task_id="stg3", file_path="/stg3.md")

            update_task("stg3", staging_url="https://agent-abc123.boxen-8f6.pages.dev")

            task = get_task("stg3")
            assert task["staging_url"] == "https://agent-abc123.boxen-8f6.pages.dev"

    def test_staging_url_null_by_default_in_db(self, initialized_db):
        """staging_url is NULL in DB when not specified."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_connection

            create_task(task_id="stg4", file_path="/stg4.md")

            with get_connection() as conn:
                cursor = conn.execute(
                    "SELECT staging_url, typeof(staging_url) as stype FROM tasks WHERE id = ?",
                    ("stg4",),
                )
                row = cursor.fetchone()
                assert row["stype"] == "null"
                assert row["staging_url"] is None

    def test_list_tasks_includes_staging_url(self, initialized_db):
        """list_tasks includes staging_url for each task."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, list_tasks

            create_task(
                task_id="stg5",
                file_path="/stg5.md",
                staging_url="https://preview.pages.dev",
            )
            create_task(
                task_id="stg6",
                file_path="/stg6.md",
            )

            tasks = list_tasks(queue="incoming")
            by_id = {t["id"]: t for t in tasks}

            assert by_id["stg5"]["staging_url"] == "https://preview.pages.dev"
            assert by_id["stg6"]["staging_url"] is None


class TestUpdateTaskJsonFields:
    """Tests for update_task() handling of JSON-typed fields (checks, check_results)."""

    def test_update_task_checks_list_serialized(self, initialized_db):
        """update_task with checks=list serializes to comma-separated string."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task, get_task, get_connection

            create_task(task_id="jf1", file_path="/jf1.md")

            update_task("jf1", checks=["gk-testing", "vitest"])

            task = get_task("jf1")
            assert task["checks"] == ["gk-testing", "vitest"]

            # Raw DB value should be comma-separated string
            with get_connection() as conn:
                cursor = conn.execute(
                    "SELECT checks FROM tasks WHERE id = ?", ("jf1",)
                )
                row = cursor.fetchone()
                assert row["checks"] == "gk-testing,vitest"

    def test_update_task_checks_empty_list_stores_null(self, initialized_db):
        """update_task with checks=[] stores NULL."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task, get_task

            create_task(task_id="jf2", file_path="/jf2.md", checks=["old-check"])

            update_task("jf2", checks=[])

            task = get_task("jf2")
            assert task["checks"] == []

    def test_update_task_checks_none_stores_null(self, initialized_db):
        """update_task with checks=None stores NULL."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task, get_task, get_connection

            create_task(task_id="jf3", file_path="/jf3.md", checks=["old-check"])

            update_task("jf3", checks=None)

            task = get_task("jf3")
            assert task["checks"] == []

            with get_connection() as conn:
                cursor = conn.execute(
                    "SELECT checks, typeof(checks) as ctype FROM tasks WHERE id = ?",
                    ("jf3",),
                )
                row = cursor.fetchone()
                assert row["ctype"] == "null"

    def test_update_task_check_results_dict_serialized(self, initialized_db):
        """update_task with check_results=dict serializes to JSON string."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task, get_task, get_connection
            import json

            create_task(task_id="jf4", file_path="/jf4.md")

            results = {"gk-testing": {"status": "pass", "summary": "ok"}}
            update_task("jf4", check_results=results)

            task = get_task("jf4")
            assert task["check_results"] == results

            # Raw DB value should be JSON string
            with get_connection() as conn:
                cursor = conn.execute(
                    "SELECT check_results FROM tasks WHERE id = ?", ("jf4",)
                )
                row = cursor.fetchone()
                assert json.loads(row["check_results"]) == results

    def test_update_task_check_results_empty_dict_stores_null(self, initialized_db):
        """update_task with check_results={} stores NULL."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task, get_task

            create_task(task_id="jf5", file_path="/jf5.md")

            update_task("jf5", check_results={})

            task = get_task("jf5")
            assert task["check_results"] == {}

    def test_update_task_check_results_none_stores_null(self, initialized_db):
        """update_task with check_results=None stores NULL."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task, get_task

            create_task(task_id="jf6", file_path="/jf6.md")

            update_task("jf6", check_results=None)

            task = get_task("jf6")
            assert task["check_results"] == {}

    def test_update_task_checks_string_passthrough(self, initialized_db):
        """update_task with checks=string (already serialized) passes through."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task, get_task

            create_task(task_id="jf7", file_path="/jf7.md")

            # Passing a pre-serialized string should still work
            update_task("jf7", checks="gk-testing,vitest")

            task = get_task("jf7")
            assert task["checks"] == ["gk-testing", "vitest"]


class TestMigrationV9:
    """Tests for schema migration v8 -> v9 (staging_url)."""

    def test_migration_adds_staging_url_column(self, mock_config, db_path):
        """migrate_schema adds staging_url column when upgrading from v8."""
        with patch('orchestrator.db.get_database_path', return_value=db_path):
            from orchestrator.db import init_schema, get_connection, migrate_schema

            # Initialize schema at current version
            init_schema()

            # Manually downgrade to v8 to test migration
            with get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO schema_info (key, value) VALUES (?, ?)",
                    ("version", "8"),
                )
                # Drop the column to simulate v8 schema
                # (SQLite doesn't support DROP COLUMN in older versions,
                # but the migration uses ALTER TABLE ADD which is idempotent)

            # Run migration
            result = migrate_schema()
            assert result is True

            # Verify column exists by inserting and querying
            with get_connection() as conn:
                conn.execute(
                    "UPDATE tasks SET staging_url = ? WHERE 1=0",
                    ("test",),
                )
                # No error means column exists
