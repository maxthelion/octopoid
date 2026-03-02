"""Unit tests for octopoid/jobs.py.

Covers:
- load_jobs_yaml() — file exists/missing/empty
- run_due_jobs() — local/remote job dispatch, poll batching
- _run_job() — script/agent/unknown types, error isolation
- _run_agent_job() — capacity check, spawn, failure
- Job handler delegates (check_and_update_finished_agents, _register_orchestrator, etc.)
- _load_github_issues_state() — exists/missing/invalid JSON
- _save_github_issues_state() — success/OSError
- _fetch_github_issues() — success/failure/timeout/JSON error
- _create_task_from_github_issue() — priority mapping, success, failure
- _comment_on_github_issue() — success, exception
- _forward_github_issue_to_server() — success, failure, exception
- poll_github_issues() — no issues, new issues, already processed, server-labelled
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from octopoid.jobs import (
    JOB_REGISTRY,
    JobContext,
    _create_task_from_github_issue,
    _fetch_github_issues,
    _forward_github_issue_to_server,
    _load_github_issues_state,
    _run_agent_job,
    _run_job,
    _save_github_issues_state,
    load_jobs_yaml,
    register_job,
    run_due_jobs,
)


# =============================================================================
# load_jobs_yaml
# =============================================================================


class TestLoadJobsYaml:
    def test_returns_empty_list_when_file_missing(self, tmp_path):
        with patch("octopoid.jobs.get_orchestrator_dir", return_value=tmp_path):
            result = load_jobs_yaml()
        assert result == []

    def test_returns_jobs_from_file(self, tmp_path):
        jobs_yaml = tmp_path / "jobs.yaml"
        jobs_yaml.write_text(
            "jobs:\n  - name: my_job\n    interval: 60\n    type: script\n"
        )
        with patch("octopoid.jobs.get_orchestrator_dir", return_value=tmp_path):
            result = load_jobs_yaml()
        assert len(result) == 1
        assert result[0]["name"] == "my_job"
        assert result[0]["interval"] == 60

    def test_returns_empty_list_for_empty_yaml(self, tmp_path):
        jobs_yaml = tmp_path / "jobs.yaml"
        jobs_yaml.write_text("")
        with patch("octopoid.jobs.get_orchestrator_dir", return_value=tmp_path):
            result = load_jobs_yaml()
        assert result == []

    def test_returns_empty_list_when_jobs_key_missing(self, tmp_path):
        jobs_yaml = tmp_path / "jobs.yaml"
        jobs_yaml.write_text("other_key: value\n")
        with patch("octopoid.jobs.get_orchestrator_dir", return_value=tmp_path):
            result = load_jobs_yaml()
        assert result == []

    def test_returns_multiple_jobs(self, tmp_path):
        jobs_yaml = tmp_path / "jobs.yaml"
        jobs_yaml.write_text(
            "jobs:\n"
            "  - name: job_a\n    interval: 30\n"
            "  - name: job_b\n    interval: 120\n"
        )
        with patch("octopoid.jobs.get_orchestrator_dir", return_value=tmp_path):
            result = load_jobs_yaml()
        assert len(result) == 2
        names = [j["name"] for j in result]
        assert "job_a" in names
        assert "job_b" in names


# =============================================================================
# register_job
# =============================================================================


class TestRegisterJob:
    def test_register_job_adds_to_registry(self):
        original_registry = dict(JOB_REGISTRY)
        try:
            def _test_register_fn(ctx):
                pass

            result = register_job(_test_register_fn)
            assert "_test_register_fn" in JOB_REGISTRY
            assert JOB_REGISTRY["_test_register_fn"] is _test_register_fn
            assert result is _test_register_fn
        finally:
            # Clean up test entry
            JOB_REGISTRY.pop("_test_register_fn", None)


# =============================================================================
# run_due_jobs
# =============================================================================


class TestRunDueJobs:
    def _make_job_def(self, name, interval=60, job_type="script", group="remote"):
        return {"name": name, "interval": interval, "type": job_type, "group": group}

    def test_no_jobs_returns_none(self):
        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=[]),
            patch("octopoid.scheduler.is_job_due", return_value=False),
        ):
            result = run_due_jobs({})
        assert result is None

    def test_local_job_runs_without_poll(self):
        job_def = self._make_job_def("my_local_job", group="local")
        scheduler_state = {"jobs": {}}

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=[job_def]),
            patch("octopoid.scheduler.is_job_due", return_value=True),
            patch("octopoid.scheduler.record_job_run") as mock_record,
            patch("octopoid.scheduler._fetch_poll_data") as mock_poll,
            patch("octopoid.jobs._run_job") as mock_run,
        ):
            result = run_due_jobs(scheduler_state)

        # local job runs but no poll data
        mock_run.assert_called_once()
        mock_poll.assert_not_called()
        mock_record.assert_called_once()
        assert result is None

    def test_remote_job_fetches_poll_data(self):
        job_def = self._make_job_def("my_remote_job", group="remote")
        scheduler_state = {"jobs": {}}
        poll_data = {"queue_counts": {"incoming": 3}}

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=[job_def]),
            patch("octopoid.scheduler.is_job_due", return_value=True),
            patch("octopoid.scheduler.record_job_run"),
            patch("octopoid.scheduler._fetch_poll_data", return_value=poll_data),
            patch("octopoid.jobs._run_job") as mock_run,
        ):
            result = run_due_jobs(scheduler_state)

        # remote job runs with poll data
        mock_run.assert_called_once()
        ctx_arg = mock_run.call_args[0][1]
        assert ctx_arg.poll_data == poll_data
        assert result == poll_data

    def test_job_not_due_is_skipped(self):
        job_def = self._make_job_def("slow_job", interval=3600)
        scheduler_state = {"jobs": {}}

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=[job_def]),
            patch("octopoid.scheduler.is_job_due", return_value=False),
            patch("octopoid.scheduler._fetch_poll_data") as mock_poll,
            patch("octopoid.jobs._run_job") as mock_run,
        ):
            result = run_due_jobs(scheduler_state)

        mock_run.assert_not_called()
        mock_poll.assert_not_called()
        assert result is None

    def test_poll_called_once_for_multiple_remote_jobs(self):
        job_a = self._make_job_def("job_a", group="remote")
        job_b = self._make_job_def("job_b", group="remote")
        poll_data = {"queue_counts": {}}

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=[job_a, job_b]),
            patch("octopoid.scheduler.is_job_due", return_value=True),
            patch("octopoid.scheduler.record_job_run"),
            patch("octopoid.scheduler._fetch_poll_data", return_value=poll_data) as mock_poll,
            patch("octopoid.jobs._run_job"),
        ):
            run_due_jobs({})

        # Poll fetched exactly once, shared across both remote jobs
        mock_poll.assert_called_once()

    def test_local_job_context_has_no_poll_data(self):
        job_def = self._make_job_def("local_job", group="local")

        captured_ctx = []

        def capture_ctx(job_def, ctx):
            captured_ctx.append(ctx)

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=[job_def]),
            patch("octopoid.scheduler.is_job_due", return_value=True),
            patch("octopoid.scheduler.record_job_run"),
            patch("octopoid.jobs._run_job", side_effect=capture_ctx),
        ):
            run_due_jobs({})

        assert len(captured_ctx) == 1
        assert captured_ctx[0].poll_data is None

    def test_remote_job_context_has_poll_data(self):
        job_def = self._make_job_def("remote_job", group="remote")
        poll_data = {"orchestrator_registered": True}
        captured_ctx = []

        def capture_ctx(job_def, ctx):
            captured_ctx.append(ctx)

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=[job_def]),
            patch("octopoid.scheduler.is_job_due", return_value=True),
            patch("octopoid.scheduler.record_job_run"),
            patch("octopoid.scheduler._fetch_poll_data", return_value=poll_data),
            patch("octopoid.jobs._run_job", side_effect=capture_ctx),
        ):
            run_due_jobs({})

        assert len(captured_ctx) == 1
        assert captured_ctx[0].poll_data == poll_data

    def test_no_remote_jobs_due_returns_none(self):
        local_job = self._make_job_def("local_only", group="local")

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=[local_job]),
            patch("octopoid.scheduler.is_job_due", return_value=True),
            patch("octopoid.scheduler.record_job_run"),
            patch("octopoid.jobs._run_job"),
        ):
            result = run_due_jobs({})

        assert result is None

    def test_poll_none_returned_when_fetch_fails(self):
        job_def = self._make_job_def("remote_job", group="remote")

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=[job_def]),
            patch("octopoid.scheduler.is_job_due", return_value=True),
            patch("octopoid.scheduler.record_job_run"),
            patch("octopoid.scheduler._fetch_poll_data", return_value=None),
            patch("octopoid.jobs._run_job"),
        ):
            result = run_due_jobs({})

        assert result is None


# =============================================================================
# _run_job
# =============================================================================


class TestRunJob:
    def test_script_type_calls_registered_function(self):
        called_with = []

        def my_func(ctx):
            called_with.append(ctx)

        job_def = {"name": "test_script_job", "type": "script"}
        ctx = JobContext(scheduler_state={})

        original = JOB_REGISTRY.get("test_script_job")
        try:
            JOB_REGISTRY["test_script_job"] = my_func
            _run_job(job_def, ctx)
        finally:
            if original is None:
                JOB_REGISTRY.pop("test_script_job", None)
            else:
                JOB_REGISTRY["test_script_job"] = original

        assert len(called_with) == 1
        assert called_with[0] is ctx

    def test_script_type_unregistered_does_nothing(self):
        job_def = {"name": "unregistered_job_xyz", "type": "script"}
        ctx = JobContext(scheduler_state={})
        # Should not raise, just log debug
        _run_job(job_def, ctx)

    def test_agent_type_calls_run_agent_job(self):
        job_def = {"name": "my_agent_job", "type": "agent"}
        ctx = JobContext(scheduler_state={})

        with patch("octopoid.jobs._run_agent_job") as mock_agent:
            _run_job(job_def, ctx)

        mock_agent.assert_called_once_with(job_def, ctx)

    def test_unknown_type_does_nothing(self):
        job_def = {"name": "weird_job", "type": "unknown_type"}
        ctx = JobContext(scheduler_state={})
        # Should not raise
        _run_job(job_def, ctx)

    def test_exception_is_caught_not_propagated(self):
        def failing_func(ctx):
            raise RuntimeError("boom")

        job_def = {"name": "failing_job_abc", "type": "script"}
        ctx = JobContext(scheduler_state={})

        original = JOB_REGISTRY.get("failing_job_abc")
        try:
            JOB_REGISTRY["failing_job_abc"] = failing_func
            # Should not raise — error isolation
            _run_job(job_def, ctx)
        finally:
            if original is None:
                JOB_REGISTRY.pop("failing_job_abc", None)
            else:
                JOB_REGISTRY["failing_job_abc"] = original

    def test_default_type_is_script(self):
        """Job with no type defaults to script lookup."""
        called = []

        def my_func(ctx):
            called.append(True)

        job_def = {"name": "implicit_script_job"}  # no "type" key
        ctx = JobContext(scheduler_state={})

        original = JOB_REGISTRY.get("implicit_script_job")
        try:
            JOB_REGISTRY["implicit_script_job"] = my_func
            _run_job(job_def, ctx)
        finally:
            if original is None:
                JOB_REGISTRY.pop("implicit_script_job", None)
            else:
                JOB_REGISTRY["implicit_script_job"] = original

        assert called == [True]


# =============================================================================
# _run_agent_job
# =============================================================================


class TestRunAgentJob:
    def _make_job_def(self, name="my_agent", max_instances=1, blueprint=None):
        job = {
            "name": name,
            "type": "agent",
            "interval": 60,
            "max_instances": max_instances,
            "agent_config": {"role": "implement"},
        }
        if blueprint:
            job["blueprint"] = blueprint
        return job

    def test_skips_when_at_capacity(self):
        job_def = self._make_job_def(max_instances=1)
        ctx = JobContext(scheduler_state={})

        with (
            patch("octopoid.jobs.count_running_instances", return_value=1),
            patch("octopoid.scheduler.get_agent_state_path"),
            patch("octopoid.scheduler.load_state"),
            patch("octopoid.scheduler.get_spawn_strategy") as mock_strategy,
        ):
            _run_agent_job(job_def, ctx)

        mock_strategy.assert_not_called()

    def test_spawns_when_under_capacity(self):
        job_def = self._make_job_def(max_instances=2)
        ctx = JobContext(scheduler_state={})

        mock_strategy_fn = MagicMock(return_value=12345)
        mock_state_path = MagicMock()
        mock_state = MagicMock()

        with (
            patch("octopoid.jobs.count_running_instances", return_value=0),
            patch("octopoid.scheduler.get_agent_state_path", return_value=mock_state_path),
            patch("octopoid.scheduler.load_state", return_value=mock_state),
            patch("octopoid.scheduler.get_spawn_strategy", return_value=mock_strategy_fn),
        ):
            _run_agent_job(job_def, ctx)

        mock_strategy_fn.assert_called_once()

    def test_uses_blueprint_from_job_def(self):
        job_def = self._make_job_def(name="my_agent", blueprint="custom_blueprint")
        ctx = JobContext(scheduler_state={})

        mock_strategy_fn = MagicMock(return_value=99)
        mock_agent_ctx_list = []

        def capture_strategy(agent_ctx):
            mock_agent_ctx_list.append(agent_ctx)
            return mock_strategy_fn

        with (
            patch("octopoid.jobs.count_running_instances", return_value=0),
            patch("octopoid.scheduler.get_agent_state_path", return_value=MagicMock()),
            patch("octopoid.scheduler.load_state", return_value=MagicMock()),
            patch("octopoid.scheduler.get_spawn_strategy", side_effect=capture_strategy),
        ):
            _run_agent_job(job_def, ctx)

        assert len(mock_agent_ctx_list) == 1
        assert mock_agent_ctx_list[0].agent_config["blueprint_name"] == "custom_blueprint"

    def test_spawn_failure_propagates(self):
        """Spawn failure propagates so _run_job() logs 'FAILED' instead of 'completed OK'."""
        job_def = self._make_job_def()
        ctx = JobContext(scheduler_state={})

        mock_strategy_fn = MagicMock(side_effect=RuntimeError("spawn error"))

        with (
            patch("octopoid.jobs.count_running_instances", return_value=0),
            patch("octopoid.scheduler.get_agent_state_path", return_value=MagicMock()),
            patch("octopoid.scheduler.load_state", return_value=MagicMock()),
            patch("octopoid.scheduler.get_spawn_strategy", return_value=mock_strategy_fn),
        ):
            with pytest.raises(RuntimeError, match="spawn error"):
                _run_agent_job(job_def, ctx)

    def test_uses_job_name_as_blueprint_when_no_blueprint_key(self):
        job_def = self._make_job_def(name="analyst_agent")  # no blueprint key
        ctx = JobContext(scheduler_state={})
        captured = []

        def capture_strategy(agent_ctx):
            captured.append(agent_ctx)
            return MagicMock(return_value=42)

        with (
            patch("octopoid.jobs.count_running_instances", return_value=0),
            patch("octopoid.scheduler.get_agent_state_path", return_value=MagicMock()),
            patch("octopoid.scheduler.load_state", return_value=MagicMock()),
            patch("octopoid.scheduler.get_spawn_strategy", side_effect=capture_strategy),
        ):
            _run_agent_job(job_def, ctx)

        assert captured[0].agent_config["blueprint_name"] == "analyst_agent"

    def test_lightweight_defaults_to_true(self):
        job_def = self._make_job_def()
        ctx = JobContext(scheduler_state={})
        captured = []

        def capture_strategy(agent_ctx):
            captured.append(agent_ctx)
            return MagicMock(return_value=1)

        with (
            patch("octopoid.jobs.count_running_instances", return_value=0),
            patch("octopoid.scheduler.get_agent_state_path", return_value=MagicMock()),
            patch("octopoid.scheduler.load_state", return_value=MagicMock()),
            patch("octopoid.scheduler.get_spawn_strategy", side_effect=capture_strategy),
        ):
            _run_agent_job(job_def, ctx)

        assert captured[0].agent_config.get("lightweight") is True


# =============================================================================
# Job handler delegates
# =============================================================================


class TestJobHandlerDelegates:
    """Each @register_job handler should delegate to the scheduler implementation."""

    def test_check_and_update_finished_agents_delegates(self):
        ctx = JobContext(scheduler_state={})
        with patch("octopoid.scheduler.check_and_update_finished_agents") as mock_impl:
            from octopoid.jobs import check_and_update_finished_agents
            check_and_update_finished_agents(ctx)
        mock_impl.assert_called_once_with()

    def test_register_orchestrator_passes_poll_data_flag(self):
        ctx = JobContext(scheduler_state={}, poll_data={"orchestrator_registered": True})
        with patch("octopoid.scheduler._register_orchestrator") as mock_impl:
            from octopoid.jobs import _register_orchestrator
            _register_orchestrator(ctx)
        mock_impl.assert_called_once_with(orchestrator_registered=True)

    def test_register_orchestrator_defaults_to_false_when_no_poll_data(self):
        ctx = JobContext(scheduler_state={}, poll_data=None)
        with patch("octopoid.scheduler._register_orchestrator") as mock_impl:
            from octopoid.jobs import _register_orchestrator
            _register_orchestrator(ctx)
        mock_impl.assert_called_once_with(orchestrator_registered=False)

    def test_check_and_requeue_expired_leases_delegates(self):
        ctx = JobContext(scheduler_state={})
        with patch("octopoid.scheduler.check_and_requeue_expired_leases") as mock_impl:
            from octopoid.jobs import check_and_requeue_expired_leases
            check_and_requeue_expired_leases(ctx)
        mock_impl.assert_called_once_with()

    def test_check_project_completion_delegates(self):
        ctx = JobContext(scheduler_state={})
        with patch("octopoid.scheduler.check_project_completion") as mock_impl:
            from octopoid.jobs import check_project_completion
            check_project_completion(ctx)
        mock_impl.assert_called_once_with()

    def test_check_queue_health_throttled_delegates(self):
        ctx = JobContext(scheduler_state={})
        with patch("octopoid.scheduler._check_queue_health_throttled") as mock_impl:
            from octopoid.jobs import _check_queue_health_throttled
            _check_queue_health_throttled(ctx)
        mock_impl.assert_called_once_with()

    def test_agent_evaluation_loop_passes_queue_counts(self):
        queue_counts = {"incoming": 5, "claimed": 1}
        ctx = JobContext(scheduler_state={}, poll_data={"queue_counts": queue_counts})
        with patch("octopoid.scheduler._run_agent_evaluation_loop") as mock_impl:
            from octopoid.jobs import agent_evaluation_loop
            agent_evaluation_loop(ctx)
        mock_impl.assert_called_once_with(queue_counts=queue_counts)

    def test_agent_evaluation_loop_passes_none_when_no_poll_data(self):
        ctx = JobContext(scheduler_state={}, poll_data=None)
        with patch("octopoid.scheduler._run_agent_evaluation_loop") as mock_impl:
            from octopoid.jobs import agent_evaluation_loop
            agent_evaluation_loop(ctx)
        mock_impl.assert_called_once_with(queue_counts=None)

    def test_sweep_stale_resources_delegates(self):
        ctx = JobContext(scheduler_state={})
        with patch("octopoid.scheduler.sweep_stale_resources") as mock_impl:
            from octopoid.jobs import sweep_stale_resources
            sweep_stale_resources(ctx)
        mock_impl.assert_called_once_with()

    def test_send_heartbeat_delegates(self):
        ctx = JobContext(scheduler_state={})
        with patch("octopoid.scheduler.send_heartbeat") as mock_impl:
            from octopoid.jobs import send_heartbeat
            send_heartbeat(ctx)
        mock_impl.assert_called_once_with()

    def test_dispatch_action_messages_delegates(self):
        ctx = JobContext(scheduler_state={})
        with patch("octopoid.message_dispatcher.dispatch_action_messages") as mock_impl:
            from octopoid.jobs import dispatch_action_messages
            dispatch_action_messages(ctx)
        mock_impl.assert_called_once_with()


# =============================================================================
# _load_github_issues_state
# =============================================================================


class TestLoadGithubIssuesState:
    def test_returns_default_when_file_missing(self, tmp_path):
        state_file = tmp_path / "nonexistent.json"
        result = _load_github_issues_state(state_file)
        assert result == {"processed_issues": []}

    def test_loads_valid_json(self, tmp_path):
        state_file = tmp_path / "state.json"
        data = {"processed_issues": [1, 2, 3]}
        state_file.write_text(json.dumps(data))
        result = _load_github_issues_state(state_file)
        assert result == {"processed_issues": [1, 2, 3]}

    def test_returns_default_on_invalid_json(self, tmp_path):
        state_file = tmp_path / "invalid.json"
        state_file.write_text("not valid json {{{")
        result = _load_github_issues_state(state_file)
        assert result == {"processed_issues": []}

    def test_returns_default_on_oserror(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text("{}")
        with patch("builtins.open", side_effect=OSError("permission denied")):
            result = _load_github_issues_state(state_file)
        assert result == {"processed_issues": []}


# =============================================================================
# _save_github_issues_state
# =============================================================================


class TestSaveGithubIssuesState:
    def test_saves_state_to_file(self, tmp_path):
        state_file = tmp_path / "state.json"
        state = {"processed_issues": [10, 20]}
        _save_github_issues_state(state_file, state)
        loaded = json.loads(state_file.read_text())
        assert loaded == {"processed_issues": [10, 20]}

    def test_creates_parent_directories(self, tmp_path):
        state_file = tmp_path / "deep" / "nested" / "state.json"
        _save_github_issues_state(state_file, {"processed_issues": []})
        assert state_file.exists()

    def test_oserror_is_silently_handled(self, tmp_path):
        state_file = tmp_path / "state.json"
        with patch("builtins.open", side_effect=OSError("disk full")):
            # Should not raise
            _save_github_issues_state(state_file, {"processed_issues": []})


# =============================================================================
# _fetch_github_issues
# =============================================================================


class TestFetchGithubIssues:
    def test_returns_issues_on_success(self, tmp_path):
        issues = [{"number": 1, "title": "Bug", "url": "https://example.com/1", "body": "", "labels": []}]
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(issues)

        with patch("octopoid.jobs.subprocess.run", return_value=mock_result):
            result = _fetch_github_issues(tmp_path)

        assert result == issues

    def test_returns_empty_on_nonzero_returncode(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error message"

        with patch("octopoid.jobs.subprocess.run", return_value=mock_result):
            result = _fetch_github_issues(tmp_path)

        assert result == []

    def test_returns_empty_on_timeout(self, tmp_path):
        with patch(
            "octopoid.jobs.subprocess.run",
            side_effect=subprocess.TimeoutExpired("gh", 30),
        ):
            result = _fetch_github_issues(tmp_path)

        assert result == []

    def test_returns_empty_on_json_decode_error(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not json"

        with patch("octopoid.jobs.subprocess.run", return_value=mock_result):
            result = _fetch_github_issues(tmp_path)

        assert result == []

    def test_returns_empty_on_file_not_found(self, tmp_path):
        with patch(
            "octopoid.jobs.subprocess.run",
            side_effect=FileNotFoundError("gh not found"),
        ):
            result = _fetch_github_issues(tmp_path)

        assert result == []

    def test_returns_empty_on_generic_exception(self, tmp_path):
        with patch(
            "octopoid.jobs.subprocess.run",
            side_effect=Exception("unexpected error"),
        ):
            result = _fetch_github_issues(tmp_path)

        assert result == []

    def test_passes_correct_args_to_subprocess(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "[]"

        with patch("octopoid.jobs.subprocess.run", return_value=mock_result) as mock_run:
            _fetch_github_issues(tmp_path)

        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "gh"
        assert "issue" in cmd
        assert "list" in cmd
        assert "--json" in cmd
        assert call_args[1]["cwd"] == tmp_path


# =============================================================================
# _create_task_from_github_issue
# =============================================================================


class TestCreateTaskFromGithubIssue:
    def _make_issue(self, number=42, title="Test Issue", body="Fix this", labels=None):
        return {
            "number": number,
            "title": title,
            "url": f"https://github.com/owner/repo/issues/{number}",
            "body": body,
            "labels": [{"name": l} for l in (labels or [])],
        }

    def test_returns_task_id_on_success(self):
        issue = self._make_issue()
        with patch("octopoid.tasks.create_task", return_value="abc123") as mock_create:
            result = _create_task_from_github_issue(issue)
        assert result == "abc123"

    def test_title_includes_issue_number(self):
        issue = self._make_issue(number=99, title="My Bug")
        with patch("octopoid.tasks.create_task", return_value="xyz") as mock_create:
            _create_task_from_github_issue(issue)
        call_kwargs = mock_create.call_args[1]
        assert "[GH-99]" in call_kwargs["title"]
        assert "My Bug" in call_kwargs["title"]

    def test_default_priority_is_p1(self):
        issue = self._make_issue(labels=[])
        with patch("octopoid.tasks.create_task", return_value="t1") as mock_create:
            _create_task_from_github_issue(issue)
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["priority"] == "P1"

    def test_urgent_label_gives_p0_priority(self):
        issue = self._make_issue(labels=["urgent"])
        with patch("octopoid.tasks.create_task", return_value="t1") as mock_create:
            _create_task_from_github_issue(issue)
        assert mock_create.call_args[1]["priority"] == "P0"

    def test_critical_label_gives_p0_priority(self):
        issue = self._make_issue(labels=["critical"])
        with patch("octopoid.tasks.create_task", return_value="t1") as mock_create:
            _create_task_from_github_issue(issue)
        assert mock_create.call_args[1]["priority"] == "P0"

    def test_p0_label_gives_p0_priority(self):
        issue = self._make_issue(labels=["P0"])
        with patch("octopoid.tasks.create_task", return_value="t1") as mock_create:
            _create_task_from_github_issue(issue)
        assert mock_create.call_args[1]["priority"] == "P0"

    def test_low_priority_label_gives_p2_priority(self):
        issue = self._make_issue(labels=["low-priority"])
        with patch("octopoid.tasks.create_task", return_value="t1") as mock_create:
            _create_task_from_github_issue(issue)
        assert mock_create.call_args[1]["priority"] == "P2"

    def test_p2_label_gives_p2_priority(self):
        issue = self._make_issue(labels=["P2"])
        with patch("octopoid.tasks.create_task", return_value="t1") as mock_create:
            _create_task_from_github_issue(issue)
        assert mock_create.call_args[1]["priority"] == "P2"

    def test_role_is_always_implement(self):
        issue = self._make_issue()
        with patch("octopoid.tasks.create_task", return_value="t1") as mock_create:
            _create_task_from_github_issue(issue)
        assert mock_create.call_args[1]["role"] == "implement"

    def test_labels_included_in_context(self):
        issue = self._make_issue(labels=["bug", "help wanted"])
        with patch("octopoid.tasks.create_task", return_value="t1") as mock_create:
            _create_task_from_github_issue(issue)
        context = mock_create.call_args[1]["context"]
        assert "bug" in context
        assert "help wanted" in context

    def test_returns_none_on_create_task_failure(self):
        issue = self._make_issue()
        with patch("octopoid.tasks.create_task", side_effect=RuntimeError("API error")):
            result = _create_task_from_github_issue(issue)
        assert result is None

    def test_created_by_is_poll_github_issues(self):
        issue = self._make_issue()
        with patch("octopoid.tasks.create_task", return_value="t1") as mock_create:
            _create_task_from_github_issue(issue)
        assert mock_create.call_args[1]["created_by"] == "poll_github_issues"

    def test_empty_body_uses_placeholder(self):
        issue = self._make_issue(body=None)
        with patch("octopoid.tasks.create_task", return_value="t1") as mock_create:
            _create_task_from_github_issue(issue)
        context = mock_create.call_args[1]["context"]
        assert "(No description provided)" in context


# =============================================================================
# _comment_on_github_issue
# =============================================================================


class TestCommentOnGithubIssue:
    def test_calls_gh_issue_comment(self, tmp_path):
        mock_result = MagicMock()
        with patch("octopoid.jobs.subprocess.run", return_value=mock_result) as mock_run:
            from octopoid.jobs import _comment_on_github_issue
            _comment_on_github_issue(42, "task-abc", tmp_path)

        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "gh"
        assert "issue" in call_args
        assert "comment" in call_args
        assert "42" in call_args

    def test_comment_mentions_task_id(self, tmp_path):
        with patch("octopoid.jobs.subprocess.run") as mock_run:
            from octopoid.jobs import _comment_on_github_issue
            _comment_on_github_issue(7, "my-task-123", tmp_path)

        call_args = mock_run.call_args[0][0]
        body_idx = call_args.index("--body") + 1
        assert "my-task-123" in call_args[body_idx]

    def test_exception_is_silently_handled(self, tmp_path):
        with patch("octopoid.jobs.subprocess.run", side_effect=Exception("network error")):
            from octopoid.jobs import _comment_on_github_issue
            # Should not raise
            _comment_on_github_issue(1, "task-id", tmp_path)


# =============================================================================
# _forward_github_issue_to_server
# =============================================================================


class TestForwardGithubIssueToServer:
    def _make_issue(self, number=5, title="Server Bug", body="Details"):
        return {
            "number": number,
            "title": title,
            "url": f"https://github.com/owner/repo/issues/{number}",
            "body": body,
            "labels": [{"name": "server"}],
        }

    def test_returns_true_on_success(self, tmp_path):
        mock_create_result = MagicMock()
        mock_create_result.returncode = 0
        mock_create_result.stdout = "https://github.com/maxthelion/octopoid-server/issues/10\n"

        mock_comment_result = MagicMock()
        mock_comment_result.returncode = 0

        with patch(
            "octopoid.jobs.subprocess.run",
            side_effect=[mock_create_result, mock_comment_result],
        ):
            result = _forward_github_issue_to_server(self._make_issue(), tmp_path)

        assert result is True

    def test_returns_false_on_create_failure(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "gh error"

        with patch("octopoid.jobs.subprocess.run", return_value=mock_result):
            result = _forward_github_issue_to_server(self._make_issue(), tmp_path)

        assert result is False

    def test_returns_false_on_exception(self, tmp_path):
        with patch(
            "octopoid.jobs.subprocess.run",
            side_effect=Exception("network error"),
        ):
            result = _forward_github_issue_to_server(self._make_issue(), tmp_path)

        assert result is False

    def test_creates_issue_on_server_repo(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://github.com/maxthelion/octopoid-server/issues/10\n"

        with patch("octopoid.jobs.subprocess.run", return_value=mock_result) as mock_run:
            _forward_github_issue_to_server(self._make_issue(), tmp_path)

        first_call = mock_run.call_args_list[0][0][0]
        assert "--repo" in first_call
        repo_idx = first_call.index("--repo") + 1
        assert first_call[repo_idx] == "maxthelion/octopoid-server"

    def test_cross_links_back_to_original_issue(self, tmp_path):
        issue = self._make_issue(number=99, body="Some details")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://github.com/maxthelion/octopoid-server/issues/1\n"

        with patch("octopoid.jobs.subprocess.run", return_value=mock_result) as mock_run:
            _forward_github_issue_to_server(issue, tmp_path)

        first_call_kwargs = mock_run.call_args_list[0][0][0]
        body_idx = first_call_kwargs.index("--body") + 1
        assert "99" in first_call_kwargs[body_idx]


# =============================================================================
# poll_github_issues (the @register_job handler)
# =============================================================================


class TestPollGithubIssues:
    def _make_issue(self, number, labels=None, title="Issue", body="Body"):
        return {
            "number": number,
            "title": title,
            "url": f"https://github.com/owner/repo/issues/{number}",
            "body": body,
            "labels": [{"name": l} for l in (labels or [])],
        }

    def test_does_nothing_when_no_issues(self, tmp_path):
        ctx = JobContext(scheduler_state={})

        with (
            patch("octopoid.jobs.get_orchestrator_dir", return_value=tmp_path),
            patch("octopoid.jobs.find_parent_project", return_value=tmp_path),
            patch("octopoid.jobs._fetch_github_issues", return_value=[]),
            patch("octopoid.jobs._create_task_from_github_issue") as mock_create,
            patch("octopoid.jobs._save_github_issues_state") as mock_save,
        ):
            from octopoid.jobs import poll_github_issues
            poll_github_issues(ctx)

        mock_create.assert_not_called()
        mock_save.assert_not_called()

    def test_creates_task_for_new_issue(self, tmp_path):
        ctx = JobContext(scheduler_state={})
        issue = self._make_issue(1)

        with (
            patch("octopoid.jobs.get_orchestrator_dir", return_value=tmp_path),
            patch("octopoid.jobs.find_parent_project", return_value=tmp_path),
            patch("octopoid.jobs._fetch_github_issues", return_value=[issue]),
            patch("octopoid.jobs._load_github_issues_state", return_value={"processed_issues": []}),
            patch("octopoid.jobs._create_task_from_github_issue", return_value="task-abc"),
            patch("octopoid.jobs._comment_on_github_issue") as mock_comment,
            patch("octopoid.jobs._save_github_issues_state") as mock_save,
        ):
            from octopoid.jobs import poll_github_issues
            poll_github_issues(ctx)

        mock_comment.assert_called_once()
        mock_save.assert_called_once()
        saved_state = mock_save.call_args[0][1]
        assert 1 in saved_state["processed_issues"]

    def test_skips_already_processed_issue(self, tmp_path):
        ctx = JobContext(scheduler_state={})
        issue = self._make_issue(5)

        with (
            patch("octopoid.jobs.get_orchestrator_dir", return_value=tmp_path),
            patch("octopoid.jobs.find_parent_project", return_value=tmp_path),
            patch("octopoid.jobs._fetch_github_issues", return_value=[issue]),
            patch("octopoid.jobs._load_github_issues_state", return_value={"processed_issues": [5]}),
            patch("octopoid.jobs._create_task_from_github_issue") as mock_create,
            patch("octopoid.jobs._save_github_issues_state") as mock_save,
        ):
            from octopoid.jobs import poll_github_issues
            poll_github_issues(ctx)

        mock_create.assert_not_called()
        # State is still saved (sorted list), but issue 5 was already there
        mock_save.assert_called_once()

    def test_server_labelled_issue_is_forwarded_not_created(self, tmp_path):
        ctx = JobContext(scheduler_state={})
        issue = self._make_issue(10, labels=["server"])

        with (
            patch("octopoid.jobs.get_orchestrator_dir", return_value=tmp_path),
            patch("octopoid.jobs.find_parent_project", return_value=tmp_path),
            patch("octopoid.jobs._fetch_github_issues", return_value=[issue]),
            patch("octopoid.jobs._load_github_issues_state", return_value={"processed_issues": []}),
            patch("octopoid.jobs._forward_github_issue_to_server", return_value=True) as mock_fwd,
            patch("octopoid.jobs._create_task_from_github_issue") as mock_create,
            patch("octopoid.jobs._save_github_issues_state") as mock_save,
        ):
            from octopoid.jobs import poll_github_issues
            poll_github_issues(ctx)

        mock_fwd.assert_called_once()
        mock_create.assert_not_called()
        saved_state = mock_save.call_args[0][1]
        assert 10 in saved_state["processed_issues"]

    def test_failed_task_creation_does_not_mark_processed(self, tmp_path):
        ctx = JobContext(scheduler_state={})
        issue = self._make_issue(20)

        with (
            patch("octopoid.jobs.get_orchestrator_dir", return_value=tmp_path),
            patch("octopoid.jobs.find_parent_project", return_value=tmp_path),
            patch("octopoid.jobs._fetch_github_issues", return_value=[issue]),
            patch("octopoid.jobs._load_github_issues_state", return_value={"processed_issues": []}),
            patch("octopoid.jobs._create_task_from_github_issue", return_value=None),
            patch("octopoid.jobs._comment_on_github_issue") as mock_comment,
            patch("octopoid.jobs._save_github_issues_state") as mock_save,
        ):
            from octopoid.jobs import poll_github_issues
            poll_github_issues(ctx)

        mock_comment.assert_not_called()
        saved_state = mock_save.call_args[0][1]
        assert 20 not in saved_state["processed_issues"]

    def test_failed_forwarding_does_not_mark_processed(self, tmp_path):
        ctx = JobContext(scheduler_state={})
        issue = self._make_issue(30, labels=["server"])

        with (
            patch("octopoid.jobs.get_orchestrator_dir", return_value=tmp_path),
            patch("octopoid.jobs.find_parent_project", return_value=tmp_path),
            patch("octopoid.jobs._fetch_github_issues", return_value=[issue]),
            patch("octopoid.jobs._load_github_issues_state", return_value={"processed_issues": []}),
            patch("octopoid.jobs._forward_github_issue_to_server", return_value=False),
            patch("octopoid.jobs._save_github_issues_state") as mock_save,
        ):
            from octopoid.jobs import poll_github_issues
            poll_github_issues(ctx)

        saved_state = mock_save.call_args[0][1]
        assert 30 not in saved_state["processed_issues"]

    def test_multiple_issues_processed_together(self, tmp_path):
        ctx = JobContext(scheduler_state={})
        issues = [self._make_issue(i) for i in range(1, 4)]

        with (
            patch("octopoid.jobs.get_orchestrator_dir", return_value=tmp_path),
            patch("octopoid.jobs.find_parent_project", return_value=tmp_path),
            patch("octopoid.jobs._fetch_github_issues", return_value=issues),
            patch("octopoid.jobs._load_github_issues_state", return_value={"processed_issues": []}),
            patch("octopoid.jobs._create_task_from_github_issue", return_value="task-x"),
            patch("octopoid.jobs._comment_on_github_issue"),
            patch("octopoid.jobs._save_github_issues_state") as mock_save,
        ):
            from octopoid.jobs import poll_github_issues
            poll_github_issues(ctx)

        saved_state = mock_save.call_args[0][1]
        assert sorted(saved_state["processed_issues"]) == [1, 2, 3]

    def test_state_file_path_is_in_runtime_dir(self, tmp_path):
        ctx = JobContext(scheduler_state={})

        state_file_used = []

        def capture_load(path):
            state_file_used.append(path)
            return {"processed_issues": []}

        with (
            patch("octopoid.jobs.get_orchestrator_dir", return_value=tmp_path),
            patch("octopoid.jobs.find_parent_project", return_value=tmp_path),
            patch("octopoid.jobs._fetch_github_issues", return_value=[]),
            patch("octopoid.jobs._load_github_issues_state", side_effect=capture_load),
            patch("octopoid.jobs._save_github_issues_state"),
        ):
            from octopoid.jobs import poll_github_issues
            poll_github_issues(ctx)

        assert len(state_file_used) == 1
        assert "github_issues_state.json" in str(state_file_used[0])
        assert "runtime" in str(state_file_used[0])
