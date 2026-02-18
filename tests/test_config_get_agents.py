"""Tests for get_agents() supporting both agents dict and fleet list formats."""

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from orchestrator.config import get_agents


def _write_agents_yaml(tmp_path: Path, content: str) -> Path:
    """Write agents.yaml to a temp .octopoid dir and return the dir path."""
    octopoid_dir = tmp_path / ".octopoid"
    octopoid_dir.mkdir(parents=True, exist_ok=True)
    agents_yaml = octopoid_dir / "agents.yaml"
    agents_yaml.write_text(textwrap.dedent(content))
    return octopoid_dir


class TestGetAgentsDictFormat:
    """Tests for the new agents: dict format."""

    def test_returns_list_from_dict(self, tmp_path):
        """agents: dict produces a list of agent configs."""
        octopoid_dir = _write_agents_yaml(
            tmp_path,
            """
            agents:
              my-implementer:
                type: implementer
                interval_seconds: 60
                max_instances: 2
            """,
        )
        # Create a fake agent dir so resolution succeeds
        agent_dir = tmp_path / "packages" / "client" / "agents" / "implementer"
        agent_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch("orchestrator.config.get_orchestrator_dir", return_value=octopoid_dir),
            patch("orchestrator.config.find_parent_project", return_value=tmp_path),
        ):
            agents = get_agents()

        assert len(agents) == 1
        agent = agents[0]
        assert agent["blueprint_name"] == "my-implementer"
        assert agent["max_instances"] == 2
        assert agent["interval_seconds"] == 60

    def test_blueprint_name_set_to_dict_key(self, tmp_path):
        """Each agent entry gets blueprint_name equal to its dict key."""
        octopoid_dir = _write_agents_yaml(
            tmp_path,
            """
            agents:
              implementer:
                type: implementer
                interval_seconds: 60
            """,
        )
        agent_dir = tmp_path / "packages" / "client" / "agents" / "implementer"
        agent_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch("orchestrator.config.get_orchestrator_dir", return_value=octopoid_dir),
            patch("orchestrator.config.find_parent_project", return_value=tmp_path),
        ):
            agents = get_agents()

        assert agents[0]["blueprint_name"] == "implementer"

    def test_max_instances_defaults_to_1(self, tmp_path):
        """max_instances defaults to 1 when not specified."""
        octopoid_dir = _write_agents_yaml(
            tmp_path,
            """
            agents:
              implementer:
                type: implementer
                interval_seconds: 60
            """,
        )
        agent_dir = tmp_path / "packages" / "client" / "agents" / "implementer"
        agent_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch("orchestrator.config.get_orchestrator_dir", return_value=octopoid_dir),
            patch("orchestrator.config.find_parent_project", return_value=tmp_path),
        ):
            agents = get_agents()

        assert agents[0]["max_instances"] == 1

    def test_explicit_agent_dir_used_directly(self, tmp_path):
        """agent_dir field is respected without requiring type resolution."""
        octopoid_dir = _write_agents_yaml(
            tmp_path,
            """
            agents:
              my-gatekeeper:
                role: gatekeeper
                agent_dir: .octopoid/agents/gatekeeper
                interval_seconds: 120
                max_instances: 1
            """,
        )
        # Create the explicit agent dir
        gk_dir = tmp_path / ".octopoid" / "agents" / "gatekeeper"
        gk_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch("orchestrator.config.get_orchestrator_dir", return_value=octopoid_dir),
            patch("orchestrator.config.find_parent_project", return_value=tmp_path),
        ):
            agents = get_agents()

        assert len(agents) == 1
        assert agents[0]["blueprint_name"] == "my-gatekeeper"
        assert agents[0]["role"] == "gatekeeper"
        assert str(gk_dir) == agents[0]["agent_dir"]

    def test_disabled_agent_excluded(self, tmp_path):
        """Agents with enabled: false are excluded."""
        octopoid_dir = _write_agents_yaml(
            tmp_path,
            """
            agents:
              monitor:
                type: custom
                path: .octopoid/agents/monitor/
                enabled: false
                interval_seconds: 900
            """,
        )
        monitor_dir = tmp_path / ".octopoid" / "agents" / "monitor"
        monitor_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch("orchestrator.config.get_orchestrator_dir", return_value=octopoid_dir),
            patch("orchestrator.config.find_parent_project", return_value=tmp_path),
        ):
            agents = get_agents()

        assert agents == []

    def test_multiple_blueprints(self, tmp_path):
        """Multiple blueprints all appear in the returned list."""
        octopoid_dir = _write_agents_yaml(
            tmp_path,
            """
            agents:
              implementer:
                type: implementer
                interval_seconds: 60
                max_instances: 2
              gatekeeper:
                role: gatekeeper
                agent_dir: .octopoid/agents/gk
                interval_seconds: 120
                max_instances: 1
            """,
        )
        impl_dir = tmp_path / "packages" / "client" / "agents" / "implementer"
        impl_dir.mkdir(parents=True, exist_ok=True)
        gk_dir = tmp_path / ".octopoid" / "agents" / "gk"
        gk_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch("orchestrator.config.get_orchestrator_dir", return_value=octopoid_dir),
            patch("orchestrator.config.find_parent_project", return_value=tmp_path),
        ):
            agents = get_agents()

        names = {a["blueprint_name"] for a in agents}
        assert names == {"implementer", "gatekeeper"}

    def test_all_fields_preserved(self, tmp_path):
        """No field from the config entry is dropped."""
        octopoid_dir = _write_agents_yaml(
            tmp_path,
            """
            agents:
              sanity-check-gatekeeper:
                role: gatekeeper
                spawn_mode: scripts
                claim_from: provisional
                interval_seconds: 120
                max_turns: 100
                model: sonnet
                agent_dir: .octopoid/agents/gatekeeper
                max_instances: 1
            """,
        )
        gk_dir = tmp_path / ".octopoid" / "agents" / "gatekeeper"
        gk_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch("orchestrator.config.get_orchestrator_dir", return_value=octopoid_dir),
            patch("orchestrator.config.find_parent_project", return_value=tmp_path),
        ):
            agents = get_agents()

        assert len(agents) == 1
        a = agents[0]
        assert a["spawn_mode"] == "scripts"
        assert a["claim_from"] == "provisional"
        assert a["max_turns"] == 100
        assert a["model"] == "sonnet"


class TestGetAgentsFleetFormat:
    """Tests for backwards-compatible fleet: list format."""

    def test_fleet_format_still_works(self, tmp_path):
        """Legacy fleet: list is read when agents: dict is absent."""
        octopoid_dir = _write_agents_yaml(
            tmp_path,
            """
            fleet:
              - name: implementer-1
                type: implementer
                interval_seconds: 60
            """,
        )
        agent_dir = tmp_path / "packages" / "client" / "agents" / "implementer"
        agent_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch("orchestrator.config.get_orchestrator_dir", return_value=octopoid_dir),
            patch("orchestrator.config.find_parent_project", return_value=tmp_path),
        ):
            agents = get_agents()

        assert len(agents) == 1
        assert agents[0]["name"] == "implementer-1"
        assert agents[0]["blueprint_name"] == "implementer-1"

    def test_fleet_max_instances_defaults_to_1(self, tmp_path):
        """Fleet entries get max_instances defaulting to 1."""
        octopoid_dir = _write_agents_yaml(
            tmp_path,
            """
            fleet:
              - name: implementer-1
                type: implementer
                interval_seconds: 60
            """,
        )
        agent_dir = tmp_path / "packages" / "client" / "agents" / "implementer"
        agent_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch("orchestrator.config.get_orchestrator_dir", return_value=octopoid_dir),
            patch("orchestrator.config.find_parent_project", return_value=tmp_path),
        ):
            agents = get_agents()

        assert agents[0]["max_instances"] == 1

    def test_agents_dict_takes_precedence_over_fleet(self, tmp_path):
        """When both agents: and fleet: are present, agents: wins."""
        octopoid_dir = _write_agents_yaml(
            tmp_path,
            """
            agents:
              implementer:
                type: implementer
                interval_seconds: 60
            fleet:
              - name: old-implementer
                type: implementer
                interval_seconds: 999
            """,
        )
        agent_dir = tmp_path / "packages" / "client" / "agents" / "implementer"
        agent_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch("orchestrator.config.get_orchestrator_dir", return_value=octopoid_dir),
            patch("orchestrator.config.find_parent_project", return_value=tmp_path),
        ):
            agents = get_agents()

        assert len(agents) == 1
        assert agents[0]["blueprint_name"] == "implementer"
        assert agents[0]["interval_seconds"] == 60

    def test_empty_fleet_returns_empty_list(self, tmp_path):
        """Empty fleet config returns an empty list."""
        octopoid_dir = _write_agents_yaml(tmp_path, "fleet: []\n")

        with (
            patch("orchestrator.config.get_orchestrator_dir", return_value=octopoid_dir),
            patch("orchestrator.config.find_parent_project", return_value=tmp_path),
        ):
            agents = get_agents()

        assert agents == []
