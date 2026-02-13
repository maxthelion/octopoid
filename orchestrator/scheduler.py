#!/usr/bin/env python3
"""Main scheduler - runs on 1-minute ticks to evaluate and spawn agents."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from string import Template



from .backpressure import check_backpressure_for_role
from .config import (
    AGENT_TASK_ROLE,
    CLAIMABLE_AGENT_ROLES,
    find_parent_project,
    get_agents,
    get_agents_runtime_dir,
    get_commands_dir,
    get_gatekeeper_config,
    get_gatekeepers,
    get_global_instructions_path,
    get_logs_dir,
    get_main_branch,
    get_orchestrator_dir,
    get_tasks_dir,
    get_templates_dir,
    is_gatekeeper_enabled,
    is_system_paused,
)
from .git_utils import ensure_worktree, get_worktree_path
from .hook_manager import HookManager
from .lock_utils import locked_or_skip
from .port_utils import get_port_env_vars
from .prompt_renderer import render_prompt
from . import queue_utils
from .state_utils import (
    AgentState,
    is_overdue,
    is_process_running,
    load_state,
    mark_finished,
    mark_started,
    save_state,
)

# Global debug flag
DEBUG = False
_log_file: Path | None = None


def setup_scheduler_debug() -> None:
    """Set up debug logging for the scheduler."""
    global _log_file
    logs_dir = get_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    _log_file = logs_dir / f"scheduler-{date_str}.log"


def debug_log(message: str) -> None:
    """Write a debug message to the scheduler log."""
    if not DEBUG or not _log_file:
        return

    timestamp = datetime.now().isoformat()
    log_line = f"[{timestamp}] [SCHEDULER] {message}\n"

    try:
        with open(_log_file, "a") as f:
            f.write(log_line)
    except OSError:
        pass



def run_pre_check(agent_name: str, agent_config: dict) -> bool:
    """Run the agent's pre-check command to see if there's work available.

    Args:
        agent_name: Name of the agent
        agent_config: Agent configuration dict

    Returns:
        True if agent should be spawned (work available or no pre-check configured)
        False if pre-check indicates no work available
    """
    pre_check_cmd = agent_config.get("pre_check")
    if not pre_check_cmd:
        # No pre-check configured, always spawn
        return True

    trigger = agent_config.get("pre_check_trigger", "non_empty")
    debug_log(f"Running pre-check for {agent_name}: {pre_check_cmd}")

    try:
        # Run from the parent project directory
        result = subprocess.run(
            pre_check_cmd,
            shell=True,
            cwd=find_parent_project(),
            capture_output=True,
            text=True,
            timeout=10,  # Pre-checks should be fast
        )

        if trigger == "non_empty":
            has_work = bool(result.stdout.strip())
        elif trigger == "exit_zero":
            has_work = result.returncode == 0
        elif trigger == "exit_nonzero":
            has_work = result.returncode != 0
        else:
            debug_log(f"Unknown pre_check_trigger: {trigger}, defaulting to spawn")
            has_work = True

        debug_log(f"Pre-check for {agent_name}: has_work={has_work} (stdout={result.stdout.strip()!r})")
        return has_work

    except subprocess.TimeoutExpired:
        debug_log(f"Pre-check for {agent_name} timed out, spawning anyway")
        return True
    except Exception as e:
        debug_log(f"Pre-check for {agent_name} failed: {e}, spawning anyway")
        return True


def _verify_submodule_isolation(sub_path: Path, agent_name: str) -> None:
    """Verify that a worktree's submodule has its own git object store.

    Orchestrator_impl agents work in a submodule inside their worktree.
    The worktree's submodule and the main checkout's submodule have
    SEPARATE git object stores. A commit in one is invisible from the
    other. This function verifies the submodule .git pointer is correct.

    If the submodule's .git points to the main checkout's object store
    (instead of the worktree's), the agent would commit to the wrong
    location and the approve script would not find the commits.

    Args:
        sub_path: Path to the submodule directory in the worktree
        agent_name: Agent name for logging
    """
    git_pointer = sub_path / ".git"
    if not git_pointer.exists():
        debug_log(f"WARNING: {agent_name} submodule has no .git at {git_pointer}")
        return

    content = git_pointer.read_text().strip()

    # A submodule .git is a file containing "gitdir: <path>"
    if not content.startswith("gitdir:"):
        debug_log(f"WARNING: {agent_name} submodule .git is not a gitdir pointer: {content[:80]}")
        return

    gitdir = content.split("gitdir:", 1)[1].strip()

    # The gitdir should reference the worktree's modules directory, NOT
    # the main checkout's modules. A healthy worktree submodule points to
    # something like: ../../.git/worktrees/<name>/modules/orchestrator
    # A BROKEN one would point to: ../../.git/modules/orchestrator
    # (which is the main checkout's object store).
    if "worktrees" in gitdir or "worktree" in gitdir:
        debug_log(f"{agent_name} submodule .git correctly points to worktree store: {gitdir}")
    else:
        # This is the dangerous case — submodule shares the main checkout's store
        debug_log(
            f"WARNING: {agent_name} submodule .git points to MAIN checkout store: {gitdir}. "
            f"Commits may go to the wrong location! "
            f"Expected a path containing 'worktrees/' for isolated worktree storage."
        )
        print(
            f"WARNING: Agent {agent_name} submodule may share git store with main checkout. "
            f"gitdir={gitdir}"
        )


def peek_task_branch(role: str) -> str | None:
    """Peek at the next task for a role and return its branch.

    Used by the scheduler to create worktrees on the correct branch.
    For breakdown agents, this peeks at the breakdown queue.
    For implement agents, this peeks at incoming queue.

    orchestrator_impl agents always use a Boxen worktree based on main.
    They work inside the orchestrator/ submodule within that worktree,
    so the worktree itself must be on main (not a submodule branch).

    Args:
        role: Agent role (breakdown, implement, etc.)

    Returns:
        Branch name if a task is available, None otherwise
    """
    # orchestrator_impl always uses main — the agent works inside the
    # orchestrator/ submodule, not on a Boxen feature branch.
    if role == "orchestrator_impl":
        return None

    # Map roles to the queues they pull from
    role_queues = {
        "breakdown": "breakdown",
        "implement": "incoming",
        "test": "incoming",
    }

    queue = role_queues.get(role)
    if not queue:
        return None

    tasks = queue_utils.list_tasks(queue)
    if not tasks:
        return None

    # Return the branch of the first (highest priority) task
    branch = tasks[0].get("branch")
    return branch if branch and branch != "main" else None


def check_continuation_for_agent(agent_name: str) -> dict | None:
    """Check if an agent has continuation work to resume.

    Looks for:
    1. Task marker file (current_task.json) linking agent to a task
    2. Tasks in needs_continuation queue assigned to this agent

    Args:
        agent_name: Name of the agent to check

    Returns:
        Task dict with '_continuation' flag if work found, None otherwise
    """
    from .config import ACTIVE_QUEUES

    # Check task marker first - most reliable signal
    marker = queue_utils.read_task_marker_for(agent_name)
    if marker:
        task_id = marker.get("task_id")
        if task_id and queue_utils.is_task_still_valid(task_id):
            task = queue_utils.find_task_by_id(task_id, queues=ACTIVE_QUEUES)
            if task:
                task["_continuation"] = True
                return task
        else:
            # Task is done/failed - clear stale marker
            queue_utils.clear_task_marker_for(agent_name)

    # Check needs_continuation queue
    continuation_tasks = queue_utils.get_continuation_tasks(agent_name=agent_name)
    if continuation_tasks:
        task = continuation_tasks[0]
        task["_continuation"] = True
        return task

    return None


def claim_and_prepare_task(agent_name: str, role: str, type_filter: str | None = None) -> dict | None:
    """Claim a task and write it to the agent's runtime dir.

    Checks for continuation work first, then tries to claim a fresh task.
    Writes the full task dict (including file content) to claimed_task.json
    so the agent can read it without resolving file paths.

    Args:
        agent_name: Name of the agent
        role: Agent role (e.g. 'implement')
        type_filter: Only claim tasks with this type (from agent config)

    Returns:
        Task dict if work is available, None otherwise
    """
    # 1. Check for continuation work
    task = check_continuation_for_agent(agent_name)

    # 2. If no continuation, claim a fresh task
    if task is None:
        task = queue_utils.claim_task(role_filter=role, agent_name=agent_name, type_filter=type_filter)

    if task is None:
        return None

    # 3. Write full task dict to agent runtime dir
    agent_dir = get_agents_runtime_dir() / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    task_file = agent_dir / "claimed_task.json"
    with open(task_file, "w") as f:
        json.dump(task, f, indent=2)

    return task


def get_scheduler_lock_path() -> Path:
    """Get path to the global scheduler lock file."""
    from .config import get_runtime_dir
    return get_runtime_dir() / "scheduler.lock"


def get_agent_lock_path(agent_name: str) -> Path:
    """Get path to an agent's lock file."""
    return get_agents_runtime_dir() / agent_name / "lock"


def get_agent_state_path(agent_name: str) -> Path:
    """Get path to an agent's state file."""
    return get_agents_runtime_dir() / agent_name / "state.json"


def get_agent_env_path(agent_name: str) -> Path:
    """Get path to an agent's env.sh file."""
    return get_agents_runtime_dir() / agent_name / "env.sh"


def setup_agent_commands(agent_name: str, role: str) -> None:
    """Copy commands to the agent's worktree.

    Args:
        agent_name: Name of the agent
        role: Agent role (determines which commands to copy)
    """
    worktree_path = get_worktree_path(agent_name)
    commands_dest = worktree_path / ".claude" / "commands"
    commands_dest.mkdir(parents=True, exist_ok=True)

    # Source directories
    submodule_commands = get_commands_dir() / "agent"
    project_overrides = get_orchestrator_dir() / "commands"

    # Copy all agent commands from submodule
    if submodule_commands.exists():
        for cmd_file in submodule_commands.glob("*.md"):
            dest_file = commands_dest / cmd_file.name
            shutil.copy2(cmd_file, dest_file)

    # Override with project-specific commands if they exist
    if project_overrides.exists():
        for cmd_file in project_overrides.glob("*.md"):
            dest_file = commands_dest / cmd_file.name
            shutil.copy2(cmd_file, dest_file)


def generate_agent_instructions(
    agent_name: str,
    role: str,
    agent_config: dict,
    task_info: dict | None = None,
) -> Path:
    """Generate .agent-instructions.md in the agent's worktree.

    Args:
        agent_name: Name of the agent
        role: Agent role
        agent_config: Full agent configuration
        task_info: Optional current task information

    Returns:
        Path to generated instructions file
    """
    worktree_path = get_worktree_path(agent_name)
    instructions_path = worktree_path / ".agent-instructions.md"

    # Load template
    template_path = get_templates_dir() / "agent_instructions.md.tmpl"
    if template_path.exists():
        template_content = template_path.read_text()
    else:
        template_content = DEFAULT_AGENT_INSTRUCTIONS_TEMPLATE

    # Load global instructions
    global_instructions_path = get_global_instructions_path()
    global_instructions = ""
    if global_instructions_path.exists():
        global_instructions = global_instructions_path.read_text()

    # Build task section
    task_section = ""
    if task_info:
        task_section = f"""
## Current Task

**Task ID:** {task_info.get('id', 'unknown')}
**Title:** {task_info.get('title', 'unknown')}
**Priority:** {task_info.get('priority', 'P2')}
**Target Branch:** {task_info.get('branch', 'main')}

{task_info.get('content', '')}
"""

    # Build constraints based on role
    constraints = get_role_constraints(role)

    # Substitute template
    template = Template(template_content)
    content = template.safe_substitute(
        agent_name=agent_name,
        role=role,
        timestamp=datetime.now().isoformat(),
        global_instructions=global_instructions,
        task_section=task_section,
        constraints=constraints,
    )

    instructions_path.write_text(content)
    return instructions_path


def get_role_constraints(role: str) -> str:
    """Get role-specific constraints for agent instructions.

    Args:
        role: Agent role

    Returns:
        Markdown string with constraints
    """
    constraints = {
        # Task model (v1)
        "product_manager": """
- You may read any files in the repository
- You may NOT modify code files
- Your output is task files in the queue
- Focus on high-value, well-scoped tasks
- Consider existing PRs and in-progress work
""",
        # Proposal model (v2)
        "proposer": """
- You may read any files in the repository
- You may NOT modify code files
- Your output is proposal files
- Stay focused on your designated area
- Review your rejected proposals before creating new ones
- Create well-scoped, actionable proposals
""",
        "curator": """
- You may read any files in the repository
- You may NOT modify code files
- Evaluate proposals based on project priorities
- Provide constructive feedback when rejecting
- Escalate conflicts to the project owner
- Do not explore the codebase directly
""",
        # Gatekeeper system
        "gatekeeper": """
- You may read any files in the repository
- You may NOT modify code files
- Review the PR diff from your specialized perspective
- Be thorough but fair in your assessment
- Provide specific, actionable feedback for any issues
- Record your check result using the /record-check skill
""",
        "gatekeeper_coordinator": """
- You may read any files in the repository
- You may NOT modify code files
- Monitor PRs and coordinate gatekeeper checks
- Aggregate check results
- Create fix tasks when checks fail
- Approve PRs when all checks pass
""",
        "pr_coordinator": """
- You may read any files in the repository
- You may NOT modify code files
- Watch for open PRs that need review
- Create review tasks for agent-created PRs
- Avoid creating duplicate review tasks
""",
        # Execution layer (both models)
        "implementer": """
- You may read and modify code files
- Create focused, atomic commits
- Follow existing code patterns and conventions
- Write tests for new functionality
- Create a PR when work is complete
""",
        "orchestrator_impl": """
- You work on the orchestrator infrastructure (Python), NOT the Boxen app
- All code is in the orchestrator/ submodule directory (already initialized by the scheduler)
- Work inside orchestrator/ for all edits and commits
- Run tests: cd orchestrator && ./venv/bin/python -m pytest tests/ -v
- Do NOT run `pip install -e .` — it will corrupt the shared scheduler venv. Just edit code and run tests.
- Key files: orchestrator/orchestrator/db.py, queue_utils.py, scheduler.py
- CRITICAL: Commit ONLY in the worktree's orchestrator/ submodule, not the main repo root
- Use `git -C orchestrator/ commit` to ensure commits go to the submodule
- Do NOT create a PR in the main repo — commit to a feature branch in the submodule
- Do NOT use absolute paths to /Users/.../dev/boxen/orchestrator/ — that is a DIFFERENT git repo
""",
        "tester": """
- You may read all files
- You may modify test files only
- Run existing tests and report results
- Add missing test coverage
- Do not modify production code
""",
        "reviewer": """
- You are in READ-ONLY mode by default
- Review code for bugs, security issues, and style
- Leave constructive feedback
- Approve or request changes via PR review
- Do not modify code directly
""",
        # Pre-check layer (scheduler-level submission filtering)
        "pre_check": """
- You do NOT need a worktree (lightweight agent)
- Pre-check provisional tasks for commits
- Accept tasks with valid commits (pass to review)
- Reject tasks without commits (send back for retry)
- Recycle or escalate after max retries
- Reset stuck claimed tasks
- This role runs without Claude invocation
""",
        "recycler": """
- You do NOT need a worktree (lightweight agent)
- Poll provisional queue for burned-out tasks (0 commits, high turns)
- Recycle burned-out tasks to the breakdown queue with project context
- Accept depth-capped tasks for human review
- This role runs without Claude invocation
""",
        "rebaser": """
- You do NOT need a worktree (lightweight agent)
- Rebase stale task branches onto current main
- Re-run tests after rebase; escalate if tests fail
- Force-push rebased branches with --force-with-lease
- Skip orchestrator_impl tasks (v1 limitation)
- Add notes to task files when human intervention is needed
- This role runs without Claude invocation
""",

    }
    return constraints.get(role, "- Follow standard development practices")


def write_agent_env(agent_name: str, agent_id: int, role: str, agent_config: dict | None = None) -> Path:
    """Write environment variables file for an agent.

    Args:
        agent_name: Name of the agent
        agent_id: Numeric ID of the agent
        role: Agent role
        agent_config: Optional agent configuration for extra vars

    Returns:
        Path to env.sh file
    """
    env_path = get_agent_env_path(agent_name)
    env_path.parent.mkdir(parents=True, exist_ok=True)

    from .config import get_shared_dir
    parent_project = find_parent_project()
    worktree_path = get_worktree_path(agent_name)
    shared_dir = get_shared_dir()

    port_vars = get_port_env_vars(agent_id)

    lines = [
        "#!/bin/bash",
        f"export AGENT_NAME='{agent_name}'",
        f"export AGENT_ID='{agent_id}'",
        f"export AGENT_ROLE='{role}'",
        f"export PARENT_PROJECT='{parent_project}'",
        f"export WORKTREE='{worktree_path}'",
        f"export SHARED_DIR='{shared_dir}'",
        f"export ORCHESTRATOR_DIR='{get_orchestrator_dir()}'",
    ]

    # Add model override from agent config
    if agent_config and "model" in agent_config:
        lines.append(f"export AGENT_MODEL='{agent_config['model']}'")

    # Add focus for proposers and gatekeepers (specialists)
    if agent_config and role in ("proposer", "gatekeeper") and "focus" in agent_config:
        lines.append(f"export AGENT_FOCUS='{agent_config['focus']}'")

    # Pass debug mode
    if DEBUG:
        lines.append("export ORCHESTRATOR_DEBUG='1'")

    for key, value in port_vars.items():
        lines.append(f"export {key}='{value}'")

    env_path.write_text("\n".join(lines) + "\n")
    return env_path


def spawn_agent(agent_name: str, agent_id: int, role: str, agent_config: dict) -> int:
    """Spawn an agent subprocess.

    Args:
        agent_name: Name of the agent
        agent_id: Numeric ID of the agent
        role: Agent role
        agent_config: Full agent configuration

    Returns:
        Process ID of spawned agent
    """
    # Determine working directory - lightweight agents use parent project
    is_lightweight = agent_config.get("lightweight", False)
    if is_lightweight:
        cwd = find_parent_project()
        worktree_path = cwd  # For env var
    else:
        worktree_path = get_worktree_path(agent_name)
        cwd = worktree_path

    # Build environment
    env = os.environ.copy()
    env["AGENT_NAME"] = agent_name
    env["AGENT_ID"] = str(agent_id)
    env["AGENT_ROLE"] = role
    env["PARENT_PROJECT"] = str(find_parent_project())
    env["WORKTREE"] = str(worktree_path)
    from .config import get_shared_dir
    env["SHARED_DIR"] = str(get_shared_dir())
    env["ORCHESTRATOR_DIR"] = str(get_orchestrator_dir())

    # Set PYTHONPATH to include the orchestrator submodule
    # This allows `import orchestrator.orchestrator...` to work
    orchestrator_submodule = find_parent_project() / "orchestrator"
    existing_pythonpath = env.get("PYTHONPATH", "")
    if existing_pythonpath:
        env["PYTHONPATH"] = f"{orchestrator_submodule}:{existing_pythonpath}"
    else:
        env["PYTHONPATH"] = str(orchestrator_submodule)

    # Pass model override from agent config (e.g., "sonnet", "opus")
    if "model" in agent_config:
        env["AGENT_MODEL"] = agent_config["model"]

    # Pass focus for proposers and gatekeepers (specialists)
    if role in ("proposer", "gatekeeper") and "focus" in agent_config:
        env["AGENT_FOCUS"] = agent_config["focus"]

    # Pass gatekeeper review context
    if role == "gatekeeper":
        if "review_task_id" in agent_config:
            env["REVIEW_TASK_ID"] = agent_config["review_task_id"]
        if "review_check_name" in agent_config:
            env["REVIEW_CHECK_NAME"] = agent_config["review_check_name"]

    # Pass debug mode to agents
    if DEBUG:
        env["ORCHESTRATOR_DEBUG"] = "1"

    port_vars = get_port_env_vars(agent_id)
    env.update(port_vars)

    # Determine the role module to run
    role_module = f"orchestrator.roles.{role}"

    debug_log(f"Spawning agent {agent_name}: module={role_module}, cwd={cwd}, lightweight={is_lightweight}")
    debug_log(f"Agent env: AGENT_FOCUS={env.get('AGENT_FOCUS', 'N/A')}, ports={port_vars}")

    # Set up log files for agent output
    agent_dir = get_agents_runtime_dir() / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = agent_dir / "stdout.log"
    stderr_log = agent_dir / "stderr.log"

    # Open log files (truncate on each run to keep them manageable)
    stdout_file = open(stdout_log, "w")
    stderr_file = open(stderr_log, "w")

    # Spawn the role as a subprocess
    process = subprocess.Popen(
        [sys.executable, "-m", role_module],
        cwd=cwd,
        env=env,
        stdout=stdout_file,
        stderr=stderr_file,
        start_new_session=True,  # Detach from parent
    )

    # Note: We don't close the files here - the subprocess will write to them
    # and they'll be closed when the subprocess exits. The file descriptors
    # are inherited by the child process.

    debug_log(f"Agent {agent_name} spawned with PID {process.pid}, logs: {stderr_log}")
    return process.pid


def read_agent_exit_code(agent_name: str) -> int | None:
    """Read the exit code written by an agent.

    Args:
        agent_name: Name of the agent

    Returns:
        Exit code or None if not found
    """
    exit_code_path = get_agents_runtime_dir() / agent_name / "exit_code"
    if not exit_code_path.exists():
        return None

    try:
        content = exit_code_path.read_text().strip()
        exit_code = int(content)
        # Clean up the file after reading
        exit_code_path.unlink()
        return exit_code
    except (ValueError, OSError):
        return None


def _get_server_url_from_config() -> str:
    """Read server URL from .octopoid/config.yaml."""
    try:
        import yaml
        config_path = get_orchestrator_dir() / "config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f)
            return config.get("server", {}).get("url", "")
    except Exception:
        pass
    return ""


def prepare_task_directory(
    task: dict,
    agent_name: str,
    agent_config: dict,
) -> Path:
    """Prepare a self-contained task directory for script-based agents.

    Creates:
        {task_dir}/worktree/     - git worktree (agent's cwd)
        {task_dir}/task.json     - task metadata
        {task_dir}/prompt.md     - rendered prompt
        {task_dir}/env.sh        - environment for scripts
        {task_dir}/scripts/      - executable agent scripts
        {task_dir}/result.json   - (written by scripts, read by scheduler)
        {task_dir}/notes.md      - progress notes
    """
    from .git_utils import create_task_worktree

    task_id = task["id"]
    task_dir = get_tasks_dir() / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    # Create worktree
    base_branch = task.get("branch", get_main_branch())
    worktree_path = create_task_worktree(task)

    # Write task.json
    import json
    (task_dir / "task.json").write_text(json.dumps(task, indent=2))

    # Copy and template scripts
    scripts_src = Path(__file__).parent / "agent_scripts"
    scripts_dest = task_dir / "scripts"
    scripts_dest.mkdir(exist_ok=True)

    venv_python = sys.executable  # Use the scheduler's Python

    for script in scripts_src.iterdir():
        if script.name.startswith("."):
            continue
        dest = scripts_dest / script.name
        content = script.read_text()
        # Replace shebang with explicit venv python
        if content.startswith("#!/usr/bin/env python3"):
            content = f"#!{venv_python}\n" + content.split("\n", 1)[1]
        dest.write_text(content)
        dest.chmod(0o755)

    # Write env.sh
    orchestrator_submodule = find_parent_project() / "orchestrator"
    env_lines = [
        "#!/bin/bash",
        f"export TASK_ID='{task_id}'",
        f"export TASK_TITLE='{task.get('title', '')}'",
        f"export BASE_BRANCH='{base_branch}'",
        f"export OCTOPOID_SERVER_URL='{os.environ.get('OCTOPOID_SERVER_URL') or _get_server_url_from_config()}'",
        f"export AGENT_NAME='{agent_name}'",
        f"export WORKTREE='{worktree_path}'",
        f"export ORCHESTRATOR_PYTHONPATH='{orchestrator_submodule}'",
        f"export RESULT_FILE='{task_dir / 'result.json'}'",
        f"export NOTES_FILE='{task_dir / 'notes.md'}'",
    ]
    (task_dir / "env.sh").write_text("\n".join(env_lines) + "\n")

    # Render prompt
    global_instructions = ""
    gi_path = get_global_instructions_path()
    if gi_path.exists():
        global_instructions = gi_path.read_text()

    # Get agent hooks from task
    hooks = task.get("hooks")
    agent_hooks = None
    if hooks:
        if isinstance(hooks, str):
            agent_hooks = [
                h for h in json.loads(hooks)
                if h.get("type") == "agent"
            ]
        elif isinstance(hooks, list):
            agent_hooks = [h for h in hooks if h.get("type") == "agent"]

    prompt = render_prompt(
        role="implementer",
        task=task,
        global_instructions=global_instructions,
        scripts_dir="../scripts",
        agent_hooks=agent_hooks,
    )
    (task_dir / "prompt.md").write_text(prompt)

    # Setup commands
    setup_agent_commands(agent_name, "implementer")

    debug_log(f"Prepared task directory: {task_dir}")
    return task_dir


def invoke_claude(task_dir: Path, agent_config: dict) -> int:
    """Invoke claude -p directly for a script-based agent.

    Args:
        task_dir: Path to the prepared task directory
        agent_config: Agent configuration dict

    Returns:
        PID of the spawned claude process
    """
    import json

    worktree_path = task_dir / "worktree"
    if not worktree_path.exists():
        # Worktree may be at a different location - check task.json
        task_data = json.loads((task_dir / "task.json").read_text())
        task_id = task_data.get("id", "")
        worktree_path = get_tasks_dir() / task_id / "worktree"

    prompt_path = task_dir / "prompt.md"
    prompt = prompt_path.read_text()

    model = agent_config.get("model", "sonnet")
    max_turns = agent_config.get("max_turns", 50)

    cmd = [
        "claude",
        "-p", prompt,
        "--allowedTools", "Read,Write,Edit,Glob,Grep,Bash,Skill",
        "--max-turns", str(max_turns),
        "--model", model,
    ]

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    # Source env.sh values using shlex to handle quoted values correctly
    env_sh = task_dir / "env.sh"
    if env_sh.exists():
        import shlex
        for line in env_sh.read_text().splitlines():
            if line.startswith("export "):
                assignment = line[7:]  # strip "export "
                eq_pos = assignment.find("=")
                if eq_pos < 1:
                    continue
                key = assignment[:eq_pos]
                raw_val = assignment[eq_pos + 1:]
                try:
                    val = shlex.split(raw_val)[0] if raw_val else ""
                except ValueError:
                    val = raw_val.strip("'\"")
                env[key] = val

    # Set up log files
    stdout_log = task_dir / "stdout.log"
    stderr_log = task_dir / "stderr.log"
    stdout_file = open(stdout_log, "w")
    stderr_file = open(stderr_log, "w")

    process = subprocess.Popen(
        cmd,
        cwd=worktree_path,
        env=env,
        stdout=stdout_file,
        stderr=stderr_file,
        start_new_session=True,
    )

    debug_log(f"Invoked claude for task dir {task_dir} with PID {process.pid}")
    return process.pid


def handle_agent_result(task_id: str, agent_name: str, task_dir: Path) -> None:
    """Handle the result of a script-based agent run.

    Reads result.json and transitions the task accordingly.

    Args:
        task_id: Task identifier
        agent_name: Name of the agent
        task_dir: Path to the task directory
    """
    import json

    result_path = task_dir / "result.json"

    if result_path.exists():
        try:
            result = json.loads(result_path.read_text())
        except json.JSONDecodeError:
            result = {"outcome": "error", "reason": "Invalid result.json"}
    else:
        # No result.json — check for progress
        notes_path = task_dir / "notes.md"
        has_notes = notes_path.exists() and notes_path.read_text().strip()

        if has_notes:
            result = {"outcome": "needs_continuation"}
        else:
            result = {"outcome": "error", "reason": "No result.json produced"}

    outcome = result.get("outcome", "error")
    debug_log(f"Task {task_id} result: {outcome}")

    try:
        sdk = queue_utils.get_sdk()

        if outcome == "submitted":
            # Count commits
            worktree = task_dir / "worktree"
            commits = 0
            if worktree.exists():
                try:
                    base = result.get("base_branch", get_main_branch())
                    count_result = subprocess.run(
                        ["git", "rev-list", "--count", f"origin/{base}..HEAD"],
                        cwd=worktree, capture_output=True, text=True, check=False,
                    )
                    if count_result.returncode == 0:
                        commits = int(count_result.stdout.strip())
                except (ValueError, subprocess.SubprocessError):
                    pass

            sdk.tasks.submit(
                task_id=task_id,
                commits_count=commits,
                turns_used=0,
            )

            # Update PR info if available
            pr_url = result.get("pr_url")
            pr_number = result.get("pr_number")
            if pr_url or pr_number:
                update_kwargs = {}
                if pr_url:
                    update_kwargs["pr_url"] = pr_url
                if pr_number:
                    update_kwargs["pr_number"] = pr_number
                if update_kwargs:
                    sdk.tasks.update(task_id, **update_kwargs)

        elif outcome == "done":
            sdk.tasks.submit(
                task_id=task_id,
                commits_count=0,
                turns_used=0,
            )

        elif outcome == "failed":
            reason = result.get("reason", "Agent reported failure")
            queue_utils.fail_task(task_id, reason=reason)

        elif outcome == "needs_continuation":
            queue_utils.mark_needs_continuation(task_id, agent_name=agent_name)

        else:
            queue_utils.fail_task(task_id, reason=f"Unknown outcome: {outcome}")

    except Exception as e:
        debug_log(f"Error handling result for {task_id}: {e}")
        try:
            queue_utils.fail_task(task_id, reason=f"Result handling error: {e}")
        except Exception:
            pass


def assign_qa_checks() -> None:
    """Auto-assign gk-qa check to provisional app tasks that have a staging_url.

    Scans provisional tasks and adds 'gk-qa' to the checks list for tasks that:
    - Have a staging_url (deployment is ready)
    - Are NOT orchestrator_impl tasks (those use gk-testing-octopoid)
    - Don't already have gk-qa in their checks list

    Tasks without a staging_url are silently skipped — they'll be picked up
    on a future tick once the staging URL is populated by _store_staging_url().
    """
    return




def process_orchestrator_hooks() -> None:
    """Run orchestrator-side hooks on provisional tasks.

    For each provisional task that has pending orchestrator hooks (e.g. merge_pr):
    1. Get pending orchestrator hooks
    2. Run each one via HookManager
    3. Record evidence
    4. If all hooks pass, accept the task
    """
    try:
        sdk = queue_utils.get_sdk()
        hook_manager = HookManager(sdk)

        # List provisional tasks
        provisional = sdk.tasks.list(queue="provisional")
        if not provisional:
            return

        for task in provisional:
            task_id = task.get("id", "")
            pending = hook_manager.get_pending_hooks(task, hook_type="orchestrator")
            if not pending:
                continue

            debug_log(f"Task {task_id}: {len(pending)} pending orchestrator hooks")

            for hook in pending:
                evidence = hook_manager.run_orchestrator_hook(task, hook)
                hook_manager.record_evidence(task_id, hook["name"], evidence)
                debug_log(f"  Hook {hook['name']}: {evidence.status} - {evidence.message}")

                if evidence.status == "failed":
                    debug_log(f"  Orchestrator hook {hook['name']} failed for {task_id}")
                    break

            # Re-fetch task to get updated hooks
            updated_task = sdk.tasks.get(task_id)
            if updated_task:
                can_accept, still_pending = hook_manager.can_transition(updated_task, "before_merge")
                if can_accept:
                    debug_log(f"All orchestrator hooks passed for {task_id}, accepting")
                    sdk.tasks.accept(task_id=task_id, accepted_by="scheduler-hooks")
                    print(f"[{datetime.now().isoformat()}] Accepted task {task_id} (all hooks passed)")

    except Exception as e:
        debug_log(f"Error processing orchestrator hooks: {e}")

def process_auto_accept_tasks() -> None:
    """Process provisional tasks that have auto_accept enabled.

    Moves tasks from provisional to done if auto_accept is true
    (either on the task itself or its parent project).
    """
    return


def process_gatekeeper_reviews() -> None:
    """Process provisional tasks that have per-task checks.

    Uses the DB-based check system (task.checks + task.check_results).
    Gatekeeper agents record results; this function acts as a safety-net
    pass that rejects any failed tasks the gatekeeper may have missed.

    Tasks with all checks passed are left in provisional for human review —
    they are NOT auto-accepted here.
    """
    return


def dispatch_gatekeeper_agents() -> None:
    """Dispatch gatekeeper agents for provisional tasks with pending checks.

    For each provisional task that has pending checks, find the first one
    and spawn a gatekeeper agent to evaluate it.

    Sequential execution: only one pending check is dispatched at a time per task.
    The next check will be dispatched on the following scheduler tick after the
    first one completes.
    """
    return


def check_and_update_finished_agents() -> None:
    """Check for agents that have finished and update their state."""
    agents_dir = get_agents_runtime_dir()
    if not agents_dir.exists():
        return

    for agent_dir in agents_dir.iterdir():
        if not agent_dir.is_dir():
            continue

        agent_name = agent_dir.name
        state_path = get_agent_state_path(agent_name)
        state = load_state(state_path)

        if state.running and state.pid:
            if not is_process_running(state.pid):
                # Process has finished, read exit code from file
                exit_code = read_agent_exit_code(agent_name)
                if exit_code is None:
                    # No exit code file - assume crashed
                    exit_code = 1
                    debug_log(f"Agent {agent_name} finished without exit code file, assuming crash")
                else:
                    debug_log(f"Agent {agent_name} finished with exit code {exit_code}")

                new_state = mark_finished(state, exit_code)
                save_state(new_state, state_path)

                # Handle script-mode agent results
                if state.extra.get("agent_mode") == "scripts" and state.extra.get("task_dir"):
                    task_dir_str = state.extra["task_dir"]
                    current_task = state.extra.get("current_task_id", "")
                    if current_task:
                        handle_agent_result(
                            current_task, agent_name, Path(task_dir_str)
                        )

                print(f"[{datetime.now().isoformat()}] Agent {agent_name} finished (exit code: {exit_code})")


# =============================================================================
# Queue Health Detection (for queue-manager agent)
# =============================================================================

def detect_queue_health_issues() -> dict[str, list[dict]]:
    """Detect queue health issues: file-DB mismatches, orphan files, zombie claims.

    Returns:
        Dictionary with keys 'file_db_mismatches', 'orphan_files', 'zombie_claims'
        Each value is a list of issue dictionaries with details.
    """
    return {'file_db_mismatches': [], 'orphan_files': [], 'zombie_claims': []}


def should_trigger_queue_manager() -> tuple[bool, str]:
    """Check if queue-manager agent should be triggered.

    Returns:
        (should_trigger, trigger_reason) where trigger_reason describes why
    """
    issues = detect_queue_health_issues()

    total_issues = (
        len(issues['file_db_mismatches']) +
        len(issues['orphan_files']) +
        len(issues['zombie_claims'])
    )

    if total_issues == 0:
        return False, ""

    # Build trigger reason
    parts = []
    if issues['file_db_mismatches']:
        parts.append(f"{len(issues['file_db_mismatches'])} file-DB mismatch(es)")
    if issues['orphan_files']:
        parts.append(f"{len(issues['orphan_files'])} orphan file(s)")
    if issues['zombie_claims']:
        parts.append(f"{len(issues['zombie_claims'])} zombie claim(s)")

    trigger_reason = ", ".join(parts)
    return True, trigger_reason


def ensure_rebaser_worktree() -> Path | None:
    """Ensure the dedicated rebaser worktree exists.

    Creates .octopoid/runtime/agents/rebaser-worktree/ on first use,
    detached at HEAD. Runs npm install if node_modules is missing.

    Returns:
        Path to the rebaser worktree, or None on failure
    """
    parent_project = find_parent_project()
    worktree_path = get_agents_runtime_dir() / "rebaser-worktree"

    if worktree_path.exists() and (worktree_path / ".git").exists():
        return worktree_path

    debug_log("Creating dedicated rebaser worktree")

    try:
        # Create the worktree detached at HEAD
        result = subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree_path), "HEAD"],
            cwd=parent_project,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            debug_log(f"Failed to create rebaser worktree: {result.stderr}")
            return None

        # Run npm install
        debug_log("Running npm install in rebaser worktree")
        subprocess.run(
            ["npm", "install"],
            cwd=worktree_path,
            capture_output=True,
            timeout=120,
        )

        print(f"[{datetime.now().isoformat()}] Created rebaser worktree at {worktree_path}")
        return worktree_path

    except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
        debug_log(f"Error creating rebaser worktree: {e}")
        return None


def check_branch_freshness() -> None:
    """Check provisional app tasks for stale branches and rebase them.

    For each provisional task where role='implement' and pr_url is set:
    - Uses git merge-base --is-ancestor to check if main is ancestor of branch
    - If main is NOT an ancestor, the branch is stale
    - Skips branches that were rebased recently (throttled by last_rebase_attempt_at)
    - Skips orchestrator_impl tasks (self-merge handles those)
    - Calls rebase_stale_branch() directly for stale branches
    """
    return


def _is_branch_fresh(parent_project: Path, branch: str) -> bool | None:
    """Check if origin/main is an ancestor of the branch (i.e. branch is fresh).

    Uses `git merge-base --is-ancestor origin/main origin/<branch>`.

    Args:
        parent_project: Path to the git repo
        branch: Branch name to check

    Returns:
        True if branch is fresh (main is ancestor), False if stale,
        None if branch not found or error
    """
    try:
        # Check that the remote branch exists
        result = subprocess.run(
            ['git', 'rev-parse', '--verify', f'origin/{branch}'],
            cwd=parent_project,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None

        # Check if origin/main is an ancestor of the branch
        result = subprocess.run(
            ['git', 'merge-base', '--is-ancestor', 'origin/main', f'origin/{branch}'],
            cwd=parent_project,
            capture_output=True,
            text=True,
            timeout=10,
        )

        # Exit code 0 means main IS ancestor (branch is fresh)
        # Exit code 1 means main is NOT ancestor (branch is stale)
        return result.returncode == 0

    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None


def rebase_stale_branch(task_id: str, branch: str) -> bool:
    """Rebase a stale branch onto main, run tests, and force-push.

    On conflict: rebase --abort, reject task back to agent with conflict details.
    On test failure: reject task back to agent with test output.
    On success: force-push with --force-with-lease, log success.

    Args:
        task_id: Task identifier
        branch: Branch name to rebase

    Returns:
        True if rebase succeeded and was pushed
    """
    return False


def check_stale_branches(commits_behind_threshold: int = 5) -> None:
    """Check provisional/review tasks for branch staleness and mark for rebase.

    Compares each task's branch against current origin/main. If the branch
    is behind by N+ commits, sets needs_rebase=True in the DB.

    Skips:
    - Tasks already marked for rebase
    - orchestrator_impl tasks (v1 limitation)
    - Tasks without a branch (or on main)

    Args:
        commits_behind_threshold: Number of commits behind main before
            marking for rebase (default 5)
    """
    return


def _count_commits_behind(parent_project: Path, branch: str) -> int | None:
    """Count how many commits a branch is behind origin/main.

    Args:
        parent_project: Path to the git repo
        branch: Branch name to check

    Returns:
        Number of commits behind, or None if branch not found
    """
    try:
        # Check that the remote branch exists
        result = subprocess.run(
            ['git', 'rev-parse', '--verify', f'origin/{branch}'],
            cwd=parent_project,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None

        # Count commits on main that are not on the branch
        result = subprocess.run(
            ['git', 'rev-list', '--count', f'origin/{branch}..origin/main'],
            cwd=parent_project,
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            return None

        return int(result.stdout.strip())

    except (subprocess.TimeoutExpired, subprocess.SubprocessError, ValueError):
        return None


# Track last queue health check time (global state)
_last_queue_health_check: datetime | None = None
QUEUE_HEALTH_CHECK_INTERVAL_SECONDS = 1800  # 30 minutes


def _check_queue_health_throttled() -> None:
    """Check queue health with throttling to avoid running too frequently."""
    global _last_queue_health_check

    now = datetime.now()

    # Check if enough time has passed since last check
    if _last_queue_health_check is not None:
        elapsed = (now - _last_queue_health_check).total_seconds()
        if elapsed < QUEUE_HEALTH_CHECK_INTERVAL_SECONDS:
            return  # Not time yet

    # Update last check time
    _last_queue_health_check = now

    # Run the actual check
    check_queue_health()


def check_queue_health() -> None:
    """Check queue health and invoke queue-manager agent if issues found.

    Runs the diagnostic script and spawns queue-manager agent if any issues
    are detected. This is called periodically from the scheduler (every 30 minutes).
    """
    # Path to diagnostic script
    parent_project = find_parent_project()
    script_path = parent_project / ".octopoid" / "scripts" / "diagnose_queue_health.py"

    if not script_path.exists():
        debug_log("Queue health diagnostic script not found, skipping")
        return

    # Run diagnostic script with JSON output
    try:
        result = subprocess.run(
            [sys.executable, str(script_path), "--json"],
            cwd=parent_project,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            # No issues found
            debug_log("Queue health check: no issues found")
            return

        # Parse diagnostic output
        import json
        try:
            diagnostic_data = json.loads(result.stdout)
        except json.JSONDecodeError:
            debug_log(f"Failed to parse diagnostic output: {result.stdout[:200]}")
            return

        # Count issues
        mismatches = len(diagnostic_data.get("file_db_mismatches", []))
        orphans = len(diagnostic_data.get("orphan_files", []))
        zombies = len(diagnostic_data.get("zombie_claims", []))

        total_issues = mismatches + orphans + zombies

        if total_issues == 0:
            debug_log("Queue health check: no issues found")
            return

        # Issues found - log summary
        print(f"[{datetime.now().isoformat()}] Queue health issues detected:")
        print(f"  File-DB mismatches: {mismatches}")
        print(f"  Orphan files: {orphans}")
        print(f"  Zombie claims: {zombies}")
        debug_log(f"Queue health issues: {mismatches} mismatches, {orphans} orphans, {zombies} zombies")

        # Check if queue-manager agent is configured and ready to run
        agents = get_agents()
        queue_manager = next((a for a in agents if a.get("role") == "queue_manager"), None)

        if not queue_manager:
            debug_log("No queue-manager agent configured")
            return

        if queue_manager.get("paused", False):
            debug_log("Queue-manager agent is paused, not invoking")
            print(f"  (queue-manager agent is paused - issues not auto-reported)")
            return

        # Trigger queue-manager agent by setting environment variable
        # The agent's prompt will check this variable to know why it was triggered
        agent_name = queue_manager.get("name", "queue-manager")
        print(f"  Triggering {agent_name} to diagnose and report issues")
        debug_log(f"Triggering {agent_name} with {total_issues} issues")

        # Write diagnostic data to a temp file for the agent to read
        from .config import get_notes_dir as _get_notes_dir
        notes_dir = _get_notes_dir()
        notes_dir.mkdir(parents=True, exist_ok=True)
        diagnostic_file = notes_dir / f"queue-health-diagnostic-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        diagnostic_file.write_text(json.dumps(diagnostic_data, indent=2))

        debug_log(f"Wrote diagnostic data to {diagnostic_file}")

        # The queue-manager agent will read this file and generate a report
        # For now, we just log that issues were found. In a future phase, we
        # could automatically spawn the agent here.

    except subprocess.TimeoutExpired:
        debug_log("Queue health diagnostic timed out")
    except Exception as e:
        debug_log(f"Queue health check failed: {e}")


def _register_orchestrator() -> None:
    """Register this orchestrator with the API server (idempotent)."""
    try:
        from .queue_utils import get_sdk, get_orchestrator_id
        sdk = get_sdk()
        orch_id = get_orchestrator_id()
        parts = orch_id.split("-", 1)
        cluster = parts[0] if len(parts) > 1 else "default"
        machine_id = parts[1] if len(parts) > 1 else orch_id
        sdk._request("POST", "/api/v1/orchestrators/register", json={
            "id": orch_id,
            "cluster": cluster,
            "machine_id": machine_id,
            "repo_url": "",
            "version": "2.0.0",
            "max_agents": 3,
        })
        debug_log(f"Registered orchestrator: {orch_id}")
    except Exception as e:
        debug_log(f"Orchestrator registration failed (non-fatal): {e}")


def run_scheduler() -> None:
    """Main scheduler loop - evaluate and spawn agents."""
    print(f"[{datetime.now().isoformat()}] Scheduler starting")
    debug_log("Scheduler tick starting")

    # Register orchestrator with API server (idempotent)
    _register_orchestrator()

    # Check global pause flag
    if is_system_paused():
        print("System is paused (set 'paused: false' in agents.yaml to resume)")
        debug_log("System is paused globally")
        return

    # Check for finished agents first
    check_and_update_finished_agents()

    # Check queue health (runs every 30 minutes)
    _check_queue_health_throttled()

    # Run orchestrator-side hooks on provisional tasks (e.g. merge_pr)
    process_orchestrator_hooks()

    # Process auto-accept tasks in provisional queue
    process_auto_accept_tasks()

    # Auto-assign gk-qa checks to app tasks with staging URLs
    assign_qa_checks()

    # Process gatekeeper reviews for provisional tasks
    process_gatekeeper_reviews()

    # Dispatch gatekeeper agents for pending checks
    dispatch_gatekeeper_agents()

    # Check for stale branches that need rebasing
    check_stale_branches()

    # Check branch freshness and auto-rebase stale provisional branches
    check_branch_freshness()

    # Check queue health and trigger queue-manager if issues detected
    should_trigger, trigger_reason = should_trigger_queue_manager()
    if should_trigger:
        debug_log(f"Queue health issues detected: {trigger_reason}")
        # The queue-manager agent will be evaluated in the normal agent loop below
        # We just log the detection here; the agent's pre-check handles actual triggering

    # Load agent configuration
    try:
        agents = get_agents()
        debug_log(f"Loaded {len(agents)} agents from config")
    except FileNotFoundError as e:
        print(f"Error: {e}")
        debug_log(f"Failed to load agents config: {e}")
        sys.exit(1)

    if not agents:
        print("No agents configured in agents.yaml")
        debug_log("No agents configured")
        return

    for agent_id, agent_config in enumerate(agents):
        agent_name = agent_config.get("name")
        role = agent_config.get("role")
        interval = agent_config.get("interval_seconds", 300)
        paused = agent_config.get("paused", False)

        if not agent_name or not role:
            print(f"Skipping invalid agent config: {agent_config}")
            debug_log(f"Invalid agent config: {agent_config}")
            continue

        if paused:
            print(f"Agent {agent_name} is paused, skipping")
            debug_log(f"Agent {agent_name} is paused")
            continue

        debug_log(f"Evaluating agent {agent_name}: role={role}, interval={interval}s")

        # Try to acquire agent lock
        agent_lock_path = get_agent_lock_path(agent_name)

        with locked_or_skip(agent_lock_path) as acquired:
            if not acquired:
                print(f"Agent {agent_name} is locked (another instance running?)")
                debug_log(f"Agent {agent_name} lock not acquired")
                continue

            # Load agent state
            state_path = get_agent_state_path(agent_name)
            state = load_state(state_path)
            debug_log(f"Agent {agent_name} state: running={state.running}, pid={state.pid}, last_finished={state.last_finished}")

            # Check if still running
            if state.running and state.pid and is_process_running(state.pid):
                print(f"Agent {agent_name} is still running (PID {state.pid})")
                debug_log(f"Agent {agent_name} still running (PID {state.pid})")
                continue

            # If was marked running but process died, update state
            if state.running:
                debug_log(f"Agent {agent_name} was marked running but process died, marking as crashed")
                state = mark_finished(state, 1)  # Assume crashed
                save_state(state, state_path)

            # Check if overdue
            if not is_overdue(state, interval):
                print(f"Agent {agent_name} is not due yet")
                debug_log(f"Agent {agent_name} not due yet")
                continue

            # Check role-based backpressure (runs before spawning any process)
            can_proceed, blocked_reason = check_backpressure_for_role(role)
            if not can_proceed:
                print(f"Agent {agent_name} blocked: {blocked_reason}")
                debug_log(f"Agent {agent_name} blocked by backpressure: {blocked_reason}")
                # Update state to track blocked status
                state.extra["blocked_reason"] = blocked_reason
                state.extra["blocked_at"] = datetime.now().isoformat()
                save_state(state, state_path)
                continue

            # Clear any previous blocked status
            if "blocked_reason" in state.extra:
                del state.extra["blocked_reason"]
            if "blocked_at" in state.extra:
                del state.extra["blocked_at"]

            # Run pre-check if configured (additional cheap check for work availability)
            if not run_pre_check(agent_name, agent_config):
                print(f"Agent {agent_name} pre-check: no work available")
                debug_log(f"Agent {agent_name} pre-check returned no work")
                continue

            # For claimable agents: claim task before spawning.
            # This ensures the agent has work and avoids wasted startups.
            claimed_task = None
            if role in CLAIMABLE_AGENT_ROLES:
                task_role = AGENT_TASK_ROLE[role]
                # Read allowed task types from agent config (None = any type)
                allowed_types = agent_config.get("allowed_task_types")
                type_filter = allowed_types[0] if isinstance(allowed_types, list) and len(allowed_types) == 1 else (
                    ",".join(allowed_types) if isinstance(allowed_types, list) else allowed_types
                )
                claimed_task = claim_and_prepare_task(agent_name, task_role, type_filter=type_filter)
                if claimed_task is None:
                    debug_log(f"No task available for {agent_name}")
                    continue
                debug_log(f"Claimed task {claimed_task['id']} for {agent_name}")

            print(f"[{datetime.now().isoformat()}] Starting agent {agent_name} (role: {role})")
            debug_log(f"Starting agent {agent_name} (role: {role})")

            # Implementers use scripts mode: prepare task dir and invoke claude directly
            if role == "implementer" and claimed_task:
                task_dir = prepare_task_directory(claimed_task, agent_name, agent_config)
                pid = invoke_claude(task_dir, agent_config)

                new_state = mark_started(state, pid)
                new_state.extra["agent_mode"] = "scripts"
                new_state.extra["task_dir"] = str(task_dir)
                new_state.extra["current_task_id"] = claimed_task["id"]
                save_state(new_state, state_path)

                print(f"Agent {agent_name} started with PID {pid}")
                continue

            # Check if this is a lightweight agent (no worktree needed)
            is_lightweight = agent_config.get("lightweight", False)

            if not is_lightweight:
                # Determine base branch for worktree
                base_branch = agent_config.get("base_branch", get_main_branch())

                if claimed_task:
                    # Use the claimed task's branch for the worktree
                    task_branch = claimed_task.get("branch")
                    if task_branch and task_branch != "main":
                        debug_log(f"Using claimed task branch for {agent_name}: {task_branch}")
                        base_branch = task_branch
                else:
                    # For non-implementer agents, peek at queue for branch hint
                    task_branch = peek_task_branch(role)
                    if task_branch:
                        debug_log(f"Peeked task branch for {agent_name}: {task_branch}")
                        base_branch = task_branch

                debug_log(f"Ensuring worktree for {agent_name} on branch {base_branch}")
                ensure_worktree(agent_name, base_branch)

                # Initialize submodule for orchestrator_impl agents
                if role == "orchestrator_impl":
                    worktree_path = get_worktree_path(agent_name)
                    debug_log(f"Initializing submodule in worktree for {agent_name}")
                    try:
                        subprocess.run(
                            ["git", "submodule", "update", "--init", "orchestrator"],
                            cwd=worktree_path,
                            capture_output=True,
                            text=True,
                            timeout=120,
                        )
                        # Checkout main in the submodule and fetch latest
                        sub_path = worktree_path / "orchestrator"
                        subprocess.run(
                            ["git", "checkout", "main"],
                            cwd=sub_path,
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                        subprocess.run(
                            ["git", "fetch", "origin", "main"],
                            cwd=sub_path,
                            capture_output=True,
                            text=True,
                            timeout=60,
                        )
                        subprocess.run(
                            ["git", "reset", "--hard", "origin/main"],
                            cwd=sub_path,
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                        # Verify the submodule has its own git object store
                        # (not sharing with the main checkout's submodule)
                        _verify_submodule_isolation(sub_path, agent_name)
                        debug_log(f"Submodule initialized for {agent_name}")
                    except Exception as e:
                        debug_log(f"Submodule init failed for {agent_name}: {e}")

                # Setup commands in worktree
                debug_log(f"Setting up commands for {agent_name}")
                setup_agent_commands(agent_name, role)

                # Generate agent instructions
                debug_log(f"Generating instructions for {agent_name}")
                generate_agent_instructions(agent_name, role, agent_config)
            else:
                debug_log(f"Agent {agent_name} is lightweight, skipping worktree setup")

            # Write env file
            debug_log(f"Writing env file for {agent_name}")
            write_agent_env(agent_name, agent_id, role, agent_config)

            # Spawn agent
            pid = spawn_agent(agent_name, agent_id, role, agent_config)

            # Update JSON state
            new_state = mark_started(state, pid)
            save_state(new_state, state_path)

            print(f"Agent {agent_name} started with PID {pid}")

    print(f"[{datetime.now().isoformat()}] Scheduler tick complete")
    debug_log("Scheduler tick complete")


def _check_venv_integrity() -> None:
    """Verify the orchestrator module is loaded from the correct location.

    If an agent runs `pip install -e .` inside its worktree, it hijacks the
    shared venv to load code from the wrong directory. Detect this and abort.
    """
    import orchestrator as _orch
    mod_file = getattr(_orch, "__file__", None) or ""
    # Also check a submodule to catch editable installs that set __file__ on the package
    scheduler_file = str(Path(__file__).resolve())
    if "agents/" in scheduler_file and "worktree" in scheduler_file:
        print(
            f"FATAL: orchestrator module loaded from agent worktree: {scheduler_file}\n"
            f"Fix: cd orchestrator && pip install -e .",
            file=sys.stderr,
        )
        sys.exit(1)


def main() -> None:
    """Entry point for scheduler."""
    global DEBUG

    _check_venv_integrity()

    parser = argparse.ArgumentParser(description="Run the orchestrator scheduler")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging to .octopoid/runtime/logs/",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (don't wait for lock)",
    )
    args = parser.parse_args()

    DEBUG = args.debug
    if DEBUG:
        setup_scheduler_debug()
        debug_log("Scheduler starting with debug mode enabled")
        print("Debug mode enabled - logs in .octopoid/runtime/logs/")

    scheduler_lock_path = get_scheduler_lock_path()

    with locked_or_skip(scheduler_lock_path) as acquired:
        if not acquired:
            print("Another scheduler instance is running, exiting")
            debug_log("Scheduler lock not acquired - another instance running")
            sys.exit(0)

        debug_log("Scheduler lock acquired")
        run_scheduler()


# Default template if file doesn't exist
DEFAULT_AGENT_INSTRUCTIONS_TEMPLATE = """# Agent Instructions

**Agent:** $agent_name
**Role:** $role
**Generated:** $timestamp

## Identity

You are an autonomous agent named **$agent_name** with the role of **$role**.
You are part of a multi-agent system coordinated by an orchestrator.

## Global Instructions

$global_instructions

$task_section

## Constraints

$constraints

## Important Notes

- Always commit your changes with clear, descriptive messages
- If you encounter errors, document them clearly
- Do not modify files outside your authorized scope
- Coordinate through the task queue, not direct communication
"""


if __name__ == "__main__":
    main()
