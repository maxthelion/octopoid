"""Tests for scope configuration: get_scope() and scheduler startup validation."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


class TestGetScope:
    """Tests for orchestrator.config.get_scope()."""

    def test_returns_scope_from_config(self, tmp_path: Path) -> None:
        """get_scope() returns the scope value from config.yaml."""
        config = {"scope": "myproject", "server": {"enabled": True}}
        config_path = tmp_path / ".octopoid" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(yaml.dump(config))

        with patch("octopoid.config.find_parent_project", return_value=tmp_path):
            from octopoid.config import get_scope
            assert get_scope() == "myproject"

    def test_returns_none_when_scope_missing(self, tmp_path: Path) -> None:
        """get_scope() returns None when scope is absent from config.yaml."""
        config = {"server": {"enabled": True}}
        config_path = tmp_path / ".octopoid" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(yaml.dump(config))

        with patch("octopoid.config.find_parent_project", return_value=tmp_path):
            from octopoid.config import get_scope
            assert get_scope() is None

    def test_returns_none_when_config_missing(self, tmp_path: Path) -> None:
        """get_scope() returns None when config.yaml does not exist."""
        with patch("octopoid.config.find_parent_project", return_value=tmp_path):
            from octopoid.config import get_scope
            assert get_scope() is None

    def test_scope_coerced_to_string(self, tmp_path: Path) -> None:
        """get_scope() coerces non-string values to str."""
        config = {"scope": 42}
        config_path = tmp_path / ".octopoid" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(yaml.dump(config))

        with patch("octopoid.config.find_parent_project", return_value=tmp_path):
            from octopoid.config import get_scope
            assert get_scope() == "42"


class TestSDKScopeInjection:
    """Tests that OctopoidSDK auto-injects scope on API calls."""

    def test_sdk_injects_scope_on_post(self) -> None:
        """OctopoidSDK injects scope into POST request body when scope is set."""
        from octopoid_sdk import OctopoidSDK

        sdk = OctopoidSDK(server_url="http://example.com", scope="myproject")
        captured = {}

        def fake_request(method: str, url: str, **kwargs: object) -> MagicMock:
            captured.update(kwargs)
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {}
            r.raise_for_status.return_value = None
            return r

        sdk.session.request = fake_request  # type: ignore[method-assign]
        sdk._request("POST", "/api/v1/tasks", json={"title": "Test"})

        assert captured["json"].get("scope") == "myproject"

    def test_sdk_injects_scope_on_get(self) -> None:
        """OctopoidSDK injects scope into GET query params when scope is set."""
        from octopoid_sdk import OctopoidSDK

        sdk = OctopoidSDK(server_url="http://example.com", scope="myproject")
        captured = {}

        def fake_request(method: str, url: str, **kwargs: object) -> MagicMock:
            captured.update(kwargs)
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = []
            r.raise_for_status.return_value = None
            return r

        sdk.session.request = fake_request  # type: ignore[method-assign]
        sdk._request("GET", "/api/v1/tasks")

        assert captured.get("params", {}).get("scope") == "myproject"

    def test_sdk_does_not_inject_scope_when_not_set(self) -> None:
        """OctopoidSDK does not inject scope when scope is None."""
        from octopoid_sdk import OctopoidSDK

        sdk = OctopoidSDK(server_url="http://example.com", scope=None)
        captured = {}

        def fake_request(method: str, url: str, **kwargs: object) -> MagicMock:
            captured.update(kwargs)
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {}
            r.raise_for_status.return_value = None
            return r

        sdk.session.request = fake_request  # type: ignore[method-assign]
        sdk._request("POST", "/api/v1/tasks", json={"title": "Test"})

        assert "scope" not in (captured.get("json") or {})


class TestListTasksScopeFiltering:
    """Tests that list_tasks() filters out tasks from other scopes."""

    def test_list_tasks_filters_by_current_scope(self) -> None:
        """list_tasks() only returns tasks matching the current scope."""
        tasks_from_server = [
            {"id": "t1", "scope": "octopoid", "queue": "incoming"},
            {"id": "t2", "scope": "boxen", "queue": "incoming"},
            {"id": "t3", "scope": "octopoid", "queue": "incoming"},
        ]
        with (
            patch("octopoid.tasks.get_scope", return_value="octopoid"),
            patch("octopoid.tasks.get_sdk") as mock_get_sdk,
        ):
            mock_sdk = MagicMock()
            mock_sdk.tasks.list.return_value = tasks_from_server
            mock_get_sdk.return_value = mock_sdk

            from octopoid.tasks import list_tasks
            result = list_tasks("incoming")

        assert len(result) == 2
        assert all(t["scope"] == "octopoid" for t in result)
        task_ids = {t["id"] for t in result}
        assert task_ids == {"t1", "t3"}

    def test_list_tasks_no_scope_returns_all(self) -> None:
        """list_tasks() returns all tasks when scope is not configured."""
        tasks_from_server = [
            {"id": "t1", "scope": "octopoid", "queue": "incoming"},
            {"id": "t2", "scope": "boxen", "queue": "incoming"},
        ]
        with (
            patch("octopoid.tasks.get_scope", return_value=None),
            patch("octopoid.tasks.get_sdk") as mock_get_sdk,
        ):
            mock_sdk = MagicMock()
            mock_sdk.tasks.list.return_value = tasks_from_server
            mock_get_sdk.return_value = mock_sdk

            from octopoid.tasks import list_tasks
            result = list_tasks("incoming")

        assert len(result) == 2

    def test_list_tasks_excludes_tasks_without_scope_field(self) -> None:
        """list_tasks() excludes tasks that have no scope when scope is configured."""
        tasks_from_server = [
            {"id": "t1", "scope": "octopoid", "queue": "incoming"},
            {"id": "t2", "queue": "incoming"},  # No scope field
        ]
        with (
            patch("octopoid.tasks.get_scope", return_value="octopoid"),
            patch("octopoid.tasks.get_sdk") as mock_get_sdk,
        ):
            mock_sdk = MagicMock()
            mock_sdk.tasks.list.return_value = tasks_from_server
            mock_get_sdk.return_value = mock_sdk

            from octopoid.tasks import list_tasks
            result = list_tasks("incoming")

        # Only the scoped task should be returned
        assert len(result) == 1
        assert result[0]["id"] == "t1"


class TestCanClaimTaskScopeIsolation:
    """Tests that can_claim_task() uses scope-filtered claimed counts."""

    def test_can_claim_uses_scoped_claimed_count_not_poll_data(self) -> None:
        """can_claim_task() always re-fetches claimed count via count_queue(), not poll data."""
        # Poll data says claimed=5 (cross-scope), but scope-filtered count is 0
        poll_queue_counts = {"incoming": 3, "claimed": 5, "provisional": 0}

        with (
            patch("octopoid.backpressure.get_queue_limits", return_value={
                "max_incoming": 20, "max_claimed": 2, "max_provisional": 5
            }),
            patch("octopoid.backpressure.count_queue", return_value=0) as mock_count,
        ):
            from octopoid.backpressure import can_claim_task
            can_proceed, reason = can_claim_task(queue_counts=poll_queue_counts)

        assert can_proceed is True
        # count_queue should have been called for "claimed" (and never uses poll's claimed=5)
        mock_count.assert_called_once_with("claimed")

    def test_can_claim_blocked_when_scoped_claimed_at_limit(self) -> None:
        """can_claim_task() is blocked when scope-filtered claimed tasks hit the limit."""
        poll_queue_counts = {"incoming": 3, "claimed": 0, "provisional": 0}

        with (
            patch("octopoid.backpressure.get_queue_limits", return_value={
                "max_incoming": 20, "max_claimed": 2, "max_provisional": 5
            }),
            # count_queue returns 2 (at limit) for scope-filtered claimed tasks
            patch("octopoid.backpressure.count_queue", return_value=2),
        ):
            from octopoid.backpressure import can_claim_task
            can_proceed, reason = can_claim_task(queue_counts=poll_queue_counts)

        assert can_proceed is False
        assert "claimed" in reason

    def test_can_claim_not_blocked_by_other_scopes_claimed_tasks(self) -> None:
        """can_claim_task() is not blocked by claimed tasks from other scopes.

        Regression test for GH-227: cross-scope pool capacity sharing.
        """
        # Simulate: poll returned 2 claimed tasks (from another scope),
        # but scope-filtered count is 0. Should allow claiming.
        poll_queue_counts = {"incoming": 1, "claimed": 2, "provisional": 0}

        with (
            patch("octopoid.backpressure.get_queue_limits", return_value={
                "max_incoming": 20, "max_claimed": 2, "max_provisional": 5
            }),
            # Scope-filtered count is 0 — we have no claimed tasks in our scope
            patch("octopoid.backpressure.count_queue", return_value=0),
        ):
            from octopoid.backpressure import can_claim_task
            can_proceed, reason = can_claim_task(queue_counts=poll_queue_counts)

        assert can_proceed is True, f"Should be able to claim but got: {reason}"


class TestSchedulerScopeValidation:
    """Tests for scheduler startup scope validation."""

    def test_scheduler_exits_when_scope_missing(self) -> None:
        """run_scheduler() exits with code 1 if scope is not configured."""
        from octopoid.scheduler import run_scheduler

        with (
            patch("octopoid.scheduler.is_system_paused", return_value=False),
            patch("octopoid.scheduler.get_scope", return_value=None),
            pytest.raises(SystemExit) as exc_info,
        ):
            run_scheduler()

        assert exc_info.value.code == 1

    def test_scheduler_proceeds_when_scope_set(self) -> None:
        """run_scheduler() does not exit when scope is configured."""
        # run_due_jobs is imported locally inside run_scheduler(), so patch it at source
        with (
            patch("octopoid.scheduler.is_system_paused", return_value=False),
            patch("octopoid.scheduler.get_scope", return_value="octopoid"),
            patch("octopoid.scheduler.load_scheduler_state", return_value={}),
            patch("octopoid.jobs.run_due_jobs"),
            patch("octopoid.scheduler.save_scheduler_state"),
        ):
            from octopoid.scheduler import run_scheduler
            # Should not raise SystemExit
            run_scheduler()
