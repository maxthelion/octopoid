"""Tests for orchestrator.planning module."""

import pytest
from pathlib import Path
from unittest.mock import patch


class TestCreatePlanningTask:
    """Tests for create_planning_task function."""

    def test_create_planning_task(self, mock_orchestrator_dir, sample_task_file):
        """Test creating a planning task for a failed task."""
        with patch('orchestrator.planning.get_queue_dir', return_value=mock_orchestrator_dir / "shared" / "queue"):
            from orchestrator.planning import create_planning_task

            plan_id = create_planning_task("abc12345", sample_task_file)

            assert plan_id is not None
            assert len(plan_id) == 8  # UUID hex[:8]

            # Check planning task was created
            incoming_dir = mock_orchestrator_dir / "shared" / "queue" / "incoming"
            plan_files = list(incoming_dir.glob(f"TASK-{plan_id}.md"))
            assert len(plan_files) == 1

            content = plan_files[0].read_text()
            assert "Create implementation plan" in content
            assert "abc12345" in content
            assert "ORIGINAL_TASK:" in content

    def test_create_planning_task_includes_original_content(self, mock_orchestrator_dir, sample_task_file):
        """Test that planning task includes original task content."""
        with patch('orchestrator.planning.get_queue_dir', return_value=mock_orchestrator_dir / "shared" / "queue"):
            from orchestrator.planning import create_planning_task

            plan_id = create_planning_task("abc12345", sample_task_file)

            incoming_dir = mock_orchestrator_dir / "shared" / "queue" / "incoming"
            plan_file = incoming_dir / f"TASK-{plan_id}.md"
            content = plan_file.read_text()

            # Original task content should be included
            assert "Implement feature X" in content
            assert "Feature X works correctly" in content

    def test_create_planning_task_invalid_original(self, mock_orchestrator_dir):
        """Test creating planning task for invalid original task."""
        with patch('orchestrator.planning.get_queue_dir', return_value=mock_orchestrator_dir / "shared" / "queue"):
            from orchestrator.planning import create_planning_task

            with pytest.raises(ValueError, match="Could not parse"):
                create_planning_task("invalid", mock_orchestrator_dir / "nonexistent.md")


class TestParsePlanDocument:
    """Tests for parse_plan_document function."""

    def test_parse_simple_plan(self, temp_dir):
        """Test parsing a simple plan document."""
        from orchestrator.planning import parse_plan_document

        plan_content = """# Plan: Implement Feature

## Analysis

The original task was too complex.

## Micro-Tasks

### 1. Create database schema

**Description:** Create the initial database tables.

**Acceptance Criteria:**
- [ ] Tables are created
- [ ] Migrations work

**Dependencies:** None

### 2. Implement API endpoints

**Description:** Create the REST API endpoints.

**Acceptance Criteria:**
- [ ] GET endpoint works
- [ ] POST endpoint works

**Dependencies:** Task 1

### 3. Add frontend components

**Description:** Create React components.

**Acceptance Criteria:**
- [ ] Components render
- [ ] State management works

**Dependencies:** Task 1, Task 2
"""
        plan_path = temp_dir / "PLAN-test.md"
        plan_path.write_text(plan_content)

        micro_tasks = parse_plan_document(plan_path)

        assert len(micro_tasks) == 3

        # Check first task
        assert micro_tasks[0]["number"] == 1
        assert micro_tasks[0]["title"] == "Create database schema"
        assert "Tables are created" in micro_tasks[0]["acceptance_criteria"]
        assert micro_tasks[0]["dependencies"] == []

        # Check second task
        assert micro_tasks[1]["number"] == 2
        assert micro_tasks[1]["dependencies"] == [1]

        # Check third task
        assert micro_tasks[2]["number"] == 3
        assert micro_tasks[2]["dependencies"] == [1, 2]

    def test_parse_plan_no_dependencies(self, temp_dir):
        """Test parsing plan where all tasks are independent."""
        from orchestrator.planning import parse_plan_document

        plan_content = """# Plan

## Micro-Tasks

### 1. Task A

**Description:** Do A

**Acceptance Criteria:**
- [ ] A done

**Dependencies:** None

### 2. Task B

**Description:** Do B

**Acceptance Criteria:**
- [ ] B done

**Dependencies:** N/A
"""
        plan_path = temp_dir / "PLAN-nodeps.md"
        plan_path.write_text(plan_content)

        micro_tasks = parse_plan_document(plan_path)

        assert len(micro_tasks) == 2
        assert micro_tasks[0]["dependencies"] == []
        assert micro_tasks[1]["dependencies"] == []

    def test_parse_plan_various_dependency_formats(self, temp_dir):
        """Test parsing dependencies in various formats."""
        from orchestrator.planning import parse_plan_document

        plan_content = """# Plan

## Micro-Tasks

### 1. First

**Description:** First task

**Acceptance Criteria:**
- [ ] Done

**Dependencies:** None

### 2. Second

**Description:** Second task

**Acceptance Criteria:**
- [ ] Done

**Dependencies:** 1

### 3. Third

**Description:** Third task

**Acceptance Criteria:**
- [ ] Done

**Dependencies:** Task 1, Task 2

### 4. Fourth

**Description:** Fourth task

**Acceptance Criteria:**
- [ ] Done

**Dependencies:** 1, 2, 3
"""
        plan_path = temp_dir / "PLAN-formats.md"
        plan_path.write_text(plan_content)

        micro_tasks = parse_plan_document(plan_path)

        assert micro_tasks[1]["dependencies"] == [1]
        assert micro_tasks[2]["dependencies"] == [1, 2]
        assert micro_tasks[3]["dependencies"] == [1, 2, 3]

    def test_parse_empty_plan(self, temp_dir):
        """Test parsing an empty plan document."""
        from orchestrator.planning import parse_plan_document

        plan_path = temp_dir / "PLAN-empty.md"
        plan_path.write_text("# Empty Plan\n\nNo tasks here.")

        micro_tasks = parse_plan_document(plan_path)

        assert micro_tasks == []


class TestCreateMicroTasks:
    """Tests for create_micro_tasks function."""

    def test_create_micro_tasks(self, mock_orchestrator_dir):
        """Test creating micro-tasks from parsed plan."""
        tasks_dir = mock_orchestrator_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        with patch('orchestrator.queue_utils.get_tasks_file_dir', return_value=tasks_dir):
            from orchestrator.planning import create_micro_tasks

            micro_tasks = [
                {
                    "number": 1,
                    "title": "First task",
                    "description": "Do the first thing",
                    "acceptance_criteria": ["First done"],
                    "dependencies": [],
                },
                {
                    "number": 2,
                    "title": "Second task",
                    "description": "Do the second thing",
                    "acceptance_criteria": ["Second done"],
                    "dependencies": [1],
                },
            ]

            created_ids = create_micro_tasks(
                micro_tasks,
                original_task_id="original123",
                branch="main",
                created_by="planner",
            )

            assert len(created_ids) == 2

            # Check files were created in the tasks directory
            task_files = list(tasks_dir.glob("TASK-*.md"))
            assert len([f for f in task_files if created_ids[0] in f.name or created_ids[1] in f.name]) == 2

    def test_create_micro_tasks_empty_list(self, mock_orchestrator_dir):
        """Test creating micro-tasks with empty list."""
        tasks_dir = mock_orchestrator_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        with patch('orchestrator.queue_utils.get_tasks_file_dir', return_value=tasks_dir):
            from orchestrator.planning import create_micro_tasks

            created_ids = create_micro_tasks([], original_task_id="test")

            assert created_ids == []


class TestPlanningIntegration:
    """Integration tests for the planning workflow."""

    def test_full_planning_workflow(self, mock_orchestrator_dir, sample_task_file):
        """Test the full planning workflow: create plan -> parse -> create micro-tasks."""
        tasks_dir = mock_orchestrator_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        with patch('orchestrator.planning.get_queue_dir', return_value=mock_orchestrator_dir / "shared" / "queue"):
            with patch('orchestrator.queue_utils.get_tasks_file_dir', return_value=tasks_dir):
                from orchestrator.planning import create_planning_task, parse_plan_document, create_micro_tasks

                # Step 1: Create planning task
                plan_id = create_planning_task("abc12345", sample_task_file)

                # Step 2: Simulate plan document being created
                plans_dir = mock_orchestrator_dir / "plans"
                plans_dir.mkdir(exist_ok=True)
                plan_doc = plans_dir / f"PLAN-{plan_id}.md"
                plan_doc.write_text("""# Plan

## Analysis

Task was too big.

## Micro-Tasks

### 1. Part A

**Description:** Do part A

**Acceptance Criteria:**
- [ ] A works

**Dependencies:** None

### 2. Part B

**Description:** Do part B

**Acceptance Criteria:**
- [ ] B works

**Dependencies:** Task 1
""")

                # Step 3: Parse the plan
                micro_tasks = parse_plan_document(plan_doc)
                assert len(micro_tasks) == 2

                # Step 4: Create micro-tasks
                created_ids = create_micro_tasks(
                    micro_tasks,
                    original_task_id="abc12345",
                )
                assert len(created_ids) == 2
