"""Tests for the structured project report API."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.reports import (
    _extract_staging_url,
    _format_task,
    _gather_agents,
    _gather_health,
    _gather_messages,
    _gather_prs,
    _gather_proposals,
    _gather_work,
    _get_agent_notes,
    _get_recent_tasks_for_agent,
    _is_recent,
    _load_agent_state,
    _store_staging_url,
    get_project_report,
)


# ---------------------------------------------------------------------------
# Top-level report structure
# ---------------------------------------------------------------------------


class TestGetProjectReport:
    """Tests for the main get_project_report() function."""

    @patch("orchestrator.reports._gather_work")
    @patch("orchestrator.reports._gather_done_tasks")
    @patch("orchestrator.reports._gather_prs")
    @patch("orchestrator.reports._gather_proposals")
    @patch("orchestrator.reports._gather_messages")
    @patch("orchestrator.reports._gather_agents")
    @patch("orchestrator.reports._gather_health")
    def test_report_has_all_top_level_keys(
        self,
        mock_health,
        mock_agents,
        mock_messages,
        mock_proposals,
        mock_prs,
        mock_done,
        mock_work,
    ):
        mock_work.return_value = {"incoming": [], "in_progress": [], "in_review": [], "done_today": []}
        mock_done.return_value = []
        mock_prs.return_value = []
        mock_proposals.return_value = []
        mock_messages.return_value = []
        mock_agents.return_value = []
        mock_health.return_value = {"scheduler": "unknown", "idle_agents": 0, "queue_depth": 0}

        report = get_project_report()

        assert "work" in report
        assert "done_tasks" in report
        assert "prs" in report
        assert "proposals" in report
        assert "messages" in report
        assert "agents" in report
        assert "health" in report
        assert "generated_at" in report

    @patch("orchestrator.reports._gather_work")
    @patch("orchestrator.reports._gather_done_tasks")
    @patch("orchestrator.reports._gather_prs")
    @patch("orchestrator.reports._gather_proposals")
    @patch("orchestrator.reports._gather_messages")
    @patch("orchestrator.reports._gather_agents")
    @patch("orchestrator.reports._gather_health")
    def test_generated_at_is_iso_timestamp(
        self,
        mock_health,
        mock_agents,
        mock_messages,
        mock_proposals,
        mock_prs,
        mock_done,
        mock_work,
    ):
        mock_work.return_value = {"incoming": [], "in_progress": [], "in_review": [], "done_today": []}
        mock_done.return_value = []
        mock_prs.return_value = []
        mock_proposals.return_value = []
        mock_messages.return_value = []
        mock_agents.return_value = []
        mock_health.return_value = {}

        report = get_project_report()

        dt = datetime.fromisoformat(report["generated_at"])
        assert isinstance(dt, datetime)

    @patch("orchestrator.reports._gather_work")
    @patch("orchestrator.reports._gather_done_tasks")
    @patch("orchestrator.reports._gather_prs")
    @patch("orchestrator.reports._gather_proposals")
    @patch("orchestrator.reports._gather_messages")
    @patch("orchestrator.reports._gather_agents")
    @patch("orchestrator.reports._gather_health")
    def test_work_has_expected_sub_keys(
        self,
        mock_health,
        mock_agents,
        mock_messages,
        mock_proposals,
        mock_prs,
        mock_done,
        mock_work,
    ):
        mock_work.return_value = {"incoming": [], "in_progress": [], "in_review": [], "done_today": []}
        mock_done.return_value = []
        mock_prs.return_value = []
        mock_proposals.return_value = []
        mock_messages.return_value = []
        mock_agents.return_value = []
        mock_health.return_value = {}

        report = get_project_report()

        work = report["work"]
        assert "incoming" in work
        assert "in_progress" in work
        assert "in_review" in work
        assert "done_today" in work


# ---------------------------------------------------------------------------
# Task formatting
# ---------------------------------------------------------------------------


class TestFormatTask:
    """Tests for _format_task()."""

    def test_formats_all_expected_fields(self):
        task = {
            "id": "abc12345",
            "title": "Implement feature X",
            "role": "implement",
            "priority": "P1",
            "branch": "main",
            "created": "2026-02-07T10:00:00",
            "claimed_by": "impl-agent-1",
            "turns_used": 42,
            "commits_count": 3,
            "pr_number": 55,
            "blocked_by": None,
            "project_id": "PROJ-001",
            "attempt_count": 1,
            "rejection_count": 0,
        }

        result = _format_task(task)

        assert result["id"] == "abc12345"
        assert result["title"] == "Implement feature X"
        assert result["role"] == "implement"
        assert result["priority"] == "P1"
        assert result["branch"] == "main"
        assert result["created"] == "2026-02-07T10:00:00"
        assert result["agent"] == "impl-agent-1"
        assert result["turns"] == 42
        assert result["commits"] == 3
        assert result["pr_number"] == 55
        assert result["blocked_by"] is None
        assert result["project_id"] == "PROJ-001"
        assert result["attempt_count"] == 1
        assert result["rejection_count"] == 0

    def test_handles_missing_fields_gracefully(self):
        task = {"id": "xyz"}

        result = _format_task(task)

        assert result["id"] == "xyz"
        assert result["title"] is None
        assert result["turns"] == 0
        assert result["commits"] == 0
        assert result["pr_number"] is None


# ---------------------------------------------------------------------------
# _is_recent
# ---------------------------------------------------------------------------


class TestIsRecent:
    """Tests for _is_recent()."""

    def test_recent_task_returns_true(self):
        task = {"created": datetime.now().isoformat()}
        cutoff = datetime.now() - timedelta(hours=24)
        assert _is_recent(task, cutoff) is True

    def test_old_task_returns_false(self):
        task = {"created": (datetime.now() - timedelta(days=3)).isoformat()}
        cutoff = datetime.now() - timedelta(hours=24)
        assert _is_recent(task, cutoff) is False

    def test_missing_timestamp_returns_false(self):
        assert _is_recent({}, datetime.now()) is False

    def test_invalid_timestamp_returns_false(self):
        assert _is_recent({"created": "not-a-date"}, datetime.now()) is False

    def test_handles_timezone_aware_timestamp(self):
        task = {"created": "2026-02-07T10:00:00Z"}
        cutoff = datetime(2026, 2, 7, 9, 0, 0)
        assert _is_recent(task, cutoff) is True


# ---------------------------------------------------------------------------
# PRs
# ---------------------------------------------------------------------------


class TestGatherPrs:
    """Tests for _gather_prs()."""

    @patch("subprocess.run")
    def test_returns_formatted_prs(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([
                {
                    "number": 55,
                    "title": "Fix z-fighting",
                    "headRefName": "agent/f737dc48",
                    "author": {"login": "bot"},
                    "updatedAt": "2026-02-07T10:00:00Z",
                    "createdAt": "2026-02-07T09:00:00Z",
                    "url": "https://github.com/owner/repo/pull/55",
                },
            ]),
        )

        prs = _gather_prs()

        assert len(prs) == 1
        assert prs[0]["number"] == 55
        assert prs[0]["title"] == "Fix z-fighting"
        assert prs[0]["branch"] == "agent/f737dc48"
        assert prs[0]["author"] == "bot"
        assert prs[0]["url"] == "https://github.com/owner/repo/pull/55"

    @patch("subprocess.run")
    def test_returns_empty_on_gh_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        prs = _gather_prs()
        assert prs == []

    @patch("subprocess.run", side_effect=FileNotFoundError("gh not found"))
    def test_returns_empty_when_gh_not_installed(self, mock_run):
        prs = _gather_prs()
        assert prs == []

    @patch("subprocess.run")
    def test_handles_null_author(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{"number": 1, "title": "PR", "author": None}]),
        )

        prs = _gather_prs()
        assert prs[0]["author"] is None


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


class TestGatherProposals:
    """Tests for _gather_proposals()."""

    @patch("orchestrator.proposal_utils.list_proposals")
    def test_returns_formatted_proposals(self, mock_list):
        mock_list.return_value = [
            {
                "id": "PROP-001",
                "title": "Store migration",
                "proposer": "architect",
                "category": "refactor",
                "complexity": "L",
                "created": "2026-02-07T09:00:00",
                "content": "full content here",
            }
        ]

        proposals = _gather_proposals()

        assert len(proposals) == 1
        assert proposals[0]["id"] == "PROP-001"
        assert proposals[0]["title"] == "Store migration"
        assert proposals[0]["proposer"] == "architect"
        # Should not include full content
        assert "content" not in proposals[0]

    @patch("orchestrator.proposal_utils.list_proposals", side_effect=Exception("no proposals dir"))
    def test_returns_empty_on_error(self, mock_list):
        proposals = _gather_proposals()
        assert proposals == []


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class TestGatherMessages:
    """Tests for _gather_messages()."""

    @patch("orchestrator.message_utils.list_messages")
    def test_returns_formatted_messages(self, mock_list):
        mock_list.return_value = [
            {"filename": "warning-20260207-test.md", "type": "warning", "created": 1234567890.0},
        ]

        messages = _gather_messages()

        assert len(messages) == 1
        assert messages[0]["filename"] == "warning-20260207-test.md"
        assert messages[0]["type"] == "warning"

    @patch("orchestrator.message_utils.list_messages", side_effect=Exception("no dir"))
    def test_returns_empty_on_error(self, mock_list):
        messages = _gather_messages()
        assert messages == []


# ---------------------------------------------------------------------------
# Agent state helpers
# ---------------------------------------------------------------------------


class TestLoadAgentState:
    """Tests for _load_agent_state()."""

    def test_loads_valid_state(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({
            "running": True,
            "pid": 12345,
            "current_task": "abc123",
            "last_started": "2026-02-07T10:00:00",
        }))

        state = _load_agent_state(state_file)

        assert state["running"] is True
        assert state["pid"] == 12345
        assert state["current_task"] == "abc123"

    def test_returns_empty_dict_for_missing_file(self, tmp_path):
        state = _load_agent_state(tmp_path / "nonexistent.json")
        assert state == {}

    def test_returns_empty_dict_for_invalid_json(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text("not valid json")

        state = _load_agent_state(state_file)
        assert state == {}


class TestGetAgentNotes:
    """Tests for _get_agent_notes()."""

    def test_returns_notes_for_current_task(self, tmp_path):
        notes_file = tmp_path / "TASK-abc123.md"
        notes_file.write_text("# Agent Notes: TASK-abc123\n\nSome notes here.")

        notes = _get_agent_notes(tmp_path, "abc123")
        assert notes is not None
        assert "Some notes here" in notes

    def test_returns_none_when_no_current_task(self, tmp_path):
        notes = _get_agent_notes(tmp_path, None)
        assert notes is None

    def test_returns_none_when_no_notes_file(self, tmp_path):
        notes = _get_agent_notes(tmp_path, "nonexistent")
        assert notes is None

    def test_truncates_long_notes(self, tmp_path):
        notes_file = tmp_path / "TASK-abc123.md"
        notes_file.write_text("x" * 1000)

        notes = _get_agent_notes(tmp_path, "abc123")
        assert notes is not None
        assert len(notes) == 500


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


class TestGatherAgents:
    """Tests for _gather_agents()."""

    @patch("orchestrator.reports._get_recent_tasks_for_agent", return_value=[])
    @patch("orchestrator.config.get_agents")
    @patch("orchestrator.config.get_agents_runtime_dir")
    @patch("orchestrator.config.get_notes_dir")
    def test_returns_agent_entries_with_all_fields(
        self, mock_notes_dir, mock_runtime_dir, mock_agents, mock_recent, tmp_path
    ):
        mock_agents.return_value = [
            {"name": "impl-1", "role": "implementer", "paused": False},
        ]
        runtime_dir = tmp_path / "agents"
        (runtime_dir / "impl-1").mkdir(parents=True)
        state_file = runtime_dir / "impl-1" / "state.json"
        state_file.write_text(json.dumps({
            "running": False,
            "last_started": "2026-02-07T09:00:00",
            "last_finished": "2026-02-07T09:30:00",
            "last_exit_code": 0,
            "consecutive_failures": 0,
            "total_runs": 10,
            "current_task": None,
        }))
        mock_runtime_dir.return_value = runtime_dir
        mock_notes_dir.return_value = tmp_path / "notes"
        (tmp_path / "notes").mkdir()

        agents = _gather_agents()

        assert len(agents) == 1
        agent = agents[0]
        assert agent["name"] == "impl-1"
        assert agent["role"] == "implementer"
        assert agent["status"] == "idle"
        assert agent["paused"] is False
        assert agent["current_task"] is None
        assert agent["last_started"] == "2026-02-07T09:00:00"
        assert agent["last_finished"] == "2026-02-07T09:30:00"
        assert agent["total_runs"] == 10
        assert agent["recent_tasks"] == []
        assert agent["notes"] is None

    @patch("orchestrator.reports._get_recent_tasks_for_agent", return_value=[])
    @patch("orchestrator.config.get_agents")
    @patch("orchestrator.config.get_agents_runtime_dir")
    @patch("orchestrator.config.get_notes_dir")
    def test_paused_agent_shows_paused_status(
        self, mock_notes_dir, mock_runtime_dir, mock_agents, mock_recent, tmp_path
    ):
        mock_agents.return_value = [
            {"name": "agent-1", "role": "implementer", "paused": True},
        ]
        runtime_dir = tmp_path / "agents"
        (runtime_dir / "agent-1").mkdir(parents=True)
        mock_runtime_dir.return_value = runtime_dir
        mock_notes_dir.return_value = tmp_path / "notes"
        (tmp_path / "notes").mkdir()

        agents = _gather_agents()
        assert agents[0]["status"] == "paused"

    @patch("orchestrator.reports._get_recent_tasks_for_agent", return_value=[])
    @patch("orchestrator.config.get_agents")
    @patch("orchestrator.config.get_agents_runtime_dir")
    @patch("orchestrator.config.get_notes_dir")
    @patch("orchestrator.state_utils.is_process_running", return_value=True)
    def test_running_agent_shows_running_status(
        self, mock_pid_check, mock_notes_dir, mock_runtime_dir, mock_agents,
        mock_recent, tmp_path
    ):
        mock_agents.return_value = [
            {"name": "agent-1", "role": "implementer", "paused": False},
        ]
        runtime_dir = tmp_path / "agents"
        (runtime_dir / "agent-1").mkdir(parents=True)
        (runtime_dir / "agent-1" / "state.json").write_text(
            json.dumps({"running": True, "pid": 12345, "current_task": "task123"})
        )
        mock_runtime_dir.return_value = runtime_dir
        mock_notes_dir.return_value = tmp_path / "notes"
        (tmp_path / "notes").mkdir()

        agents = _gather_agents()
        assert agents[0]["status"] == "running"
        assert agents[0]["current_task"] == "task123"

    @patch("orchestrator.reports._get_recent_tasks_for_agent", return_value=[])
    @patch("orchestrator.config.get_agents")
    @patch("orchestrator.config.get_agents_runtime_dir")
    @patch("orchestrator.config.get_notes_dir")
    @patch("orchestrator.state_utils.is_process_running", return_value=False)
    def test_stale_running_flag_shows_idle(
        self, mock_pid_check, mock_notes_dir, mock_runtime_dir, mock_agents,
        mock_recent, tmp_path
    ):
        """Agent with running=True but dead process should show idle."""
        mock_agents.return_value = [
            {"name": "agent-1", "role": "implementer", "paused": False},
        ]
        runtime_dir = tmp_path / "agents"
        (runtime_dir / "agent-1").mkdir(parents=True)
        (runtime_dir / "agent-1" / "state.json").write_text(
            json.dumps({"running": True, "pid": 99999, "current_task": "task123"})
        )
        mock_runtime_dir.return_value = runtime_dir
        mock_notes_dir.return_value = tmp_path / "notes"
        (tmp_path / "notes").mkdir()

        agents = _gather_agents()
        assert agents[0]["status"] == "idle"
        assert agents[0]["current_task"] is None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestGatherHealth:
    """Tests for _gather_health()."""

    @patch("orchestrator.reports._get_scheduler_status", return_value="running")
    @patch("orchestrator.queue_utils.count_queue")
    @patch("orchestrator.config.is_system_paused", return_value=False)
    @patch("orchestrator.config.get_orchestrator_dir")
    @patch("orchestrator.config.get_agents")
    def test_returns_health_fields(
        self, mock_agents, mock_orch_dir, mock_paused, mock_count, mock_sched, tmp_path
    ):
        mock_agents.return_value = [
            {"name": "agent-1", "paused": False},
            {"name": "agent-2", "paused": True},
        ]
        runtime_dir = tmp_path / "agents"
        (runtime_dir / "agent-1").mkdir(parents=True)
        (runtime_dir / "agent-2").mkdir(parents=True)
        mock_orch_dir.return_value = tmp_path

        # count_queue returns 3 for incoming, 1 for claimed, 0 for breakdown
        mock_count.side_effect = [3, 1, 0]

        health = _gather_health()

        assert health["scheduler"] == "running"
        assert health["system_paused"] is False
        assert health["idle_agents"] == 1
        assert health["paused_agents"] == 1
        assert health["total_agents"] == 2
        assert health["queue_depth"] == 4

    @patch("orchestrator.reports._get_scheduler_status", return_value="not_loaded")
    @patch("orchestrator.queue_utils.count_queue", return_value=0)
    @patch("orchestrator.config.is_system_paused", return_value=True)
    @patch("orchestrator.config.get_orchestrator_dir")
    @patch("orchestrator.config.get_agents", return_value=[])
    def test_handles_empty_agents(
        self, mock_agents, mock_orch_dir, mock_paused, mock_count, mock_sched, tmp_path
    ):
        mock_orch_dir.return_value = tmp_path
        (tmp_path / "agents").mkdir()

        health = _gather_health()

        assert health["idle_agents"] == 0
        assert health["running_agents"] == 0
        assert health["paused_agents"] == 0
        assert health["total_agents"] == 0
        assert health["system_paused"] is True


# ---------------------------------------------------------------------------
# Integration-style test with DB
# ---------------------------------------------------------------------------


class TestGatherWorkWithDB:
    """Integration tests using the DB fixtures from conftest."""

    def test_work_sections_populated_from_db(self, mock_config, initialized_db):
        """Test that _gather_work returns data from DB tasks."""
        from orchestrator.db import create_task, update_task, update_task_queue

        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            incoming_dir = mock_config / "shared" / "queue" / "incoming"
            claimed_dir = mock_config / "shared" / "queue" / "claimed"
            prov_dir = mock_config / "shared" / "queue" / "provisional"
            done_dir = mock_config / "shared" / "queue" / "done"

            for d in [incoming_dir, claimed_dir, prov_dir, done_dir]:
                d.mkdir(parents=True, exist_ok=True)

            # incoming task
            fp1 = incoming_dir / "TASK-inc001.md"
            fp1.write_text("# [TASK-inc001] Incoming task\n\nROLE: implement\nPRIORITY: P1\n")
            create_task(task_id="inc001", file_path=str(fp1), role="implement")

            # claimed task
            fp2 = claimed_dir / "TASK-clm001.md"
            fp2.write_text("# [TASK-clm001] Claimed task\n\nROLE: implement\nPRIORITY: P1\n")
            create_task(task_id="clm001", file_path=str(fp2), role="implement")
            update_task_queue("clm001", "claimed", claimed_by="agent-1")

            # provisional task
            fp3 = prov_dir / "TASK-prv001.md"
            fp3.write_text("# [TASK-prv001] Provisional task\n\nROLE: implement\nPRIORITY: P2\n")
            create_task(task_id="prv001", file_path=str(fp3), role="implement")
            update_task_queue("prv001", "provisional")

            work = _gather_work()

            assert len(work["incoming"]) == 1
            assert work["incoming"][0]["id"] == "inc001"
            assert len(work["in_progress"]) == 1
            assert work["in_progress"][0]["id"] == "clm001"
            assert work["in_progress"][0]["agent"] == "agent-1"
            assert len(work["in_review"]) == 1
            assert work["in_review"][0]["id"] == "prv001"

    def test_done_today_filters_by_recency(self, mock_config, initialized_db):
        """Test that done_today only includes recent tasks."""
        from orchestrator.db import create_task, update_task, update_task_queue

        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            done_dir = mock_config / "shared" / "queue" / "done"
            done_dir.mkdir(parents=True, exist_ok=True)

            now_str = datetime.now().isoformat()
            fp1 = done_dir / "TASK-recent1.md"
            fp1.write_text(f"# [TASK-recent1] Recent task\n\nROLE: implement\nCREATED: {now_str}\n")
            create_task(task_id="recent1", file_path=str(fp1), role="implement")
            update_task_queue("recent1", "done")

            work = _gather_work()

            done_ids = [t["id"] for t in work["done_today"]]
            assert "recent1" in done_ids


class TestRecentTasksForAgent:
    """Tests for _get_recent_tasks_for_agent()."""

    def test_returns_tasks_for_agent_from_db(self, mock_config, initialized_db):
        from orchestrator.db import create_task, update_task, update_task_queue

        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            done_dir = mock_config / "shared" / "queue" / "done"
            done_dir.mkdir(parents=True, exist_ok=True)

            for i in range(7):
                tid = f"agt{i:04d}"
                fp = done_dir / f"TASK-{tid}.md"
                fp.write_text(f"# [TASK-{tid}] Task {i}\n\nROLE: implement\n")
                create_task(task_id=tid, file_path=str(fp), role="implement")
                update_task_queue(tid, "done", claimed_by="test-agent", commits_count=i)

            tasks = _get_recent_tasks_for_agent("test-agent", limit=5)

            assert len(tasks) == 5
            for t in tasks:
                assert "id" in t
                assert "title" in t
                assert "queue" in t
                assert "commits" in t
                assert "turns" in t

    @patch("orchestrator.config.is_db_enabled", return_value=False)
    def test_returns_empty_when_db_disabled(self, mock_db):
        tasks = _get_recent_tasks_for_agent("any-agent")
        assert tasks == []


# ---------------------------------------------------------------------------
# Staging URL extraction
# ---------------------------------------------------------------------------


class TestExtractStagingUrl:
    """Tests for _extract_staging_url()."""

    @patch("subprocess.run")
    def test_extracts_url_from_cloudflare_table_row(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="| Branch Preview | https://abc123.boxen-app.pages.dev |\n",
        )

        url = _extract_staging_url(55)
        assert url == "https://abc123.boxen-app.pages.dev"

    @patch("subprocess.run")
    def test_extracts_url_from_bold_markdown_format(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="| **Branch Preview** | [Visit Preview](https://feature-branch.boxen-app.pages.dev) |\n",
        )

        url = _extract_staging_url(55)
        assert url == "https://feature-branch.boxen-app.pages.dev"

    @patch("subprocess.run")
    def test_returns_none_when_no_cloudflare_comment(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Just a regular comment\nAnother comment\n",
        )

        url = _extract_staging_url(55)
        assert url is None

    @patch("subprocess.run")
    def test_returns_none_on_gh_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        url = _extract_staging_url(55)
        assert url is None

    @patch("subprocess.run", side_effect=FileNotFoundError("gh not found"))
    def test_returns_none_when_gh_not_installed(self, mock_run):
        url = _extract_staging_url(55)
        assert url is None

    @patch("subprocess.run")
    def test_handles_multiple_comments_finds_first(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "Some unrelated comment\n"
                "| Branch Preview | https://first.boxen-app.pages.dev |\n"
                "| Branch Preview | https://second.boxen-app.pages.dev |\n"
            ),
        )

        url = _extract_staging_url(55)
        assert url == "https://first.boxen-app.pages.dev"


class TestStoreStagingUrl:
    """Tests for _store_staging_url()."""

    def test_stores_url_on_matching_task(self, mock_config, initialized_db):
        from orchestrator.db import create_task, get_connection, update_task

        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            task_dir = mock_config / "shared" / "queue" / "incoming"
            task_dir.mkdir(parents=True, exist_ok=True)
            fp = task_dir / "TASK-store01.md"
            fp.write_text("# [TASK-store01] Test task\n")
            create_task(task_id="store01", file_path=str(fp), role="implement")
            update_task("store01", pr_number=77)

            _store_staging_url(77, "https://preview.pages.dev")

            with get_connection() as conn:
                row = conn.execute(
                    "SELECT staging_url FROM tasks WHERE id = ?", ("store01",)
                ).fetchone()
                assert row["staging_url"] == "https://preview.pages.dev"

    def test_no_error_when_no_matching_task(self, mock_config, initialized_db):
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            # Should not raise - best effort
            _store_staging_url(9999, "https://preview.pages.dev")

    @patch("orchestrator.config.is_db_enabled", return_value=False)
    def test_noop_when_db_disabled(self, mock_db):
        # Should not raise
        _store_staging_url(55, "https://preview.pages.dev")

    def test_falls_back_to_branch_pattern(self, mock_config, initialized_db):
        """When pr_number doesn't match, fall back to branch name pattern."""
        from orchestrator.db import create_task, get_connection

        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            task_dir = mock_config / "shared" / "queue" / "incoming"
            task_dir.mkdir(parents=True, exist_ok=True)
            fp = task_dir / "TASK-abc12345.md"
            fp.write_text("# [TASK-abc12345] Test task\n")
            create_task(task_id="abc12345", file_path=str(fp), role="implement")
            # pr_number is NOT set on this task

            _store_staging_url(99, "https://preview.pages.dev", branch_name="agent/abc12345-20260209")

            with get_connection() as conn:
                row = conn.execute(
                    "SELECT staging_url FROM tasks WHERE id = ?", ("abc12345",)
                ).fetchone()
                assert row["staging_url"] == "https://preview.pages.dev"

    def test_staging_url_updates_on_new_pr(self, mock_config, initialized_db):
        """When a new PR is created for the same task, staging_url should update."""
        from orchestrator.db import create_task, get_connection, update_task

        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            task_dir = mock_config / "shared" / "queue" / "incoming"
            task_dir.mkdir(parents=True, exist_ok=True)
            fp = task_dir / "TASK-def45678.md"
            fp.write_text("# [TASK-def45678] Test task\n")
            create_task(task_id="def45678", file_path=str(fp), role="implement")

            # First PR sets pr_number and staging_url
            update_task("def45678", pr_number=10, staging_url="https://old.pages.dev")

            # New PR with new number — update_task overwrites pr_number
            update_task("def45678", pr_number=20)

            # _store_staging_url finds the task by new pr_number and updates staging_url
            _store_staging_url(20, "https://new.pages.dev")

            with get_connection() as conn:
                row = conn.execute(
                    "SELECT staging_url, pr_number FROM tasks WHERE id = ?", ("def45678",)
                ).fetchone()
                assert row["staging_url"] == "https://new.pages.dev"
                assert row["pr_number"] == 20

    def test_branch_fallback_no_match(self, mock_config, initialized_db):
        """Branch fallback with non-matching branch pattern does nothing."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            # No task exists — should not raise
            _store_staging_url(9999, "https://preview.pages.dev", branch_name="feature/unrelated")


class TestGatherPrsStagingUrl:
    """Tests for staging_url integration in _gather_prs()."""

    @patch("orchestrator.reports._store_staging_url")
    @patch("orchestrator.reports._extract_staging_url")
    @patch("subprocess.run")
    def test_pr_includes_staging_url(self, mock_run, mock_extract, mock_store):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{
                "number": 55,
                "title": "Feature PR",
                "headRefName": "agent/abc123",
                "author": {"login": "bot"},
                "updatedAt": "2026-02-07T10:00:00Z",
                "createdAt": "2026-02-07T09:00:00Z",
                "url": "https://github.com/owner/repo/pull/55",
            }]),
        )
        mock_extract.return_value = "https://abc123.pages.dev"

        prs = _gather_prs()

        assert len(prs) == 1
        assert prs[0]["staging_url"] == "https://abc123.pages.dev"
        mock_store.assert_called_once_with(55, "https://abc123.pages.dev", branch_name="agent/abc123")

    @patch("orchestrator.reports._store_staging_url")
    @patch("orchestrator.reports._extract_staging_url")
    @patch("subprocess.run")
    def test_pr_staging_url_none_when_not_found(self, mock_run, mock_extract, mock_store):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{
                "number": 55,
                "title": "Feature PR",
                "headRefName": "agent/abc123",
                "author": {"login": "bot"},
                "updatedAt": "2026-02-07T10:00:00Z",
                "createdAt": "2026-02-07T09:00:00Z",
                "url": "https://github.com/owner/repo/pull/55",
            }]),
        )
        mock_extract.return_value = None

        prs = _gather_prs()

        assert len(prs) == 1
        assert prs[0]["staging_url"] is None
        mock_store.assert_not_called()


class TestFormatTaskStagingUrl:
    """Tests for staging_url in _format_task()."""

    def test_includes_staging_url_when_present(self):
        task = {
            "id": "abc123",
            "title": "Test task",
            "staging_url": "https://preview.pages.dev",
        }
        result = _format_task(task)
        assert result["staging_url"] == "https://preview.pages.dev"

    def test_staging_url_none_when_absent(self):
        task = {"id": "abc123", "title": "Test task"}
        result = _format_task(task)
        assert result["staging_url"] is None


# ---------------------------------------------------------------------------
# PR number storage from implementer
# ---------------------------------------------------------------------------


class TestStorePrInDb:
    """Tests for ImplementerRole._store_pr_in_db()."""

    def test_stores_pr_number_and_url(self, mock_config, initialized_db):
        from orchestrator.db import create_task, get_connection
        from orchestrator.roles.implementer import ImplementerRole

        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            task_dir = mock_config / "shared" / "queue" / "incoming"
            task_dir.mkdir(parents=True, exist_ok=True)
            fp = task_dir / "TASK-pr00001.md"
            fp.write_text("# [TASK-pr00001] Test task\n")
            create_task(task_id="pr00001", file_path=str(fp), role="implement")

            ImplementerRole._store_pr_in_db("pr00001", "https://github.com/owner/repo/pull/42")

            with get_connection() as conn:
                row = conn.execute(
                    "SELECT pr_number, pr_url FROM tasks WHERE id = ?", ("pr00001",)
                ).fetchone()
                assert row["pr_number"] == 42
                assert row["pr_url"] == "https://github.com/owner/repo/pull/42"

    def test_ignores_invalid_url(self, mock_config, initialized_db):
        from orchestrator.db import create_task, get_connection
        from orchestrator.roles.implementer import ImplementerRole

        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            task_dir = mock_config / "shared" / "queue" / "incoming"
            task_dir.mkdir(parents=True, exist_ok=True)
            fp = task_dir / "TASK-pr00002.md"
            fp.write_text("# [TASK-pr00002] Test task\n")
            create_task(task_id="pr00002", file_path=str(fp), role="implement")

            # URL without /pull/N pattern — should silently skip
            ImplementerRole._store_pr_in_db("pr00002", "https://github.com/owner/repo/issues/42")

            with get_connection() as conn:
                row = conn.execute(
                    "SELECT pr_number, pr_url FROM tasks WHERE id = ?", ("pr00002",)
                ).fetchone()
                assert row["pr_number"] is None
                assert row["pr_url"] is None

    @patch("orchestrator.config.is_db_enabled", return_value=False)
    def test_noop_when_db_disabled(self, mock_db):
        from orchestrator.roles.implementer import ImplementerRole

        # Should not raise
        ImplementerRole._store_pr_in_db("any", "https://github.com/owner/repo/pull/1")

    def test_overwrites_on_new_pr(self, mock_config, initialized_db):
        """When a task gets a new PR, pr_number and pr_url should update."""
        from orchestrator.db import create_task, get_connection
        from orchestrator.roles.implementer import ImplementerRole

        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            task_dir = mock_config / "shared" / "queue" / "incoming"
            task_dir.mkdir(parents=True, exist_ok=True)
            fp = task_dir / "TASK-pr00003.md"
            fp.write_text("# [TASK-pr00003] Test task\n")
            create_task(task_id="pr00003", file_path=str(fp), role="implement")

            # First PR
            ImplementerRole._store_pr_in_db("pr00003", "https://github.com/owner/repo/pull/10")
            # Second PR (e.g. after force-push and new PR)
            ImplementerRole._store_pr_in_db("pr00003", "https://github.com/owner/repo/pull/20")

            with get_connection() as conn:
                row = conn.execute(
                    "SELECT pr_number, pr_url FROM tasks WHERE id = ?", ("pr00003",)
                ).fetchone()
                assert row["pr_number"] == 20
                assert row["pr_url"] == "https://github.com/owner/repo/pull/20"
