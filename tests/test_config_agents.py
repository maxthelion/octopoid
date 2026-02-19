"""Tests for get_agents() with both the new agents-dict and legacy fleet-list formats."""

import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal fake project with .octopoid structure."""
    octopoid = tmp_path / ".octopoid"
    octopoid.mkdir()
    return tmp_path


def _write_agents_yaml(project: Path, content: dict) -> None:
    (project / ".octopoid" / "agents.yaml").write_text(yaml.dump(content))


def _patch_project(project: Path):
    """Context manager that patches config functions to use tmp_project."""
    return patch("orchestrator.config.find_parent_project", return_value=project)


class TestGetAgentsNewFormat:
    """Tests for the new agents-dict format."""

    def test_basic_dict_format(self, tmp_project):
        _write_agents_yaml(tmp_project, {
            "agents": {
                "implementer": {
                    "type": "implementer",
                    "max_instances": 2,
                    "interval_seconds": 60,
                    "model": "sonnet",
                }
            }
        })

        with _patch_project(tmp_project):
            from orchestrator.config import get_agents
            agents = get_agents()

        assert len(agents) == 1
        agent = agents[0]
        assert agent["blueprint_name"] == "implementer"
        assert agent["name"] == "implementer"
        assert agent["max_instances"] == 2
        assert agent["interval_seconds"] == 60
        assert agent["model"] == "sonnet"

    def test_blueprint_name_key_injected(self, tmp_project):
        _write_agents_yaml(tmp_project, {
            "agents": {
                "my-agent": {
                    "type": "implementer",
                }
            }
        })

        with _patch_project(tmp_project):
            from orchestrator.config import get_agents
            agents = get_agents()

        assert agents[0]["blueprint_name"] == "my-agent"

    def test_max_instances_defaults_to_1(self, tmp_project):
        _write_agents_yaml(tmp_project, {
            "agents": {
                "implementer": {
                    "type": "implementer",
                }
            }
        })

        with _patch_project(tmp_project):
            from orchestrator.config import get_agents
            agents = get_agents()

        assert agents[0]["max_instances"] == 1

    def test_name_defaults_to_blueprint_name(self, tmp_project):
        _write_agents_yaml(tmp_project, {
            "agents": {
                "my-blueprint": {
                    "type": "implementer",
                }
            }
        })

        with _patch_project(tmp_project):
            from orchestrator.config import get_agents
            agents = get_agents()

        assert agents[0]["name"] == "my-blueprint"

    def test_explicit_name_preserved(self, tmp_project):
        _write_agents_yaml(tmp_project, {
            "agents": {
                "implementer": {
                    "type": "implementer",
                    "name": "custom-name",
                }
            }
        })

        with _patch_project(tmp_project):
            from orchestrator.config import get_agents
            agents = get_agents()

        assert agents[0]["name"] == "custom-name"
        assert agents[0]["blueprint_name"] == "implementer"

    def test_multiple_blueprints(self, tmp_project):
        _write_agents_yaml(tmp_project, {
            "agents": {
                "implementer": {
                    "type": "implementer",
                    "max_instances": 3,
                },
                "monitor": {
                    "type": "custom",
                    "path": "/some/path",
                    "max_instances": 1,
                },
            }
        })

        with _patch_project(tmp_project):
            from orchestrator.config import get_agents
            agents = get_agents()

        assert len(agents) == 2
        names = {a["blueprint_name"] for a in agents}
        assert names == {"implementer", "monitor"}

    def test_empty_agents_dict_returns_empty(self, tmp_project):
        _write_agents_yaml(tmp_project, {"agents": {}})

        with _patch_project(tmp_project):
            from orchestrator.config import get_agents
            agents = get_agents()

        assert agents == []

    def test_paused_agent_skipped(self, tmp_project):
        _write_agents_yaml(tmp_project, {
            "agents": {
                "implementer": {"type": "implementer"},
                "paused-one": {"type": "implementer", "paused": True, "enabled": False},
            }
        })

        with _patch_project(tmp_project):
            from orchestrator.config import get_agents
            agents = get_agents()

        blueprint_names = [a["blueprint_name"] for a in agents]
        assert "paused-one" not in blueprint_names

    def test_gatekeeper_with_agent_dir(self, tmp_project):
        # Create a fake agent_dir with agent.yaml
        agent_dir = tmp_project / ".octopoid" / "agents" / "gatekeeper"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.yaml").write_text(yaml.dump({
            "role": "gatekeeper",
            "spawn_mode": "scripts",
        }))

        _write_agents_yaml(tmp_project, {
            "agents": {
                "sanity-check-gatekeeper": {
                    "role": "gatekeeper",
                    "spawn_mode": "scripts",
                    "agent_dir": ".octopoid/agents/gatekeeper",
                    "interval_seconds": 120,
                    "max_turns": 50,
                    "model": "sonnet",
                    "max_instances": 1,
                }
            }
        })

        with _patch_project(tmp_project):
            from orchestrator.config import get_agents
            agents = get_agents()

        assert len(agents) == 1
        agent = agents[0]
        assert agent["blueprint_name"] == "sanity-check-gatekeeper"
        assert agent["role"] == "gatekeeper"
        assert agent["spawn_mode"] == "scripts"
        assert agent["max_instances"] == 1
        assert "agent_dir" in agent


class TestGetAgentsLegacyFleetFormat:
    """Tests for backwards compatibility with the legacy fleet-list format."""

    def test_fleet_list_still_works(self, tmp_project):
        _write_agents_yaml(tmp_project, {
            "fleet": [
                {
                    "name": "implementer-1",
                    "type": "implementer",
                    "interval_seconds": 60,
                }
            ]
        })

        with _patch_project(tmp_project):
            from orchestrator.config import get_agents
            agents = get_agents()

        assert len(agents) == 1
        agent = agents[0]
        assert agent["name"] == "implementer-1"
        assert agent["blueprint_name"] == "implementer-1"
        assert agent["max_instances"] == 1

    def test_fleet_list_multiple_entries(self, tmp_project):
        _write_agents_yaml(tmp_project, {
            "fleet": [
                {"name": "implementer-1", "type": "implementer"},
                {"name": "implementer-2", "type": "implementer"},
            ]
        })

        with _patch_project(tmp_project):
            from orchestrator.config import get_agents
            agents = get_agents()

        assert len(agents) == 2
        names = {a["name"] for a in agents}
        assert names == {"implementer-1", "implementer-2"}

    def test_fleet_preferred_over_none_when_agents_not_dict(self, tmp_project):
        # agents is an empty list (old template format), fleet exists — use fleet
        _write_agents_yaml(tmp_project, {
            "agents": [],
            "fleet": [
                {"name": "implementer-1", "type": "implementer"},
            ]
        })

        with _patch_project(tmp_project):
            from orchestrator.config import get_agents
            agents = get_agents()

        # agents is not a dict, so falls back to fleet
        assert len(agents) == 1
        assert agents[0]["name"] == "implementer-1"

    def test_empty_fleet_returns_empty(self, tmp_project):
        _write_agents_yaml(tmp_project, {"fleet": []})

        with _patch_project(tmp_project):
            from orchestrator.config import get_agents
            agents = get_agents()

        assert agents == []

    def test_no_agents_no_fleet_returns_empty(self, tmp_project):
        _write_agents_yaml(tmp_project, {"queue_limits": {"max_claimed": 1}})

        with _patch_project(tmp_project):
            from orchestrator.config import get_agents
            agents = get_agents()

        assert agents == []


class TestGetAgentsFormatPriority:
    """Test that agents dict takes priority over fleet list."""

    def test_agents_dict_takes_priority_over_fleet(self, tmp_project):
        _write_agents_yaml(tmp_project, {
            "agents": {
                "my-blueprint": {"type": "implementer"},
            },
            "fleet": [
                {"name": "fleet-agent", "type": "implementer"},
            ]
        })

        with _patch_project(tmp_project):
            from orchestrator.config import get_agents
            agents = get_agents()

        blueprint_names = [a["blueprint_name"] for a in agents]
        assert "my-blueprint" in blueprint_names
        assert "fleet-agent" not in blueprint_names
