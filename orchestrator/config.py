"""Configuration loading and constants for the orchestrator."""

import os
from pathlib import Path
from typing import Any, Literal

import yaml


# ---------------------------------------------------------------------------
# Task queue states — must match packages/shared/src/task.ts TaskQueue type
# ---------------------------------------------------------------------------

TaskQueue = Literal[
    "incoming",
    "claimed",
    "provisional",
    "done",
    "failed",
    "rejected",
    "escalated",
    "recycled",
    "breakdown",
    "needs_continuation",
    "backlog",
    "blocked",
]

# Queues where a task is still actively being worked on
ACTIVE_QUEUES: list[TaskQueue] = ["claimed", "needs_continuation"]

# Queues where a task is waiting for work
PENDING_QUEUES: list[TaskQueue] = ["incoming", "backlog", "blocked"]

# Terminal queues — task is finished (successfully or not)
TERMINAL_QUEUES: list[TaskQueue] = ["done", "failed", "rejected", "escalated", "recycled"]


# ---------------------------------------------------------------------------
# Roles are defined in agents.yaml and registered with the server at startup.
# No hardcoded role lists — the server is the source of truth for valid roles.


# Port allocation
BASE_PORT = 41000
PORT_STRIDE = 10

# Queue limits (defaults, can be overridden in agents.yaml)
DEFAULT_QUEUE_LIMITS = {
    "max_incoming": 20,
    "max_claimed": 1,
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
    """Get the .octopoid directory in the parent project.

    Can be overridden via ORCHESTRATOR_DIR environment variable (used by tests).
    """
    env_override = os.environ.get("ORCHESTRATOR_DIR")
    if env_override:
        return Path(env_override)
    return find_parent_project() / ".octopoid"


def get_agents_config_path() -> Path:
    """Get path to agents.yaml in parent project."""
    return get_orchestrator_dir() / "agents.yaml"


def get_global_instructions_path() -> Path:
    """Get path to global-instructions.md in parent project."""
    return get_orchestrator_dir() / "global-instructions.md"


def get_base_branch() -> str:
    """Get the configured base branch from .octopoid/config.yaml.

    Reads ``repo.base_branch``. Defaults to ``"main"`` if not set.
    This is the branch that tasks branch from and rebase onto.
    """
    try:
        config_path = find_parent_project() / ".octopoid" / "config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
            return config.get("repo", {}).get("base_branch", "main")
    except Exception:
        pass
    return "main"


def get_tasks_file_dir() -> Path:
    """Get the single directory where all task files live.

    Task state is owned by the API. The filesystem just stores content.
    All task .md files go in .octopoid/tasks/ regardless of queue state.
    """
    d = find_parent_project() / ".octopoid" / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_queue_dir() -> Path:
    """Get the shared queue directory."""
    return get_shared_dir() / "queue"


def get_proposals_dir() -> Path:
    """Get the shared proposals directory."""
    return get_shared_dir() / "proposals"


def get_prs_dir() -> Path:
    """Get the shared PRs directory for gatekeeper checks."""
    return get_shared_dir() / "prs"


def get_prompts_dir() -> Path:
    """Get the prompts directory for domain-specific proposer prompts."""
    return get_orchestrator_dir() / "prompts"


def get_notes_dir() -> Path:
    """Get the shared notes directory for agent learning persistence."""
    notes_dir = get_shared_dir() / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    return notes_dir


def get_runtime_dir() -> Path:
    """Get the runtime directory for ephemeral state.

    Returns:
        Path to .octopoid/runtime/ (gitignored)
    """
    return get_orchestrator_dir() / "runtime"


def get_agents_runtime_dir() -> Path:
    """Get the agents runtime directory."""
    return get_runtime_dir() / "agents"


def get_tasks_dir() -> Path:
    """Get the tasks directory for ephemeral task worktrees.

    Returns:
        Path to .octopoid/runtime/tasks/ where ephemeral task worktrees are created
    """
    return get_runtime_dir() / "tasks"


def get_logs_dir() -> Path:
    """Get the logs directory."""
    return get_runtime_dir() / "logs"


def get_shared_dir() -> Path:
    """Get the shared directory for notes, reviews, proposals."""
    return get_runtime_dir() / "shared"


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
    """Get list of configured agents.

    Reads the 'fleet:' key from agents.yaml, which references agent directories.

    Returns:
        List of agent configs with merged type defaults and fleet overrides.
        Each entry includes an 'agent_dir' key pointing to the agent directory.
    """
    config = load_agents_config()

    # Fleet format only
    fleet = config.get("fleet", [])
    if not fleet:
        return []

    agents = []
    for entry in fleet:
        agent_type = entry.get("type", "")
        explicit_agent_dir = entry.get("agent_dir", "")

        # Resolve agent directory
        if explicit_agent_dir:
            # Explicit agent_dir takes priority — no type needed
            agent_dir = Path(explicit_agent_dir)
            if not agent_dir.is_absolute():
                agent_dir = find_parent_project() / agent_dir
            if not agent_dir.exists():
                continue
        elif not agent_type:
            continue
        elif agent_type == "custom":
            agent_dir_str = entry.get("path", "")
            if not agent_dir_str:
                continue
            agent_dir = Path(agent_dir_str)
            if not agent_dir.is_absolute():
                agent_dir = find_parent_project() / agent_dir
        else:
            # Look in packages/client/agents/<type>/ first (product templates)
            # Fall back to .octopoid/agents/<type>/ (scaffolded copies)
            product_dir = find_parent_project() / "packages" / "client" / "agents" / agent_type
            scaffolded_dir = find_parent_project() / ".octopoid" / "agents" / agent_type

            if product_dir.exists():
                agent_dir = product_dir
            elif scaffolded_dir.exists():
                agent_dir = scaffolded_dir
            else:
                # Agent type not found, skip this entry
                continue

        # Load type defaults from agent.yaml
        type_defaults = {}
        agent_yaml = agent_dir / "agent.yaml"
        if agent_yaml.exists():
            with open(agent_yaml) as f:
                type_defaults = yaml.safe_load(f) or {}

        # Merge: type defaults < fleet overrides
        merged = {**type_defaults, **entry}
        merged["agent_dir"] = str(agent_dir)

        # Ensure 'enabled' defaults to True if not specified
        merged.setdefault("enabled", True)

        # Skip disabled agents
        if not merged.get("enabled", True):
            continue

        agents.append(merged)

    return agents


def is_system_paused() -> bool:
    """Check if the entire orchestrator system is paused.

    Checks for a PAUSE file in .octopoid/ first (touch .octopoid/PAUSE to pause,
    rm .octopoid/PAUSE to resume). Falls back to the 'paused' flag in agents.yaml.
    """
    pause_file = get_orchestrator_dir() / "PAUSE"
    if pause_file.exists():
        return True
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


# Default pre-check settings (scheduler-level submission filtering)
DEFAULT_PRE_CHECK_CONFIG = {
    "require_commits": True,
    "max_attempts_before_planning": 3,
    "claim_timeout_minutes": 60,
}


def get_pre_check_config() -> dict[str, Any]:
    """Get pre-check configuration for the scheduler pre-check role.

    Returns:
        Dictionary with require_commits, max_attempts_before_planning, claim_timeout_minutes
    """
    try:
        config = load_agents_config()
        val_config = config.get("pre_check", config.get("validation", {}))
        return {
            "require_commits": val_config.get(
                "require_commits",
                DEFAULT_PRE_CHECK_CONFIG["require_commits"]
            ),
            "max_attempts_before_planning": val_config.get(
                "max_attempts_before_planning",
                DEFAULT_PRE_CHECK_CONFIG["max_attempts_before_planning"]
            ),
            "claim_timeout_minutes": val_config.get(
                "claim_timeout_minutes",
                DEFAULT_PRE_CHECK_CONFIG["claim_timeout_minutes"]
            ),
        }
    except FileNotFoundError:
        return DEFAULT_PRE_CHECK_CONFIG.copy()


def get_pre_checkers() -> list[dict[str, Any]]:
    """Get list of configured pre-check agents."""
    agents = get_agents()
    return [a for a in agents if a.get("role") in ("pre_check", "validator")]


# =============================================================================
# Hooks Configuration
# =============================================================================

# Default hooks when nothing is configured
DEFAULT_HOOKS_CONFIG: dict[str, list[str]] = {
    "before_submit": ["create_pr"],
    "before_merge": ["merge_pr"],
}


def _load_project_config() -> dict[str, Any]:
    """Load .octopoid/config.yaml from the parent project.

    Returns:
        Parsed YAML config dict, or empty dict if not found
    """
    try:
        config_path = find_parent_project() / ".octopoid" / "config.yaml"
        if not config_path.exists():
            return {}
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except (RuntimeError, IOError):
        return {}


def get_hooks_config() -> dict[str, list[str]]:
    """Get project-level hooks configuration.

    Reads the top-level ``hooks:`` key from .octopoid/config.yaml.

    Returns:
        Dict mapping hook point names to ordered lists of hook names.
        Falls back to DEFAULT_HOOKS_CONFIG if not configured.
    """
    config = _load_project_config()
    hooks = config.get("hooks")
    if hooks and isinstance(hooks, dict):
        return hooks
    return DEFAULT_HOOKS_CONFIG.copy()
