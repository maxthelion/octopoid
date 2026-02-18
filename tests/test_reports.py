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
    @patch("orchestrator.reports._gather_messages")
    @patch("orchestrator.reports._gather_agents")
    @patch("orchestrator.reports._gather_health")
    def test_report_has_all_top_level_keys(
        self,
        mock_health,
        mock_agents,
        mock_messages,
        mock_prs,
        mock_done,
        mock_work,
    ):
        mock_work.return_value = {"incoming": [], "in_progress": [], "in_review": [], "done_today": []}
        mock_done.return_value = []
        mock_prs.return_value = []
        mock_messages.return_value = []
        mock_agents.return_value = []
        mock_health.return_value = {"scheduler": "unknown", "idle_agents": 0, "queue_depth": 0}

        report = get_project_report(MagicMock())

        assert "work" in report
        assert "done_tasks" in report
        assert "prs" in report
        assert "messages" in report
        assert "agents" in report
        assert "health" in report
        assert "generated_at" in report

    @patch("orchestrator.reports._gather_work")
    @patch("orchestrator.reports._gather_done_tasks")
    @patch("orchestrator.reports._gather_prs")
    @patch("orchestrator.reports._gather_messages")
    @patch("orchestrator.reports._gather_agents")
    @patch("orchestrator.reports._gather_health")
    def test_generated_at_is_iso_timestamp(
        self,
        mock_health,
        mock_agents,
        mock_messages,
        mock_prs,
        mock_done,
        mock_work,
    ):
        mock_work.return_value = {"incoming": [], "in_progress": [], "in_review": [], "done_today": []}
        mock_done.return_value = []
        mock_prs.return_value = []
        mock_messages.return_value = []
        mock_agents.return_value = []
        mock_health.return_value = {}

        report = get_project_report(MagicMock())

        dt = datetime.fromisoformat(report["generated_at"])
        assert isinstance(dt, datetime)

    @patch("orchestrator.reports._gather_work")
    @patch("orchestrator.reports._gather_done_tasks")
    @patch("orchestrator.reports._gather_prs")
    @patch("orchestrator.reports._gather_messages")
    @patch("orchestrator.reports._gather_agents")
    @patch("orchestrator.reports._gather_health")
    def test_work_has_expected_sub_keys(
        self,
        mock_health,
        mock_agents,
        mock_messages,
        mock_prs,
        mock_done,
        mock_work,
    ):
        mock_work.return_value = {"incoming": [], "in_progress": [], "in_review": [], "done_today": []}
        mock_done.return_value = []
        mock_prs.return_value = []
        mock_messages.return_value = []
        mock_agents.return_value = []
        mock_health.return_value = {}

        report = get_project_report(MagicMock())

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
        task = {"completed_at": datetime.now().isoformat()}
        cutoff = datetime.now() - timedelta(hours=24)
        assert _is_recent(task, cutoff) is True

    def test_old_task_returns_false(self):
        task = {"completed_at": (datetime.now() - timedelta(days=3)).isoformat()}
        cutoff = datetime.now() - timedelta(hours=24)
        assert _is_recent(task, cutoff) is False

    def test_missing_timestamp_returns_false(self):
        assert _is_recent({}, datetime.now()) is False

    def test_invalid_timestamp_returns_false(self):
        assert _is_recent({"completed_at": "not-a-date"}, datetime.now()) is False

    def test_handles_timezone_aware_timestamp(self):
        task = {"completed_at": "2026-02-07T10:00:00Z"}
        cutoff = datetime(2026, 2, 7, 9, 0, 0)
        assert _is_recent(task, cutoff) is True

    def test_uses_completed_at_over_updated_at(self):
        """completed_at should be preferred over updated_at."""
        task = {
            "completed_at": datetime.now().isoformat(),
            "updated_at": (datetime.now() - timedelta(days=10)).isoformat(),
        }
        cutoff = datetime.now() - timedelta(hours=24)
        assert _is_recent(task, cutoff) is True

    def test_falls_back_to_updated_at(self):
        """Should use updated_at when completed_at is missing."""
        task = {"updated_at": datetime.now().isoformat()}
        cutoff = datetime.now() - timedelta(hours=24)
        assert _is_recent(task, cutoff) is True

    def test_falls_back_to_created_at(self):
        """Should use created_at when completed_at and updated_at are missing."""
        task = {"created_at": datetime.now().isoformat()}
        cutoff = datetime.now() - timedelta(hours=24)
        assert _is_recent(task, cutoff) is True

    def test_old_created_recent_completed_returns_true(self):
        """Task created 30 days ago but completed today should be recent."""
        task = {
            "created_at": (datetime.now() - timedelta(days=30)).isoformat(),
            "completed_at": datetime.now().isoformat(),
        }
        cutoff = datetime.now() - timedelta(days=7)
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


class TestRecentTasksForAgent:
    """Tests for _get_recent_tasks_for_agent()."""

    def test_returns_empty_list(self):
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

    def test_noop_without_db(self):
        # Should not raise - DB mode is always off now
        _store_staging_url(55, "https://preview.pages.dev")


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
        mock_store.assert_called_once_with(55, "https://abc123.pages.dev", branch_name="agent/abc123", sdk=None)

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
# PR number storage tests removed — ImplementerRole._store_pr_in_db() no
# longer exists. Implementers now use scripts mode (scheduler.py).
# ---------------------------------------------------------------------------
