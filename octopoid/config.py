"""Configuration loading and constants for the orchestrator."""

import os
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Task queue states — validated at runtime by server against registered flows
# ---------------------------------------------------------------------------

TaskQueue = str  # validated at runtime by server against registered flows
BUILT_IN_QUEUES: set[str] = {"incoming", "claimed", "done", "failed"}

# Queues where a task is still actively being worked on
ACTIVE_QUEUES: list[str] = ["claimed", "needs_continuation"]

# Queues where a task is waiting for work
PENDING_QUEUES: list[str] = ["incoming", "backlog", "blocked"]

# Terminal queues — task is finished (successfully or not)
TERMINAL_QUEUES: list[str] = ["done", "failed", "rejected", "escalated", "recycled"]


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
    "max_provisional": 10,
}

# Default gatekeeper configuration
DEFAULT_GATEKEEPER_CONFIG = {
    "enabled": False,
    "auto_approve": False,  # Auto-approve PR if all checks pass?
    "required_checks": ["lint", "tests"],
    "optional_checks": ["style", "architecture"],
}

def find_parent_project() -> Path:
    """Find the parent project root by walking up to find .git.

    Returns the directory containing .git (the parent project root).

    Resolution order:
    1. ORCHESTRATOR_DIR env var → its parent directory
    2. Walk up from cwd (correct when scheduler runs with WorkingDirectory set)
    3. Walk up from __file__ (fallback for import-time usage)
    """
    env_override = os.environ.get("ORCHESTRATOR_DIR")
    if env_override:
        return Path(env_override).parent

    # Prefer cwd — each project's scheduler sets WorkingDirectory in its
    # launchd plist, so cwd is the project root. Walking from __file__
    # resolves to whichever project contains the orchestrator package,
    # which breaks when multiple projects share the same package.
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        current = start
        while current != current.parent:
            if (current / ".octopoid").is_dir() and (current / ".git").exists():
                return current
            current = current.parent

    raise RuntimeError(
        "Could not find parent project root. "
        "Make sure the working directory is inside a git repository with .octopoid/."
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


def get_jobs_dir() -> Path:
    """Get the jobs runtime directory for taskless agent job runs.

    Returns:
        Path to .octopoid/runtime/jobs/ where taskless agent job directories are created
    """
    return get_runtime_dir() / "jobs"


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


def get_queue_limits() -> dict[str, int]:
    """Get queue limits from config or use defaults."""
    try:
        config = load_agents_config()
        limits = config.get("queue_limits", {})
        return {
            "max_incoming": limits.get("max_incoming", DEFAULT_QUEUE_LIMITS["max_incoming"]),
            "max_claimed": limits.get("max_claimed", DEFAULT_QUEUE_LIMITS["max_claimed"]),
            "max_provisional": limits.get("max_provisional", DEFAULT_QUEUE_LIMITS["max_provisional"]),
        }
    except FileNotFoundError:
        return DEFAULT_QUEUE_LIMITS.copy()


def _resolve_agent_dir(entry: dict[str, Any]) -> "Path | None":
    """Resolve the agent directory for a given agent config entry.

    Returns the resolved agent directory Path, or None if unresolvable.
    """
    agent_type = entry.get("type", "")
    explicit_agent_dir = entry.get("agent_dir", "")

    if explicit_agent_dir:
        # Explicit agent_dir takes priority — no type needed
        agent_dir = Path(explicit_agent_dir)
        if not agent_dir.is_absolute():
            agent_dir = find_parent_project() / agent_dir
        if not agent_dir.exists():
            return None
        return agent_dir
    elif agent_type == "custom":
        agent_dir_str = entry.get("path", "")
        if not agent_dir_str:
            return None
        agent_dir = Path(agent_dir_str)
        if not agent_dir.is_absolute():
            agent_dir = find_parent_project() / agent_dir
        return agent_dir
    elif agent_type:
        # Look in packages/client/agents/<type>/ first (product templates)
        # Fall back to .octopoid/agents/<type>/ (scaffolded copies)
        product_dir = find_parent_project() / "packages" / "client" / "agents" / agent_type
        scaffolded_dir = find_parent_project() / ".octopoid" / "agents" / agent_type

        if product_dir.exists():
            return product_dir
        elif scaffolded_dir.exists():
            return scaffolded_dir
        else:
            return None
    else:
        return None


def get_agents_base_dir() -> Path:
    """Get the base directory where per-agent directories live."""
    return get_orchestrator_dir() / "agents"


def discover_agent_config(agent_dir: Path) -> dict[str, Any] | None:
    """Load and return the agent config from an agent directory's agent.yaml.

    Args:
        agent_dir: Path to the agent directory (e.g. .octopoid/agents/implementer)

    Returns:
        Merged agent config dict with 'name', 'blueprint_name', 'agent_dir' injected,
        or None if the directory has no agent.yaml.

    The ``job_agent: true`` flag is preserved in the returned config for callers
    that need to distinguish pool agents (claim tasks from the queue) from
    job-scheduled agents (invoked by jobs.yaml on a fixed interval).
    """
    agent_yaml_path = agent_dir / "agent.yaml"
    if not agent_yaml_path.exists():
        return None

    with open(agent_yaml_path) as f:
        config = yaml.safe_load(f) or {}

    blueprint_name = agent_dir.name
    config.setdefault("name", blueprint_name)
    config.setdefault("blueprint_name", blueprint_name)
    config.setdefault("max_instances", 1)
    config.setdefault("enabled", True)
    config["agent_dir"] = str(agent_dir)

    return config


def get_agents() -> list[dict[str, Any]]:
    """Get list of all configured agents by scanning .octopoid/agents/*/agent.yaml.

    Each subdirectory of .octopoid/agents/ that contains an agent.yaml is
    returned. This includes both pool agents (claim tasks from the queue) and
    job-scheduled agents (``job_agent: true``, invoked by jobs.yaml on a fixed
    interval). Callers that only want pool agents should filter on
    ``not agent.get("job_agent")``.

    The directory name becomes the ``blueprint_name`` and ``name`` for the agent
    unless the agent.yaml explicitly sets a ``name`` field.

    Falls back to reading from agents.yaml (legacy 'agents' dict or 'fleet' list)
    if the agents base directory does not exist or contains no valid agent.yaml files.

    Returns:
        List of agent configs. Each entry includes:
        - 'name': agent name (used for state/lock file paths)
        - 'blueprint_name': pool blueprint name (used for PID tracking)
        - 'agent_dir': absolute path to the agent directory
        - 'max_instances': max concurrent pool instances (default 1)
        - 'job_agent': True if the agent is invoked via jobs.yaml (not the pool loop)
        - all fields from agent.yaml (role, model, max_turns, interval_seconds, etc.)
    """
    agents_base = get_agents_base_dir()

    if agents_base.exists() and agents_base.is_dir():
        discovered: list[dict[str, Any]] = []
        for agent_dir in sorted(agents_base.iterdir()):
            if not agent_dir.is_dir():
                continue
            config = discover_agent_config(agent_dir)
            if config is None:
                continue
            if not config.get("enabled", True):
                continue
            discovered.append(config)

        if discovered:
            return discovered

    # Legacy fallback: read from agents.yaml agents/fleet keys
    try:
        config = load_agents_config()
    except FileNotFoundError:
        return []

    agents_raw = config.get("agents")
    fleet_raw = config.get("fleet")

    if isinstance(agents_raw, dict):
        source_entries: list[dict[str, Any]] = [
            {"blueprint_name": k, **v}
            for k, v in agents_raw.items()
        ]
    elif fleet_raw:
        source_entries = [
            {"blueprint_name": entry.get("name", ""), **entry}
            for entry in fleet_raw
        ]
    else:
        return []

    agents = []
    for entry in source_entries:
        blueprint_name = entry.get("blueprint_name", "")
        entry.setdefault("name", blueprint_name)
        entry.setdefault("max_instances", 1)

        agent_dir = _resolve_agent_dir(entry)

        if agent_dir is not None:
            type_defaults: dict[str, Any] = {}
            agent_yaml = agent_dir / "agent.yaml"
            if agent_yaml.exists():
                with open(agent_yaml) as f:
                    type_defaults = yaml.safe_load(f) or {}
            merged = {**type_defaults, **entry}
            merged["agent_dir"] = str(agent_dir)
        else:
            merged = dict(entry)

        merged.setdefault("enabled", True)
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


def get_scope() -> str | None:
    """Get the scope for this orchestrator from .octopoid/config.yaml.

    Scope isolates this orchestrator's tasks from other projects on the same
    server. Returns None if the field is absent (caller should treat this as
    a misconfiguration).
    """
    config = _load_project_config()
    scope = config.get("scope")
    if scope:
        return str(scope)
    return None


def save_api_key(api_key: str) -> None:
    """Persist an API key to .octopoid/.api_key (gitignored).

    This file is separate from config.yaml (which is tracked) to avoid
    accidentally committing secrets.

    Args:
        api_key: The oct_-prefixed API key returned by the server.
    """
    key_path = find_parent_project() / ".octopoid" / ".api_key"
    key_path.write_text(api_key + "\n")


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
