#!/usr/bin/env python3
"""Main scheduler - runs on 1-minute ticks to evaluate and spawn agents."""

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from string import Template



from .backpressure import check_backpressure_for_role
from .config import (
    find_parent_project,
    get_agents,
    get_agents_runtime_dir,
    get_commands_dir,
    get_gatekeeper_config,
    get_global_instructions_path,
    get_orchestrator_dir,
    get_templates_dir,
    is_db_enabled,
    is_gatekeeper_enabled,
    is_system_paused,
)
from .git_utils import ensure_worktree, get_worktree_path
from .lock_utils import locked_or_skip
from .port_utils import get_port_env_vars
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
    logs_dir = get_orchestrator_dir() / "logs"
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

    if not is_db_enabled():
        return None

    from . import db

    # Map roles to the queues they pull from
    role_queues = {
        "breakdown": "breakdown",
        "implement": "incoming",
        "test": "incoming",
    }

    queue = role_queues.get(role)
    if not queue:
        return None

    tasks = db.list_tasks(queue=queue)
    if not tasks:
        return None

    # Return the branch of the first (highest priority) task
    branch = tasks[0].get("branch")
    return branch if branch and branch != "main" else None


def get_scheduler_lock_path() -> Path:
    """Get path to the global scheduler lock file."""
    return get_orchestrator_dir() / "scheduler.lock"


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
        "check_runner": """
- You do NOT need a worktree (lightweight agent)
- Run automated checks on provisional tasks with pending checks
- For pytest-submodule: cherry-pick agent commits, run pytest
- Record pass/fail results in the DB check_results field
- Reject failed tasks back to agents with test output
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

    parent_project = find_parent_project()
    worktree_path = get_worktree_path(agent_name)
    shared_dir = get_orchestrator_dir() / "shared"

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
    env["SHARED_DIR"] = str(get_orchestrator_dir() / "shared")
    env["ORCHESTRATOR_DIR"] = str(get_orchestrator_dir())

    # Set PYTHONPATH to include the orchestrator submodule
    # This allows `import orchestrator.orchestrator...` to work
    orchestrator_submodule = find_parent_project() / "orchestrator"
    existing_pythonpath = env.get("PYTHONPATH", "")
    if existing_pythonpath:
        env["PYTHONPATH"] = f"{orchestrator_submodule}:{existing_pythonpath}"
    else:
        env["PYTHONPATH"] = str(orchestrator_submodule)

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


def process_auto_accept_tasks() -> None:
    """Process provisional tasks that have auto_accept enabled.

    Moves tasks from provisional to done if auto_accept is true
    (either on the task itself or its parent project).
    """
    if not is_db_enabled():
        return

    try:
        from . import db

        # Get all provisional tasks
        provisional_tasks = db.list_tasks(queue="provisional")

        for task in provisional_tasks:
            task_id = task["id"]
            auto_accept = task.get("auto_accept", False)

            # Check project-level auto_accept if task doesn't have it
            if not auto_accept and task.get("project_id"):
                project = db.get_project(task["project_id"])
                if project:
                    auto_accept = project.get("auto_accept", False)

            if auto_accept:
                debug_log(f"Auto-accepting task {task_id}")
                db.accept_completion(task_id, accepted_by="scheduler")
                print(f"[{datetime.now().isoformat()}] Auto-accepted task {task_id}")

    except Exception as e:
        debug_log(f"Error processing auto-accept tasks: {e}")


def process_gatekeeper_reviews() -> None:
    """Process provisional tasks that need gatekeeper review.

    For each provisional task with commits (passed pre-check but not yet reviewed):
    1. Check if gatekeeper review tracking already exists
    2. If not, initialize review tracking
    3. If all checks complete, apply pass/fail decision
    """
    if not is_db_enabled() or not is_gatekeeper_enabled():
        return

    try:
        from . import db
        from .review_utils import (
            all_reviews_complete,
            all_reviews_passed,
            cleanup_review,
            get_review_feedback,
            has_active_review,
            init_task_review,
        )
        from .queue_utils import review_reject_task

        gk_config = get_gatekeeper_config()
        required_checks = gk_config.get("required_checks", ["architecture", "testing", "qa"])
        max_rejections = gk_config.get("max_rejections", 3)

        # Get all provisional tasks with commits
        provisional_tasks = db.list_tasks(queue="provisional")

        for task in provisional_tasks:
            task_id = task["id"]
            commits = task.get("commits_count", 0)
            auto_accept = task.get("auto_accept", False)

            # Skip auto-accept tasks (handled by process_auto_accept_tasks)
            if auto_accept:
                continue

            # Skip tasks without commits (pre-check handles these)
            if commits == 0:
                continue

            # Check if review is already initialized
            if has_active_review(task_id):
                # Check if all reviews are complete
                if all_reviews_complete(task_id):
                    passed, failed_checks = all_reviews_passed(task_id)
                    task_file_path = task.get("file_path", "")

                    if passed:
                        # All checks passed — accept the task
                        debug_log(f"All gatekeeper checks passed for task {task_id}")
                        db.accept_completion(task_id, accepted_by="gatekeeper")
                        cleanup_review(task_id)
                        print(f"[{datetime.now().isoformat()}] Gatekeeper approved task {task_id}")
                    else:
                        # Some checks failed — reject with feedback
                        feedback = get_review_feedback(task_id)
                        debug_log(f"Gatekeeper checks failed for task {task_id}: {failed_checks}")

                        if task_file_path:
                            review_reject_task(
                                task_file_path,
                                feedback,
                                rejected_by="gatekeeper",
                                max_rejections=max_rejections,
                            )
                        else:
                            # No file path, just update DB
                            db.review_reject_completion(
                                task_id,
                                reason=f"Failed checks: {', '.join(failed_checks)}",
                                reviewer="gatekeeper",
                            )

                        cleanup_review(task_id)
                        print(f"[{datetime.now().isoformat()}] Gatekeeper rejected task {task_id}: {failed_checks}")
            else:
                # Initialize review tracking for this task
                branch = task.get("branch", "main")
                debug_log(f"Initializing gatekeeper review for task {task_id} (branch: {branch})")
                init_task_review(
                    task_id,
                    branch=branch,
                    base_branch="main",
                    required_checks=required_checks,
                )
                print(f"[{datetime.now().isoformat()}] Initialized gatekeeper review for task {task_id}")

    except Exception as e:
        debug_log(f"Error processing gatekeeper reviews: {e}")
        import traceback
        debug_log(traceback.format_exc())


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

                # Also update DB if enabled
                if is_db_enabled():
                    try:
                        from . import db
                        db.mark_agent_finished(agent_name)
                    except Exception as e:
                        debug_log(f"Failed to update agent {agent_name} in DB: {e}")

                print(f"[{datetime.now().isoformat()}] Agent {agent_name} finished (exit code: {exit_code})")


def run_scheduler() -> None:
    """Main scheduler loop - evaluate and spawn agents."""
    print(f"[{datetime.now().isoformat()}] Scheduler starting")
    debug_log("Scheduler tick starting")

    # Check global pause flag
    if is_system_paused():
        print("System is paused (set 'paused: false' in agents.yaml to resume)")
        debug_log("System is paused globally")
        return

    # Check for finished agents first
    check_and_update_finished_agents()

    # Process auto-accept tasks in provisional queue
    process_auto_accept_tasks()

    # Process gatekeeper reviews for provisional tasks
    process_gatekeeper_reviews()

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

            print(f"[{datetime.now().isoformat()}] Starting agent {agent_name} (role: {role})")
            debug_log(f"Starting agent {agent_name} (role: {role})")

            # Check if this is a lightweight agent (no worktree needed)
            is_lightweight = agent_config.get("lightweight", False)

            if not is_lightweight:
                # Determine base branch for worktree
                base_branch = agent_config.get("base_branch", "main")

                # For agents that work on specific queues, peek at the next task's branch
                # This avoids agents wasting turns on git checkout
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

            # Also update DB if enabled
            if is_db_enabled():
                try:
                    from . import db
                    db.upsert_agent(agent_name, role=role, running=True, pid=pid)
                    db.mark_agent_started(agent_name, pid)
                except Exception as e:
                    debug_log(f"Failed to update agent {agent_name} in DB: {e}")

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
            f"Fix: cd orchestrator && ../.orchestrator/venv/bin/pip install -e .",
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
        help="Enable debug logging to .orchestrator/logs/",
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
        print("Debug mode enabled - logs in .orchestrator/logs/")

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
