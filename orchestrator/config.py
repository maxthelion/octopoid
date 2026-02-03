"""Configuration loading and constants for the orchestrator."""

import os
from pathlib import Path
from typing import Any

import yaml


# Port allocation
BASE_PORT = 41000
PORT_STRIDE = 10

# Queue limits (defaults, can be overridden in agents.yaml)
DEFAULT_QUEUE_LIMITS = {
    "max_incoming": 20,
    "max_claimed": 5,
    "max_open_prs": 10,
}


def find_parent_project() -> Path:
    """Find the parent project root by walking up from orchestrator/ to find .git.

    Returns the directory containing .git (the parent project root).
    """
    current = Path(__file__).resolve().parent

    # Walk up looking for .git
    while current != current.parent:
        # Skip if we're still inside the orchestrator submodule
        if current.name == "orchestrator" and (current / "orchestrator").is_dir():
            current = current.parent
            continue

        if (current / ".git").exists():
            return current
        current = current.parent

    raise RuntimeError(
        "Could not find parent project root. "
        "Make sure orchestrator is installed as a submodule in a git repository."
    )


def get_orchestrator_dir() -> Path:
    """Get the .orchestrator directory in the parent project."""
    return find_parent_project() / ".orchestrator"


def get_agents_config_path() -> Path:
    """Get path to agents.yaml in parent project."""
    return get_orchestrator_dir() / "agents.yaml"


def get_global_instructions_path() -> Path:
    """Get path to global-instructions.md in parent project."""
    return get_orchestrator_dir() / "global-instructions.md"


def get_queue_dir() -> Path:
    """Get the shared queue directory."""
    return get_orchestrator_dir() / "shared" / "queue"


def get_agents_runtime_dir() -> Path:
    """Get the agents runtime directory."""
    return get_orchestrator_dir() / "agents"


def load_agents_config() -> dict[str, Any]:
    """Load agents configuration from parent project's agents.yaml."""
    config_path = get_agents_config_path()

    if not config_path.exists():
        raise FileNotFoundError(
            f"Agents config not found at {config_path}. "
            "Run 'python orchestrator/orchestrator/init.py' to initialize."
        )

    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    return config


def get_queue_limits() -> dict[str, int]:
    """Get queue limits from config or use defaults."""
    try:
        config = load_agents_config()
        limits = config.get("queue_limits", {})
        return {
            "max_incoming": limits.get("max_incoming", DEFAULT_QUEUE_LIMITS["max_incoming"]),
            "max_claimed": limits.get("max_claimed", DEFAULT_QUEUE_LIMITS["max_claimed"]),
            "max_open_prs": limits.get("max_open_prs", DEFAULT_QUEUE_LIMITS["max_open_prs"]),
        }
    except FileNotFoundError:
        return DEFAULT_QUEUE_LIMITS.copy()


def get_agents() -> list[dict[str, Any]]:
    """Get list of configured agents."""
    config = load_agents_config()
    return config.get("agents", [])


def get_orchestrator_submodule_path() -> Path:
    """Get the path to the orchestrator submodule."""
    return Path(__file__).resolve().parent.parent


def get_commands_dir() -> Path:
    """Get the commands directory in the orchestrator submodule."""
    return get_orchestrator_submodule_path() / "commands"


def get_templates_dir() -> Path:
    """Get the templates directory in the orchestrator submodule."""
    return get_orchestrator_submodule_path() / "templates"
