"""Flow definition and validation for declarative task state machines.

A flow defines how tasks move through the system as a conditional state machine.
Transitions between states have conditions (gates) and actions (runs).

Flow files are YAML stored in .octopoid/flows/ and used as source files for
`octopoid sync-flows`. At runtime, flows are read from the server via the SDK.
"""

import json
import logging
import subprocess
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

    def evaluate(self, cwd: Path | None = None) -> bool:
        """Evaluate this condition.

        For type=script: runs the script as a subprocess shell command and
        returns True if the exit code is 0, False for any non-zero exit code.

        For type=agent or type=manual: raises NotImplementedError — these are
        evaluated externally by the scheduler (agent spawning or human approval).

        If skip=True, returns True immediately without running anything.

        Args:
            cwd: Working directory for script execution (optional)

        Returns:
            True if the condition passes, False if it fails

        Raises:
            NotImplementedError: For agent/manual conditions
            ValueError: If script is not set for script conditions
        """
        if self.skip:
            return True

        if self.type != "script":
            raise NotImplementedError(
                f"Condition '{self.name}' of type '{self.type}' cannot be evaluated "
                "programmatically; agent/manual conditions are handled by the scheduler"
            )

        if not self.script:
            raise ValueError(
                f"Condition '{self.name}': script type requires 'script' field"
            )

        result = subprocess.run(
            self.script,
            shell=True,
            cwd=cwd,
            capture_output=True,
        )
        return result.returncode == 0

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
                    valid_refs = set()
                    for a in agents:
                        valid_refs.add(a["name"])
                        bp = a.get("blueprint_name")
                        if bp:
                            valid_refs.add(bp)
                        role = a.get("role")
                        if role:
                            valid_refs.add(role)
                    if self.agent not in valid_refs:
                        errors.append(
                            f"Flow '{flow_name}' transition '{transition}' condition '{self.name}': "
                            f"agent '{self.agent}' not found in agents.yaml (by name, blueprint_name, or role)"
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
                valid_refs = set()
                for a in agents:
                    valid_refs.add(a["name"])
                    bp = a.get("blueprint_name")
                    if bp:
                        valid_refs.add(bp)
                    role = a.get("role")
                    if role:
                        valid_refs.add(role)
                if self.agent not in valid_refs:
                    errors.append(
                        f"Flow '{flow_name}' transition '{transition_key}': "
                        f"agent '{self.agent}' not found in agents.yaml (by name, blueprint_name, or role)"
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


_REQUIRED_TERMINAL_STEPS = ["rebase_on_base", "merge_pr"]


def _inject_terminal_steps(transitions: list["Transition"]) -> None:
    """Append required terminal steps to any transition targeting 'done'.

    Ensures rebase_on_base and merge_pr are always present in the runs list
    of transitions that target 'done', preventing individual flow YAMLs from
    accidentally omitting them. Existing entries are preserved and no
    duplication occurs.
    """
    for transition in transitions:
        if transition.to_state == "done":
            for step in _REQUIRED_TERMINAL_STEPS:
                if step not in transition.runs:
                    transition.runs.append(step)


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

        _inject_terminal_steps(transitions)

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

    @classmethod
    def from_server_dict(cls, data: dict[str, Any]) -> "Flow":
        """Create a Flow from a server response dict (e.g. from sdk.flows.list()).

        Handles JSON-encoded strings for states and transitions, and supports
        both 'from_state'/'to_state' and 'from'/'to' key names in transitions.
        Full transition detail (agent, runs, conditions) is preserved when
        present (stored by sync-flows).

        Args:
            data: Flow dict from the server, with keys 'name', 'transitions', etc.

        Returns:
            Flow object
        """
        name = data["name"]

        # Transitions may be a JSON-encoded string or a native list
        transitions_raw = data.get("transitions", [])
        if isinstance(transitions_raw, str):
            try:
                transitions_raw = json.loads(transitions_raw)
            except (ValueError, json.JSONDecodeError):
                transitions_raw = []

        transitions = []
        for t in transitions_raw:
            if not isinstance(t, dict):
                continue
            # Server may use from_state/to_state or from/to key names
            from_state = t.get("from_state") or t.get("from")
            to_state = t.get("to_state") or t.get("to")
            if not from_state or not to_state:
                continue
            conditions = [
                Condition.from_dict(c) for c in t.get("conditions", [])
            ]
            transitions.append(Transition(
                from_state=from_state,
                to_state=to_state,
                agent=t.get("agent"),
                runs=t.get("runs", []),
                conditions=conditions,
            ))

        _inject_terminal_steps(transitions)

        # child_flow — only present if sync-flows stored it on the server
        child_flow = None
        child_flow_raw = data.get("child_flow")
        if child_flow_raw:
            if isinstance(child_flow_raw, str):
                try:
                    child_flow_raw = json.loads(child_flow_raw)
                except (ValueError, json.JSONDecodeError):
                    child_flow_raw = None
            if isinstance(child_flow_raw, dict):
                child_flow = cls.from_server_dict({
                    "name": f"{name}_child",
                    "transitions": child_flow_raw.get("transitions", []),
                })

        return cls(
            name=name,
            description=data.get("description", ""),
            transitions=transitions,
            child_flow=child_flow,
        )

    def get_all_states(self) -> set[str]:
        """Get all states referenced in this flow.

        Always includes built-in states (failed) that the server requires
        even if no flow transition references them directly.
        """
        states = {"failed", "needs_continuation"}
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
    """Load a flow by name from the server.

    Args:
        flow_name: Name of the flow

    Returns:
        Flow object

    Raises:
        FileNotFoundError: If the flow is not found on the server
        RuntimeError: If the server is unreachable
    """
    from .sdk import get_sdk  # noqa: PLC0415

    try:
        sdk = get_sdk()
        flows = sdk.flows.list()
    except Exception as e:
        logging.error(f"load_flow: failed to reach server for flow '{flow_name}': {e}")
        raise

    for flow_data in flows:
        if flow_data.get("name") == flow_name:
            return Flow.from_server_dict(flow_data)

    available = [f.get("name") for f in flows if f.get("name")]
    raise FileNotFoundError(
        f"Flow '{flow_name}' not found on server. "
        f"Available flows: {available}"
    )


def list_flows() -> list[str]:
    """List all available flow names from the server."""
    from .sdk import get_sdk  # noqa: PLC0415

    try:
        sdk = get_sdk()
        flows = sdk.flows.list()
        return [f.get("name") for f in flows if f.get("name")]
    except Exception as e:
        logging.warning(f"list_flows: failed to fetch flows from server: {e}")
        return []


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

    The flow engine owns transitions — steps are pre-transition side effects.
    - incoming → claimed: implementer agent claims
    - claimed → provisional: runs push_branch, run_tests, create_pr
    - provisional → done: gatekeeper agent reviews, runs post_review_comment, merge_pr
    """
    return """name: default
description: Standard implementation with review

transitions:
  "incoming -> claimed":
    agent: implementer

  "claimed -> provisional":
    runs: [push_branch, run_tests, create_pr]

  "provisional -> done":
    conditions:
      - name: gatekeeper_review
        type: agent
        agent: gatekeeper
        on_fail: incoming
    runs: [post_review_comment, merge_pr]
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
    runs: [create_project_pr]
    conditions:
      - name: all_tests_pass
        type: script
        script: run-tests

  "provisional -> done":
    conditions:
      - name: human_approval
        type: manual
    runs: [merge_project_pr]
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


def _serialize_condition(c: Condition) -> dict:
    """Serialize a Condition to a dict for server storage."""
    d: dict[str, Any] = {"name": c.name, "type": c.type}
    if c.script:
        d["script"] = c.script
    if c.agent:
        d["agent"] = c.agent
    if c.on_fail:
        d["on_fail"] = c.on_fail
    return d


def _serialize_transitions(transitions: list[Transition]) -> list[dict]:
    """Serialize transitions including agent, runs, and conditions."""
    result = []
    for t in transitions:
        td: dict[str, Any] = {"from": t.from_state, "to": t.to_state}
        if t.agent:
            td["agent"] = t.agent
        if t.runs:
            td["runs"] = t.runs
        if t.conditions:
            td["conditions"] = [_serialize_condition(c) for c in t.conditions]
        result.append(td)
    return result


def _implicit_reverse_transitions(transitions: list[Transition]) -> list[dict]:
    """Derive implicit reverse transitions that the server needs for reject/requeue.

    The server's canTransition() checks the transitions array for explicit
    {from, to} entries. Without these, reject (provisional → incoming) and
    requeue (claimed → incoming) return 409.

    This function adds:
    1. on_fail transitions: if a condition has on_fail, the task can move from
       the transition's from_state (where it sits waiting) to the on_fail state.
    2. claimed → incoming: agents can always requeue from claimed.
    """
    seen: set[tuple[str, str]] = set()
    for t in transitions:
        seen.add((t.from_state, t.to_state))

    implicit: list[dict] = []

    # on_fail reverse transitions
    for t in transitions:
        for c in t.conditions:
            if c.on_fail and (t.from_state, c.on_fail) not in seen:
                implicit.append({"from": t.from_state, "to": c.on_fail})
                seen.add((t.from_state, c.on_fail))

    # Implicit requeue: claimed → incoming (agents can always fail/requeue)
    if ("claimed", "incoming") not in seen:
        implicit.append({"from": "claimed", "to": "incoming"})
        seen.add(("claimed", "incoming"))

    # Implicit continuation: claimed → needs_continuation
    if ("claimed", "needs_continuation") not in seen:
        implicit.append({"from": "claimed", "to": "needs_continuation"})
        seen.add(("claimed", "needs_continuation"))

    return implicit


def flow_to_server_registration(flow: Flow) -> dict:
    """Convert a Flow to the dict format expected by sdk.flows.register().

    Returns:
        Dict with 'states', 'transitions' (with full detail: agent, runs,
        conditions), and optionally 'description' and 'child_flow'.
    """
    all_states = flow.get_all_states()
    if flow.child_flow:
        all_states |= flow.child_flow.get_all_states()
    states = sorted(all_states)
    transitions = _serialize_transitions(flow.transitions)
    transitions += _implicit_reverse_transitions(flow.transitions)
    reg: dict[str, Any] = {"states": states, "transitions": transitions}
    if flow.description:
        reg["description"] = flow.description
    if flow.child_flow:
        child_transitions = _serialize_transitions(flow.child_flow.transitions)
        child_transitions += _implicit_reverse_transitions(flow.child_flow.transitions)
        reg["child_flow"] = {
            "transitions": child_transitions,
        }
    return reg


def evaluate_script_conditions(
    conditions: list[Condition],
    cwd: Path | None = None,
) -> tuple[bool, "Condition | None"]:
    """Evaluate script-type conditions in declaration order.

    Short-circuits on the first failing condition — later conditions are NOT
    evaluated once one fails. Non-script conditions (agent, manual) are skipped
    by this function; they are handled separately by the scheduler.

    Args:
        conditions: List of Condition objects from a Transition.
        cwd: Working directory for script execution (optional).

    Returns:
        (all_passed, first_failed_condition) where first_failed_condition is
        None if all script conditions passed.
    """
    for condition in conditions:
        if condition.type != "script":
            continue
        script = condition.script
        if not script:
            continue
        result = subprocess.run(
            [script],
            cwd=cwd,
            capture_output=True,
        )
        if result.returncode != 0:
            return (False, condition)
    return (True, None)
