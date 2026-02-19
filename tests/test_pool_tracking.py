"""Unit tests for orchestrator.pool — PID tracking per blueprint."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.pool import (
    cleanup_dead_pids,
    count_running_instances,
    get_blueprint_pids_path,
    load_blueprint_pids,
    register_instance_pid,
    save_blueprint_pids,
)


@pytest.fixture()
def agents_runtime_dir(tmp_path, monkeypatch):
    """Redirect get_agents_runtime_dir() to a temp directory."""
    runtime = tmp_path / "agents"
    runtime.mkdir()
    monkeypatch.setattr(
        "orchestrator.pool.get_agents_runtime_dir", lambda: runtime
    )
    return runtime


# ---------------------------------------------------------------------------
# get_blueprint_pids_path
# ---------------------------------------------------------------------------


class TestGetBlueprintPidsPath:
    def test_returns_expected_path(self, agents_runtime_dir):
        path = get_blueprint_pids_path("implementer")
        assert path == agents_runtime_dir / "implementer" / "running_pids.json"


# ---------------------------------------------------------------------------
# load_blueprint_pids
# ---------------------------------------------------------------------------


class TestLoadBlueprintPids:
    def test_returns_empty_dict_when_file_missing(self, agents_runtime_dir):
        result = load_blueprint_pids("implementer")
        assert result == {}

    def test_returns_entries_with_int_keys(self, agents_runtime_dir):
        blueprint_dir = agents_runtime_dir / "implementer"
        blueprint_dir.mkdir(parents=True, exist_ok=True)
        pids_file = blueprint_dir / "running_pids.json"
        pids_file.write_text(
            json.dumps(
                {
                    "12345": {
                        "task_id": "TASK-abc",
                        "started_at": "2026-02-18T10:00:00+00:00",
                        "instance_name": "implementer-1",
                    }
                }
            )
        )

        result = load_blueprint_pids("implementer")
        assert 12345 in result
        assert result[12345]["task_id"] == "TASK-abc"
        assert result[12345]["instance_name"] == "implementer-1"

    def test_returns_empty_dict_on_corrupt_json(self, agents_runtime_dir):
        blueprint_dir = agents_runtime_dir / "implementer"
        blueprint_dir.mkdir(parents=True, exist_ok=True)
        (blueprint_dir / "running_pids.json").write_text("NOT JSON {{")

        result = load_blueprint_pids("implementer")
        assert result == {}


# ---------------------------------------------------------------------------
# save_blueprint_pids
# ---------------------------------------------------------------------------


class TestSaveBlueprintPids:
    def test_creates_file_with_string_keys(self, agents_runtime_dir):
        save_blueprint_pids("implementer", {99999: {"task_id": "TASK-x", "started_at": "t", "instance_name": "i-1"}})

        path = agents_runtime_dir / "implementer" / "running_pids.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert "99999" in data

    def test_creates_parent_directory(self, agents_runtime_dir):
        # Directory does not exist yet
        save_blueprint_pids("reviewer", {1: {"task_id": "T", "started_at": "t", "instance_name": "r-1"}})
        assert (agents_runtime_dir / "reviewer" / "running_pids.json").exists()

    def test_write_is_atomic_via_rename(self, agents_runtime_dir, monkeypatch):
        """Verify atomic write: temp file is created then renamed."""
        rename_calls = []
        original_rename = os.rename

        def tracking_rename(src, dst):
            rename_calls.append((src, dst))
            original_rename(src, dst)

        monkeypatch.setattr(os, "rename", tracking_rename)

        save_blueprint_pids("implementer", {42: {"task_id": "T", "started_at": "t", "instance_name": "i-1"}})

        assert len(rename_calls) == 1
        src, dst = rename_calls[0]
        assert str(dst).endswith("running_pids.json")
        # temp file should have been in the same directory
        assert Path(src).parent == Path(dst).parent

    def test_roundtrip(self, agents_runtime_dir):
        pids = {
            11111: {"task_id": "TASK-a", "started_at": "2026-01-01T00:00:00+00:00", "instance_name": "imp-1"},
            22222: {"task_id": "TASK-b", "started_at": "2026-01-01T00:01:00+00:00", "instance_name": "imp-2"},
        }
        save_blueprint_pids("implementer", pids)
        loaded = load_blueprint_pids("implementer")
        assert loaded == pids


# ---------------------------------------------------------------------------
# register_instance_pid
# ---------------------------------------------------------------------------


class TestRegisterInstancePid:
    def test_adds_entry_to_empty_file(self, agents_runtime_dir):
        register_instance_pid("implementer", 55555, "TASK-z", "implementer-1")
        pids = load_blueprint_pids("implementer")
        assert 55555 in pids
        assert pids[55555]["task_id"] == "TASK-z"
        assert pids[55555]["instance_name"] == "implementer-1"
        assert "started_at" in pids[55555]

    def test_appends_to_existing_entries(self, agents_runtime_dir):
        register_instance_pid("implementer", 11111, "TASK-a", "implementer-1")
        register_instance_pid("implementer", 22222, "TASK-b", "implementer-2")
        pids = load_blueprint_pids("implementer")
        assert 11111 in pids
        assert 22222 in pids

    def test_started_at_is_iso8601(self, agents_runtime_dir):
        register_instance_pid("implementer", 33333, "TASK-c", "implementer-1")
        pids = load_blueprint_pids("implementer")
        from datetime import datetime
        # Should parse without error
        datetime.fromisoformat(pids[33333]["started_at"])


# ---------------------------------------------------------------------------
# count_running_instances
# ---------------------------------------------------------------------------


class TestCountRunningInstances:
    def test_returns_zero_for_empty_file(self, agents_runtime_dir):
        assert count_running_instances("implementer") == 0

    def test_counts_only_alive_pids(self, agents_runtime_dir):
        alive_pid = 11111
        dead_pid = 22222

        def fake_kill(pid, sig):
            if pid == dead_pid:
                raise ProcessLookupError
            # alive_pid succeeds silently

        with patch("orchestrator.pool.os.kill", side_effect=fake_kill):
            save_blueprint_pids(
                "implementer",
                {
                    alive_pid: {"task_id": "T1", "started_at": "t", "instance_name": "i-1"},
                    dead_pid: {"task_id": "T2", "started_at": "t", "instance_name": "i-2"},
                },
            )
            count = count_running_instances("implementer")

        assert count == 1

    def test_all_alive(self, agents_runtime_dir):
        def fake_kill(pid, sig):
            pass  # all alive

        with patch("orchestrator.pool.os.kill", side_effect=fake_kill):
            save_blueprint_pids(
                "implementer",
                {
                    1: {"task_id": "T1", "started_at": "t", "instance_name": "i-1"},
                    2: {"task_id": "T2", "started_at": "t", "instance_name": "i-2"},
                    3: {"task_id": "T3", "started_at": "t", "instance_name": "i-3"},
                },
            )
            count = count_running_instances("implementer")

        assert count == 3

    def test_all_dead(self, agents_runtime_dir):
        def fake_kill(pid, sig):
            raise OSError

        with patch("orchestrator.pool.os.kill", side_effect=fake_kill):
            save_blueprint_pids(
                "implementer",
                {
                    1: {"task_id": "T1", "started_at": "t", "instance_name": "i-1"},
                    2: {"task_id": "T2", "started_at": "t", "instance_name": "i-2"},
                },
            )
            count = count_running_instances("implementer")

        assert count == 0


# ---------------------------------------------------------------------------
# cleanup_dead_pids
# ---------------------------------------------------------------------------


class TestCleanupDeadPids:
    def test_returns_zero_when_no_pids(self, agents_runtime_dir):
        removed = cleanup_dead_pids("implementer")
        assert removed == 0

    def test_removes_dead_pids_and_returns_count(self, agents_runtime_dir):
        alive_pid = 11111
        dead_pid1 = 22222
        dead_pid2 = 33333

        def fake_kill(pid, sig):
            if pid != alive_pid:
                raise ProcessLookupError

        save_blueprint_pids(
            "implementer",
            {
                alive_pid: {"task_id": "T1", "started_at": "t", "instance_name": "i-1"},
                dead_pid1: {"task_id": "T2", "started_at": "t", "instance_name": "i-2"},
                dead_pid2: {"task_id": "T3", "started_at": "t", "instance_name": "i-3"},
            },
        )

        with patch("orchestrator.pool.os.kill", side_effect=fake_kill):
            removed = cleanup_dead_pids("implementer")

        assert removed == 2
        remaining = load_blueprint_pids("implementer")
        assert alive_pid in remaining
        assert dead_pid1 not in remaining
        assert dead_pid2 not in remaining

    def test_does_not_rewrite_file_when_nothing_to_remove(self, agents_runtime_dir):
        """File should not be rewritten if no dead PIDs are found."""
        def fake_kill(pid, sig):
            pass  # all alive

        save_blueprint_pids(
            "implementer",
            {1: {"task_id": "T1", "started_at": "t", "instance_name": "i-1"}},
        )

        mtime_before = (agents_runtime_dir / "implementer" / "running_pids.json").stat().st_mtime

        with patch("orchestrator.pool.os.kill", side_effect=fake_kill):
            removed = cleanup_dead_pids("implementer")

        mtime_after = (agents_runtime_dir / "implementer" / "running_pids.json").stat().st_mtime

        assert removed == 0
        assert mtime_before == mtime_after  # not rewritten

    def test_all_dead_leaves_empty_file(self, agents_runtime_dir):
        def fake_kill(pid, sig):
            raise OSError

        save_blueprint_pids(
            "implementer",
            {
                1: {"task_id": "T1", "started_at": "t", "instance_name": "i-1"},
                2: {"task_id": "T2", "started_at": "t", "instance_name": "i-2"},
            },
        )

        with patch("orchestrator.pool.os.kill", side_effect=fake_kill):
            removed = cleanup_dead_pids("implementer")

        assert removed == 2
        assert load_blueprint_pids("implementer") == {}
