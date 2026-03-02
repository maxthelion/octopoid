"""Tests for the needs_continuation consumer: continuer agent logic.

Covers:
- _infer_implementer recognises needs_continuation outcome
- _get_continuation_count / _increment_continuation_count file helpers
- _handle_continuation_outcome cycle limit (escalates to intervention at max cycles)
- _load_continuation_section in scheduler (returns context for continuer, empty otherwise)
"""

from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


# =============================================================================
# _infer_implementer — needs_continuation classification
# =============================================================================


class TestInferImplementerNeedsContinuation:
    """_infer_implementer maps haiku 'needs_continuation' response correctly."""

    def test_needs_continuation_response_returns_needs_continuation(self):
        """Haiku returning 'needs_continuation' maps to outcome=needs_continuation."""
        from octopoid.result_handler import _infer_implementer

        with patch("octopoid.result_handler._call_haiku", return_value="needs_continuation"):
            result = _infer_implementer("I ran out of turns.")

        assert result == {"outcome": "needs_continuation"}

    def test_continuation_partial_match_returns_needs_continuation(self):
        """Haiku returning 'continuation' maps to outcome=needs_continuation."""
        from octopoid.result_handler import _infer_implementer

        with patch("octopoid.result_handler._call_haiku", return_value="continuation"):
            result = _infer_implementer("Hit the turn limit mid-task.")

        assert result == {"outcome": "needs_continuation"}

    def test_done_still_returns_done(self):
        """Haiku returning 'done' still maps to done (no regression)."""
        from octopoid.result_handler import _infer_implementer

        with patch("octopoid.result_handler._call_haiku", return_value="done"):
            result = _infer_implementer("All tasks complete.")

        assert result["outcome"] == "done"

    def test_failed_still_returns_failed(self):
        """Haiku returning 'failed' still maps to failed (no regression)."""
        from octopoid.result_handler import _infer_implementer

        with patch("octopoid.result_handler._call_haiku", return_value="failed"):
            result = _infer_implementer("Cannot complete the task.")

        assert result["outcome"] == "failed"


# =============================================================================
# _get_continuation_count / _increment_continuation_count
# =============================================================================


class TestContinuationCountHelpers:
    """File-based continuation cycle counters work correctly."""

    def test_get_count_returns_zero_when_no_file(self, tmp_path):
        """_get_continuation_count returns 0 when continuation_count file is absent."""
        from octopoid.result_handler import _get_continuation_count

        assert _get_continuation_count(tmp_path) == 0

    def test_increment_creates_file_and_returns_one(self, tmp_path):
        """_increment_continuation_count creates the file and returns 1 on first call."""
        from octopoid.result_handler import _increment_continuation_count

        count = _increment_continuation_count(tmp_path)

        assert count == 1
        assert (tmp_path / "continuation_count").read_text() == "1"

    def test_increment_increments_existing_count(self, tmp_path):
        """_increment_continuation_count increments an existing counter."""
        from octopoid.result_handler import _increment_continuation_count

        (tmp_path / "continuation_count").write_text("2")
        count = _increment_continuation_count(tmp_path)

        assert count == 3
        assert (tmp_path / "continuation_count").read_text() == "3"

    def test_get_count_reads_file(self, tmp_path):
        """_get_continuation_count reads the counter from the file."""
        from octopoid.result_handler import _get_continuation_count

        (tmp_path / "continuation_count").write_text("5")
        assert _get_continuation_count(tmp_path) == 5

    def test_get_count_returns_zero_on_corrupt_file(self, tmp_path):
        """_get_continuation_count returns 0 gracefully when file content is invalid."""
        from octopoid.result_handler import _get_continuation_count

        (tmp_path / "continuation_count").write_text("not-a-number")
        assert _get_continuation_count(tmp_path) == 0


# =============================================================================
# _handle_continuation_outcome — cycle limit and queue routing
# =============================================================================


class TestHandleContinuationOutcomeCycleLimit:
    """_handle_continuation_outcome escalates to intervention after max cycles."""

    def _make_sdk(self):
        sdk = MagicMock()
        sdk.tasks.get.return_value = {"id": "task-abc", "queue": "claimed", "flow": "default"}
        sdk.tasks.update.return_value = {"id": "task-abc", "queue": "needs_continuation"}
        return sdk

    def test_first_continuation_moves_to_needs_continuation_queue(self, tmp_path):
        """First continuation cycle: task moves to needs_continuation queue."""
        from octopoid.result_handler import _handle_continuation_outcome

        sdk = self._make_sdk()
        task = {"id": "task-abc", "flow": "default", "queue": "claimed"}

        with patch("octopoid.config.get_tasks_dir", return_value=tmp_path):
            result = _handle_continuation_outcome(sdk, "task-abc", task, "implementer-1", "claimed")

        assert result is True
        sdk.tasks.update.assert_called_once_with("task-abc", queue="needs_continuation")

    def test_continuation_count_incremented_on_each_cycle(self, tmp_path):
        """Each call to _handle_continuation_outcome increments the cycle counter."""
        from octopoid.result_handler import _handle_continuation_outcome, _get_continuation_count

        sdk = self._make_sdk()
        task = {"id": "task-abc", "flow": "default", "queue": "claimed"}
        task_dir = tmp_path / "task-abc"
        task_dir.mkdir()

        with patch("octopoid.config.get_tasks_dir", return_value=tmp_path):
            _handle_continuation_outcome(sdk, "task-abc", task, "implementer-1", "claimed")
            assert _get_continuation_count(task_dir) == 1

            _handle_continuation_outcome(sdk, "task-abc", task, "implementer-1", "claimed")
            assert _get_continuation_count(task_dir) == 2

    def test_at_max_cycles_escalates_to_intervention(self, tmp_path):
        """At _MAX_CONTINUATION_CYCLES, task goes to intervention instead of needs_continuation."""
        from octopoid.result_handler import _handle_continuation_outcome, _MAX_CONTINUATION_CYCLES

        sdk = self._make_sdk()
        task = {"id": "task-abc", "flow": "default", "queue": "claimed"}
        task_dir = tmp_path / "task-abc"
        task_dir.mkdir()

        # Pre-seed the count to one below the limit
        (task_dir / "continuation_count").write_text(str(_MAX_CONTINUATION_CYCLES - 1))

        with patch("octopoid.config.get_tasks_dir", return_value=tmp_path), \
             patch("octopoid.result_handler.request_intervention") as mock_intervention:
            result = _handle_continuation_outcome(sdk, "task-abc", task, "continuer-1", "claimed")

        assert result is True
        mock_intervention.assert_called_once()
        # Intervention should NOT move to needs_continuation
        sdk.tasks.update.assert_not_called()

        # Check the intervention message mentions the cycle limit
        call_kwargs = mock_intervention.call_args
        reason = call_kwargs.kwargs.get("reason", "")
        assert "continuation" in reason.lower()

    def test_at_max_cycles_intervention_source_is_cycle_limit(self, tmp_path):
        """Intervention source tag is 'continuation-cycle-limit'."""
        from octopoid.result_handler import _handle_continuation_outcome, _MAX_CONTINUATION_CYCLES

        sdk = self._make_sdk()
        task = {"id": "task-abc", "flow": "default", "queue": "claimed"}
        task_dir = tmp_path / "task-abc"
        task_dir.mkdir()
        (task_dir / "continuation_count").write_text(str(_MAX_CONTINUATION_CYCLES - 1))

        with patch("octopoid.config.get_tasks_dir", return_value=tmp_path), \
             patch("octopoid.result_handler.request_intervention") as mock_intervention:
            _handle_continuation_outcome(sdk, "task-abc", task, "continuer-1", "claimed")

        call_kwargs = mock_intervention.call_args.kwargs
        assert call_kwargs.get("source") == "continuation-cycle-limit"

    def test_terminal_queue_returns_true_no_update(self, tmp_path):
        """Task already in 'done' queue: returns True without any API call."""
        from octopoid.result_handler import _handle_continuation_outcome

        sdk = self._make_sdk()
        task = {"id": "task-abc", "flow": "default", "queue": "done"}

        # For terminal queues, get_tasks_dir is never reached, but provide it anyway
        with patch("octopoid.config.get_tasks_dir", return_value=tmp_path):
            result = _handle_continuation_outcome(sdk, "task-abc", task, "implementer-1", "done")

        assert result is True
        sdk.tasks.update.assert_not_called()

    def test_non_claimed_non_terminal_queue_returns_false(self, tmp_path):
        """Task in non-claimed, non-terminal queue: returns False to retry."""
        from octopoid.result_handler import _handle_continuation_outcome

        sdk = self._make_sdk()
        task = {"id": "task-abc", "flow": "default", "queue": "incoming"}

        with patch("octopoid.config.get_tasks_dir", return_value=tmp_path):
            result = _handle_continuation_outcome(sdk, "task-abc", task, "implementer-1", "incoming")

        assert result is False
        sdk.tasks.update.assert_not_called()


# =============================================================================
# _load_continuation_section — scheduler prompt helper
# =============================================================================


class TestLoadContinuationSection:
    """_load_continuation_section returns context for continuer agents only."""

    def test_non_continuation_agent_returns_empty_string(self, tmp_path):
        """For implementer (claim_from=incoming), returns empty string."""
        from octopoid.scheduler import _load_continuation_section

        agent_config = {"claim_from": "incoming"}
        with patch("octopoid.scheduler.get_tasks_dir", return_value=tmp_path):
            result = _load_continuation_section("task-abc", agent_config)

        assert result == ""

    def test_missing_prev_stdout_returns_empty_string(self, tmp_path):
        """When prev_stdout.log doesn't exist, returns empty string."""
        from octopoid.scheduler import _load_continuation_section

        agent_config = {"claim_from": "needs_continuation"}
        with patch("octopoid.scheduler.get_tasks_dir", return_value=tmp_path):
            result = _load_continuation_section("task-abc", agent_config)

        assert result == ""

    def test_with_prev_stdout_returns_continuation_section(self, tmp_path):
        """When prev_stdout.log exists, returns a populated continuation section."""
        from octopoid.scheduler import _load_continuation_section

        task_dir = tmp_path / "task-abc"
        task_dir.mkdir()
        (task_dir / "prev_stdout.log").write_text("I made progress but ran out of turns.")

        agent_config = {"claim_from": "needs_continuation"}
        with patch("octopoid.scheduler.get_tasks_dir", return_value=tmp_path):
            result = _load_continuation_section("task-abc", agent_config)

        assert "Continuation Context" in result
        assert "I made progress but ran out of turns." in result

    def test_empty_prev_stdout_returns_empty_string(self, tmp_path):
        """When prev_stdout.log is empty/whitespace, returns empty string."""
        from octopoid.scheduler import _load_continuation_section

        task_dir = tmp_path / "task-abc"
        task_dir.mkdir()
        (task_dir / "prev_stdout.log").write_text("   ")

        agent_config = {"claim_from": "needs_continuation"}
        with patch("octopoid.scheduler.get_tasks_dir", return_value=tmp_path):
            result = _load_continuation_section("task-abc", agent_config)

        assert result == ""

    def test_empty_task_id_returns_empty_string(self, tmp_path):
        """Empty task_id returns empty string without error."""
        from octopoid.scheduler import _load_continuation_section

        agent_config = {"claim_from": "needs_continuation"}
        with patch("octopoid.scheduler.get_tasks_dir", return_value=tmp_path):
            result = _load_continuation_section("", agent_config)

        assert result == ""
