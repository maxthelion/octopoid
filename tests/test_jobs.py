"""Tests for octopoid/jobs.py and octopoid/job_conditions.py.

Coverage targets:
- jobs.py: >60% (from 24% baseline)
- job_conditions.py: >80% (from 0% baseline)

Key areas:
- register_job / JOB_REGISTRY
- load_jobs_yaml: missing file, empty file, valid data
- run_due_jobs: local jobs, remote jobs, poll batching
- _run_job: script type, agent type, unknown type, exception isolation
- _run_agent_job: capacity check, spawn success, spawn failure
- GitHub polling helpers: state I/O, gh CLI calls, task creation
- job_conditions: no_agents_running, has_open_prs
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# job_conditions.py
# ---------------------------------------------------------------------------


class TestRegisterCondition:
    """register_condition decorator populates CONDITION_REGISTRY."""

    def test_decorator_registers_function(self):
        from octopoid.job_conditions import CONDITION_REGISTRY, register_condition

        def _my_condition(ctx: dict) -> bool:
            return True

        # Register a fresh function to avoid polluting existing registry
        decorated = register_condition(_my_condition)
        assert "_my_condition" in CONDITION_REGISTRY
        assert CONDITION_REGISTRY["_my_condition"] is _my_condition
        assert decorated is _my_condition


class TestNoAgentsRunning:
    """no_agents_running condition."""

    def test_returns_true_when_no_agents_configured(self):
        from octopoid.job_conditions import no_agents_running

        with patch("octopoid.config.get_agents", return_value=[]):
            result = no_agents_running({})
        assert result is True

    def test_returns_true_when_no_instances_running(self):
        from octopoid.job_conditions import no_agents_running

        agents = [{"blueprint_name": "implementer"}]
        with (
            patch("octopoid.config.get_agents", return_value=agents),
            patch("octopoid.pool.count_running_instances", return_value=0),
        ):
            result = no_agents_running({})
        assert result is True

    def test_returns_false_when_instance_running(self):
        from octopoid.job_conditions import no_agents_running

        agents = [{"blueprint_name": "implementer"}]
        with (
            patch("octopoid.config.get_agents", return_value=agents),
            patch("octopoid.pool.count_running_instances", return_value=1),
        ):
            result = no_agents_running({})
        assert result is False

    def test_fails_open_on_config_error(self):
        from octopoid.job_conditions import no_agents_running

        with patch("octopoid.config.get_agents", side_effect=Exception("config error")):
            result = no_agents_running({})
        assert result is True  # Fail open

    def test_uses_name_field_as_fallback(self):
        """Uses 'name' when 'blueprint_name' is absent."""
        from octopoid.job_conditions import no_agents_running

        agents = [{"name": "reviewer"}]
        with (
            patch("octopoid.config.get_agents", return_value=agents),
            patch("octopoid.pool.count_running_instances", return_value=0) as mock_count,
        ):
            result = no_agents_running({})
        mock_count.assert_called_once_with("reviewer")
        assert result is True

    def test_skips_agents_with_no_blueprint_or_name(self):
        """Agent entry with no blueprint_name or name is skipped."""
        from octopoid.job_conditions import no_agents_running

        agents = [{}]
        with (
            patch("octopoid.config.get_agents", return_value=agents),
            patch("octopoid.pool.count_running_instances") as mock_count,
        ):
            result = no_agents_running({})
        mock_count.assert_not_called()
        assert result is True

    def test_multiple_agents_one_running(self):
        """Returns False as soon as any agent has running instances."""
        from octopoid.job_conditions import no_agents_running

        agents = [{"blueprint_name": "implementer"}, {"blueprint_name": "reviewer"}]

        def _count(bp: str) -> int:
            return 1 if bp == "implementer" else 0

        with (
            patch("octopoid.config.get_agents", return_value=agents),
            patch("octopoid.pool.count_running_instances", side_effect=_count),
        ):
            result = no_agents_running({})
        assert result is False


class TestHasOpenPrs:
    """has_open_prs condition."""

    def test_returns_true_when_prs_exist(self):
        from octopoid.job_conditions import has_open_prs

        mock_proc = MagicMock()
        mock_proc.stdout = '[{"number": 1}, {"number": 2}]'
        with patch("subprocess.run", return_value=mock_proc):
            result = has_open_prs({})
        assert result is True

    def test_returns_false_when_no_prs(self):
        from octopoid.job_conditions import has_open_prs

        mock_proc = MagicMock()
        mock_proc.stdout = "[]"
        with patch("subprocess.run", return_value=mock_proc):
            result = has_open_prs({})
        assert result is False

    def test_fails_open_on_exception(self):
        from octopoid.job_conditions import has_open_prs

        with patch("subprocess.run", side_effect=Exception("gh not found")):
            result = has_open_prs({})
        assert result is True

    def test_fails_open_on_empty_stdout(self):
        from octopoid.job_conditions import has_open_prs

        mock_proc = MagicMock()
        mock_proc.stdout = ""
        with patch("subprocess.run", return_value=mock_proc):
            result = has_open_prs({})
        assert result is False


# ---------------------------------------------------------------------------
# jobs.py — registry and YAML loading
# ---------------------------------------------------------------------------


class TestRegisterJob:
    """register_job decorator and JOB_REGISTRY."""

    def test_decorator_registers_function(self):
        from octopoid.jobs import JOB_REGISTRY, register_job

        def _test_register_job_func(ctx):  # type: ignore[override]
            pass

        register_job(_test_register_job_func)
        assert "_test_register_job_func" in JOB_REGISTRY
        assert JOB_REGISTRY["_test_register_job_func"] is _test_register_job_func

    def test_decorator_returns_original_function(self):
        from octopoid.jobs import register_job

        def _another_test_func(ctx):  # type: ignore[override]
            pass

        result = register_job(_another_test_func)
        assert result is _another_test_func

    def test_known_jobs_registered(self):
        """Built-in jobs should be registered at import time."""
        from octopoid.jobs import JOB_REGISTRY

        expected = {
            "check_and_update_finished_agents",
            "_register_orchestrator",
            "check_and_requeue_expired_leases",
            "check_project_completion",
            "_check_queue_health_throttled",
            "agent_evaluation_loop",
            "sweep_stale_resources",
            "send_heartbeat",
            "dispatch_action_messages",
            "poll_github_issues",
        }
        for name in expected:
            assert name in JOB_REGISTRY, f"Expected job '{name}' to be registered"


class TestGetJobsYamlPath:
    """get_jobs_yaml_path returns path relative to orchestrator dir."""

    def test_returns_path_in_orchestrator_dir(self, tmp_path):
        from octopoid.jobs import get_jobs_yaml_path

        with patch("octopoid.jobs.get_orchestrator_dir", return_value=tmp_path):
            result = get_jobs_yaml_path()
        assert result == tmp_path / "jobs.yaml"


class TestLoadJobsYaml:
    """load_jobs_yaml handles missing file, empty file, and valid data."""

    def test_returns_empty_list_when_file_missing(self, tmp_path):
        from octopoid.jobs import load_jobs_yaml

        with patch("octopoid.jobs.get_orchestrator_dir", return_value=tmp_path):
            result = load_jobs_yaml()
        assert result == []

    def test_returns_empty_list_for_empty_yaml(self, tmp_path):
        from octopoid.jobs import load_jobs_yaml

        (tmp_path / "jobs.yaml").write_text("")
        with patch("octopoid.jobs.get_orchestrator_dir", return_value=tmp_path):
            result = load_jobs_yaml()
        assert result == []

    def test_returns_empty_list_for_yaml_without_jobs_key(self, tmp_path):
        from octopoid.jobs import load_jobs_yaml

        (tmp_path / "jobs.yaml").write_text("other_key: value\n")
        with patch("octopoid.jobs.get_orchestrator_dir", return_value=tmp_path):
            result = load_jobs_yaml()
        assert result == []

    def test_returns_job_list(self, tmp_path):
        from octopoid.jobs import load_jobs_yaml

        yaml_content = """
jobs:
  - name: my_job
    interval: 60
    type: script
  - name: another_job
    interval: 300
    type: agent
"""
        (tmp_path / "jobs.yaml").write_text(yaml_content)
        with patch("octopoid.jobs.get_orchestrator_dir", return_value=tmp_path):
            result = load_jobs_yaml()
        assert len(result) == 2
        assert result[0]["name"] == "my_job"
        assert result[1]["name"] == "another_job"

    def test_returns_real_jobs_yaml(self):
        """The actual jobs.yaml in the project should load cleanly."""
        from octopoid.jobs import load_jobs_yaml

        jobs = load_jobs_yaml()
        assert isinstance(jobs, list)
        assert len(jobs) > 0
        for job in jobs:
            assert "name" in job
            assert "interval" in job


# ---------------------------------------------------------------------------
# jobs.py — JobContext
# ---------------------------------------------------------------------------


class TestJobContext:
    """JobContext dataclass."""

    def test_default_poll_data_is_none(self):
        from octopoid.jobs import JobContext

        ctx = JobContext(scheduler_state={})
        assert ctx.poll_data is None

    def test_stores_scheduler_state(self):
        from octopoid.jobs import JobContext

        state = {"jobs": {"some_job": "2026-01-01"}}
        ctx = JobContext(scheduler_state=state, poll_data={"queue_counts": {}})
        assert ctx.scheduler_state is state
        assert ctx.poll_data == {"queue_counts": {}}


# ---------------------------------------------------------------------------
# jobs.py — _run_job
# ---------------------------------------------------------------------------


class TestRunJob:
    """_run_job dispatches to script or agent, isolates exceptions."""

    def test_dispatches_script_type_to_registry(self):
        from octopoid.jobs import JOB_REGISTRY, JobContext, _run_job

        called = []

        def _my_script_job(ctx):
            called.append(ctx)

        JOB_REGISTRY["_my_script_job"] = _my_script_job
        try:
            ctx = JobContext(scheduler_state={})
            _run_job({"name": "_my_script_job", "type": "script"}, ctx)
            assert len(called) == 1
        finally:
            del JOB_REGISTRY["_my_script_job"]

    def test_skips_script_job_not_in_registry(self):
        """No error when job name not found in JOB_REGISTRY."""
        from octopoid.jobs import JobContext, _run_job

        ctx = JobContext(scheduler_state={})
        # Should not raise
        _run_job({"name": "nonexistent_job", "type": "script"}, ctx)

    def test_dispatches_agent_type(self):
        from octopoid.jobs import JobContext, _run_job

        ctx = JobContext(scheduler_state={})
        with patch("octopoid.jobs._run_agent_job") as mock_agent:
            _run_job({"name": "my_agent", "type": "agent"}, ctx)
        mock_agent.assert_called_once()

    def test_unknown_job_type_is_a_noop(self):
        """Unknown type is silently skipped (logged at DEBUG level)."""
        from octopoid.jobs import JobContext, _run_job

        ctx = JobContext(scheduler_state={})
        # Should not raise
        _run_job({"name": "job", "type": "webhook"}, ctx)

    def test_exception_in_script_is_isolated(self):
        """Exception in job function is caught — caller does not see it."""
        from octopoid.jobs import JOB_REGISTRY, JobContext, _run_job

        def _exploding_job(ctx):
            raise RuntimeError("boom")

        JOB_REGISTRY["_exploding_job"] = _exploding_job
        try:
            ctx = JobContext(scheduler_state={})
            _run_job({"name": "_exploding_job", "type": "script"}, ctx)
        finally:
            del JOB_REGISTRY["_exploding_job"]

    def test_default_type_is_script(self):
        """Job def without 'type' defaults to script dispatch."""
        from octopoid.jobs import JOB_REGISTRY, JobContext, _run_job

        called = []

        def _typed_default(ctx):
            called.append(True)

        JOB_REGISTRY["_typed_default"] = _typed_default
        try:
            ctx = JobContext(scheduler_state={})
            _run_job({"name": "_typed_default"}, ctx)  # no 'type' key
            assert called
        finally:
            del JOB_REGISTRY["_typed_default"]


# ---------------------------------------------------------------------------
# jobs.py — _run_agent_job
# ---------------------------------------------------------------------------


class TestRunAgentJob:
    """_run_agent_job spawns agents via pool strategy."""

    def _make_job_def(self, name: str = "my_agent_job", max_instances: int = 1) -> dict:
        return {
            "name": name,
            "blueprint": name,
            "type": "agent",
            "max_instances": max_instances,
            "interval": 60,
            "agent_config": {"role": "implement"},
        }

    def test_skips_when_at_capacity(self):
        from octopoid.jobs import JobContext, _run_agent_job

        ctx = JobContext(scheduler_state={})
        with patch("octopoid.jobs.count_running_instances", return_value=1):
            # max_instances=1 and 1 already running — should skip
            _run_agent_job(self._make_job_def(max_instances=1), ctx)
        # No further calls expected — test just verifies no exception

    def test_spawns_when_under_capacity(self):
        from octopoid.jobs import JobContext, _run_agent_job
        from octopoid.state_utils import AgentState

        ctx = JobContext(scheduler_state={})
        mock_strategy = MagicMock(return_value=12345)

        with (
            patch("octopoid.jobs.count_running_instances", return_value=0),
            patch("octopoid.scheduler.get_agent_state_path", return_value=Path("/tmp/state.json")),
            patch("octopoid.scheduler.load_state", return_value=AgentState()),
            patch("octopoid.scheduler.get_spawn_strategy", return_value=mock_strategy),
        ):
            _run_agent_job(self._make_job_def(), ctx)

        mock_strategy.assert_called_once()

    def test_spawn_failure_propagates(self):
        """Spawn failure should propagate (so _run_job can log 'FAILED')."""
        from octopoid.jobs import JobContext, _run_agent_job
        from octopoid.state_utils import AgentState

        ctx = JobContext(scheduler_state={})
        mock_strategy = MagicMock(side_effect=RuntimeError("spawn error"))

        with (
            patch("octopoid.jobs.count_running_instances", return_value=0),
            patch("octopoid.scheduler.get_agent_state_path", return_value=Path("/tmp/state.json")),
            patch("octopoid.scheduler.load_state", return_value=AgentState()),
            patch("octopoid.scheduler.get_spawn_strategy", return_value=mock_strategy),
        ):
            with pytest.raises(RuntimeError, match="spawn error"):
                _run_agent_job(self._make_job_def(), ctx)

    def test_defaults_blueprint_to_name(self):
        """When 'blueprint' key is absent, uses job name as blueprint."""
        from octopoid.jobs import JobContext, _run_agent_job
        from octopoid.state_utils import AgentState

        ctx = JobContext(scheduler_state={})
        captured_blueprint = []

        def _fake_count(bp: str) -> int:
            captured_blueprint.append(bp)
            return 0

        mock_strategy = MagicMock(return_value=99)
        job_def = {"name": "my_job", "type": "agent", "interval": 60}  # no 'blueprint' key

        with (
            patch("octopoid.jobs.count_running_instances", side_effect=_fake_count),
            patch("octopoid.scheduler.get_agent_state_path", return_value=Path("/tmp/state.json")),
            patch("octopoid.scheduler.load_state", return_value=AgentState()),
            patch("octopoid.scheduler.get_spawn_strategy", return_value=mock_strategy),
        ):
            _run_agent_job(job_def, ctx)

        assert captured_blueprint == ["my_job"]


# ---------------------------------------------------------------------------
# jobs.py — run_due_jobs
# ---------------------------------------------------------------------------


class TestRunDueJobs:
    """run_due_jobs dispatches local/remote jobs and batches poll calls."""

    def _make_state(self) -> dict:
        return {"jobs": {}}

    def test_no_jobs_returns_none(self):
        from octopoid.jobs import run_due_jobs

        with patch("octopoid.jobs.load_jobs_yaml", return_value=[]):
            result = run_due_jobs(self._make_state())
        assert result is None

    def test_local_job_runs_without_poll(self):
        from octopoid.jobs import run_due_jobs

        jobs = [{"name": "check_and_update_finished_agents", "interval": 10, "group": "local"}]

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=jobs),
            patch("octopoid.scheduler.is_job_due", return_value=True),
            patch("octopoid.scheduler.record_job_run"),
            patch("octopoid.jobs._run_job") as mock_run,
            patch("octopoid.scheduler._fetch_poll_data") as mock_poll,
        ):
            run_due_jobs(self._make_state())

        mock_run.assert_called_once()
        mock_poll.assert_not_called()

    def test_remote_job_triggers_poll(self):
        from octopoid.jobs import run_due_jobs

        jobs = [{"name": "agent_evaluation_loop", "interval": 60, "group": "remote"}]
        mock_poll_data = {"queue_counts": {"incoming": 5}}

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=jobs),
            patch("octopoid.scheduler.is_job_due", return_value=True),
            patch("octopoid.scheduler.record_job_run"),
            patch("octopoid.jobs._run_job") as mock_run,
            patch("octopoid.scheduler._fetch_poll_data", return_value=mock_poll_data) as mock_poll,
        ):
            result = run_due_jobs(self._make_state())

        mock_poll.assert_called_once()
        mock_run.assert_called_once()
        assert result == mock_poll_data

    def test_poll_called_once_for_multiple_remote_jobs(self):
        """Single poll() call regardless of how many remote jobs are due."""
        from octopoid.jobs import run_due_jobs

        jobs = [
            {"name": "agent_evaluation_loop", "interval": 60, "group": "remote"},
            {"name": "check_project_completion", "interval": 60, "group": "remote"},
        ]

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=jobs),
            patch("octopoid.scheduler.is_job_due", return_value=True),
            patch("octopoid.scheduler.record_job_run"),
            patch("octopoid.jobs._run_job"),
            patch("octopoid.scheduler._fetch_poll_data", return_value={}) as mock_poll,
        ):
            run_due_jobs(self._make_state())

        mock_poll.assert_called_once()

    def test_skipped_jobs_not_run(self):
        """Jobs where is_job_due returns False are not dispatched."""
        from octopoid.jobs import run_due_jobs

        jobs = [{"name": "send_heartbeat", "interval": 300, "group": "remote"}]

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=jobs),
            patch("octopoid.scheduler.is_job_due", return_value=False),
            patch("octopoid.scheduler.record_job_run") as mock_record,
            patch("octopoid.jobs._run_job") as mock_run,
            patch("octopoid.scheduler._fetch_poll_data") as mock_poll,
        ):
            result = run_due_jobs(self._make_state())

        mock_run.assert_not_called()
        mock_record.assert_not_called()
        mock_poll.assert_not_called()
        assert result is None

    def test_default_group_is_remote(self):
        """Jobs without 'group' key are treated as remote."""
        from octopoid.jobs import run_due_jobs

        jobs = [{"name": "send_heartbeat", "interval": 300}]  # no 'group' key

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=jobs),
            patch("octopoid.scheduler.is_job_due", return_value=True),
            patch("octopoid.scheduler.record_job_run"),
            patch("octopoid.jobs._run_job"),
            patch("octopoid.scheduler._fetch_poll_data", return_value={}) as mock_poll,
        ):
            run_due_jobs(self._make_state())

        mock_poll.assert_called_once()

    def test_record_job_run_called_for_each_due_job(self):
        from octopoid.jobs import run_due_jobs

        jobs = [
            {"name": "send_heartbeat", "interval": 300, "group": "local"},
            {"name": "_register_orchestrator", "interval": 60, "group": "local"},
        ]

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=jobs),
            patch("octopoid.scheduler.is_job_due", return_value=True),
            patch("octopoid.scheduler.record_job_run") as mock_record,
            patch("octopoid.jobs._run_job"),
        ):
            run_due_jobs(self._make_state())

        assert mock_record.call_count == 2


# ---------------------------------------------------------------------------
# jobs.py — GitHub issues state helpers
# ---------------------------------------------------------------------------


class TestGithubIssuesStateHelpers:
    """_load_github_issues_state and _save_github_issues_state."""

    def test_load_returns_empty_when_file_missing(self, tmp_path):
        from octopoid.jobs import _load_github_issues_state

        result = _load_github_issues_state(tmp_path / "nonexistent.json")
        assert result == {"processed_issues": []}

    def test_load_returns_data_from_file(self, tmp_path):
        from octopoid.jobs import _load_github_issues_state

        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"processed_issues": [1, 2, 3]}))
        result = _load_github_issues_state(state_file)
        assert result == {"processed_issues": [1, 2, 3]}

    def test_load_returns_empty_on_json_error(self, tmp_path):
        from octopoid.jobs import _load_github_issues_state

        state_file = tmp_path / "state.json"
        state_file.write_text("not json {{")
        result = _load_github_issues_state(state_file)
        assert result == {"processed_issues": []}

    def test_save_creates_file(self, tmp_path):
        from octopoid.jobs import _save_github_issues_state

        state_file = tmp_path / "subdir" / "state.json"
        _save_github_issues_state(state_file, {"processed_issues": [10, 20]})
        data = json.loads(state_file.read_text())
        assert data == {"processed_issues": [10, 20]}

    def test_save_load_roundtrip(self, tmp_path):
        from octopoid.jobs import _load_github_issues_state, _save_github_issues_state

        state_file = tmp_path / "state.json"
        original = {"processed_issues": [42, 99]}
        _save_github_issues_state(state_file, original)
        loaded = _load_github_issues_state(state_file)
        assert loaded == original


# ---------------------------------------------------------------------------
# jobs.py — _fetch_github_issues
# ---------------------------------------------------------------------------


class TestFetchGithubIssues:
    """_fetch_github_issues calls gh CLI and handles errors."""

    def test_returns_issues_on_success(self, tmp_path):
        from octopoid.jobs import _fetch_github_issues

        issues = [{"number": 1, "title": "Bug", "url": "http://x", "body": "", "labels": []}]
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(issues)

        with patch("subprocess.run", return_value=mock_proc):
            result = _fetch_github_issues(tmp_path)

        assert result == issues

    def test_returns_empty_on_nonzero_returncode(self, tmp_path):
        from octopoid.jobs import _fetch_github_issues

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "auth error"

        with patch("subprocess.run", return_value=mock_proc):
            result = _fetch_github_issues(tmp_path)
        assert result == []

    def test_returns_empty_on_timeout(self, tmp_path):
        from octopoid.jobs import _fetch_github_issues

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 30)):
            result = _fetch_github_issues(tmp_path)
        assert result == []

    def test_returns_empty_on_json_error(self, tmp_path):
        from octopoid.jobs import _fetch_github_issues

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "not json"

        with patch("subprocess.run", return_value=mock_proc):
            result = _fetch_github_issues(tmp_path)
        assert result == []

    def test_returns_empty_on_file_not_found(self, tmp_path):
        from octopoid.jobs import _fetch_github_issues

        with patch("subprocess.run", side_effect=FileNotFoundError("gh not found")):
            result = _fetch_github_issues(tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# jobs.py — _create_task_from_github_issue
# ---------------------------------------------------------------------------


class TestCreateTaskFromGithubIssue:
    """_create_task_from_github_issue maps labels to priority and calls create_task."""

    def _make_issue(self, number: int = 42, title: str = "My Issue", labels: list[str] | None = None) -> dict:
        return {
            "number": number,
            "title": title,
            "url": f"https://github.com/test/repo/issues/{number}",
            "body": "Issue description",
            "labels": [{"name": lbl} for lbl in (labels or [])],
        }

    def test_creates_task_with_p1_priority_by_default(self):
        from octopoid.jobs import _create_task_from_github_issue

        with patch("octopoid.tasks.create_task", return_value="task-abc") as mock_create:
            result = _create_task_from_github_issue(self._make_issue())
        assert result == "task-abc"
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["priority"] == "P1"

    def test_creates_task_with_p0_for_urgent_label(self):
        from octopoid.jobs import _create_task_from_github_issue

        with patch("octopoid.tasks.create_task", return_value="t") as mock_create:
            _create_task_from_github_issue(self._make_issue(labels=["urgent"]))
        assert mock_create.call_args.kwargs["priority"] == "P0"

    def test_creates_task_with_p0_for_critical_label(self):
        from octopoid.jobs import _create_task_from_github_issue

        with patch("octopoid.tasks.create_task", return_value="t") as mock_create:
            _create_task_from_github_issue(self._make_issue(labels=["critical"]))
        assert mock_create.call_args.kwargs["priority"] == "P0"

    def test_creates_task_with_p2_for_low_priority_label(self):
        from octopoid.jobs import _create_task_from_github_issue

        with patch("octopoid.tasks.create_task", return_value="t") as mock_create:
            _create_task_from_github_issue(self._make_issue(labels=["low-priority"]))
        assert mock_create.call_args.kwargs["priority"] == "P2"

    def test_title_includes_issue_number(self):
        from octopoid.jobs import _create_task_from_github_issue

        with patch("octopoid.tasks.create_task", return_value="t") as mock_create:
            _create_task_from_github_issue(self._make_issue(number=99, title="Test issue"))
        title = mock_create.call_args.kwargs["title"]
        assert "[GH-99]" in title
        assert "Test issue" in title

    def test_returns_none_on_create_task_failure(self):
        from octopoid.jobs import _create_task_from_github_issue

        with patch("octopoid.tasks.create_task", side_effect=Exception("server down")):
            result = _create_task_from_github_issue(self._make_issue())
        assert result is None

    def test_role_is_implement(self):
        from octopoid.jobs import _create_task_from_github_issue

        with patch("octopoid.tasks.create_task", return_value="t") as mock_create:
            _create_task_from_github_issue(self._make_issue())
        assert mock_create.call_args.kwargs["role"] == "implement"

    def test_body_included_in_context(self):
        from octopoid.jobs import _create_task_from_github_issue

        issue = self._make_issue()
        issue["body"] = "Detailed description here"
        with patch("octopoid.tasks.create_task", return_value="t") as mock_create:
            _create_task_from_github_issue(issue)
        context = mock_create.call_args.kwargs["context"]
        assert "Detailed description here" in context

    def test_labels_included_in_context(self):
        from octopoid.jobs import _create_task_from_github_issue

        with patch("octopoid.tasks.create_task", return_value="t") as mock_create:
            _create_task_from_github_issue(self._make_issue(labels=["bug", "help wanted"]))
        context = mock_create.call_args.kwargs["context"]
        assert "bug" in context
        assert "help wanted" in context

    def test_empty_body_uses_placeholder(self):
        from octopoid.jobs import _create_task_from_github_issue

        issue = self._make_issue()
        issue["body"] = None
        with patch("octopoid.tasks.create_task", return_value="t") as mock_create:
            _create_task_from_github_issue(issue)
        context = mock_create.call_args.kwargs["context"]
        assert "No description" in context


# ---------------------------------------------------------------------------
# jobs.py — _comment_on_github_issue
# ---------------------------------------------------------------------------


class TestCommentOnGithubIssue:
    """_comment_on_github_issue calls gh issue comment."""

    def test_calls_gh_issue_comment(self, tmp_path):
        from octopoid.jobs import _comment_on_github_issue

        with patch("subprocess.run") as mock_run:
            _comment_on_github_issue(42, "task-xyz", tmp_path)

        mock_run.assert_called_once()
        cmd = mock_run.call_args.args[0]
        assert "gh" in cmd
        assert "issue" in cmd
        assert "comment" in cmd
        assert "42" in cmd

    def test_silently_ignores_exceptions(self, tmp_path):
        from octopoid.jobs import _comment_on_github_issue

        with patch("subprocess.run", side_effect=Exception("network error")):
            _comment_on_github_issue(42, "task-xyz", tmp_path)
        # Should not raise


# ---------------------------------------------------------------------------
# jobs.py — _forward_github_issue_to_server
# ---------------------------------------------------------------------------


class TestForwardGithubIssueToServer:
    """_forward_github_issue_to_server creates issue on server repo."""

    def _make_issue(self) -> dict:
        return {
            "number": 10,
            "title": "Server feature request",
            "url": "https://github.com/test/repo/issues/10",
            "body": "Please add this feature",
            "labels": [{"name": "server"}],
        }

    def test_returns_true_on_success(self, tmp_path):
        from octopoid.jobs import _forward_github_issue_to_server

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "https://github.com/server/issues/5\n"

        with patch("subprocess.run", return_value=mock_proc):
            result = _forward_github_issue_to_server(self._make_issue(), tmp_path)
        assert result is True

    def test_returns_false_on_nonzero_returncode(self, tmp_path):
        from octopoid.jobs import _forward_github_issue_to_server

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "auth error"
        mock_proc.stdout = ""

        with patch("subprocess.run", return_value=mock_proc):
            result = _forward_github_issue_to_server(self._make_issue(), tmp_path)
        assert result is False

    def test_returns_false_on_exception(self, tmp_path):
        from octopoid.jobs import _forward_github_issue_to_server

        with patch("subprocess.run", side_effect=Exception("network")):
            result = _forward_github_issue_to_server(self._make_issue(), tmp_path)
        assert result is False

    def test_forwards_to_server_repo(self, tmp_path):
        from octopoid.jobs import _forward_github_issue_to_server

        calls = []

        def _fake_run(cmd, **kwargs):
            calls.append(cmd)
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = "https://github.com/server/issues/5\n"
            return proc

        with patch("subprocess.run", side_effect=_fake_run):
            _forward_github_issue_to_server(self._make_issue(), tmp_path)

        # First call should create the issue on server repo
        create_cmd = calls[0]
        assert "maxthelion/octopoid-server" in create_cmd


# ---------------------------------------------------------------------------
# jobs.py — poll_github_issues integration
# ---------------------------------------------------------------------------


class TestPollGithubIssues:
    """poll_github_issues end-to-end with mocked dependencies."""

    def _make_ctx(self) -> object:
        from octopoid.jobs import JobContext
        return JobContext(scheduler_state={})

    def test_does_nothing_when_no_issues(self, tmp_path):
        from octopoid.jobs import poll_github_issues

        ctx = self._make_ctx()
        with (
            patch("octopoid.config.get_orchestrator_dir", return_value=tmp_path),
            patch("octopoid.config.find_parent_project", return_value=tmp_path),
            patch("octopoid.jobs._fetch_github_issues", return_value=[]),
            patch("octopoid.tasks.create_task") as mock_create,
        ):
            poll_github_issues(ctx)
        mock_create.assert_not_called()

    def test_creates_task_for_new_issue(self, tmp_path):
        from octopoid.jobs import poll_github_issues

        (tmp_path / "runtime").mkdir(parents=True)
        ctx = self._make_ctx()
        issues = [{"number": 1, "title": "Bug", "url": "http://x", "body": "desc", "labels": []}]

        with (
            patch("octopoid.config.get_orchestrator_dir", return_value=tmp_path),
            patch("octopoid.config.find_parent_project", return_value=tmp_path),
            patch("octopoid.jobs._fetch_github_issues", return_value=issues),
            patch("octopoid.tasks.create_task", return_value="task-001"),
            patch("octopoid.jobs._comment_on_github_issue"),
        ):
            poll_github_issues(ctx)

        state_file = tmp_path / "runtime" / "github_issues_state.json"
        state = json.loads(state_file.read_text())
        assert 1 in state["processed_issues"]

    def test_skips_already_processed_issue(self, tmp_path):
        from octopoid.jobs import poll_github_issues

        runtime_dir = tmp_path / "runtime"
        runtime_dir.mkdir(parents=True)
        state_file = runtime_dir / "github_issues_state.json"
        state_file.write_text(json.dumps({"processed_issues": [1]}))

        ctx = self._make_ctx()
        issues = [{"number": 1, "title": "Old", "url": "http://x", "body": "desc", "labels": []}]

        with (
            patch("octopoid.config.get_orchestrator_dir", return_value=tmp_path),
            patch("octopoid.config.find_parent_project", return_value=tmp_path),
            patch("octopoid.jobs._fetch_github_issues", return_value=issues),
            patch("octopoid.tasks.create_task") as mock_create,
        ):
            poll_github_issues(ctx)
        mock_create.assert_not_called()

    def test_forwards_server_labelled_issue(self, tmp_path):
        from octopoid.jobs import poll_github_issues

        (tmp_path / "runtime").mkdir(parents=True)
        ctx = self._make_ctx()
        issues = [
            {"number": 2, "title": "Server feature", "url": "http://x", "body": "desc",
             "labels": [{"name": "server"}]}
        ]

        with (
            patch("octopoid.config.get_orchestrator_dir", return_value=tmp_path),
            patch("octopoid.config.find_parent_project", return_value=tmp_path),
            patch("octopoid.jobs._fetch_github_issues", return_value=issues),
            patch("octopoid.jobs._forward_github_issue_to_server", return_value=True) as mock_fwd,
            patch("octopoid.tasks.create_task") as mock_create,
        ):
            poll_github_issues(ctx)

        mock_fwd.assert_called_once()
        mock_create.assert_not_called()

        state_file = tmp_path / "runtime" / "github_issues_state.json"
        state = json.loads(state_file.read_text())
        assert 2 in state["processed_issues"]


# ---------------------------------------------------------------------------
# jobs.py — registered @register_job handlers
# ---------------------------------------------------------------------------


class TestRegisteredJobHandlers:
    """Thin wrappers registered with @register_job forward to scheduler impls."""

    def test_check_and_update_finished_agents_delegates(self):
        from octopoid.jobs import JOB_REGISTRY, JobContext

        ctx = JobContext(scheduler_state={})
        with patch("octopoid.scheduler.check_and_update_finished_agents") as mock_impl:
            JOB_REGISTRY["check_and_update_finished_agents"](ctx)
        mock_impl.assert_called_once_with()

    def test_register_orchestrator_passes_poll_data_flag(self):
        from octopoid.jobs import JOB_REGISTRY, JobContext

        ctx = JobContext(scheduler_state={}, poll_data={"orchestrator_registered": True})
        with patch("octopoid.scheduler._register_orchestrator") as mock_impl:
            JOB_REGISTRY["_register_orchestrator"](ctx)
        mock_impl.assert_called_once_with(orchestrator_registered=True)

    def test_register_orchestrator_defaults_to_false_when_no_poll_data(self):
        from octopoid.jobs import JOB_REGISTRY, JobContext

        ctx = JobContext(scheduler_state={}, poll_data=None)
        with patch("octopoid.scheduler._register_orchestrator") as mock_impl:
            JOB_REGISTRY["_register_orchestrator"](ctx)
        mock_impl.assert_called_once_with(orchestrator_registered=False)

    def test_check_and_requeue_expired_leases_delegates(self):
        from octopoid.jobs import JOB_REGISTRY, JobContext

        ctx = JobContext(scheduler_state={})
        with patch("octopoid.scheduler.check_and_requeue_expired_leases") as mock_impl:
            JOB_REGISTRY["check_and_requeue_expired_leases"](ctx)
        mock_impl.assert_called_once_with()

    def test_check_project_completion_delegates(self):
        from octopoid.jobs import JOB_REGISTRY, JobContext

        ctx = JobContext(scheduler_state={})
        with patch("octopoid.scheduler.check_project_completion") as mock_impl:
            JOB_REGISTRY["check_project_completion"](ctx)
        mock_impl.assert_called_once_with()

    def test_agent_evaluation_loop_passes_queue_counts(self):
        from octopoid.jobs import JOB_REGISTRY, JobContext

        counts = {"incoming": 2, "claimed": 0}
        ctx = JobContext(scheduler_state={}, poll_data={"queue_counts": counts})
        with patch("octopoid.scheduler._run_agent_evaluation_loop") as mock_impl:
            JOB_REGISTRY["agent_evaluation_loop"](ctx)
        mock_impl.assert_called_once_with(queue_counts=counts)

    def test_agent_evaluation_loop_passes_none_queue_counts_when_no_poll(self):
        from octopoid.jobs import JOB_REGISTRY, JobContext

        ctx = JobContext(scheduler_state={}, poll_data=None)
        with patch("octopoid.scheduler._run_agent_evaluation_loop") as mock_impl:
            JOB_REGISTRY["agent_evaluation_loop"](ctx)
        mock_impl.assert_called_once_with(queue_counts=None)

    def test_send_heartbeat_delegates(self):
        from octopoid.jobs import JOB_REGISTRY, JobContext

        ctx = JobContext(scheduler_state={})
        with patch("octopoid.scheduler.send_heartbeat") as mock_impl:
            JOB_REGISTRY["send_heartbeat"](ctx)
        mock_impl.assert_called_once_with()

    def test_dispatch_action_messages_delegates(self):
        from octopoid.jobs import JOB_REGISTRY, JobContext

        ctx = JobContext(scheduler_state={})
        with patch("octopoid.message_dispatcher.dispatch_action_messages") as mock_impl:
            JOB_REGISTRY["dispatch_action_messages"](ctx)
        mock_impl.assert_called_once_with()
