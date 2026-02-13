"""Integration tests for the hooks system.

Tests that:
- Tasks can be created with a `type` field via the API
- The type field persists and is returned on read
- Type field can be updated
- Hook resolution works end-to-end with real config files
- Built-in hooks execute correctly (with mocked git operations)
"""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from orchestrator.hooks import (
    HookContext,
    HookPoint,
    HookStatus,
    hook_rebase_on_main,
    hook_run_tests,
    resolve_hooks,
    run_hooks,
    BUILTIN_HOOKS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(worktree: Path | None = None, **overrides) -> HookContext:
    """Build a HookContext with sensible test defaults."""
    defaults = dict(
        task_id="hooks-001",
        task_title="Hook test task",
        task_path="/tmp/hooks-001.md",
        task_type=None,
        branch_name="task/hooks-001",
        base_branch="main",
        worktree=worktree or Path("/tmp/fake-worktree"),
        agent_name="implementer-1",
        commits_count=3,
    )
    defaults.update(overrides)
    return HookContext(**defaults)


# ---------------------------------------------------------------------------
# Task type field via API
# ---------------------------------------------------------------------------


class TestTaskTypeAPI:
    """Test that the task `type` field works end-to-end via the API."""

    def test_create_task_with_type(self, sdk, clean_tasks):
        """Tasks can be created with a type field."""
        task = sdk.tasks.create(
            id="hooks-type-001",
            file_path="/tmp/hooks-type-001.md",
            title="Product Feature",
            role="implement",
            type="product",
        )
        assert task["id"] == "hooks-type-001"
        assert task.get("type") == "product"

    def test_create_task_without_type(self, sdk, clean_tasks):
        """Tasks without a type field have type=None."""
        task = sdk.tasks.create(
            id="hooks-notype-001",
            file_path="/tmp/hooks-notype-001.md",
            title="Untyped Task",
            role="implement",
        )
        assert task["id"] == "hooks-notype-001"
        assert task.get("type") is None

    def test_type_persists_on_read(self, sdk, clean_tasks):
        """Type field is returned when fetching the task."""
        sdk.tasks.create(
            id="hooks-persist-001",
            file_path="/tmp/hooks-persist-001.md",
            title="Persist Type",
            type="infrastructure",
        )
        task = sdk.tasks.get("hooks-persist-001")
        assert task.get("type") == "infrastructure"

    def test_update_task_type(self, sdk, clean_tasks):
        """Type field can be updated after creation."""
        sdk.tasks.create(
            id="hooks-update-001",
            file_path="/tmp/hooks-update-001.md",
            title="Update Type",
            type="product",
        )
        updated = sdk.tasks.update("hooks-update-001", type="hotfix")
        assert updated.get("type") == "hotfix"

    def test_type_survives_lifecycle(self, sdk, orchestrator_id, clean_tasks):
        """Type field persists through claim → submit → accept."""
        sdk.tasks.create(
            id="hooks-lifecycle-001",
            file_path="/tmp/hooks-lifecycle-001.md",
            title="Lifecycle Type",
            role="implement",
            type="product",
        )

        # Claim
        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )
        assert claimed["id"] == "hooks-lifecycle-001"
        assert claimed.get("type") == "product"

        # Submit
        submitted = sdk.tasks.submit(
            task_id="hooks-lifecycle-001",
            commits_count=2,
            turns_used=10,
        )
        assert submitted.get("type") == "product"

        # Accept
        accepted = sdk.tasks.accept(
            task_id="hooks-lifecycle-001",
            accepted_by="test-gatekeeper",
        )
        assert accepted.get("type") == "product"


# ---------------------------------------------------------------------------
# Hook resolution with real config files
# ---------------------------------------------------------------------------


class TestHookResolutionWithConfig:
    """Test hook resolution using real YAML config files on disk."""

    def test_resolve_from_project_config(self, tmp_path):
        """Hooks resolve from a real .octopoid/config.yaml file."""
        config_dir = tmp_path / ".octopoid"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            "hooks:\n"
            "  before_submit:\n"
            "    - rebase_on_main\n"
            "    - run_tests\n"
            "    - create_pr\n"
        )

        with patch("orchestrator.config.find_parent_project", return_value=tmp_path):
            hooks = resolve_hooks(HookPoint.BEFORE_SUBMIT, task_type=None)

        assert len(hooks) == 3
        assert hooks[0] is BUILTIN_HOOKS["rebase_on_main"]
        assert hooks[1] is BUILTIN_HOOKS["run_tests"]
        assert hooks[2] is BUILTIN_HOOKS["create_pr"]

    def test_resolve_type_overrides_project(self, tmp_path):
        """Task type hooks take precedence over project-level hooks."""
        config_dir = tmp_path / ".octopoid"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            "hooks:\n"
            "  before_submit:\n"
            "    - rebase_on_main\n"
            "    - create_pr\n"
            "\n"
            "task_types:\n"
            "  hotfix:\n"
            "    hooks:\n"
            "      before_submit:\n"
            "        - run_tests\n"
            "        - create_pr\n"
        )

        with patch("orchestrator.config.find_parent_project", return_value=tmp_path):
            # Hotfix type uses its own hooks (no rebase)
            hotfix_hooks = resolve_hooks(HookPoint.BEFORE_SUBMIT, task_type="hotfix")
            assert len(hotfix_hooks) == 2
            assert hotfix_hooks[0] is BUILTIN_HOOKS["run_tests"]
            assert hotfix_hooks[1] is BUILTIN_HOOKS["create_pr"]

            # Untyped task falls through to project-level hooks
            default_hooks = resolve_hooks(HookPoint.BEFORE_SUBMIT, task_type=None)
            assert len(default_hooks) == 2
            assert default_hooks[0] is BUILTIN_HOOKS["rebase_on_main"]
            assert default_hooks[1] is BUILTIN_HOOKS["create_pr"]

    def test_unknown_type_falls_through(self, tmp_path):
        """Unknown task type falls through to project-level hooks."""
        config_dir = tmp_path / ".octopoid"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            "hooks:\n"
            "  before_submit:\n"
            "    - create_pr\n"
            "\n"
            "task_types:\n"
            "  product:\n"
            "    hooks:\n"
            "      before_submit:\n"
            "        - run_tests\n"
            "        - create_pr\n"
        )

        with patch("orchestrator.config.find_parent_project", return_value=tmp_path):
            hooks = resolve_hooks(HookPoint.BEFORE_SUBMIT, task_type="unknown_type")

        # Should fall through to project-level
        assert len(hooks) == 1
        assert hooks[0] is BUILTIN_HOOKS["create_pr"]

    def test_no_config_uses_defaults(self):
        """No config file at all uses DEFAULT_HOOKS (just create_pr)."""
        with patch("orchestrator.config.find_parent_project", return_value=Path("/nonexistent")):
            hooks = resolve_hooks(HookPoint.BEFORE_SUBMIT, task_type=None)

        assert len(hooks) == 1
        assert hooks[0] is BUILTIN_HOOKS["create_pr"]


# ---------------------------------------------------------------------------
# Hook execution end-to-end
# ---------------------------------------------------------------------------


class TestHookExecution:
    """Test built-in hooks execute correctly end-to-end (with mocked git)."""

    def test_rebase_skip_when_up_to_date(self):
        """Rebase hook skips when branch is already up to date."""
        ctx = _make_ctx()

        def mock_run(cmd, **kwargs):
            m = MagicMock()
            m.returncode = 0
            m.stdout = "0\n"
            m.stderr = ""
            return m

        with patch("orchestrator.hooks.subprocess.run", side_effect=mock_run):
            result = hook_rebase_on_main(ctx)

        assert result.status == HookStatus.SKIP

    def test_run_tests_with_real_tmpdir(self, tmp_path):
        """run_tests detects pytest from pyproject.toml in a real directory."""
        (tmp_path / "pyproject.toml").write_text("[tool.pytest]\n")
        ctx = _make_ctx(worktree=tmp_path)

        with patch("orchestrator.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="3 passed", stderr="")
            result = hook_run_tests(ctx)

        assert result.status == HookStatus.SUCCESS
        # Verify it ran pytest
        cmd = mock_run.call_args[0][0]
        assert cmd == ["python", "-m", "pytest", "--tb=short", "-q"]

    def test_full_pipeline_with_config(self, tmp_path):
        """Run full before_submit pipeline resolved from config."""
        # Set up config
        config_dir = tmp_path / ".octopoid"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            "hooks:\n"
            "  before_submit:\n"
            "    - create_pr\n"
        )

        ctx = _make_ctx(extra={"stdout": "implemented feature"})

        with patch("orchestrator.config.find_parent_project", return_value=tmp_path), \
             patch("orchestrator.git_utils.create_pull_request", return_value="https://github.com/test/pr/42"):
            all_ok, results = run_hooks(HookPoint.BEFORE_SUBMIT, ctx)

        assert all_ok is True
        assert len(results) == 1
        assert results[0].status == HookStatus.SUCCESS
        assert results[0].context["pr_url"] == "https://github.com/test/pr/42"

    def test_pipeline_fail_fast(self, tmp_path):
        """Pipeline stops on first failure — second hook is not reached."""
        config_dir = tmp_path / ".octopoid"
        config_dir.mkdir()
        # run_tests will fail, create_pr should not run
        (config_dir / "config.yaml").write_text(
            "hooks:\n"
            "  before_submit:\n"
            "    - run_tests\n"
            "    - create_pr\n"
        )

        # Create a pyproject.toml so run_tests detects pytest
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        (worktree / "pyproject.toml").write_text("[tool.pytest]\n")

        ctx = _make_ctx(worktree=worktree)

        with patch("orchestrator.config.find_parent_project", return_value=tmp_path), \
             patch("orchestrator.hooks.subprocess.run") as mock_subprocess, \
             patch("orchestrator.git_utils.create_pull_request") as mock_pr:
            # Tests fail
            mock_subprocess.return_value = MagicMock(
                returncode=1, stdout="FAILED test_foo.py", stderr=""
            )
            all_ok, results = run_hooks(HookPoint.BEFORE_SUBMIT, ctx)

        assert all_ok is False
        assert len(results) == 1  # Only run_tests, not create_pr
        assert results[0].status == HookStatus.FAILURE
        assert results[0].remediation_prompt is not None
        mock_pr.assert_not_called()

    def test_pipeline_type_specific(self, tmp_path):
        """Pipeline uses type-specific hooks when task has a type."""
        config_dir = tmp_path / ".octopoid"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            "hooks:\n"
            "  before_submit:\n"
            "    - rebase_on_main\n"
            "    - create_pr\n"
            "\n"
            "task_types:\n"
            "  hotfix:\n"
            "    hooks:\n"
            "      before_submit:\n"
            "        - create_pr\n"
        )

        ctx = _make_ctx(task_type="hotfix", extra={"stdout": "hotfix applied"})

        with patch("orchestrator.config.find_parent_project", return_value=tmp_path), \
             patch("orchestrator.git_utils.create_pull_request", return_value="https://github.com/test/pr/99"):
            all_ok, results = run_hooks(HookPoint.BEFORE_SUBMIT, ctx)

        # Should only have create_pr (no rebase for hotfix)
        assert all_ok is True
        assert len(results) == 1
        assert results[0].context["pr_url"] == "https://github.com/test/pr/99"


# ---------------------------------------------------------------------------
# Server-side hook enforcement (Phase 2)
# ---------------------------------------------------------------------------


class TestServerHooks:
    """Test server-side hook storage and evidence recording."""

    def test_create_task_with_hooks(self, sdk, clean_tasks):
        """Tasks can be created with a hooks field (JSON string)."""
        import json
        hooks = json.dumps([
            {"name": "run_tests", "point": "before_submit", "type": "agent", "status": "pending"},
            {"name": "create_pr", "point": "before_submit", "type": "agent", "status": "pending"},
            {"name": "merge_pr", "point": "before_merge", "type": "orchestrator", "status": "pending"},
        ])
        task = sdk.tasks.create(
            id="hooks-server-001",
            file_path="/tmp/hooks-server-001.md",
            title="Hooks Server Test",
            role="implement",
            hooks=hooks,
        )
        assert task["id"] == "hooks-server-001"
        stored = task.get("hooks")
        assert stored is not None
        parsed = json.loads(stored) if isinstance(stored, str) else stored
        assert len(parsed) == 3
        assert parsed[0]["name"] == "run_tests"
        assert parsed[0]["status"] == "pending"

    def test_hooks_persist_through_lifecycle(self, sdk, orchestrator_id, clean_tasks):
        """Hooks field persists through claim and submit."""
        import json
        hooks = json.dumps([
            {"name": "create_pr", "point": "before_submit", "type": "agent", "status": "pending"},
        ])
        sdk.tasks.create(
            id="hooks-persist-lc-001",
            file_path="/tmp/hooks-persist-lc-001.md",
            title="Hooks Persist Lifecycle",
            role="implement",
            hooks=hooks,
        )
        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )
        assert claimed["id"] == "hooks-persist-lc-001"
        stored = claimed.get("hooks")
        assert stored is not None
        parsed = json.loads(stored) if isinstance(stored, str) else stored
        assert parsed[0]["name"] == "create_pr"

        submitted = sdk.tasks.submit(
            task_id="hooks-persist-lc-001",
            commits_count=1,
            turns_used=5,
        )
        stored = submitted.get("hooks")
        assert stored is not None

    def test_record_hook_evidence(self, sdk, clean_tasks):
        """Hook evidence endpoint updates hook status."""
        import json
        import requests

        hooks = json.dumps([
            {"name": "run_tests", "point": "before_submit", "type": "agent", "status": "pending"},
            {"name": "create_pr", "point": "before_submit", "type": "agent", "status": "pending"},
        ])
        sdk.tasks.create(
            id="hooks-evidence-001",
            file_path="/tmp/hooks-evidence-001.md",
            title="Hook Evidence Test",
            hooks=hooks,
        )

        resp = requests.post(
            "http://localhost:9787/api/v1/tasks/hooks-evidence-001/hooks/run_tests/complete",
            json={"status": "passed", "evidence": {"output": "3 tests passed"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        parsed = json.loads(data["hooks"]) if isinstance(data.get("hooks"), str) else data.get("hooks", [])
        run_tests = next(h for h in parsed if h["name"] == "run_tests")
        create_pr = next(h for h in parsed if h["name"] == "create_pr")
        assert run_tests["status"] == "passed"
        assert run_tests.get("evidence", {}).get("output") == "3 tests passed"
        assert create_pr["status"] == "pending"

    def test_record_evidence_unknown_hook(self, sdk, clean_tasks):
        """Recording evidence for a non-existent hook returns 404."""
        import json
        import requests

        hooks = json.dumps([
            {"name": "run_tests", "point": "before_submit", "type": "agent", "status": "pending"},
        ])
        sdk.tasks.create(
            id="hooks-unknown-001",
            file_path="/tmp/hooks-unknown-001.md",
            title="Unknown Hook Test",
            hooks=hooks,
        )
        resp = requests.post(
            "http://localhost:9787/api/v1/tasks/hooks-unknown-001/hooks/nonexistent/complete",
            json={"status": "passed"},
        )
        assert resp.status_code == 404

    def test_record_evidence_invalid_status(self, sdk, clean_tasks):
        """Recording evidence with invalid status returns 400."""
        import json
        import requests

        hooks = json.dumps([
            {"name": "run_tests", "point": "before_submit", "type": "agent", "status": "pending"},
        ])
        sdk.tasks.create(
            id="hooks-invalid-001",
            file_path="/tmp/hooks-invalid-001.md",
            title="Invalid Status Test",
            hooks=hooks,
        )
        resp = requests.post(
            "http://localhost:9787/api/v1/tasks/hooks-invalid-001/hooks/run_tests/complete",
            json={"status": "maybe"},
        )
        assert resp.status_code == 400

    def test_hooks_update_via_patch(self, sdk, clean_tasks):
        """Hooks can be updated via PATCH."""
        import json

        hooks = json.dumps([
            {"name": "run_tests", "point": "before_submit", "type": "agent", "status": "pending"},
        ])
        sdk.tasks.create(
            id="hooks-patch-001",
            file_path="/tmp/hooks-patch-001.md",
            title="Hooks Patch Test",
            hooks=hooks,
        )
        new_hooks = json.dumps([
            {"name": "run_tests", "point": "before_submit", "type": "agent", "status": "passed"},
        ])
        updated = sdk.tasks.update("hooks-patch-001", hooks=new_hooks)
        parsed = json.loads(updated["hooks"]) if isinstance(updated.get("hooks"), str) else updated.get("hooks", [])
        assert parsed[0]["status"] == "passed"

    def test_task_without_hooks(self, sdk, clean_tasks):
        """Tasks created without hooks have hooks=null."""
        task = sdk.tasks.create(
            id="hooks-none-001",
            file_path="/tmp/hooks-none-001.md",
            title="No Hooks Task",
        )
        assert task.get("hooks") is None
