"""Configuration loading and constants for the orchestrator."""

import os
from pathlib import Path
from typing import Any, Literal

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

# Default proposal limits per proposer type
DEFAULT_PROPOSAL_LIMITS = {
    "max_active": 5,
    "max_per_run": 2,
}

# Default voice weights
DEFAULT_VOICE_WEIGHTS = {
    "plan-reader": 1.5,
    "architect": 1.2,
    "test-checker": 1.0,
    "app-designer": 0.8,
}

# Default curator scoring weights
DEFAULT_CURATOR_SCORING = {
    "priority_alignment": 0.30,
    "complexity_reduction": 0.25,
    "risk": 0.15,
    "dependencies_met": 0.15,
    "voice_weight": 0.15,
}

# Default gatekeeper configuration
DEFAULT_GATEKEEPER_CONFIG = {
    "enabled": False,
    "auto_approve": False,  # Auto-approve PR if all checks pass?
    "required_checks": ["lint", "tests"],
    "optional_checks": ["style", "architecture"],
}

ModelType = Literal["task", "proposal"]


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


def get_proposals_dir() -> Path:
    """Get the shared proposals directory."""
    return get_orchestrator_dir() / "shared" / "proposals"


def get_prs_dir() -> Path:
    """Get the shared PRs directory for gatekeeper checks."""
    return get_orchestrator_dir() / "shared" / "prs"


def get_prompts_dir() -> Path:
    """Get the prompts directory for domain-specific proposer prompts."""
    return get_orchestrator_dir() / "prompts"


def get_notes_dir() -> Path:
    """Get the shared notes directory for agent learning persistence."""
    notes_dir = get_orchestrator_dir() / "shared" / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    return notes_dir


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


def get_model_type() -> ModelType:
    """Get the orchestrator model type (task or proposal).

    Returns:
        "task" for legacy task-driven model
        "proposal" for proposal-driven model
    """
    try:
        config = load_agents_config()
        return config.get("model", "task")
    except FileNotFoundError:
        return "task"


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


def get_proposal_limits(proposer_type: str | None = None) -> dict[str, int]:
    """Get proposal limits for a proposer type.

    Args:
        proposer_type: The proposer type (e.g., "test-checker", "architect")
                      If None, returns default limits.

    Returns:
        Dictionary with max_active and max_per_run
    """
    try:
        config = load_agents_config()
        all_limits = config.get("proposal_limits", {})

        if proposer_type and proposer_type in all_limits:
            limits = all_limits[proposer_type]
            return {
                "max_active": limits.get("max_active", DEFAULT_PROPOSAL_LIMITS["max_active"]),
                "max_per_run": limits.get("max_per_run", DEFAULT_PROPOSAL_LIMITS["max_per_run"]),
            }

        return DEFAULT_PROPOSAL_LIMITS.copy()
    except FileNotFoundError:
        return DEFAULT_PROPOSAL_LIMITS.copy()


def get_voice_weights() -> dict[str, float]:
    """Get voice weights for all proposer types.

    Returns:
        Dictionary mapping proposer type to weight multiplier
    """
    try:
        config = load_agents_config()
        weights = config.get("voice_weights", {})
        # Merge with defaults
        result = DEFAULT_VOICE_WEIGHTS.copy()
        result.update(weights)
        return result
    except FileNotFoundError:
        return DEFAULT_VOICE_WEIGHTS.copy()


def get_voice_weight(proposer_type: str) -> float:
    """Get voice weight for a specific proposer type.

    Args:
        proposer_type: The proposer type

    Returns:
        Weight multiplier (default 1.0 if not configured)
    """
    weights = get_voice_weights()
    return weights.get(proposer_type, 1.0)


def get_curator_scoring() -> dict[str, float]:
    """Get curator scoring weights.

    Returns:
        Dictionary mapping scoring factor to weight
    """
    try:
        config = load_agents_config()
        scoring = config.get("curator_scoring", {})
        # Merge with defaults
        result = DEFAULT_CURATOR_SCORING.copy()
        result.update(scoring)
        return result
    except FileNotFoundError:
        return DEFAULT_CURATOR_SCORING.copy()


def get_agents() -> list[dict[str, Any]]:
    """Get list of configured agents."""
    config = load_agents_config()
    return config.get("agents", [])


def is_system_paused() -> bool:
    """Check if the entire orchestrator system is paused.

    Returns:
        True if the system-level 'paused' flag is set in agents.yaml
    """
    try:
        config = load_agents_config()
        return config.get("paused", False)
    except FileNotFoundError:
        return False


def get_proposers() -> list[dict[str, Any]]:
    """Get list of configured proposer agents."""
    agents = get_agents()
    return [a for a in agents if a.get("role") == "proposer"]


def get_curators() -> list[dict[str, Any]]:
    """Get list of configured curator agents."""
    agents = get_agents()
    return [a for a in agents if a.get("role") == "curator"]


def get_orchestrator_submodule_path() -> Path:
    """Get the path to the orchestrator submodule."""
    return Path(__file__).resolve().parent.parent


def get_commands_dir() -> Path:
    """Get the commands directory in the orchestrator submodule."""
    return get_orchestrator_submodule_path() / "commands"


def get_templates_dir() -> Path:
    """Get the templates directory in the orchestrator submodule."""
    return get_orchestrator_submodule_path() / "templates"


def get_gatekeeper_config() -> dict[str, Any]:
    """Get gatekeeper configuration.

    Returns:
        Dictionary with enabled, auto_approve, required_checks, optional_checks
    """
    try:
        config = load_agents_config()
        gk_config = config.get("gatekeeper", {})
        return {
            "enabled": gk_config.get("enabled", DEFAULT_GATEKEEPER_CONFIG["enabled"]),
            "auto_approve": gk_config.get("auto_approve", DEFAULT_GATEKEEPER_CONFIG["auto_approve"]),
            "required_checks": gk_config.get("required_checks", DEFAULT_GATEKEEPER_CONFIG["required_checks"]),
            "optional_checks": gk_config.get("optional_checks", DEFAULT_GATEKEEPER_CONFIG["optional_checks"]),
        }
    except FileNotFoundError:
        return DEFAULT_GATEKEEPER_CONFIG.copy()


def is_gatekeeper_enabled() -> bool:
    """Check if gatekeeper system is enabled."""
    return get_gatekeeper_config()["enabled"]


def get_gatekeepers() -> list[dict[str, Any]]:
    """Get list of configured gatekeeper agents."""
    agents = get_agents()
    return [a for a in agents if a.get("role") == "gatekeeper"]


def get_gatekeeper_coordinators() -> list[dict[str, Any]]:
    """Get list of configured gatekeeper coordinator agents."""
    agents = get_agents()
    return [a for a in agents if a.get("role") == "gatekeeper_coordinator"]


# =============================================================================
# Database Configuration
# =============================================================================

# Default database settings
DEFAULT_DATABASE_CONFIG = {
    "enabled": False,
    "path": "state.db",  # Relative to .orchestrator/
}

# Default validation settings
DEFAULT_VALIDATION_CONFIG = {
    "require_commits": True,
    "max_attempts_before_planning": 3,
    "claim_timeout_minutes": 60,
}


def get_database_config() -> dict[str, Any]:
    """Get database configuration.

    Returns:
        Dictionary with enabled, path settings
    """
    try:
        config = load_agents_config()
        db_config = config.get("database", {})
        return {
            "enabled": db_config.get("enabled", DEFAULT_DATABASE_CONFIG["enabled"]),
            "path": db_config.get("path", DEFAULT_DATABASE_CONFIG["path"]),
        }
    except FileNotFoundError:
        return DEFAULT_DATABASE_CONFIG.copy()


def get_validation_config() -> dict[str, Any]:
    """Get validation configuration for the validator role.

    Returns:
        Dictionary with require_commits, max_attempts_before_planning, claim_timeout_minutes
    """
    try:
        config = load_agents_config()
        val_config = config.get("validation", {})
        return {
            "require_commits": val_config.get(
                "require_commits",
                DEFAULT_VALIDATION_CONFIG["require_commits"]
            ),
            "max_attempts_before_planning": val_config.get(
                "max_attempts_before_planning",
                DEFAULT_VALIDATION_CONFIG["max_attempts_before_planning"]
            ),
            "claim_timeout_minutes": val_config.get(
                "claim_timeout_minutes",
                DEFAULT_VALIDATION_CONFIG["claim_timeout_minutes"]
            ),
        }
    except FileNotFoundError:
        return DEFAULT_VALIDATION_CONFIG.copy()


def is_db_enabled() -> bool:
    """Check if SQLite database mode is enabled.

    Returns:
        True if database mode is enabled in configuration
    """
    return get_database_config()["enabled"]


def get_validators() -> list[dict[str, Any]]:
    """Get list of configured validator agents."""
    agents = get_agents()
    return [a for a in agents if a.get("role") == "validator"]
