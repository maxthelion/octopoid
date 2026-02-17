"""Flow definition and validation for declarative task state machines.

A flow defines how tasks move through the system as a conditional state machine.
Transitions between states have conditions (gates) and actions (runs).

Flow files are YAML stored in .octopoid/flows/
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from .config import get_agents, get_orchestrator_dir


ConditionType = Literal["script", "agent", "manual"]


@dataclass
class Condition:
    """A gate that must pass before a transition completes."""

    name: str
    type: ConditionType
    script: str | None = None  # For type=script: script name to run
    agent: str | None = None  # For type=agent: agent role to spawn
    on_fail: str | None = None  # State to transition to if condition fails
    skip: bool = False  # For task overrides: skip this condition

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Condition":
        """Create a Condition from a dict loaded from YAML."""
        return cls(
            name=data["name"],
            type=data["type"],
            script=data.get("script"),
            agent=data.get("agent"),
            on_fail=data.get("on_fail"),
            skip=data.get("skip", False),
        )

    def validate(self, flow_name: str, transition: str) -> list[str]:
        """Validate this condition.

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        # Type-specific validation
        if self.type == "script":
            if not self.script:
                errors.append(
                    f"Flow '{flow_name}' transition '{transition}' condition '{self.name}': "
                    f"script conditions must specify 'script'"
                )
        elif self.type == "agent":
            if not self.agent:
                errors.append(
                    f"Flow '{flow_name}' transition '{transition}' condition '{self.name}': "
                    f"agent conditions must specify 'agent'"
                )
            else:
                # Check agent exists (skip if agents.yaml not found - e.g. in tests)
                try:
                    agents = get_agents()
                    agent_names = [a["name"] for a in agents]
                    if self.agent not in agent_names:
                        errors.append(
                            f"Flow '{flow_name}' transition '{transition}' condition '{self.name}': "
                            f"agent '{self.agent}' not found in agents.yaml"
                        )
                except FileNotFoundError:
                    # agents.yaml not found - skip validation (e.g. in tests)
                    pass
        elif self.type == "manual":
            # Manual conditions don't need additional fields
            pass
        else:
            errors.append(
                f"Flow '{flow_name}' transition '{transition}' condition '{self.name}': "
                f"invalid condition type '{self.type}' (must be script, agent, or manual)"
            )

        return errors


@dataclass
class Transition:
    """A transition from one state to another."""

    from_state: str
    to_state: str
    agent: str | None = None  # Agent role that handles work in from_state
    runs: list[str] = field(default_factory=list)  # Scripts to run during transition
    conditions: list[Condition] = field(default_factory=list)  # Gates that must pass

    @classmethod
    def from_dict(cls, key: str, data: dict[str, Any]) -> "Transition":
        """Create a Transition from a dict loaded from YAML.

        Args:
            key: Transition key in format "state1 -> state2"
            data: Transition configuration
        """
        # Parse "state1 -> state2"
        parts = key.split("->")
        if len(parts) != 2:
            raise ValueError(f"Invalid transition key: {key} (must be 'state1 -> state2')")

        from_state = parts[0].strip()
        to_state = parts[1].strip()

        # Parse conditions
        conditions = []
        for cond_data in data.get("conditions", []):
            conditions.append(Condition.from_dict(cond_data))

        return cls(
            from_state=from_state,
            to_state=to_state,
            agent=data.get("agent"),
            runs=data.get("runs", []),
            conditions=conditions,
        )

    def validate(self, flow_name: str, valid_states: set[str]) -> list[str]:
        """Validate this transition.

        Args:
            flow_name: Name of the flow (for error messages)
            valid_states: Set of all valid state names in the flow

        Returns:
            List of error messages (empty if valid)
        """
        errors = []
        transition_key = f"{self.from_state} -> {self.to_state}"

        # Validate agent exists if specified (skip if agents.yaml not found - e.g. in tests)
        if self.agent:
            try:
                agents = get_agents()
                # Support both blueprint_name (pool model) and legacy name field
                agent_names = [a.get("blueprint_name") or a.get("name") for a in agents]
                agent_roles = [a.get("role") for a in agents]
                if self.agent not in agent_names and self.agent not in agent_roles:
                    errors.append(
                        f"Flow '{flow_name}' transition '{transition_key}': "
                        f"agent '{self.agent}' not found in agents.yaml (by name or role)"
                    )
            except FileNotFoundError:
                # agents.yaml not found - skip validation (e.g. in tests)
                pass

        # Validate runs reference existing scripts
        # Note: We can't validate scripts exist here because we don't know which
        # agent they belong to. Script validation happens at runtime.

        # Validate conditions
        for condition in self.conditions:
            errors.extend(condition.validate(flow_name, transition_key))

            # Validate on_fail targets
            if condition.on_fail and condition.on_fail not in valid_states:
                errors.append(
                    f"Flow '{flow_name}' transition '{transition_key}' condition '{condition.name}': "
                    f"on_fail state '{condition.on_fail}' is not a valid state"
                )

        return errors


@dataclass
class Flow:
    """A declarative task flow - conditional state machine."""

    name: str
    description: str
    transitions: list[Transition]
    child_flow: "Flow | None" = None  # For projects: flow applied to child tasks

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Flow":
        """Create a Flow from a dict loaded from YAML."""
        # Parse transitions
        transitions = []
        for key, trans_data in data.get("transitions", {}).items():
            transitions.append(Transition.from_dict(key, trans_data))

        # Parse child_flow if present
        child_flow = None
        if "child_flow" in data:
            child_flow_data = {
                "name": f"{data['name']}_child",
                "description": f"Child flow for {data['name']}",
                "transitions": data["child_flow"].get("transitions", {}),
            }
            child_flow = cls.from_dict(child_flow_data)

        return cls(
            name=data["name"],
            description=data.get("description", ""),
            transitions=transitions,
            child_flow=child_flow,
        )

    @classmethod
    def from_yaml_file(cls, path: Path) -> "Flow":
        """Load a flow from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    def get_all_states(self) -> set[str]:
        """Get all states referenced in this flow."""
        states = set()
        for trans in self.transitions:
            states.add(trans.from_state)
            states.add(trans.to_state)
            # Also add states referenced in on_fail
            for cond in trans.conditions:
                if cond.on_fail:
                    states.add(cond.on_fail)
        return states

    def get_transitions_from(self, state: str) -> list[Transition]:
        """Get all transitions starting from a given state."""
        return [t for t in self.transitions if t.from_state == state]

    def validate(self) -> list[str]:
        """Validate this flow.

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        # Get all valid states
        valid_states = self.get_all_states()

        # Validate each transition
        for trans in self.transitions:
            errors.extend(trans.validate(self.name, valid_states))

        # Check for unreachable states (except 'incoming' which is the entry point)
        reachable = {"incoming"}  # Start from incoming
        changed = True
        while changed:
            changed = False
            for trans in self.transitions:
                if trans.from_state in reachable and trans.to_state not in reachable:
                    reachable.add(trans.to_state)
                    changed = True

        unreachable = valid_states - reachable
        # Filter out terminal states that are only reached via on_fail
        terminal_states = {"done", "failed", "rejected"}
        unreachable = unreachable - terminal_states

        if unreachable:
            errors.append(
                f"Flow '{self.name}' has unreachable states: {', '.join(sorted(unreachable))}"
            )

        # Validate child flow if present
        if self.child_flow:
            child_errors = self.child_flow.validate()
            errors.extend([f"Child flow: {err}" for err in child_errors])

        return errors


def load_flow(flow_name: str) -> Flow:
    """Load a flow by name from .octopoid/flows/

    Args:
        flow_name: Name of the flow (without .yaml extension)

    Returns:
        Flow object

    Raises:
        FileNotFoundError: If flow file doesn't exist
        yaml.YAMLError: If YAML is invalid
        ValueError: If flow structure is invalid
    """
    flows_dir = get_orchestrator_dir() / "flows"
    flow_path = flows_dir / f"{flow_name}.yaml"

    if not flow_path.exists():
        raise FileNotFoundError(
            f"Flow '{flow_name}' not found at {flow_path}. "
            f"Available flows: {list_flows()}"
        )

    return Flow.from_yaml_file(flow_path)


def list_flows() -> list[str]:
    """List all available flow names."""
    flows_dir = get_orchestrator_dir() / "flows"
    if not flows_dir.exists():
        return []

    return [f.stem for f in flows_dir.glob("*.yaml")]


def validate_flow_file(flow_path: Path) -> tuple[Flow | None, list[str]]:
    """Validate a flow file.

    Args:
        flow_path: Path to flow YAML file

    Returns:
        Tuple of (Flow object if valid, list of error messages)
    """
    errors = []

    try:
        flow = Flow.from_yaml_file(flow_path)
    except yaml.YAMLError as e:
        errors.append(f"Invalid YAML: {e}")
        return None, errors
    except (ValueError, KeyError) as e:
        errors.append(f"Invalid flow structure: {e}")
        return None, errors

    # Validate flow logic
    errors.extend(flow.validate())

    return flow, errors


def get_default_flow_name() -> str:
    """Get the default flow name for tasks."""
    # Read from config or use 'default'
    return "default"


def generate_default_flow() -> str:
    """Generate the default flow YAML content.

    This matches the current hardcoded behavior:
    - incoming → claimed: implementer agent claims
    - claimed → provisional: runs rebase_on_main, run_tests, create_pr
    - provisional → done: human approval, runs merge_pr
    """
    return """name: default
description: Standard implementation with review

transitions:
  "incoming -> claimed":
    agent: implementer

  "claimed -> provisional":
    runs: [rebase_on_main, run_tests, create_pr]

  "provisional -> done":
    conditions:
      - name: human_approval
        type: manual
    runs: [merge_pr]
"""


def generate_project_flow() -> str:
    """Generate the project flow YAML content.

    For projects that coordinate multiple child tasks on a shared branch.
    """
    return """name: project
description: Multi-task project with shared branch

# Flow applied to child tasks within this project.
# Children don't create PRs — the project creates one PR at the end.
child_flow:
  transitions:
    "incoming -> claimed":
      agent: implementer

    "claimed -> done":
      runs: [rebase_on_project_branch, run_tests]
      # No create_pr — children commit to the shared branch

# Flow for the project itself, after all children complete.
transitions:
  "children_complete -> provisional":
    runs: [create_pr]
    conditions:
      - name: all_tests_pass
        type: script
        script: run-tests

  "provisional -> done":
    conditions:
      - name: human_approval
        type: manual
    runs: [merge_pr]
"""


def create_flows_directory() -> None:
    """Create .octopoid/flows/ directory and generate default flows."""
    flows_dir = get_orchestrator_dir() / "flows"
    flows_dir.mkdir(parents=True, exist_ok=True)

    # Generate default.yaml if it doesn't exist
    default_flow_path = flows_dir / "default.yaml"
    if not default_flow_path.exists():
        default_flow_path.write_text(generate_default_flow())

    # Generate project.yaml if it doesn't exist
    project_flow_path = flows_dir / "project.yaml"
    if not project_flow_path.exists():
        project_flow_path.write_text(generate_project_flow())
