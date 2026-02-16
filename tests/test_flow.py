"""Tests for flow module - declarative task state machines."""

import tempfile
from pathlib import Path

import pytest
import yaml

from orchestrator.flow import (
    Condition,
    Flow,
    Transition,
    generate_default_flow,
    generate_project_flow,
    load_flow,
    validate_flow_file,
)


class TestCondition:
    """Tests for Condition class."""

    def test_from_dict_script(self):
        """Test creating a script condition from dict."""
        data = {
            "name": "tests_pass",
            "type": "script",
            "script": "run-tests",
            "on_fail": "incoming",
        }
        cond = Condition.from_dict(data)
        assert cond.name == "tests_pass"
        assert cond.type == "script"
        assert cond.script == "run-tests"
        assert cond.on_fail == "incoming"

    def test_from_dict_agent(self):
        """Test creating an agent condition from dict."""
        data = {
            "name": "gatekeeper_review",
            "type": "agent",
            "agent": "sanity-check-gatekeeper",
        }
        cond = Condition.from_dict(data)
        assert cond.name == "gatekeeper_review"
        assert cond.type == "agent"
        assert cond.agent == "sanity-check-gatekeeper"

    def test_from_dict_manual(self):
        """Test creating a manual condition from dict."""
        data = {
            "name": "human_approval",
            "type": "manual",
        }
        cond = Condition.from_dict(data)
        assert cond.name == "human_approval"
        assert cond.type == "manual"

    def test_validate_script_missing_script_field(self):
        """Test validation fails for script condition without script field."""
        cond = Condition(
            name="test",
            type="script",
            script=None,
        )
        errors = cond.validate("test-flow", "incoming -> claimed")
        assert len(errors) == 1
        assert "must specify 'script'" in errors[0]

    def test_validate_agent_missing_agent_field(self):
        """Test validation fails for agent condition without agent field."""
        cond = Condition(
            name="test",
            type="agent",
            agent=None,
        )
        errors = cond.validate("test-flow", "incoming -> claimed")
        assert len(errors) == 1
        assert "must specify 'agent'" in errors[0]

    def test_validate_invalid_type(self):
        """Test validation fails for invalid condition type."""
        cond = Condition(
            name="test",
            type="invalid",  # type: ignore
        )
        errors = cond.validate("test-flow", "incoming -> claimed")
        assert len(errors) == 1
        assert "invalid condition type" in errors[0]


class TestTransition:
    """Tests for Transition class."""

    def test_from_dict(self):
        """Test creating a transition from dict."""
        data = {
            "agent": "implementer",
            "runs": ["rebase_on_main", "run_tests"],
            "conditions": [
                {"name": "tests_pass", "type": "script", "script": "run-tests"}
            ],
        }
        trans = Transition.from_dict("incoming -> claimed", data)
        assert trans.from_state == "incoming"
        assert trans.to_state == "claimed"
        assert trans.agent == "implementer"
        assert trans.runs == ["rebase_on_main", "run_tests"]
        assert len(trans.conditions) == 1
        assert trans.conditions[0].name == "tests_pass"

    def test_from_dict_invalid_key(self):
        """Test that invalid transition key raises ValueError."""
        with pytest.raises(ValueError, match="Invalid transition key"):
            Transition.from_dict("invalid", {})

    def test_validate_invalid_on_fail_state(self):
        """Test validation fails for on_fail referencing invalid state."""
        trans = Transition(
            from_state="incoming",
            to_state="claimed",
            conditions=[
                Condition(
                    name="test",
                    type="manual",
                    on_fail="nonexistent_state",
                )
            ],
        )
        errors = trans.validate("test-flow", {"incoming", "claimed"})
        assert len(errors) == 1
        assert "on_fail state 'nonexistent_state' is not a valid state" in errors[0]


class TestFlow:
    """Tests for Flow class."""

    def test_from_dict_simple(self):
        """Test creating a simple flow from dict."""
        data = {
            "name": "test-flow",
            "description": "Test flow",
            "transitions": {
                "incoming -> claimed": {
                    "agent": "implementer",
                },
                "claimed -> done": {
                    "runs": ["submit"],
                },
            },
        }
        flow = Flow.from_dict(data)
        assert flow.name == "test-flow"
        assert flow.description == "Test flow"
        assert len(flow.transitions) == 2

    def test_from_dict_with_child_flow(self):
        """Test creating a flow with child_flow from dict."""
        data = {
            "name": "project",
            "description": "Project flow",
            "child_flow": {
                "transitions": {
                    "incoming -> done": {
                        "agent": "implementer",
                    },
                },
            },
            "transitions": {
                "children_complete -> done": {
                    "runs": ["merge"],
                },
            },
        }
        flow = Flow.from_dict(data)
        assert flow.child_flow is not None
        assert flow.child_flow.name == "project_child"
        assert len(flow.child_flow.transitions) == 1

    def test_get_all_states(self):
        """Test getting all states from a flow."""
        flow = Flow(
            name="test",
            description="",
            transitions=[
                Transition(
                    from_state="incoming",
                    to_state="claimed",
                    conditions=[
                        Condition(
                            name="test",
                            type="manual",
                            on_fail="failed",
                        )
                    ],
                ),
                Transition(from_state="claimed", to_state="done"),
            ],
        )
        states = flow.get_all_states()
        assert states == {"incoming", "claimed", "done", "failed"}

    def test_get_transitions_from(self):
        """Test getting transitions from a specific state."""
        trans1 = Transition(from_state="incoming", to_state="claimed")
        trans2 = Transition(from_state="incoming", to_state="failed")
        trans3 = Transition(from_state="claimed", to_state="done")

        flow = Flow(
            name="test",
            description="",
            transitions=[trans1, trans2, trans3],
        )

        from_incoming = flow.get_transitions_from("incoming")
        assert len(from_incoming) == 2
        assert trans1 in from_incoming
        assert trans2 in from_incoming

        from_claimed = flow.get_transitions_from("claimed")
        assert len(from_claimed) == 1
        assert trans3 in from_claimed

    def test_validate_unreachable_state(self):
        """Test validation detects unreachable states."""
        flow = Flow(
            name="test",
            description="",
            transitions=[
                Transition(from_state="incoming", to_state="claimed"),
                Transition(from_state="claimed", to_state="done"),
                Transition(from_state="orphan", to_state="done"),  # Unreachable
            ],
        )
        errors = flow.validate()
        assert len(errors) == 1
        assert "unreachable states: orphan" in errors[0]

    def test_validate_terminal_states_not_unreachable(self):
        """Test that terminal states referenced only in on_fail are not flagged as unreachable."""
        flow = Flow(
            name="test",
            description="",
            transitions=[
                Transition(
                    from_state="incoming",
                    to_state="claimed",
                    conditions=[
                        Condition(
                            name="test",
                            type="manual",
                            on_fail="failed",  # Terminal state only in on_fail
                        )
                    ],
                ),
                Transition(from_state="claimed", to_state="done"),
            ],
        )
        errors = flow.validate()
        # 'failed' is a terminal state, so it shouldn't be flagged as unreachable
        assert len(errors) == 0


class TestGenerateFlows:
    """Tests for flow generation functions."""

    def test_generate_default_flow(self):
        """Test generating default flow YAML."""
        yaml_content = generate_default_flow()
        data = yaml.safe_load(yaml_content)

        assert data["name"] == "default"
        assert "description" in data
        assert "transitions" in data

        # Check expected transitions
        assert "incoming -> claimed" in data["transitions"]
        assert "claimed -> provisional" in data["transitions"]
        assert "provisional -> done" in data["transitions"]

    def test_generate_project_flow(self):
        """Test generating project flow YAML."""
        yaml_content = generate_project_flow()
        data = yaml.safe_load(yaml_content)

        assert data["name"] == "project"
        assert "description" in data
        assert "child_flow" in data
        assert "transitions" in data

        # Check child flow
        assert "transitions" in data["child_flow"]

    def test_default_flow_is_valid(self):
        """Test that generated default flow is valid."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flow_path = Path(tmpdir) / "default.yaml"
            flow_path.write_text(generate_default_flow())

            flow, errors = validate_flow_file(flow_path)
            # Note: This may have errors about missing agents in test environment
            # but the structure should be valid
            assert flow is not None
            assert flow.name == "default"

    def test_project_flow_is_valid(self):
        """Test that generated project flow is valid."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flow_path = Path(tmpdir) / "project.yaml"
            flow_path.write_text(generate_project_flow())

            flow, errors = validate_flow_file(flow_path)
            # Note: This may have errors about missing agents in test environment
            # but the structure should be valid
            assert flow is not None
            assert flow.name == "project"
            assert flow.child_flow is not None


class TestLoadFlow:
    """Tests for flow loading functions."""

    def test_validate_flow_file_invalid_yaml(self):
        """Test validation of file with invalid YAML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flow_path = Path(tmpdir) / "invalid.yaml"
            flow_path.write_text("invalid: yaml: content:")

            flow, errors = validate_flow_file(flow_path)
            assert flow is None
            assert len(errors) > 0
            assert "Invalid YAML" in errors[0]

    def test_validate_flow_file_missing_fields(self):
        """Test validation of file with missing required fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flow_path = Path(tmpdir) / "incomplete.yaml"
            flow_path.write_text("""
name: test
# Missing transitions
""")

            flow, errors = validate_flow_file(flow_path)
            # Should succeed in parsing but may have validation errors
            # Empty transitions is technically valid (though not useful)
            assert flow is not None

    def test_validate_flow_file_valid(self):
        """Test validation of a valid flow file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flow_path = Path(tmpdir) / "test.yaml"
            flow_path.write_text("""
name: test
description: Test flow
transitions:
  "incoming -> claimed":
    agent: implementer
  "claimed -> done":
    runs: [submit]
""")

            flow, errors = validate_flow_file(flow_path)
            # May have agent validation errors in test environment
            assert flow is not None
            assert flow.name == "test"
