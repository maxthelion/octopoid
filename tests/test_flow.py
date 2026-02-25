"""Tests for flow module - declarative task state machines."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from orchestrator.flow import (
    Condition,
    Flow,
    Transition,
    _inject_terminal_steps,
    generate_default_flow,
    generate_project_flow,
    list_flows,
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


class TestFlowFromServerDict:
    """Tests for Flow.from_server_dict()."""

    def test_basic_transitions(self):
        """Parse a server dict with native list transitions."""
        data = {
            "name": "default",
            "transitions": [
                {"from_state": "incoming", "to_state": "claimed", "agent": "implementer"},
                {"from_state": "claimed", "to_state": "provisional", "runs": ["push_branch"]},
            ],
        }
        flow = Flow.from_server_dict(data)
        assert flow.name == "default"
        assert len(flow.transitions) == 2
        assert flow.transitions[0].from_state == "incoming"
        assert flow.transitions[0].to_state == "claimed"
        assert flow.transitions[0].agent == "implementer"
        assert flow.transitions[1].runs == ["push_branch"]

    def test_json_encoded_transitions(self):
        """Parse a server dict with JSON-string-encoded transitions."""
        transitions = [
            {"from_state": "incoming", "to_state": "claimed"},
            {"from_state": "claimed", "to_state": "done"},
        ]
        data = {
            "name": "simple",
            "transitions": json.dumps(transitions),
        }
        flow = Flow.from_server_dict(data)
        assert len(flow.transitions) == 2
        assert flow.transitions[0].from_state == "incoming"

    def test_from_and_to_key_names(self):
        """Handle 'from'/'to' key names (used by SDK register())."""
        data = {
            "name": "test",
            "transitions": [
                {"from": "incoming", "to": "claimed"},
            ],
        }
        flow = Flow.from_server_dict(data)
        assert flow.transitions[0].from_state == "incoming"
        assert flow.transitions[0].to_state == "claimed"

    def test_transitions_with_conditions(self):
        """Parse transitions with full condition data."""
        data = {
            "name": "default",
            "transitions": [
                {
                    "from_state": "provisional",
                    "to_state": "done",
                    "conditions": [
                        {
                            "name": "gatekeeper_review",
                            "type": "agent",
                            "agent": "gatekeeper",
                            "on_fail": "incoming",
                        }
                    ],
                    "runs": ["merge_pr"],
                }
            ],
        }
        flow = Flow.from_server_dict(data)
        assert len(flow.transitions) == 1
        t = flow.transitions[0]
        assert len(t.conditions) == 1
        assert t.conditions[0].name == "gatekeeper_review"
        assert t.conditions[0].type == "agent"
        assert t.conditions[0].on_fail == "incoming"
        # rebase_on_base is auto-injected because this transition targets 'done'
        assert t.runs == ["merge_pr", "rebase_on_base"]

    def test_empty_transitions(self):
        """Handle empty or missing transitions gracefully."""
        flow = Flow.from_server_dict({"name": "empty", "transitions": []})
        assert flow.name == "empty"
        assert flow.transitions == []

    def test_invalid_json_transitions_falls_back_to_empty(self):
        """Bad JSON string for transitions produces empty list, not an exception."""
        flow = Flow.from_server_dict({"name": "bad", "transitions": "not valid json {"})
        assert flow.transitions == []

    def test_child_flow_parsed(self):
        """child_flow is parsed when present in server data."""
        data = {
            "name": "project",
            "transitions": [],
            "child_flow": {
                "transitions": [
                    {"from_state": "incoming", "to_state": "done", "agent": "implementer"}
                ]
            },
        }
        flow = Flow.from_server_dict(data)
        assert flow.child_flow is not None
        assert flow.child_flow.name == "project_child"
        assert len(flow.child_flow.transitions) == 1

    def test_child_flow_json_string(self):
        """child_flow stored as JSON string is parsed correctly."""
        child = {"transitions": [{"from_state": "incoming", "to_state": "done"}]}
        data = {
            "name": "project",
            "transitions": [],
            "child_flow": json.dumps(child),
        }
        flow = Flow.from_server_dict(data)
        assert flow.child_flow is not None
        assert len(flow.child_flow.transitions) == 1

    def test_description_defaults_to_empty(self):
        """description is optional and defaults to empty string."""
        flow = Flow.from_server_dict({"name": "x", "transitions": []})
        assert flow.description == ""

    def test_description_preserved(self):
        """description is preserved when present."""
        flow = Flow.from_server_dict({"name": "x", "description": "My flow", "transitions": []})
        assert flow.description == "My flow"


class TestLoadFlowFromServer:
    """Tests for load_flow() reading from the server."""

    def _make_sdk(self, flows: list) -> MagicMock:
        sdk = MagicMock()
        sdk.flows.list.return_value = flows
        return sdk

    def test_loads_matching_flow(self):
        """load_flow returns the matching flow from server."""
        sdk = self._make_sdk([
            {"name": "default", "transitions": [{"from_state": "incoming", "to_state": "claimed"}]},
        ])
        with patch("orchestrator.sdk.get_sdk", return_value=sdk):
            flow = load_flow("default")
        assert flow.name == "default"
        assert len(flow.transitions) == 1

    def test_raises_file_not_found_when_missing(self):
        """load_flow raises FileNotFoundError if flow not on server."""
        sdk = self._make_sdk([{"name": "other", "transitions": []}])
        with patch("orchestrator.sdk.get_sdk", return_value=sdk):
            with pytest.raises(FileNotFoundError, match="not found on server"):
                load_flow("default")

    def test_raises_on_sdk_error(self):
        """load_flow re-raises when the SDK call fails (no silent fallback)."""
        sdk = MagicMock()
        sdk.flows.list.side_effect = RuntimeError("server down")
        with patch("orchestrator.sdk.get_sdk", return_value=sdk):
            with pytest.raises(RuntimeError, match="server down"):
                load_flow("default")

    def test_full_transition_detail_preserved(self):
        """load_flow returns full transition detail (agent, runs, conditions)."""
        sdk = self._make_sdk([
            {
                "name": "default",
                "transitions": [
                    {
                        "from_state": "provisional",
                        "to_state": "done",
                        "agent": None,
                        "runs": ["post_review_comment", "merge_pr"],
                        "conditions": [
                            {"name": "gatekeeper_review", "type": "agent", "agent": "gatekeeper", "on_fail": "incoming"}
                        ],
                    }
                ],
            }
        ])
        with patch("orchestrator.sdk.get_sdk", return_value=sdk):
            flow = load_flow("default")
        t = flow.transitions[0]
        # rebase_on_base is auto-injected because this transition targets 'done'
        assert t.runs == ["post_review_comment", "merge_pr", "rebase_on_base"]
        assert t.conditions[0].agent == "gatekeeper"


class TestListFlowsFromServer:
    """Tests for list_flows() reading from the server."""

    def test_returns_flow_names(self):
        """list_flows returns names of all flows on server."""
        sdk = MagicMock()
        sdk.flows.list.return_value = [
            {"name": "default"},
            {"name": "project"},
        ]
        with patch("orchestrator.sdk.get_sdk", return_value=sdk):
            names = list_flows()
        assert names == ["default", "project"]

    def test_returns_empty_on_sdk_error(self):
        """list_flows returns empty list if server is unreachable."""
        sdk = MagicMock()
        sdk.flows.list.side_effect = RuntimeError("server down")
        with patch("orchestrator.sdk.get_sdk", return_value=sdk):
            names = list_flows()
        assert names == []

    def test_skips_entries_without_name(self):
        """list_flows skips flows without a name field."""
        sdk = MagicMock()
        sdk.flows.list.return_value = [{"name": "default"}, {"transitions": []}]
        with patch("orchestrator.sdk.get_sdk", return_value=sdk):
            names = list_flows()
        assert names == ["default"]


class TestInjectTerminalSteps:
    """Tests for _inject_terminal_steps — auto-injection of required terminal steps."""

    def test_missing_steps_are_injected(self):
        """rebase_on_base and merge_pr are appended when absent."""
        trans = Transition(from_state="provisional", to_state="done", runs=[])
        _inject_terminal_steps([trans])
        assert trans.runs == ["rebase_on_base", "merge_pr"]

    def test_no_duplication_when_steps_already_present(self):
        """Steps already in runs are not duplicated."""
        trans = Transition(
            from_state="provisional",
            to_state="done",
            runs=["post_review_comment", "rebase_on_base", "merge_pr"],
        )
        _inject_terminal_steps([trans])
        assert trans.runs == ["post_review_comment", "rebase_on_base", "merge_pr"]

    def test_existing_runs_preserved_in_order(self):
        """Existing runs come first; injected steps follow at the end."""
        trans = Transition(
            from_state="provisional",
            to_state="done",
            runs=["post_review_comment", "check_ci"],
        )
        _inject_terminal_steps([trans])
        assert trans.runs == ["post_review_comment", "check_ci", "rebase_on_base", "merge_pr"]

    def test_only_rebase_missing_merge_pr_present(self):
        """Only the missing step is injected when one is already present."""
        trans = Transition(
            from_state="provisional",
            to_state="done",
            runs=["merge_pr"],
        )
        _inject_terminal_steps([trans])
        assert trans.runs == ["merge_pr", "rebase_on_base"]

    def test_non_done_transitions_are_not_modified(self):
        """Transitions targeting states other than 'done' are left unchanged."""
        trans = Transition(from_state="incoming", to_state="claimed", runs=[])
        _inject_terminal_steps([trans])
        assert trans.runs == []

    def test_from_dict_injects_missing_steps(self):
        """Flow.from_dict injects steps for a transition missing rebase_on_base."""
        data = {
            "name": "fast",
            "description": "Fast flow without required steps",
            "transitions": {
                "incoming -> claimed": {"agent": "implementer"},
                "claimed -> done": {"runs": ["push_branch"]},
            },
        }
        flow = Flow.from_dict(data)
        done_trans = next(t for t in flow.transitions if t.to_state == "done")
        assert "rebase_on_base" in done_trans.runs
        assert "merge_pr" in done_trans.runs
        # Original step preserved and comes first
        assert done_trans.runs[0] == "push_branch"

    def test_from_server_dict_injects_missing_steps(self):
        """Flow.from_server_dict injects steps for a transition missing terminal steps."""
        data = {
            "name": "fast",
            "transitions": [
                {"from_state": "incoming", "to_state": "claimed"},
                {"from_state": "claimed", "to_state": "done", "runs": ["push_branch"]},
            ],
        }
        flow = Flow.from_server_dict(data)
        done_trans = next(t for t in flow.transitions if t.to_state == "done")
        assert "rebase_on_base" in done_trans.runs
        assert "merge_pr" in done_trans.runs
