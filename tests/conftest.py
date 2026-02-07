"""Shared test fixtures for orchestrator tests."""

import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    tmp = tempfile.mkdtemp()
    yield Path(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def mock_orchestrator_dir(temp_dir):
    """Create a mock .orchestrator directory structure."""
    orchestrator_dir = temp_dir / ".orchestrator"

    # Create directory structure
    dirs = [
        orchestrator_dir / "shared" / "queue" / "incoming",
        orchestrator_dir / "shared" / "queue" / "claimed",
        orchestrator_dir / "shared" / "queue" / "provisional",
        orchestrator_dir / "shared" / "queue" / "done",
        orchestrator_dir / "shared" / "queue" / "failed",
        orchestrator_dir / "shared" / "queue" / "rejected",
        orchestrator_dir / "shared" / "queue" / "escalated",
        orchestrator_dir / "agents",
        orchestrator_dir / "plans",
    ]

    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    # Create a minimal agents.yaml
    agents_yaml = orchestrator_dir / "agents.yaml"
    agents_yaml.write_text("""
model: task
database:
  enabled: true
validation:
  require_commits: true
  max_attempts_before_planning: 3
  claim_timeout_minutes: 60
agents: []
""")

    yield orchestrator_dir


@pytest.fixture
def mock_config(mock_orchestrator_dir, temp_dir):
    """Patch config functions to use the mock directory."""
    with patch('orchestrator.config.find_parent_project', return_value=temp_dir):
        with patch('orchestrator.config.get_orchestrator_dir', return_value=mock_orchestrator_dir):
            yield mock_orchestrator_dir


@pytest.fixture
def db_path(mock_config):
    """Get path to test database."""
    path = mock_config / "state.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def initialized_db(mock_config, db_path):
    """Initialize the database schema."""
    # Patch get_database_path to use our test path
    with patch('orchestrator.db.get_database_path', return_value=db_path):
        from orchestrator.db import init_schema
        init_schema()
        yield db_path


@pytest.fixture
def sample_task_file(mock_orchestrator_dir):
    """Create a sample task file."""
    incoming_dir = mock_orchestrator_dir / "shared" / "queue" / "incoming"
    task_path = incoming_dir / "TASK-abc12345.md"

    content = """# [TASK-abc12345] Implement feature X

ROLE: implement
PRIORITY: P1
BRANCH: main
CREATED: 2024-01-15T10:00:00
CREATED_BY: human

## Context

This task requires implementing feature X.

## Acceptance Criteria

- [ ] Feature X works correctly
- [ ] Tests are added
"""
    task_path.write_text(content)
    yield task_path


@pytest.fixture
def sample_project_with_tasks(mock_orchestrator_dir, initialized_db):
    """Create a project with tasks at various stages.

    - Project PROJ-test1 with branch feature/test1
    - 3 completed tasks (done queue, with commit counts)
    - 1 burned-out task (provisional, 0 commits, 50 turns)
    - 1 task blocked by the burned-out task
    """
    from orchestrator.db import (
        create_project,
        create_task,
        claim_task as db_claim,
        submit_completion as db_submit,
        accept_completion as db_accept,
        update_task,
        update_task_queue,
        get_database_path,
    )

    project = create_project(
        project_id="PROJ-test1",
        title="Test project for recycling",
        description="A test project with various task states",
        branch="feature/test1",
    )

    # Create queue directories
    done_dir = mock_orchestrator_dir / "shared" / "queue" / "done"
    prov_dir = mock_orchestrator_dir / "shared" / "queue" / "provisional"
    incoming_dir = mock_orchestrator_dir / "shared" / "queue" / "incoming"
    done_dir.mkdir(parents=True, exist_ok=True)
    prov_dir.mkdir(parents=True, exist_ok=True)
    incoming_dir.mkdir(parents=True, exist_ok=True)

    # 3 completed tasks
    completed_tasks = []
    for i, (tid, title, commits) in enumerate([
        ("done0001", "First completed task", 1),
        ("done0002", "Second completed task", 2),
        ("done0003", "Third completed task", 1),
    ]):
        file_path = done_dir / f"TASK-{tid}.md"
        file_path.write_text(f"# [TASK-{tid}] {title}\n\nROLE: implement\nPRIORITY: P1\nBRANCH: feature/test1\nPROJECT: PROJ-test1\n\n## Context\nTask {i+1} context.\n\n## Acceptance Criteria\n- [ ] Done\n")
        create_task(task_id=tid, file_path=str(file_path), project_id="PROJ-test1", role="implement")
        update_task_queue(tid, "done", commits_count=commits, turns_used=20)
        completed_tasks.append({"id": tid, "title": title, "commits": commits, "path": file_path})

    # 1 burned-out task (provisional, 0 commits, 100 turns)
    burned_id = "burn0001"
    burned_path = prov_dir / f"TASK-{burned_id}.md"
    burned_path.write_text(
        f"# [TASK-{burned_id}] Verify tests and add edge cases\n\n"
        f"ROLE: implement\nPRIORITY: P1\nBRANCH: feature/test1\nPROJECT: PROJ-test1\n\n"
        f"## Context\nRun the tests, debug failures, add edge case coverage.\n\n"
        f"## Acceptance Criteria\n- [ ] All tests pass\n- [ ] Edge cases added\n"
    )
    create_task(task_id=burned_id, file_path=str(burned_path), project_id="PROJ-test1", role="implement")
    update_task_queue(burned_id, "provisional", commits_count=0, turns_used=100)

    # 1 task blocked by the burned-out task
    blocked_id = "block001"
    blocked_path = incoming_dir / f"TASK-{blocked_id}.md"
    blocked_path.write_text(
        f"# [TASK-{blocked_id}] Final cleanup\n\n"
        f"ROLE: implement\nPRIORITY: P1\nBRANCH: feature/test1\nPROJECT: PROJ-test1\n"
        f"BLOCKED_BY: {burned_id}\n\n"
        f"## Context\nFinal cleanup after tests pass.\n\n"
        f"## Acceptance Criteria\n- [ ] Cleanup done\n"
    )
    create_task(task_id=blocked_id, file_path=str(blocked_path), project_id="PROJ-test1", role="implement", blocked_by=burned_id)

    yield {
        "project": project,
        "project_id": "PROJ-test1",
        "completed_tasks": completed_tasks,
        "burned_task": {"id": burned_id, "path": burned_path},
        "blocked_task": {"id": blocked_id, "path": blocked_path},
    }


@pytest.fixture
def sample_task_with_dependencies(mock_orchestrator_dir):
    """Create sample task files with dependencies."""
    incoming_dir = mock_orchestrator_dir / "shared" / "queue" / "incoming"

    # Task 1 - no dependencies
    task1_path = incoming_dir / "TASK-task0001.md"
    task1_path.write_text("""# [TASK-task0001] First task

ROLE: implement
PRIORITY: P1
BRANCH: main
CREATED: 2024-01-15T10:00:00
CREATED_BY: human

## Context
First task with no dependencies.

## Acceptance Criteria
- [ ] Complete task 1
""")

    # Task 2 - depends on task 1
    task2_path = incoming_dir / "TASK-task0002.md"
    task2_path.write_text("""# [TASK-task0002] Second task

ROLE: implement
PRIORITY: P1
BRANCH: main
CREATED: 2024-01-15T10:01:00
CREATED_BY: human
BLOCKED_BY: task0001

## Context
Second task that depends on first.

## Acceptance Criteria
- [ ] Complete task 2
""")

    yield {"task1": task1_path, "task2": task2_path}
