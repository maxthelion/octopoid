"""Integration tests for the full project lifecycle.

Tests the end-to-end behaviour of the project system against the real local
server at localhost:9787:

- Project creation with branch name
- Child task creation with project_id and branch association
- Project task listing via /projects/{id}/tasks
- Child task lifecycle without individual PR creation (child_flow semantics)
- Project completion detection when all children are done
- Project-level flow transition to provisional (represents PR creation)

Agent execution (Claude) is mocked: instead of spawning real agents, we call
the SDK methods that an agent/scheduler would call (claim, submit, accept,
update).

Key difference between standard flow and child_flow (project.yaml):
- Standard:  incoming → claimed → provisional → done
             (with create_pr step before provisional)
- child_flow: incoming → claimed → provisional → done
             (WITHOUT create_pr — the child task commits to the shared branch,
              no individual PR. Only the project itself creates a PR at the end.)

The server's state machine requires provisional as an intermediate step; the
child_flow skips the create_pr step, not the provisional queue.

The `clean_tasks` fixture ensures each test starts with a clean task queue.
Projects use unique UUIDs so they don't conflict across runs.

Run prerequisites:
    cd submodules/server && npx wrangler dev --port 9787
"""

import uuid

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _proj_id() -> str:
    return f"PROJ-{uuid.uuid4().hex[:8]}"


def _task_id() -> str:
    return f"TASK-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProjectCreation:
    """Verify project creation and basic retrieval."""

    def test_create_project_with_branch(self, sdk):
        """Project can be created with a branch name and retrieved."""
        project_id = _proj_id()
        branch = f"feature/proj-{uuid.uuid4().hex[:6]}"

        project = sdk.projects.create(
            id=project_id,
            title="Test project",
            description="Integration test",
            status="active",
            branch=branch,
        )

        assert project["id"] == project_id
        assert project["branch"] == branch
        assert project["status"] == "active"

        # Can be retrieved by ID
        fetched = sdk.projects.get(project_id)
        assert fetched is not None
        assert fetched["id"] == project_id
        assert fetched["branch"] == branch

    def test_project_appears_in_list(self, sdk):
        """Created project appears in the projects list."""
        project_id = _proj_id()

        sdk.projects.create(
            id=project_id,
            title="List test project",
            description="Should appear in list",
            status="active",
            branch="feature/list-test",
        )

        projects = sdk.projects.list()
        project_ids = [p["id"] for p in projects]
        assert project_id in project_ids


class TestChildTaskAssociation:
    """Verify child tasks are associated with their project."""

    def test_child_tasks_carry_project_branch(self, sdk, clean_tasks):
        """Tasks created with project_id store the project's branch.

        In production, orchestrator.tasks.create_task(project_id=X) fetches
        the project and passes its branch to the SDK (tested in unit tests at
        tests/test_create_task_project_branch.py). This integration test verifies
        the server correctly stores the branch and project_id in the task record.
        """
        project_id = _proj_id()
        project_branch = f"feature/child-{uuid.uuid4().hex[:6]}"

        # Create the project
        project = sdk.projects.create(
            id=project_id,
            title="Branch association project",
            description="Tests child task branch storage",
            status="active",
            branch=project_branch,
        )
        assert project["branch"] == project_branch

        # Create two child tasks with the project's branch and project_id
        task1_id = _task_id()
        task1 = sdk.tasks.create(
            id=task1_id,
            file_path=f".octopoid/tasks/{task1_id}.md",
            title="Child task 1",
            role="implement",
            branch=project_branch,
            project_id=project_id,  # top-level field — not inside metadata
        )

        task2_id = _task_id()
        task2 = sdk.tasks.create(
            id=task2_id,
            file_path=f".octopoid/tasks/{task2_id}.md",
            title="Child task 2",
            role="implement",
            branch=project_branch,
            project_id=project_id,
        )

        # Both tasks carry the project's branch
        assert task1["branch"] == project_branch
        assert task2["branch"] == project_branch

        # Both tasks reference the project
        assert task1["project_id"] == project_id
        assert task2["project_id"] == project_id

    def test_project_tasks_endpoint_returns_children(self, sdk, clean_tasks):
        """GET /projects/{id}/tasks returns all tasks with that project_id."""
        project_id = _proj_id()

        sdk.projects.create(
            id=project_id,
            title="Task list project",
            description="Tests /projects/{id}/tasks",
            status="active",
            branch="feature/task-list",
        )

        task1_id = _task_id()
        task2_id = _task_id()

        sdk.tasks.create(
            id=task1_id,
            file_path=f".octopoid/tasks/{task1_id}.md",
            title="Child 1",
            role="implement",
            branch="feature/task-list",
            project_id=project_id,
        )
        sdk.tasks.create(
            id=task2_id,
            file_path=f".octopoid/tasks/{task2_id}.md",
            title="Child 2",
            role="implement",
            branch="feature/task-list",
            project_id=project_id,
        )

        project_tasks = sdk.projects.get_tasks(project_id)
        project_task_ids = {t["id"] for t in project_tasks}

        assert task1_id in project_task_ids
        assert task2_id in project_task_ids

    def test_tasks_without_project_id_not_in_project(self, sdk, clean_tasks):
        """Tasks without a project_id do not appear in the project task list."""
        project_id = _proj_id()

        sdk.projects.create(
            id=project_id,
            title="Exclusion test project",
            description="Checks non-project tasks are excluded",
            status="active",
            branch="feature/exclusion",
        )

        # Create a task WITH the project
        child_id = _task_id()
        sdk.tasks.create(
            id=child_id,
            file_path=f".octopoid/tasks/{child_id}.md",
            title="Child task",
            role="implement",
            branch="feature/exclusion",
            project_id=project_id,
        )

        # Create a task WITHOUT the project
        orphan_id = _task_id()
        sdk.tasks.create(
            id=orphan_id,
            file_path=f".octopoid/tasks/{orphan_id}.md",
            title="Orphan task",
            role="implement",
            branch="feature/exclusion",
        )

        project_tasks = sdk.projects.get_tasks(project_id)
        project_task_ids = {t["id"] for t in project_tasks}

        assert child_id in project_task_ids
        assert orphan_id not in project_task_ids


class TestProjectLifecycle:
    """Full end-to-end project lifecycle test."""

    def test_full_project_lifecycle(self, sdk, orchestrator_id, clean_tasks):
        """Full lifecycle: project → child tasks → all done → project provisional.

        This test simulates the scheduler + agent execution without running real
        processes:

        1. Create a project with a branch
        2. Create 2 child tasks that share the project's branch
        3. Verify both tasks appear in the project's task list
        4. Claim task 1 → submit → accept (simulating child_flow: no create_pr)
        5. Claim task 2 → submit → accept (same)
        6. Verify all project tasks are in 'done'
        7. Simulate project-level flow: scheduler detects all children done,
           transitions project to 'provisional' (which triggers create_pr in
           the project flow: "children_complete -> provisional")

        Child_flow detail: unlike the standard flow which runs [push_branch,
        run_tests, create_pr, submit_to_server], the child_flow only runs
        [rebase_on_project_branch, run_tests] — no individual PR is created.
        Child tasks commit directly to the shared project branch. Only the
        project itself creates a PR when all children are done.
        """
        project_id = _proj_id()
        project_branch = f"feature/e2e-{uuid.uuid4().hex[:6]}"

        # ── 1. Create project ────────────────────────────────────────────────
        project = sdk.projects.create(
            id=project_id,
            title="End-to-end lifecycle project",
            description="Tests the full project task lifecycle",
            status="active",
            branch=project_branch,
        )
        assert project["id"] == project_id
        assert project["branch"] == project_branch

        # ── 2. Create child tasks with project_id and shared branch ──────────
        # In production the scheduler calls orchestrator.tasks.create_task()
        # which auto-fetches the project's branch. Here we pass it explicitly.
        task1_id = _task_id()
        task1 = sdk.tasks.create(
            id=task1_id,
            file_path=f".octopoid/tasks/{task1_id}.md",
            title="Project child task 1",
            role="implement",
            branch=project_branch,   # inherits project branch
            project_id=project_id,  # top-level field links task to project
        )
        assert task1["queue"] == "incoming"
        assert task1["branch"] == project_branch
        assert task1["project_id"] == project_id

        task2_id = _task_id()
        task2 = sdk.tasks.create(
            id=task2_id,
            file_path=f".octopoid/tasks/{task2_id}.md",
            title="Project child task 2",
            role="implement",
            branch=project_branch,
            project_id=project_id,
        )
        assert task2["queue"] == "incoming"
        assert task2["branch"] == project_branch
        assert task2["project_id"] == project_id

        # ── 3. Verify project task list ──────────────────────────────────────
        project_tasks = sdk.projects.get_tasks(project_id)
        project_task_ids = {t["id"] for t in project_tasks}
        assert task1_id in project_task_ids
        assert task2_id in project_task_ids

        # ── 4. Claim and complete task 1 via child_flow ──────────────────────
        # Claim → submit → accept (no create_pr in between).
        # In child_flow the scheduler runs [rebase_on_project_branch, run_tests]
        # then moves the task to done — no PR created for this child task.
        claimed1 = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent-1",
            role_filter="implement",
        )
        assert claimed1 is not None
        assert claimed1["id"] in {task1_id, task2_id}
        assert claimed1["queue"] == "claimed"
        first_task_id = claimed1["id"]

        # Submit (moves to provisional) — agent did work on shared branch.
        # Note: no create_pr called here (child_flow skips it).
        submitted1 = sdk.tasks.submit(first_task_id, commits_count=2, turns_used=10)
        assert submitted1["queue"] == "provisional"

        # Accept (moves to done)
        accepted1 = sdk.tasks.accept(first_task_id, accepted_by="child-flow-gatekeeper")
        assert accepted1["queue"] == "done"

        # ── 5. Claim and complete task 2 ─────────────────────────────────────
        second_task_id = task2_id if first_task_id == task1_id else task1_id

        claimed2 = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent-2",
            role_filter="implement",
        )
        assert claimed2 is not None
        assert claimed2["id"] == second_task_id
        assert claimed2["queue"] == "claimed"

        submitted2 = sdk.tasks.submit(second_task_id, commits_count=3, turns_used=15)
        assert submitted2["queue"] == "provisional"

        accepted2 = sdk.tasks.accept(second_task_id, accepted_by="child-flow-gatekeeper")
        assert accepted2["queue"] == "done"

        # ── 6. Verify all project tasks are done ─────────────────────────────
        final_tasks = sdk.projects.get_tasks(project_id)
        assert len(final_tasks) == 2, (
            f"Expected 2 project tasks, got {len(final_tasks)}"
        )
        not_done = [(t["id"], t["queue"]) for t in final_tasks if t["queue"] != "done"]
        assert not not_done, f"Some project tasks not done: {not_done}"

        # ── 7. Project completion: scheduler triggers top-level flow ─────────
        # When all children are done, the scheduler detects project completion
        # and runs the project-level flow "children_complete -> provisional",
        # which executes create_pr (the project's PR). We simulate this by
        # updating the project status to 'provisional'.
        updated_project = sdk.projects.update(project_id, status="provisional")
        assert updated_project["status"] == "provisional"

        # Confirm final project state
        final_project = sdk.projects.get(project_id)
        assert final_project is not None
        assert final_project["status"] == "provisional"

    def test_child_tasks_complete_without_individual_prs(self, sdk, orchestrator_id, clean_tasks):
        """Child tasks complete without individual PRs (child_flow semantics).

        In the standard default flow, the scheduler calls create_pr between
        claimed and provisional. In child_flow, create_pr is NOT called — the
        task commits to the shared project branch and the scheduler moves it
        to done without an individual PR.

        This test verifies the child task lifecycle works end-to-end while
        only performing the operations that child_flow authorises:
          - claim, rebase (mocked), run_tests (mocked), submit, accept
          - NO create_pr call

        The task's project_id being set is the trigger that causes the
        scheduler to use child_flow instead of the default flow.
        """
        project_id = _proj_id()
        task_id = _task_id()

        sdk.projects.create(
            id=project_id,
            title="Child PR test project",
            description="Tests that child tasks do not create individual PRs",
            status="active",
            branch="feature/no-pr-test",
        )
        sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="Child task without individual PR",
            role="implement",
            branch="feature/no-pr-test",
            project_id=project_id,  # marks this as a child task
        )

        # Verify task is a child task (has project_id)
        task = sdk.tasks.get(task_id)
        assert task["project_id"] == project_id, (
            "Task must have project_id so scheduler uses child_flow"
        )

        # Simulate child_flow execution:
        # 1. Claim
        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )
        assert claimed is not None
        assert claimed["id"] == task_id
        assert claimed["queue"] == "claimed"

        # 2. Agent does work: rebase_on_project_branch + run_tests (mocked)
        #    — notably, create_pr is NOT called (child_flow skips it)

        # 3. Submit (provisional) — no PR created
        submitted = sdk.tasks.submit(task_id, commits_count=2, turns_used=8)
        assert submitted["queue"] == "provisional"
        assert submitted["pr_number"] is None  # No PR was created

        # 4. Accept (done)
        accepted = sdk.tasks.accept(task_id, accepted_by="child-flow-gatekeeper")
        assert accepted["queue"] == "done"
        assert accepted["pr_number"] is None  # Still no PR

        # The task completed without a PR — child_flow behaviour confirmed
        final_task = sdk.tasks.get(task_id)
        assert final_task["queue"] == "done"
        assert final_task["pr_number"] is None
