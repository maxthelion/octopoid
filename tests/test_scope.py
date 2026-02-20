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

        with patch("orchestrator.config.find_parent_project", return_value=tmp_path):
            from orchestrator.config import get_scope
            assert get_scope() == "myproject"

    def test_returns_none_when_scope_missing(self, tmp_path: Path) -> None:
        """get_scope() returns None when scope is absent from config.yaml."""
        config = {"server": {"enabled": True}}
        config_path = tmp_path / ".octopoid" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(yaml.dump(config))

        with patch("orchestrator.config.find_parent_project", return_value=tmp_path):
            from orchestrator.config import get_scope
            assert get_scope() is None

    def test_returns_none_when_config_missing(self, tmp_path: Path) -> None:
        """get_scope() returns None when config.yaml does not exist."""
        with patch("orchestrator.config.find_parent_project", return_value=tmp_path):
            from orchestrator.config import get_scope
            assert get_scope() is None

    def test_scope_coerced_to_string(self, tmp_path: Path) -> None:
        """get_scope() coerces non-string values to str."""
        config = {"scope": 42}
        config_path = tmp_path / ".octopoid" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(yaml.dump(config))

        with patch("orchestrator.config.find_parent_project", return_value=tmp_path):
            from orchestrator.config import get_scope
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


class TestSchedulerScopeValidation:
    """Tests for scheduler startup scope validation."""

    def test_scheduler_exits_when_scope_missing(self) -> None:
        """run_scheduler() exits with code 1 if scope is not configured."""
        from orchestrator.scheduler import run_scheduler

        with (
            patch("orchestrator.scheduler.is_system_paused", return_value=False),
            patch("orchestrator.scheduler.get_scope", return_value=None),
            pytest.raises(SystemExit) as exc_info,
        ):
            run_scheduler()

        assert exc_info.value.code == 1

    def test_scheduler_proceeds_when_scope_set(self) -> None:
        """run_scheduler() does not exit when scope is configured."""
        # run_due_jobs is imported locally inside run_scheduler(), so patch it at source
        with (
            patch("orchestrator.scheduler.is_system_paused", return_value=False),
            patch("orchestrator.scheduler.get_scope", return_value="octopoid"),
            patch("orchestrator.scheduler.load_scheduler_state", return_value={}),
            patch("orchestrator.jobs.run_due_jobs"),
            patch("orchestrator.scheduler.save_scheduler_state"),
        ):
            from orchestrator.scheduler import run_scheduler
            # Should not raise SystemExit
            run_scheduler()
