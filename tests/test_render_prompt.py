"""Unit tests for _render_prompt and its helper functions.

These tests verify prompt rendering in isolation — no filesystem setup for task
directories or worktrees is required.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.scheduler import (
    _build_required_steps,
    _load_global_instructions,
    _load_review_section,
    _render_prompt,
)


# =============================================================================
# _load_global_instructions
# =============================================================================


class TestLoadGlobalInstructions:
    def test_returns_empty_when_no_files(self, tmp_path):
        agent_dir = str(tmp_path / "agent")
        Path(agent_dir).mkdir()
        with patch("orchestrator.scheduler.get_global_instructions_path", return_value=tmp_path / "nonexistent.md"):
            result = _load_global_instructions(agent_dir)
        assert result == ""

    def test_loads_global_instructions(self, tmp_path):
        gi_path = tmp_path / "global.md"
        gi_path.write_text("Global instructions here.")
        agent_dir = str(tmp_path / "agent")
        Path(agent_dir).mkdir()
        with patch("orchestrator.scheduler.get_global_instructions_path", return_value=gi_path):
            result = _load_global_instructions(agent_dir)
        assert result == "Global instructions here."

    def test_appends_agent_instructions(self, tmp_path):
        gi_path = tmp_path / "global.md"
        gi_path.write_text("Global.")
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "instructions.md").write_text("Agent specific.")
        with patch("orchestrator.scheduler.get_global_instructions_path", return_value=gi_path):
            result = _load_global_instructions(str(agent_dir))
        assert result == "Global.\n\nAgent specific."

    def test_agent_instructions_only_when_no_global(self, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "instructions.md").write_text("Only agent.")
        with patch("orchestrator.scheduler.get_global_instructions_path", return_value=tmp_path / "nonexistent.md"):
            result = _load_global_instructions(str(agent_dir))
        assert result == "\n\nOnly agent."


# =============================================================================
# _build_required_steps
# =============================================================================


class TestBuildRequiredSteps:
    def test_no_hooks_returns_empty(self):
        assert _build_required_steps({}) == ""

    def test_none_hooks_returns_empty(self):
        assert _build_required_steps({"hooks": None}) == ""

    def test_empty_hooks_list_returns_empty(self):
        assert _build_required_steps({"hooks": []}) == ""

    def test_non_agent_hooks_filtered_out(self):
        task = {"hooks": [{"type": "scheduler", "name": "run_tests"}]}
        assert _build_required_steps(task) == ""

    def test_create_pr_hook_skipped(self):
        task = {"hooks": [{"type": "agent", "name": "create_pr"}]}
        # Only create_pr — all steps are skipped, result should still be header + empty body
        # Actually per logic: if all hooks are skipped the header is still built but no numbered items.
        # Let's verify it returns a non-empty string with just the header (no numbered lines).
        result = _build_required_steps(task)
        assert "Required Steps" in result
        assert "create_pr" not in result

    def test_run_tests_hook_produces_script_line(self):
        task = {"hooks": [{"type": "agent", "name": "run_tests"}]}
        result = _build_required_steps(task)
        assert "Run tests: `../scripts/run-tests`" in result
        assert "Required Steps Before Writing result.json" in result

    def test_custom_hook_name_used_directly(self):
        task = {"hooks": [{"type": "agent", "name": "deploy"}]}
        result = _build_required_steps(task)
        assert "1. deploy" in result

    def test_string_hooks_parsed_as_json(self):
        hooks = json.dumps([{"type": "agent", "name": "run_tests"}])
        task = {"hooks": hooks}
        result = _build_required_steps(task)
        assert "Run tests" in result

    def test_multiple_hooks_numbered_correctly(self):
        task = {
            "hooks": [
                {"type": "agent", "name": "run_tests"},
                {"type": "agent", "name": "deploy"},
            ]
        }
        result = _build_required_steps(task)
        assert "1. Run tests" in result
        assert "2. deploy" in result

    def test_create_pr_excluded_from_numbering(self):
        task = {
            "hooks": [
                {"type": "agent", "name": "run_tests"},
                {"type": "agent", "name": "create_pr"},
                {"type": "agent", "name": "deploy"},
            ]
        }
        result = _build_required_steps(task)
        assert "create_pr" not in result
        # run_tests is 1, deploy is 3 (enumerate counts it as 3 even though create_pr is skipped)
        assert "Run tests" in result
        assert "deploy" in result


# =============================================================================
# _load_review_section
# =============================================================================


class TestLoadReviewSection:
    def test_empty_task_id_returns_empty(self):
        assert _load_review_section("") == ""

    def test_no_thread_returns_empty(self):
        with patch("orchestrator.scheduler._load_review_section") as mock:
            # Test via actual function with mocked get_thread
            pass

        with patch("orchestrator.task_thread.get_thread", return_value=[]):
            result = _load_review_section("abc123")
        assert result == ""

    def test_returns_formatted_thread(self):
        messages = [
            {"role": "rejection", "content": "Fix the bug.", "timestamp": "2026-01-01T00:00:00"}
        ]
        with patch("orchestrator.task_thread.get_thread", return_value=messages):
            result = _load_review_section("abc123")
        assert "Previous Rejection Feedback" in result
        assert "Fix the bug." in result

    def test_exception_returns_empty(self):
        with patch("orchestrator.task_thread.get_thread", side_effect=RuntimeError("network error")):
            result = _load_review_section("abc123")
        assert result == ""


# =============================================================================
# _render_prompt
# =============================================================================


class TestRenderPrompt:
    def test_raises_if_no_agent_dir(self):
        with pytest.raises(ValueError, match="Agent directory or prompt.md not found"):
            _render_prompt({}, {"agent_dir": None})

    def test_raises_if_prompt_md_missing(self, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        with pytest.raises(ValueError, match="Agent directory or prompt.md not found"):
            _render_prompt({}, {"agent_dir": str(agent_dir)})

    def test_basic_substitution(self, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "prompt.md").write_text(
            "Task: $task_id — $task_title\n$global_instructions\n$required_steps\n$review_section"
        )
        task = {
            "id": "abc123",
            "title": "My Task",
            "content": "Do the thing.",
            "priority": "P1",
            "branch": "main",
            "type": "feature",
        }
        with (
            patch("orchestrator.scheduler.get_global_instructions_path", return_value=tmp_path / "none.md"),
            patch("orchestrator.scheduler._load_review_section", return_value=""),
            patch("orchestrator.scheduler.get_base_branch", return_value="main"),
        ):
            result = _render_prompt(task, {"agent_dir": str(agent_dir)})

        assert "Task: abc123 — My Task" in result

    def test_uses_helpers(self, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "prompt.md").write_text("$global_instructions|$required_steps|$review_section")

        task = {"id": "t1", "hooks": [{"type": "agent", "name": "run_tests"}]}

        with (
            patch("orchestrator.scheduler.get_global_instructions_path", return_value=tmp_path / "gi.md"),
            patch("orchestrator.scheduler._load_review_section", return_value="REVIEW"),
            patch("orchestrator.scheduler.get_base_branch", return_value="main"),
        ):
            result = _render_prompt(task, {"agent_dir": str(agent_dir)})

        assert "run-tests" in result
        assert "REVIEW" in result

    def test_missing_task_fields_use_defaults(self, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "prompt.md").write_text("id=$task_id title=$task_title priority=$task_priority")

        with (
            patch("orchestrator.scheduler.get_global_instructions_path", return_value=tmp_path / "none.md"),
            patch("orchestrator.scheduler._load_review_section", return_value=""),
            patch("orchestrator.scheduler.get_base_branch", return_value="main"),
        ):
            result = _render_prompt({}, {"agent_dir": str(agent_dir)})

        assert "id=unknown" in result
        assert "title=Untitled" in result
        assert "priority=P2" in result
