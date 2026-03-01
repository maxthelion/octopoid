"""Tests for the `octopoid trigger-agent` CLI command.

Verifies that the command:
- Triggers an agent job by name, bypassing the interval guard
- Rejects non-existent job names with a helpful error
- Rejects jobs that are not of type 'agent'
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


SAMPLE_JOBS_YAML = [
    {
        "name": "codebase_analyst",
        "type": "agent",
        "interval": 3600,
    },
    {
        "name": "check_and_update_finished_agents",
        "type": "script",
        "interval": 30,
    },
]


class TestCmdTriggerAgent:
    """Tests for cmd_trigger_agent in octopoid.cli."""

    def test_triggers_known_agent_job(self):
        """Triggering a known agent job calls _run_agent_job and exits 0."""
        from octopoid.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["trigger-agent", "codebase_analyst"])

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=SAMPLE_JOBS_YAML),
            patch("octopoid.scheduler.load_scheduler_state", return_value={}),
            patch("octopoid.jobs._run_agent_job") as mock_run,
        ):
            args.func(args)
            assert mock_run.called

    def test_passes_correct_job_def(self):
        """The correct job definition is passed to _run_agent_job."""
        from octopoid.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["trigger-agent", "codebase_analyst"])

        captured_job_def = {}

        def capture_call(job_def, ctx):
            captured_job_def.update(job_def)

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=SAMPLE_JOBS_YAML),
            patch("octopoid.scheduler.load_scheduler_state", return_value={}),
            patch("octopoid.jobs._run_agent_job", side_effect=capture_call),
        ):
            args.func(args)

        assert captured_job_def["name"] == "codebase_analyst"
        assert captured_job_def["type"] == "agent"

    def test_rejects_unknown_job(self, capsys):
        """Triggering an unknown job name exits 1 with a useful error message."""
        from octopoid.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["trigger-agent", "nonexistent_job"])

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=SAMPLE_JOBS_YAML),
            patch("octopoid.scheduler.load_scheduler_state", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc_info:
                args.func(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err

    def test_lists_available_agent_jobs_on_error(self, capsys):
        """Error message includes list of available agent jobs."""
        from octopoid.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["trigger-agent", "bad_name"])

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=SAMPLE_JOBS_YAML),
            patch("octopoid.scheduler.load_scheduler_state", return_value={}),
        ):
            with pytest.raises(SystemExit):
                args.func(args)

        captured = capsys.readouterr()
        assert "codebase_analyst" in captured.err

    def test_rejects_script_type_job(self, capsys):
        """Triggering a script-type job exits 1 because only 'agent' jobs are supported."""
        from octopoid.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["trigger-agent", "check_and_update_finished_agents"])

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=SAMPLE_JOBS_YAML),
            patch("octopoid.scheduler.load_scheduler_state", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc_info:
                args.func(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "agent" in captured.err.lower()

    def test_bypasses_interval_guard(self):
        """trigger-agent does not consult is_job_due, bypassing the interval guard.

        The invariant manual-trigger-bypasses-guard requires the trigger to fire
        even if the job ran very recently. We verify by checking that
        scheduler.is_job_due is never called by the trigger path.
        """
        from octopoid.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["trigger-agent", "codebase_analyst"])

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=SAMPLE_JOBS_YAML),
            patch("octopoid.scheduler.load_scheduler_state", return_value={}),
            patch("octopoid.jobs._run_agent_job") as mock_run,
            patch("octopoid.scheduler.is_job_due") as mock_due,
        ):
            args.func(args)

        assert mock_run.called, "Job should run"
        assert not mock_due.called, "Interval guard (is_job_due) must not be consulted"

    def test_spawn_failure_exits_nonzero(self, capsys):
        """If _run_agent_job raises, the command exits 1."""
        from octopoid.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["trigger-agent", "codebase_analyst"])

        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=SAMPLE_JOBS_YAML),
            patch("octopoid.scheduler.load_scheduler_state", return_value={}),
            patch("octopoid.jobs._run_agent_job", side_effect=RuntimeError("spawn failed")),
        ):
            with pytest.raises(SystemExit) as exc_info:
                args.func(args)

        assert exc_info.value.code == 1

    def test_trigger_agent_registered_in_parser(self):
        """trigger-agent subcommand exists in the CLI argument parser."""
        from octopoid.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["trigger-agent", "codebase_analyst"])
        assert args.job_name == "codebase_analyst"
        assert hasattr(args, "func")
