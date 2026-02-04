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
