"""Unit tests for HookManager."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from orchestrator.hook_manager import HookEvidence, HookManager


def _task(hooks=None, **kw):
    t = {"id": "test-001", "title": "Test", "file_path": "/tmp/t.md", "queue": "claimed"}
    if hooks is not None:
        t["hooks"] = hooks
    t.update(kw)
    return t


def _sdk():
    s = MagicMock()
    s.tasks = MagicMock()
    return s


YAML_PROJECT = (
    "hooks:\n"
    "  before_submit:\n"
    "    - rebase_on_main\n"
    "    - run_tests\n"
    "    - create_pr\n"
    "  before_merge:\n"
    "    - merge_pr\n"
)

YAML_TYPE_OVERRIDE = (
    "hooks:\n"
    "  before_submit:\n"
    "    - rebase_on_main\n"
    "    - create_pr\n"
    "task_types:\n"
    "  hotfix:\n"
    "    hooks:\n"
    "      before_submit:\n"
    "        - create_pr\n"
)

YAML_SIMPLE = "hooks:\n  before_submit:\n    - create_pr\n"

YAML_WITH_FAKE = "hooks:\n  before_submit:\n    - create_pr\n    - fake_hook\n"


class TestResolveHooksForTask:
    def test_project_config(self, tmp_path):
        d = tmp_path / ".octopoid"
        d.mkdir()
        (d / "config.yaml").write_text(YAML_PROJECT)
        with patch("orchestrator.config.find_parent_project", return_value=tmp_path):
            hooks = HookManager(_sdk()).resolve_hooks_for_task(task_type=None)
        assert [h["name"] for h in hooks] == ["rebase_on_main", "run_tests", "create_pr", "merge_pr"]
        for h in hooks:
            assert h["type"] == ("orchestrator" if h["name"] == "merge_pr" else "agent")
            assert h["status"] == "pending"

    def test_task_type_parameter_ignored(self, tmp_path):
        """Task type parameter is ignored since task_types config was removed."""
        d = tmp_path / ".octopoid"
        d.mkdir()
        (d / "config.yaml").write_text(YAML_TYPE_OVERRIDE)
        with patch("orchestrator.config.find_parent_project", return_value=tmp_path):
            result = HookManager(_sdk()).resolve_hooks_for_task(task_type="hotfix")
        # Should use project-level hooks, not type-specific ones
        assert [h["name"] for h in result] == ["rebase_on_main", "create_pr"]

    def test_unknown_type_falls_through(self, tmp_path):
        d = tmp_path / ".octopoid"
        d.mkdir()
        (d / "config.yaml").write_text(YAML_SIMPLE)
        with patch("orchestrator.config.find_parent_project", return_value=tmp_path):
            result = HookManager(_sdk()).resolve_hooks_for_task(task_type="nope")
        assert [h["name"] for h in result] == ["create_pr"]

    def test_defaults_when_no_config(self):
        with patch("orchestrator.config.find_parent_project", return_value=Path("/nonexistent")):
            names = [h["name"] for h in HookManager(_sdk()).resolve_hooks_for_task()]
        assert "create_pr" in names and "merge_pr" in names

    def test_unknown_hook_skipped(self, tmp_path):
        d = tmp_path / ".octopoid"
        d.mkdir()
        (d / "config.yaml").write_text(YAML_WITH_FAKE)
        with patch("orchestrator.config.find_parent_project", return_value=tmp_path):
            result = HookManager(_sdk()).resolve_hooks_for_task()
        assert [h["name"] for h in result] == ["create_pr"]


class TestPendingAndTransition:
    def test_all_pending(self):
        t = _task(hooks=[
            {"name": "a", "point": "before_submit", "type": "agent", "status": "pending"},
            {"name": "b", "point": "before_submit", "type": "agent", "status": "pending"},
        ])
        assert len(HookManager(_sdk()).get_pending_hooks(t)) == 2

    def test_filter_by_point(self):
        t = _task(hooks=[
            {"name": "a", "point": "before_submit", "type": "agent", "status": "pending"},
            {"name": "b", "point": "before_merge", "type": "orchestrator", "status": "pending"},
        ])
        p = HookManager(_sdk()).get_pending_hooks(t, point="before_merge")
        assert len(p) == 1 and p[0]["name"] == "b"

    def test_filter_by_type(self):
        t = _task(hooks=[
            {"name": "a", "point": "before_submit", "type": "agent", "status": "pending"},
            {"name": "b", "point": "before_merge", "type": "orchestrator", "status": "pending"},
        ])
        p = HookManager(_sdk()).get_pending_hooks(t, hook_type="orchestrator")
        assert len(p) == 1 and p[0]["name"] == "b"

    def test_none_pending_when_passed(self):
        t = _task(hooks=[{"name": "a", "point": "before_submit", "type": "agent", "status": "passed"}])
        assert len(HookManager(_sdk()).get_pending_hooks(t)) == 0

    def test_can_transition_true(self):
        t = _task(hooks=[{"name": "merge_pr", "point": "before_merge", "type": "orchestrator", "status": "passed"}])
        ok, names = HookManager(_sdk()).can_transition(t, "before_merge")
        assert ok and names == []

    def test_can_transition_false(self):
        t = _task(hooks=[{"name": "merge_pr", "point": "before_merge", "type": "orchestrator", "status": "pending"}])
        ok, names = HookManager(_sdk()).can_transition(t, "before_merge")
        assert not ok and names == ["merge_pr"]

    def test_can_transition_false_when_hook_failed(self):
        # Reproduces bug: a failed hook must block transition, not be silently ignored
        t = _task(hooks=[{"name": "merge_pr", "point": "before_merge", "type": "orchestrator", "status": "failed"}])
        ok, names = HookManager(_sdk()).can_transition(t, "before_merge")
        assert not ok and names == ["merge_pr"]

    def test_can_transition_returns_failed_names_not_pending(self):
        # Failed hook names are returned (not an empty list)
        t = _task(hooks=[
            {"name": "merge_pr", "point": "before_merge", "type": "orchestrator", "status": "failed"},
            {"name": "notify", "point": "before_merge", "type": "orchestrator", "status": "passed"},
        ])
        ok, names = HookManager(_sdk()).can_transition(t, "before_merge")
        assert not ok and "merge_pr" in names

    def test_hooks_as_json_string(self):
        t = _task(hooks=json.dumps([{"name": "a", "point": "before_submit", "type": "agent", "status": "pending"}]))
        assert len(HookManager(_sdk()).get_pending_hooks(t)) == 1

    def test_no_hooks_field(self):
        assert len(HookManager(_sdk()).get_pending_hooks({"id": "x"})) == 0


class TestRunOrchestratorHook:
    def test_merge_pr_success(self):
        repo = MagicMock()
        repo.merge_pr.return_value = True
        hm = HookManager(_sdk(), repo_manager_factory=lambda w, base_branch="main": repo)
        ev = hm.run_orchestrator_hook(
            _task(pr_number=42),
            {"name": "merge_pr", "point": "before_merge", "type": "orchestrator"},
        )
        assert ev.status == "passed" and "42" in ev.message
        repo.merge_pr.assert_called_once_with(42, method="merge")

    def test_merge_pr_failure(self):
        repo = MagicMock()
        repo.merge_pr.return_value = False
        hm = HookManager(_sdk(), repo_manager_factory=lambda w, base_branch="main": repo)
        ev = hm.run_orchestrator_hook(
            _task(pr_number=99),
            {"name": "merge_pr", "point": "before_merge", "type": "orchestrator"},
        )
        assert ev.status == "failed"

    def test_merge_pr_no_pr(self):
        ev = HookManager(_sdk()).run_orchestrator_hook(
            _task(),
            {"name": "merge_pr", "point": "before_merge", "type": "orchestrator"},
        )
        assert ev.status == "passed" and "skipped" in ev.message.lower()

    def test_unknown_hook(self):
        ev = HookManager(_sdk()).run_orchestrator_hook(
            _task(),
            {"name": "x", "point": "before_merge", "type": "orchestrator"},
        )
        assert ev.status == "failed"


class TestRecordEvidence:
    def test_updates_status(self):
        sdk = _sdk()
        hl = [
            {"name": "run_tests", "point": "before_submit", "type": "agent", "status": "pending"},
            {"name": "create_pr", "point": "before_submit", "type": "agent", "status": "pending"},
        ]
        sdk.tasks.get.return_value = _task(hooks=hl)
        sdk.tasks.update.return_value = _task(hooks=hl)
        HookManager(sdk).record_evidence("test-001", "run_tests", HookEvidence(status="passed"))
        updated = json.loads(sdk.tasks.update.call_args[1]["hooks"])
        assert next(h for h in updated if h["name"] == "run_tests")["status"] == "passed"
        assert next(h for h in updated if h["name"] == "create_pr")["status"] == "pending"

    def test_with_data(self):
        sdk = _sdk()
        hl = [{"name": "run_tests", "point": "before_submit", "type": "agent", "status": "pending"}]
        sdk.tasks.get.return_value = _task(hooks=hl)
        sdk.tasks.update.return_value = _task(hooks=hl)
        HookManager(sdk).record_evidence(
            "test-001", "run_tests", HookEvidence(status="passed", data={"out": "ok"})
        )
        assert json.loads(sdk.tasks.update.call_args[1]["hooks"])[0]["evidence"]["out"] == "ok"

    def test_unknown_hook(self):
        sdk = _sdk()
        sdk.tasks.get.return_value = _task(
            hooks=[{"name": "run_tests", "point": "before_submit", "type": "agent", "status": "pending"}]
        )
        HookManager(sdk).record_evidence("test-001", "nope", HookEvidence(status="passed"))
        sdk.tasks.update.assert_not_called()

    def test_task_not_found(self):
        sdk = _sdk()
        sdk.tasks.get.return_value = None
        assert HookManager(sdk).record_evidence("x", "y", HookEvidence(status="passed")) is None

    def test_sdk_exception(self):
        sdk = _sdk()
        sdk.tasks.get.side_effect = RuntimeError("boom")
        assert HookManager(sdk).record_evidence("x", "y", HookEvidence(status="passed")) is None


class TestProcessOrchestratorHooks:
    """Tests for the process_orchestrator_hooks scheduler function."""

    def _make_sdk(self, task_hooks_after_run):
        """Build a mock SDK where tasks.get (re-fetch) returns updated hooks."""
        sdk = _sdk()
        task = _task(
            hooks=[{"name": "merge_pr", "point": "before_merge", "type": "orchestrator", "status": "pending"}],
            pr_number=42,
        )
        sdk.tasks.list.return_value = [task]
        # Re-fetched task has hooks reflecting the outcome of record_evidence
        updated = _task(hooks=task_hooks_after_run, pr_number=42)
        sdk.tasks.get.return_value = updated
        sdk.tasks.update.return_value = updated
        return sdk, task

    def test_does_not_accept_when_merge_pr_fails(self):
        """Reproduces bug: task must NOT be accepted when merge_pr hook fails."""
        failed_hooks = [{"name": "merge_pr", "point": "before_merge", "type": "orchestrator", "status": "failed"}]
        sdk, _task_obj = self._make_sdk(failed_hooks)

        repo = MagicMock()
        repo.merge_pr.return_value = False  # simulate PR merge failure

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk), \
             patch("orchestrator.scheduler.HookManager") as MockHM:
            hm_instance = MagicMock()
            MockHM.return_value = hm_instance
            hm_instance.get_pending_hooks.return_value = [
                {"name": "merge_pr", "point": "before_merge", "type": "orchestrator", "status": "pending"}
            ]
            hm_instance.run_orchestrator_hook.return_value = HookEvidence(status="failed", message="PR merge failed")
            hm_instance.record_evidence.return_value = None
            # can_transition must return False for a failed hook
            hm_instance.can_transition.return_value = (False, ["merge_pr"])

            from orchestrator.scheduler import process_orchestrator_hooks
            process_orchestrator_hooks()

        sdk.tasks.accept.assert_not_called()

    def test_accepts_when_all_hooks_pass(self):
        """Task is accepted when all orchestrator hooks pass."""
        passed_hooks = [{"name": "merge_pr", "point": "before_merge", "type": "orchestrator", "status": "passed"}]
        sdk, _task_obj = self._make_sdk(passed_hooks)

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk), \
             patch("orchestrator.scheduler.HookManager") as MockHM:
            hm_instance = MagicMock()
            MockHM.return_value = hm_instance
            hm_instance.get_pending_hooks.return_value = [
                {"name": "merge_pr", "point": "before_merge", "type": "orchestrator", "status": "pending"}
            ]
            hm_instance.run_orchestrator_hook.return_value = HookEvidence(status="passed", message="Merged PR #42")
            hm_instance.record_evidence.return_value = None
            hm_instance.can_transition.return_value = (True, [])

            from orchestrator.scheduler import process_orchestrator_hooks
            process_orchestrator_hooks()

        sdk.tasks.accept.assert_called_once_with(task_id="test-001", accepted_by="scheduler-hooks")
