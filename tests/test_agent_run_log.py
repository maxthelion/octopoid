"""Tests for the agent_run_log module.

Verifies that run logs are written and read correctly, and that summary
extraction works as expected.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_runtime(tmp_path):
    """Patch get_runtime_dir to return a temp directory."""
    with patch("octopoid.agent_run_log._run_log_dir") as mock_dir:
        log_dir = tmp_path / "agent-run-logs"
        log_dir.mkdir()
        mock_dir.return_value = log_dir
        yield log_dir


class TestWriteRunLog:
    def test_creates_log_file(self, tmp_runtime):
        from octopoid.agent_run_log import write_run_log

        write_run_log("codebase_analyst", job_dir=None, started_at="2026-01-01T00:00:00")

        log_file = tmp_runtime / "codebase_analyst.jsonl"
        assert log_file.exists()

    def test_entry_has_required_fields(self, tmp_runtime):
        from octopoid.agent_run_log import write_run_log

        write_run_log("test_job", job_dir=None, started_at="2026-01-01T00:00:00")

        log_file = tmp_runtime / "test_job.jsonl"
        entry = json.loads(log_file.read_text().strip())
        assert "started_at" in entry
        assert "finished_at" in entry
        assert "outcome" in entry
        assert entry["started_at"] == "2026-01-01T00:00:00"
        assert entry["outcome"] == "ok"

    def test_multiple_entries_appended(self, tmp_runtime):
        from octopoid.agent_run_log import write_run_log

        write_run_log("test_job", job_dir=None, started_at="2026-01-01T00:00:00")
        write_run_log("test_job", job_dir=None, started_at="2026-01-01T01:00:00")

        log_file = tmp_runtime / "test_job.jsonl"
        lines = [l for l in log_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 2

    def test_extracts_summary_from_stdout(self, tmp_runtime, tmp_path):
        from octopoid.agent_run_log import write_run_log

        # Create a fake job directory with stdout.log
        job_dir = tmp_path / "codebase_analyst-20260101T000000"
        job_dir.mkdir()
        (job_dir / "stdout.log").write_text(
            "Analysing codebase...\nFound 5 large files.\nCreated 2 proposals.\n"
        )

        write_run_log("codebase_analyst", job_dir=job_dir, started_at="2026-01-01T00:00:00")

        log_file = tmp_runtime / "codebase_analyst.jsonl"
        entry = json.loads(log_file.read_text().strip())
        assert entry["summary"] is not None
        assert "proposals" in entry["summary"]

    def test_no_summary_when_stdout_empty(self, tmp_runtime, tmp_path):
        from octopoid.agent_run_log import write_run_log

        job_dir = tmp_path / "test_job-ts"
        job_dir.mkdir()
        (job_dir / "stdout.log").write_text("")

        write_run_log("test_job", job_dir=job_dir, started_at="2026-01-01T00:00:00")

        log_file = tmp_runtime / "test_job.jsonl"
        entry = json.loads(log_file.read_text().strip())
        assert entry["summary"] is None

    def test_no_summary_when_no_stdout(self, tmp_runtime, tmp_path):
        from octopoid.agent_run_log import write_run_log

        job_dir = tmp_path / "test_job-ts"
        job_dir.mkdir()
        # No stdout.log created

        write_run_log("test_job", job_dir=job_dir, started_at="2026-01-01T00:00:00")

        log_file = tmp_runtime / "test_job.jsonl"
        entry = json.loads(log_file.read_text().strip())
        assert entry["summary"] is None

    def test_stores_error_outcome(self, tmp_runtime):
        from octopoid.agent_run_log import write_run_log

        write_run_log("test_job", job_dir=None, started_at=None, outcome="error")

        log_file = tmp_runtime / "test_job.jsonl"
        entry = json.loads(log_file.read_text().strip())
        assert entry["outcome"] == "error"

    def test_trims_to_max_entries(self, tmp_runtime):
        from octopoid import agent_run_log
        from octopoid.agent_run_log import write_run_log

        original_max = agent_run_log._MAX_LOG_ENTRIES
        try:
            agent_run_log._MAX_LOG_ENTRIES = 3
            for i in range(5):
                write_run_log("test_job", job_dir=None, started_at=f"2026-01-01T{i:02d}:00:00")
        finally:
            agent_run_log._MAX_LOG_ENTRIES = original_max

        log_file = tmp_runtime / "test_job.jsonl"
        lines = [l for l in log_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 3


class TestReadRunLogs:
    def test_returns_empty_when_no_log(self, tmp_runtime):
        from octopoid.agent_run_log import read_run_logs

        result = read_run_logs("nonexistent_job")
        assert result == []

    def test_returns_entries_newest_first(self, tmp_runtime):
        from octopoid.agent_run_log import write_run_log, read_run_logs

        write_run_log("test_job", job_dir=None, started_at="2026-01-01T00:00:00")
        write_run_log("test_job", job_dir=None, started_at="2026-01-01T01:00:00")

        entries = read_run_logs("test_job")
        assert len(entries) == 2
        # Most recent first
        assert entries[0]["started_at"] == "2026-01-01T01:00:00"
        assert entries[1]["started_at"] == "2026-01-01T00:00:00"

    def test_respects_max_entries(self, tmp_runtime):
        from octopoid.agent_run_log import write_run_log, read_run_logs

        for i in range(10):
            write_run_log("test_job", job_dir=None, started_at=f"2026-01-01T{i:02d}:00:00")

        entries = read_run_logs("test_job", max_entries=3)
        assert len(entries) == 3

    def test_handles_corrupted_line(self, tmp_runtime):
        from octopoid.agent_run_log import write_run_log, read_run_logs

        write_run_log("test_job", job_dir=None, started_at="2026-01-01T00:00:00")

        # Corrupt the file
        log_file = tmp_runtime / "test_job.jsonl"
        content = log_file.read_text() + "this is not json\n"
        log_file.write_text(content)

        # Should still return the valid entry
        entries = read_run_logs("test_job")
        assert len(entries) == 1


class TestGetLastRunSummary:
    def test_returns_none_when_no_log(self, tmp_runtime):
        from octopoid.agent_run_log import get_last_run_summary

        result = get_last_run_summary("nonexistent_job")
        assert result is None

    def test_returns_most_recent_entry(self, tmp_runtime):
        from octopoid.agent_run_log import write_run_log, get_last_run_summary

        write_run_log("test_job", job_dir=None, started_at="2026-01-01T00:00:00")
        write_run_log("test_job", job_dir=None, started_at="2026-01-01T01:00:00")

        result = get_last_run_summary("test_job")
        assert result is not None
        assert result["started_at"] == "2026-01-01T01:00:00"


class TestExtractSummary:
    def test_extracts_last_lines(self, tmp_path):
        from octopoid.agent_run_log import _extract_summary

        job_dir = tmp_path / "job"
        job_dir.mkdir()
        (job_dir / "stdout.log").write_text(
            "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n"
        )

        summary = _extract_summary(job_dir)
        assert summary is not None
        # Should contain the last lines
        assert "Line 5" in summary

    def test_returns_none_for_missing_file(self, tmp_path):
        from octopoid.agent_run_log import _extract_summary

        job_dir = tmp_path / "job"
        job_dir.mkdir()

        result = _extract_summary(job_dir)
        assert result is None

    def test_truncates_long_summary(self, tmp_path):
        from octopoid import agent_run_log
        from octopoid.agent_run_log import _extract_summary

        job_dir = tmp_path / "job"
        job_dir.mkdir()
        (job_dir / "stdout.log").write_text("x" * 1000 + "\n")

        summary = _extract_summary(job_dir)
        assert summary is not None
        assert len(summary) <= agent_run_log._SUMMARY_MAX_CHARS + 3  # +3 for "..."
