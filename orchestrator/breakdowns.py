"""Breakdown management and task recycling.

This module handles breakdown approval, branch creation, and task recycling
when tasks burn out or become too large.
"""

import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import get_orchestrator_dir, get_main_branch, get_queue_dir
from .sdk import get_sdk


def get_breakdowns_dir() -> Path:
    """Get the breakdowns directory.

    Returns:
        Path to .octopoid/shared/breakdowns/
    """
    breakdowns_dir = get_orchestrator_dir() / "shared" / "breakdowns"
    breakdowns_dir.mkdir(parents=True, exist_ok=True)
    return breakdowns_dir


def list_pending_breakdowns() -> list[dict]:
    """List all pending breakdown files.

    Returns:
        List of breakdown info dicts with path, project_id, title, task_count
    """
    breakdowns_dir = get_breakdowns_dir()
    results = []

    for path in breakdowns_dir.glob("*.md"):
        content = path.read_text()

        # Parse metadata
        project_match = re.search(r'\*\*Project:\*\*\s*(\S+)', content)
        status_match = re.search(r'\*\*Status:\*\*\s*(\S+)', content)
        title_match = re.search(r'^# Breakdown:\s*(.+)$', content, re.MULTILINE)

        # Count tasks
        task_count = len(re.findall(r'^## Task \d+:', content, re.MULTILINE))

        status = status_match.group(1) if status_match else "unknown"

        if status == "pending_review":
            results.append({
                "path": path,
                "project_id": project_match.group(1) if project_match else None,
                "title": title_match.group(1) if title_match else path.stem,
                "task_count": task_count,
                "status": status,
            })

    return results


def approve_breakdown(identifier: str) -> dict[str, Any]:
    """Approve a breakdown and create tasks from it.

    Args:
        identifier: Project ID (PROJ-xxx) or breakdown filename

    Returns:
        Dict with created task info
    """
    # Import here to avoid circular dependency
    from .tasks import create_task

    breakdowns_dir = get_breakdowns_dir()

    # Find the breakdown file
    if identifier.startswith("PROJ-"):
        breakdown_path = breakdowns_dir / f"{identifier}-breakdown.md"
    else:
        breakdown_path = breakdowns_dir / f"{identifier}.md"
        if not breakdown_path.exists():
            breakdown_path = breakdowns_dir / identifier

    if not breakdown_path.exists():
        raise FileNotFoundError(f"Breakdown file not found: {breakdown_path}")

    content = breakdown_path.read_text()

    # Parse metadata
    project_match = re.search(r'\*\*Project:\*\*\s*(\S+)', content)
    branch_match = re.search(r'\*\*Branch:\*\*\s*(\S+)', content)
    status_match = re.search(r'\*\*Status:\*\*\s*(\S+)', content)
    parent_depth_match = re.search(r'\*\*Parent Breakdown Depth:\*\*\s*(\d+)', content)

    project_id = project_match.group(1) if project_match else None
    branch = branch_match.group(1) if branch_match else "main"
    status = status_match.group(1) if status_match else "unknown"
    parent_breakdown_depth = int(parent_depth_match.group(1)) if parent_depth_match else 0

    if status != "pending_review":
        raise ValueError(f"Breakdown is not pending review (status: {status})")

    # Create feature branch if not main
    if branch and branch != "main":
        _create_and_push_branch(branch)

    # Parse tasks
    tasks = _parse_breakdown_tasks(content)

    if not tasks:
        raise ValueError("No tasks found in breakdown file")

    # Create tasks
    created_ids = []
    id_map = {}  # Map from task number to actual task ID

    for task in tasks:
        task_num = task["number"]
        title = task["title"]
        role = task.get("role", "implement")
        priority = task.get("priority", "P2")
        context = task.get("context", "")
        criteria = task.get("acceptance_criteria", [])
        depends_on = task.get("depends_on", [])

        # Resolve dependencies to actual task IDs
        blocked_by = None
        if depends_on:
            blocker_ids = []
            for dep_num in depends_on:
                if dep_num in id_map:
                    blocker_ids.append(id_map[dep_num])
            if blocker_ids:
                blocked_by = ",".join(blocker_ids)

        task_path = create_task(
            title=title,
            role=role,
            context=context,
            acceptance_criteria=criteria if criteria else ["Complete the task"],
            priority=priority,
            branch=branch,
            created_by="human",
            blocked_by=blocked_by,
            project_id=project_id,
            queue="incoming",
            breakdown_depth=parent_breakdown_depth + 1,
        )

        # Extract task ID from path
        task_id = task_path.stem.replace("TASK-", "")
        id_map[task_num] = task_id
        created_ids.append(task_id)

    # Find leaf subtasks (ones that no other subtask depends on)
    # These are the "exit points" of the breakdown
    depended_on = set()
    for task in tasks:
        for dep_num in task.get("depends_on", []):
            if dep_num in id_map:
                depended_on.add(id_map[dep_num])
    leaf_ids = [tid for tid in created_ids if tid not in depended_on]

    # Update breakdown file status to approved
    updated_content = content.replace(
        "**Status:** pending_review",
        f"**Status:** approved\n**Approved:** {datetime.now().isoformat()}"
    )
    breakdown_path.write_text(updated_content)

    return {
        "breakdown_file": str(breakdown_path),
        "project_id": project_id,
        "tasks_created": len(created_ids),
        "task_ids": created_ids,
        "leaf_ids": leaf_ids,
    }


def _create_and_push_branch(branch: str, base_branch: str = "main") -> bool:
    """Create a feature branch and push it to origin.

    Called during breakdown approval to ensure the branch exists
    before agents try to check it out.

    Args:
        branch: Name of the feature branch to create
        base_branch: Branch to create from (default: main)

    Returns:
        True if branch was created or already exists, False on error
    """
    try:
        # First, fetch to make sure we have latest
        subprocess.run(
            ["git", "fetch", "origin"],
            capture_output=True,
            check=False,
        )

        # Check if branch already exists on origin
        result = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", branch],
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            # Branch already exists on origin
            return True

        # Check if branch exists locally
        result = subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            capture_output=True,
            text=True,
        )
        branch_exists_locally = result.returncode == 0

        if not branch_exists_locally:
            # Create the branch from base_branch
            subprocess.run(
                ["git", "branch", branch, f"origin/{base_branch}"],
                capture_output=True,
                check=True,
            )

        # Push to origin
        subprocess.run(
            ["git", "push", "-u", "origin", branch],
            capture_output=True,
            check=True,
        )

        return True

    except subprocess.CalledProcessError as e:
        # Log but don't fail - agents will report the error if branch is missing
        import sys
        print(f"Warning: Failed to create/push branch {branch}: {e}", file=sys.stderr)
        return False


def _parse_breakdown_tasks(content: str) -> list[dict]:
    """Parse tasks from a breakdown markdown file.

    Args:
        content: Markdown content of breakdown file

    Returns:
        List of task dicts with number, title, role, priority, context, criteria, depends_on
    """
    tasks = []

    # Split on task headers
    task_pattern = r'^## Task (\d+):\s*(.+)$'
    task_matches = list(re.finditer(task_pattern, content, re.MULTILINE))

    for i, match in enumerate(task_matches):
        task_num = int(match.group(1))
        title = match.group(2).strip()

        # Get content until next task or end
        start = match.end()
        end = task_matches[i + 1].start() if i + 1 < len(task_matches) else len(content)
        task_content = content[start:end]

        # Parse metadata
        role_match = re.search(r'\*\*Role:\*\*\s*(\S+)', task_content)
        priority_match = re.search(r'\*\*Priority:\*\*\s*(\S+)', task_content)
        depends_match = re.search(r'\*\*Depends on:\*\*\s*(.+)', task_content)

        role = role_match.group(1) if role_match else "implement"
        priority = priority_match.group(1) if priority_match else "P2"

        # Parse depends_on
        depends_on = []
        if depends_match:
            deps_str = depends_match.group(1).strip()
            if deps_str != "(none)":
                # Parse comma-separated numbers
                for dep in deps_str.split(","):
                    dep = dep.strip()
                    if dep.isdigit():
                        depends_on.append(int(dep))

        # Parse context
        context = ""
        context_match = re.search(r'### Context\s*\n(.+?)(?=\n###|\n---|\Z)', task_content, re.DOTALL)
        if context_match:
            context = context_match.group(1).strip()

        # Parse acceptance criteria
        criteria = []
        criteria_match = re.search(r'### Acceptance Criteria\s*\n(.+?)(?=\n###|\n---|\Z)', task_content, re.DOTALL)
        if criteria_match:
            criteria_text = criteria_match.group(1)
            for line in criteria_text.strip().split("\n"):
                # Match both checked and unchecked items
                item_match = re.match(r'^-\s*\[[ x]\]\s*(.+)$', line.strip())
                if item_match:
                    criteria.append(item_match.group(1))

        # Parse verification (user code path test requirement)
        verification = ""
        verification_match = re.search(
            r'### Verification \(User Code Path\)\s*\n(.+?)(?=\n###|\n---|\Z)',
            task_content, re.DOTALL,
        )
        if verification_match:
            verification = verification_match.group(1).strip()
            # Strip leading > from blockquote
            verification = re.sub(r'^>\s*', '', verification)

        # Append verification to context so agents see it prominently
        if verification:
            context += f"\n\n## VERIFICATION REQUIREMENT\n\nYou MUST write a test that exercises the user's actual code path:\n\n{verification}\n\nThis test must call the real user-facing function, NOT a helper. If this test passes, the feature works. If you can't make this test pass, the task is not done."

        tasks.append({
            "number": task_num,
            "title": title,
            "role": role,
            "priority": priority,
            "context": context,
            "acceptance_criteria": criteria,
            "depends_on": depends_on,
        })

    return tasks


# =============================================================================
# Task Recycling
# =============================================================================


BURNED_OUT_TURN_THRESHOLD = 80


def is_burned_out(commits_count: int, turns_used: int) -> bool:
    """Check if a task is burned out (used many turns without producing commits).

    DISABLED: This check is currently disabled due to persistent false positives
    from commit counting bugs. The commit counting system has issues with
    persistent worktrees + branch switching during agent work, causing tasks
    with real commits to be incorrectly detected as burned out and recycled.

    Multiple false positives observed in 2026-02 (tasks e11a484b, f7b4d710,
    58e22e70) where tasks reported 0 commits but actually had commits.

    This check can be re-enabled after ephemeral worktrees are implemented
    (TASK-f7b4d710), which will resolve the underlying commit counting issues.

    Args:
        commits_count: Number of commits the agent made
        turns_used: Number of turns the agent used

    Returns:
        Always False (check disabled)
    """
    # Disabled - return False unconditionally to prevent false positive recycling
    return False


def recycle_to_breakdown(task_path, reason="too_large") -> dict | None:
    """Recycle a failed/burned-out task back to the breakdown queue.

    Builds rich context from the project state (completed siblings, branch info)
    and creates a new breakdown task. The original task is moved to a 'recycled'
    state and any tasks blocked by it are rewired to depend on the new breakdown task.

    Args:
        task_path: Path to the burned-out task file
        reason: Why the task is being recycled

    Returns:
        Dictionary with breakdown_task info, or None if recycling is not appropriate
        (e.g., depth cap exceeded)
    """
    # Import here to avoid circular dependency
    from .tasks import create_task
    from .projects import get_project, get_project_tasks

    task_path = Path(task_path)

    # Look up task via SDK
    match = re.match(r"TASK-(.+)\.md", task_path.name)
    if not match:
        return None
    task_id = match.group(1)

    try:
        sdk = get_sdk()
        db_task = sdk.tasks.get(task_id)
    except Exception:
        db_task = None

    if not db_task:
        return None

    # Read the original task content, stripping agent metadata and error
    # sections to keep re-breakdown tasks lean (only human-written content)
    task_content = task_path.read_text() if task_path.exists() else ""
    for marker in ["CLAIMED_BY:", "CLAIMED_AT:", "SUBMITTED_AT:", "COMMITS_COUNT:", "TURNS_USED:", "FAILED_AT:", "## Error"]:
        idx = task_content.find(marker)
        if idx > 0:
            task_content = task_content[:idx].rstrip()
            break

    # Check depth cap - don't recycle tasks that are already re-breakdowns
    depth_match = re.search(r"RE_BREAKDOWN_DEPTH:\s*(\d+)", task_content)
    current_depth = int(depth_match.group(1)) if depth_match else 0
    if current_depth >= 1:
        return None  # Escalate to human instead

    # Gather project context
    project_id = db_task.get("project_id")
    project = None
    sibling_tasks = []
    project_context = ""

    if project_id:
        project = get_project(project_id)
        all_project_tasks = get_project_tasks(project_id)
        sibling_tasks = [t for t in all_project_tasks if t["id"] != task_id]

        if project:
            project_context = (
                f"## Project Context\n\n"
                f"**Project:** {project_id}\n"
                f"**Title:** {project.get('title', 'Unknown')}\n"
                f"**Branch:** {project.get('branch', 'main')}\n\n"
            )

        # Build completed siblings summary
        done_tasks = [t for t in sibling_tasks if t.get("queue") == "done"]
        if done_tasks:
            project_context += "### Completed Tasks\n\n"
            for t in done_tasks:
                commits = t.get("commits_count", 0)
                commit_label = f"{commits} commit{'s' if commits != 1 else ''}"
                project_context += f"- **{t['id']}** ({commit_label})\n"
            project_context += "\n"

    # Build the breakdown task content
    new_depth = current_depth + 1
    breakdown_context = (
        f"{project_context}"
        f"## Recycled Task\n\n"
        f"The following task burned out (0 commits after max turns) and needs "
        f"to be re-broken-down into smaller subtasks.\n\n"
        f"### Original Task: {task_id}\n\n"
        f"```\n{task_content}\n```\n\n"
        f"## Instructions\n\n"
        f"1. Check out the project branch and examine the current state of the code\n"
        f"2. Identify what work from the original task has NOT been completed\n"
        f"3. Break the remaining work into smaller, focused subtasks\n"
        f"4. Each subtask should be completable in <30 agent turns\n"
    )

    # Create the breakdown task
    breakdown_task_path = create_task(
        title=f"Re-breakdown: {task_id}",
        role="breakdown",
        context=breakdown_context,
        acceptance_criteria=[
            "Examine branch state to identify completed vs remaining work",
            "Decompose remaining work into right-sized tasks (<30 turns each)",
            "Map dependencies between new subtasks",
            "Include RE_BREAKDOWN_DEPTH in new subtasks",
        ],
        priority="P1",
        branch=project.get("branch") or db_task.get("branch") or get_main_branch(),
        created_by="recycler",
        project_id=project_id,
        queue="breakdown",
    )

    # Extract the new breakdown task ID from filename
    breakdown_match = re.match(r"TASK-(.+)\.md", breakdown_task_path.name)
    breakdown_task_id = breakdown_match.group(1) if breakdown_match else None

    # Add RE_BREAKDOWN_DEPTH to the breakdown task file
    if breakdown_task_path.exists():
        content = breakdown_task_path.read_text()
        # Insert after CREATED_BY line
        content = content.replace(
            "CREATED_BY: recycler\n",
            f"CREATED_BY: recycler\nRE_BREAKDOWN_DEPTH: {new_depth}\n",
        )
        breakdown_task_path.write_text(content)

    # Move original task to recycled state
    recycled_dir = get_queue_dir() / "recycled"
    recycled_dir.mkdir(parents=True, exist_ok=True)
    recycled_path = recycled_dir / task_path.name

    if task_path.exists():
        task_path.rename(recycled_path)

    # NOTE: We intentionally do NOT rewire dependencies here.
    # External tasks stay blocked by the original (recycled) task ID.
    # When the breakdown is approved, approve_breakdown() rewires from
    # the original task to the leaf subtasks. This avoids a race where
    # the breakdown task gets accepted → _unblock_dependent_tasks fires
    # → external tasks unblock before the real work is done.

    return {
        "breakdown_task": str(breakdown_task_path),
        "breakdown_task_id": breakdown_task_id,
        "original_task_id": task_id,
        "action": "recycled",
    }
