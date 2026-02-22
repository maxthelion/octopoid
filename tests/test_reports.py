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
    _gather_drafts,
    _gather_flows,
    _gather_health,
    _gather_messages,
    _gather_proposals,
    _gather_work,
    _get_agent_notes,
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
        mock_done,
        mock_work,
    ):
        mock_work.return_value = {"incoming": [], "in_progress": [], "in_review": [], "done_today": []}
        mock_done.return_value = []
        mock_proposals.return_value = []
        mock_messages.return_value = []
        mock_agents.return_value = []
        mock_health.return_value = {"scheduler": "unknown", "idle_agents": 0, "queue_depth": 0}

        report = get_project_report(MagicMock())

        assert "work" in report
        assert "flows" in report
        assert "done_tasks" in report
        assert "prs" in report
        assert "proposals" in report
        assert "messages" in report
        assert "agents" in report
        assert "health" in report
        assert "generated_at" in report

    @patch("orchestrator.reports._gather_work")
    @patch("orchestrator.reports._gather_done_tasks")
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
        mock_done,
        mock_work,
    ):
        mock_work.return_value = {"incoming": [], "in_progress": [], "in_review": [], "done_today": []}
        mock_done.return_value = []
        mock_proposals.return_value = []
        mock_messages.return_value = []
        mock_agents.return_value = []
        mock_health.return_value = {}

        report = get_project_report(MagicMock())

        dt = datetime.fromisoformat(report["generated_at"])
        assert isinstance(dt, datetime)

    @patch("orchestrator.reports._gather_work")
    @patch("orchestrator.reports._gather_done_tasks")
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
        mock_done,
        mock_work,
    ):
        mock_work.return_value = {"incoming": [], "in_progress": [], "in_review": [], "done_today": []}
        mock_done.return_value = []
        mock_proposals.return_value = []
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

    def test_includes_flow_and_queue_fields(self):
        task = {
            "id": "abc123",
            "title": "Test task",
            "queue": "claimed",
            "flow": "default",
        }

        result = _format_task(task)

        assert result["queue"] == "claimed"
        assert result["flow"] == "default"

    def test_flow_defaults_to_default_when_missing(self):
        task = {"id": "abc123", "title": "Test task"}

        result = _format_task(task)

        assert result["flow"] == "default"
        assert result["queue"] is None

    def test_flow_defaults_to_default_when_none(self):
        task = {"id": "abc123", "title": "Test task", "flow": None}

        result = _format_task(task)

        assert result["flow"] == "default"


# ---------------------------------------------------------------------------
# _gather_flows
# ---------------------------------------------------------------------------


class TestGatherFlows:
    """Tests for _gather_flows()."""

    def test_returns_flows_with_parsed_states(self):
        sdk = MagicMock()
        sdk.flows.list.return_value = [
            {
                "name": "default",
                "states": ["incoming", "claimed", "provisional", "done"],
                "transitions": [],
            }
        ]

        result = _gather_flows(sdk)

        assert len(result) == 1
        assert result[0]["name"] == "default"
        assert result[0]["states"] == ["incoming", "claimed", "provisional", "done"]
        assert result[0]["transitions"] == []

    def test_parses_json_string_states(self):
        sdk = MagicMock()
        sdk.flows.list.return_value = [
            {
                "name": "default",
                "states": '["incoming","claimed","provisional","done"]',
                "transitions": "[]",
            }
        ]

        result = _gather_flows(sdk)

        assert len(result) == 1
        assert result[0]["states"] == ["incoming", "claimed", "provisional", "done"]
        assert result[0]["transitions"] == []

    def test_returns_multiple_flows(self):
        sdk = MagicMock()
        sdk.flows.list.return_value = [
            {"name": "default", "states": ["incoming", "claimed"], "transitions": []},
            {"name": "review", "states": ["incoming", "review", "done"], "transitions": []},
        ]

        result = _gather_flows(sdk)

        assert len(result) == 2
        names = [f["name"] for f in result]
        assert "default" in names
        assert "review" in names

    def test_returns_empty_list_on_exception(self):
        sdk = MagicMock()
        sdk.flows.list.side_effect = Exception("network error")

        result = _gather_flows(sdk)

        assert result == []

    def test_returns_empty_list_when_no_flows(self):
        sdk = MagicMock()
        sdk.flows.list.return_value = []

        result = _gather_flows(sdk)

        assert result == []

    def test_handles_invalid_json_string_states_gracefully(self):
        sdk = MagicMock()
        sdk.flows.list.return_value = [
            {"name": "broken", "states": "not-valid-json", "transitions": "[]"}
        ]

        result = _gather_flows(sdk)

        assert len(result) == 1
        assert result[0]["states"] == []


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
# Drafts
# ---------------------------------------------------------------------------


class TestGatherDrafts:
    """Tests for _gather_drafts() — verifies bulk action fetch and grouping."""

    def test_returns_drafts_with_actions_field(self):
        sdk = MagicMock()
        sdk.drafts.list.return_value = [
            {"id": "1", "title": "Draft A", "status": "idea", "file_path": "/tmp/a.md", "created_at": "2026-01-01"},
            {"id": "2", "title": "Draft B", "status": "active", "file_path": "/tmp/b.md", "created_at": "2026-01-02"},
        ]
        sdk.actions.list.return_value = [
            {"id": "act-1", "entity_id": "1", "entity_type": "draft", "action_type": "archive_draft", "label": "Archive", "status": "pending"},
            {"id": "act-2", "entity_id": "1", "entity_type": "draft", "action_type": "update_draft_status", "label": "Mark active", "status": "pending"},
        ]

        result = _gather_drafts(sdk)

        assert len(result) == 2
        draft_a = next(d for d in result if d["id"] == "1")
        assert len(draft_a["actions"]) == 2
        assert draft_a["actions"][0]["id"] == "act-1"
        assert draft_a["actions"][1]["id"] == "act-2"
        # Draft B has no server-side actions → defaults are injected
        draft_b = next(d for d in result if d["id"] == "2")
        assert len(draft_b["actions"]) == 3
        assert {a["action_type"] for a in draft_b["actions"]} == {
            "enqueue_draft", "process_draft", "archive_draft"
        }

    def test_fetches_actions_with_single_api_call(self):
        """Actions must be fetched in one bulk call, not one per draft."""
        sdk = MagicMock()
        sdk.drafts.list.return_value = [
            {"id": str(i), "title": f"Draft {i}", "status": "idea", "file_path": None, "created_at": None}
            for i in range(10)
        ]
        sdk.actions.list.return_value = []

        _gather_drafts(sdk)

        # actions.list must be called exactly once regardless of draft count
        sdk.actions.list.assert_called_once_with(entity_type="draft", status="pending")

    def test_actions_grouped_by_entity_id(self):
        sdk = MagicMock()
        sdk.drafts.list.return_value = [
            {"id": "10", "title": "Draft X", "status": "partial", "file_path": None, "created_at": None},
            {"id": "20", "title": "Draft Y", "status": "active", "file_path": None, "created_at": None},
        ]
        sdk.actions.list.return_value = [
            {"id": "a1", "entity_id": "20", "entity_type": "draft", "action_type": "archive_draft", "label": "Archive", "status": "pending"},
        ]

        result = _gather_drafts(sdk)

        draft_x = next(d for d in result if d["id"] == "10")
        draft_y = next(d for d in result if d["id"] == "20")
        # Draft X has no server-side actions → defaults injected
        assert len(draft_x["actions"]) == 3
        assert {a["action_type"] for a in draft_x["actions"]} == {
            "enqueue_draft", "process_draft", "archive_draft"
        }
        # Draft Y has a server-side action → shown as-is, no defaults
        assert len(draft_y["actions"]) == 1
        assert draft_y["actions"][0]["id"] == "a1"

    def test_returns_empty_list_when_drafts_api_fails(self):
        sdk = MagicMock()
        sdk.drafts.list.side_effect = Exception("network error")

        result = _gather_drafts(sdk)

        assert result == []

    def test_returns_drafts_without_actions_when_actions_api_fails(self):
        sdk = MagicMock()
        sdk.drafts.list.return_value = [
            {"id": "5", "title": "Draft Z", "status": "idea", "file_path": None, "created_at": None},
        ]
        sdk.actions.list.side_effect = Exception("actions unavailable")

        result = _gather_drafts(sdk)

        assert len(result) == 1
        assert result[0]["id"] == "5"
        # When the actions API fails, default actions are injected
        assert len(result[0]["actions"]) == 3
        assert {a["action_type"] for a in result[0]["actions"]} == {
            "enqueue_draft", "process_draft", "archive_draft"
        }

    def test_includes_expected_draft_fields(self):
        sdk = MagicMock()
        sdk.drafts.list.return_value = [
            {"id": "99", "title": "My Draft", "status": "complete", "author": "human", "file_path": "/path/to/draft.md", "created_at": "2026-02-01T12:00:00"},
        ]
        sdk.actions.list.return_value = []

        result = _gather_drafts(sdk)

        assert len(result) == 1
        d = result[0]
        assert d["id"] == "99"
        assert d["title"] == "My Draft"
        assert d["status"] == "complete"
        assert d["author"] == "human"
        assert d["file_path"] == "/path/to/draft.md"
        assert d["created_at"] == "2026-02-01T12:00:00"
        # No server-side actions → defaults injected
        assert len(d["actions"]) == 3
        assert {a["action_type"] for a in d["actions"]} == {
            "enqueue_draft", "process_draft", "archive_draft"
        }

    def test_includes_author_field_for_agent_drafts(self):
        sdk = MagicMock()
        sdk.drafts.list.return_value = [
            {"id": "10", "title": "Agent Draft", "status": "idea", "author": "agent", "file_path": None, "created_at": None},
            {"id": "11", "title": "User Draft", "status": "active", "author": "human", "file_path": None, "created_at": None},
            {"id": "12", "title": "No Author Draft", "status": "partial", "file_path": None, "created_at": None},
        ]
        sdk.actions.list.return_value = []

        result = _gather_drafts(sdk)

        assert len(result) == 3
        by_id = {d["id"]: d for d in result}
        assert by_id["10"]["author"] == "agent"
        assert by_id["11"]["author"] == "human"
        assert by_id["12"]["author"] is None


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

    def test_returns_messages_from_sdk(self):
        mock_sdk = MagicMock()
        mock_sdk.messages.list.return_value = [
            {"id": 1, "type": "worker_result", "from_actor": "agent", "to_actor": "human",
             "content": "Done", "created_at": "2026-02-22T10:00:00Z"},
            {"id": 2, "type": "action_proposal", "from_actor": "agent", "to_actor": "human",
             "content": "Proposal", "created_at": "2026-02-22T11:00:00Z"},
        ]

        messages = _gather_messages(mock_sdk)

        mock_sdk.messages.list.assert_called_once_with(to_actor="human")
        # Newest first
        assert len(messages) == 2
        assert messages[0]["id"] == 2
        assert messages[1]["id"] == 1

    def test_returns_empty_on_error(self):
        mock_sdk = MagicMock()
        mock_sdk.messages.list.side_effect = Exception("server error")
        messages = _gather_messages(mock_sdk)
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
    """Tests for _gather_agents() using the pool model."""

    @patch("orchestrator.config.get_notes_dir")
    @patch("orchestrator.config.get_agents")
    @patch("orchestrator.pool.get_active_task_ids", return_value=set())
    @patch("orchestrator.pool.count_running_instances", return_value=0)
    def test_returns_agent_entries_with_pool_fields(
        self, mock_count, mock_tasks, mock_agents, mock_notes_dir, tmp_path
    ):
        mock_agents.return_value = [
            {
                "name": "implementer",
                "blueprint_name": "implementer",
                "role": "implementer",
                "paused": False,
                "max_instances": 3,
            },
        ]
        mock_notes_dir.return_value = tmp_path / "notes"
        (tmp_path / "notes").mkdir()

        agents = _gather_agents()

        assert len(agents) == 1
        agent = agents[0]
        assert agent["name"] == "implementer"
        assert agent["blueprint_name"] == "implementer"
        assert agent["role"] == "implementer"
        assert agent["status"] == "idle"
        assert agent["paused"] is False
        assert agent["max_instances"] == 3
        assert agent["running_instances"] == 0
        assert agent["idle_capacity"] == 3
        assert agent["current_tasks"] == []
        assert agent["notes"] is None

    @patch("orchestrator.config.get_notes_dir")
    @patch("orchestrator.config.get_agents")
    @patch("orchestrator.pool.get_active_task_ids", return_value=set())
    @patch("orchestrator.pool.count_running_instances", return_value=0)
    def test_paused_agent_shows_paused_status(
        self, mock_count, mock_tasks, mock_agents, mock_notes_dir, tmp_path
    ):
        mock_agents.return_value = [
            {
                "name": "implementer",
                "blueprint_name": "implementer",
                "role": "implementer",
                "paused": True,
                "max_instances": 1,
            },
        ]
        mock_notes_dir.return_value = tmp_path / "notes"
        (tmp_path / "notes").mkdir()

        agents = _gather_agents()
        assert agents[0]["status"] == "paused"

    @patch("orchestrator.config.get_notes_dir")
    @patch("orchestrator.config.get_agents")
    @patch("orchestrator.pool.get_active_task_ids", return_value={"TASK-abc", "TASK-def"})
    @patch("orchestrator.pool.count_running_instances", return_value=2)
    def test_running_instances_shown_in_status(
        self, mock_count, mock_tasks, mock_agents, mock_notes_dir, tmp_path
    ):
        mock_agents.return_value = [
            {
                "name": "implementer",
                "blueprint_name": "implementer",
                "role": "implementer",
                "paused": False,
                "max_instances": 3,
            },
        ]
        mock_notes_dir.return_value = tmp_path / "notes"
        (tmp_path / "notes").mkdir()

        agents = _gather_agents()
        assert agents[0]["status"] == "running"
        assert agents[0]["running_instances"] == 2
        assert agents[0]["idle_capacity"] == 1
        assert set(agents[0]["current_tasks"]) == {"TASK-abc", "TASK-def"}

    @patch("orchestrator.config.get_notes_dir")
    @patch("orchestrator.config.get_agents")
    @patch("orchestrator.pool.get_active_task_ids", return_value=set())
    @patch("orchestrator.pool.count_running_instances", return_value=0)
    def test_idle_capacity_is_max_when_no_instances_running(
        self, mock_count, mock_tasks, mock_agents, mock_notes_dir, tmp_path
    ):
        mock_agents.return_value = [
            {
                "name": "gatekeeper",
                "blueprint_name": "gatekeeper",
                "role": "gatekeeper",
                "paused": False,
                "max_instances": 2,
            },
        ]
        mock_notes_dir.return_value = tmp_path / "notes"
        (tmp_path / "notes").mkdir()

        agents = _gather_agents()
        assert agents[0]["idle_capacity"] == 2
        assert agents[0]["running_instances"] == 0


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestGatherHealth:
    """Tests for _gather_health()."""

    @patch("orchestrator.reports._get_scheduler_status", return_value="running")
    @patch("orchestrator.queue_utils.count_queue")
    @patch("orchestrator.config.is_system_paused", return_value=False)
    @patch("orchestrator.config.get_agents")
    @patch("orchestrator.pool.count_running_instances")
    def test_returns_health_fields(
        self, mock_count_running, mock_agents, mock_paused, mock_count, mock_sched
    ):
        mock_agents.return_value = [
            {"name": "implementer", "blueprint_name": "implementer", "paused": False, "max_instances": 2},
            {"name": "gatekeeper", "blueprint_name": "gatekeeper", "paused": True, "max_instances": 1},
        ]
        # implementer has 1 of 2 running → 1 running, 1 idle
        mock_count_running.return_value = 1

        # count_queue returns 3 for incoming, 1 for claimed, 0 for breakdown
        mock_count.side_effect = [3, 1, 0]

        health = _gather_health()

        assert health["scheduler"] == "running"
        assert health["system_paused"] is False
        assert health["running_agents"] == 1
        assert health["idle_agents"] == 1
        assert health["paused_agents"] == 1
        assert health["total_agents"] == 2
        assert health["queue_depth"] == 4

    @patch("orchestrator.reports._get_scheduler_status", return_value="not_loaded")
    @patch("orchestrator.queue_utils.count_queue", return_value=0)
    @patch("orchestrator.config.is_system_paused", return_value=True)
    @patch("orchestrator.config.get_agents", return_value=[])
    @patch("orchestrator.pool.count_running_instances", return_value=0)
    def test_handles_empty_agents(
        self, mock_count_running, mock_agents, mock_paused, mock_count, mock_sched
    ):
        health = _gather_health()

        assert health["idle_agents"] == 0
        assert health["running_agents"] == 0
        assert health["paused_agents"] == 0
        assert health["total_agents"] == 0
        assert health["system_paused"] is True


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
