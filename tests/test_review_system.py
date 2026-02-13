"""Tests for the gatekeeper review system.

Covers:
- _insert_rejection_feedback() helper
- review_utils.py (init, record, complete, pass/fail checks)
- Scheduler environment variable passing to gatekeepers
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# =============================================================================
# Rejection Feedback Insertion Helper Tests
# =============================================================================


class TestInsertRejectionFeedback:
    """Tests for _insert_rejection_feedback() helper."""

    def test_inserts_before_first_heading(self):
        """Test insertion before the first ## heading."""
        from orchestrator.queue_utils import _insert_rejection_feedback

        content = "# Title\n\nROLE: implement\n\n## Context\nSome context.\n"
        feedback = "## Rejection Notice\n\nFeedback here.\n"

        result = _insert_rejection_feedback(content, feedback)

        assert "## Rejection Notice" in result
        notice_pos = result.index("## Rejection Notice")
        context_pos = result.index("## Context")
        assert notice_pos < context_pos
        # Original content preserved
        assert "# Title" in result
        assert "ROLE: implement" in result
        assert "Some context." in result

    def test_replaces_existing_rejection_notice(self):
        """Test that existing rejection notice is replaced."""
        from orchestrator.queue_utils import _insert_rejection_feedback

        content = (
            "# Title\n\n"
            "## Rejection Notice (rejection #1)\n\n"
            "Old feedback.\n\n"
            "## Context\nSome context.\n"
        )
        feedback = "## Rejection Notice (rejection #2)\n\nNew feedback.\n"

        result = _insert_rejection_feedback(content, feedback)

        assert "Old feedback." not in result
        assert "New feedback." in result
        assert result.count("## Rejection Notice") == 1

    def test_replaces_old_format_review_feedback(self):
        """Test that old-format Review Feedback sections are stripped."""
        from orchestrator.queue_utils import _insert_rejection_feedback

        content = (
            "# Title\n\n"
            "## Context\nSome context.\n\n"
            "## Review Feedback (rejection #1)\n\nOld feedback.\n"
        )
        feedback = "## Rejection Notice (rejection #1)\n\nNew feedback.\n"

        result = _insert_rejection_feedback(content, feedback)

        assert "## Review Feedback" not in result
        assert "Old feedback." not in result
        assert "New feedback." in result

    def test_handles_no_headings(self):
        """Test insertion when there are no ## headings."""
        from orchestrator.queue_utils import _insert_rejection_feedback

        content = "# Title\n\nJust some text.\n"
        feedback = "## Rejection Notice\n\nFeedback.\n"

        result = _insert_rejection_feedback(content, feedback)

        assert "## Rejection Notice" in result
        assert "Just some text." in result


# =============================================================================
# Review Utils Tests
# =============================================================================


class TestReviewUtils:
    """Tests for review_utils.py module."""

    def test_init_task_review(self, mock_config):
        """Test initializing review tracking for a task."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import init_task_review, load_review_meta

            review_dir = init_task_review(
                "test1",
                branch="agent/test1",
                base_branch="main",
                required_checks=["architecture", "testing"],
            )

            assert review_dir.exists()
            assert (review_dir / "meta.json").exists()
            assert (review_dir / "checks" / "architecture.json").exists()
            assert (review_dir / "checks" / "testing.json").exists()

            meta = load_review_meta("test1")
            assert meta["status"] == "in_progress"
            assert meta["branch"] == "agent/test1"
            assert len(meta["required_checks"]) == 2

    def test_record_review_result(self, mock_config):
        """Test recording a single review check result."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import (
                init_task_review, record_review_result, load_check_result,
            )

            init_task_review("rec1", branch="agent/rec1")

            record_review_result(
                "rec1",
                "architecture",
                "pass",
                "All good",
                details="No issues found.",
                submitted_by="gk-arch",
            )

            result = load_check_result("rec1", "architecture")
            assert result["status"] == "pass"
            assert result["summary"] == "All good"
            assert result["submitted_by"] == "gk-arch"

    def test_all_reviews_complete_when_all_done(self, mock_config):
        """Test all_reviews_complete returns True when all checks are done."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import (
                init_task_review, record_review_result, all_reviews_complete,
            )

            init_task_review("comp1", branch="b", required_checks=["a", "b"])
            assert all_reviews_complete("comp1") is False

            record_review_result("comp1", "a", "pass", "ok")
            assert all_reviews_complete("comp1") is False

            record_review_result("comp1", "b", "fail", "bad")
            assert all_reviews_complete("comp1") is True

    def test_all_reviews_passed(self, mock_config):
        """Test all_reviews_passed correctly identifies failures."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import (
                init_task_review, record_review_result, all_reviews_passed,
            )

            init_task_review("pass1", branch="b", required_checks=["a", "b", "c"])
            record_review_result("pass1", "a", "pass", "ok")
            record_review_result("pass1", "b", "fail", "bad")
            record_review_result("pass1", "c", "pass", "ok")

            passed, failed = all_reviews_passed("pass1")
            assert passed is False
            assert failed == ["b"]

    def test_all_reviews_passed_when_all_pass(self, mock_config):
        """Test all_reviews_passed returns True when all pass."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import (
                init_task_review, record_review_result, all_reviews_passed,
            )

            init_task_review("allp1", branch="b", required_checks=["x", "y"])
            record_review_result("allp1", "x", "pass", "ok")
            record_review_result("allp1", "y", "pass", "ok")

            passed, failed = all_reviews_passed("allp1")
            assert passed is True
            assert failed == []

    def test_get_review_feedback_aggregates(self, mock_config):
        """Test get_review_feedback aggregates failed check details."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import (
                init_task_review, record_review_result,
                get_review_feedback as review_feedback,
            )

            init_task_review("agg1", branch="b", required_checks=["arch", "test"])
            record_review_result("agg1", "arch", "fail", "Boundary issue", details="Engine.ts line 42")
            record_review_result("agg1", "test", "pass", "Tests look good")

            feedback = review_feedback("agg1")
            assert "REJECTED" in feedback
            assert "Boundary issue" in feedback
            assert "Engine.ts line 42" in feedback
            assert "PASSED" in feedback

    def test_cleanup_review(self, mock_config):
        """Test cleanup_review removes the review directory."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import init_task_review, cleanup_review, get_review_dir

            init_task_review("clean1", branch="b")
            assert get_review_dir("clean1").exists()

            result = cleanup_review("clean1")
            assert result is True
            assert not get_review_dir("clean1").exists()

    def test_has_active_review(self, mock_config):
        """Test has_active_review checks for in-progress reviews."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import init_task_review, has_active_review

            assert has_active_review("norev") is False

            init_task_review("actrev", branch="b")
            assert has_active_review("actrev") is True

    def test_idempotent_init(self, mock_config):
        """Test that initializing review twice doesn't corrupt state."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import (
                init_task_review, record_review_result, load_check_result,
            )

            init_task_review("idem1", branch="b", required_checks=["a"])
            record_review_result("idem1", "a", "pass", "ok")

            # Re-init should overwrite (fresh review)
            init_task_review("idem1", branch="b", required_checks=["a"])
            result = load_check_result("idem1", "a")
            assert result["status"] == "pending"


# =============================================================================
# Scheduler Environment Variable Tests
# =============================================================================


class TestSchedulerGatekeeperEnv:
    """Tests for scheduler passing review env vars to gatekeepers."""

    def test_spawn_agent_passes_review_env(self, tmp_path):
        """Test that spawn_agent passes REVIEW_TASK_ID and REVIEW_CHECK_NAME."""
        fake_parent = tmp_path / "project"
        fake_parent.mkdir()
        (fake_parent / "orchestrator").mkdir()  # for PYTHONPATH
        fake_agents = tmp_path / "agents"
        fake_agents.mkdir()
        fake_orch = tmp_path / ".octopoid"
        fake_orch.mkdir()

        with patch('orchestrator.scheduler.find_parent_project', return_value=fake_parent):
            with patch('orchestrator.scheduler.get_worktree_path', return_value=fake_parent):
                with patch('orchestrator.scheduler.get_agents_runtime_dir', return_value=fake_agents):
                    with patch('orchestrator.scheduler.get_port_env_vars', return_value={}):
                        with patch('orchestrator.scheduler.get_orchestrator_dir', return_value=fake_orch):
                            with patch('subprocess.Popen') as mock_popen:
                                mock_popen.return_value = MagicMock(pid=12345)

                                from orchestrator.scheduler import spawn_agent

                                config = {
                                    "role": "gatekeeper",
                                    "focus": "architecture",
                                    "review_task_id": "abc123",
                                    "review_check_name": "architecture",
                                }

                                pid = spawn_agent("gk-arch", 10, "gatekeeper", config)

                                # Verify env vars were set
                                call_kwargs = mock_popen.call_args
                                env = call_kwargs.kwargs.get("env", call_kwargs[1].get("env", {}))
                                assert env.get("REVIEW_TASK_ID") == "abc123"
                                assert env.get("REVIEW_CHECK_NAME") == "architecture"
                                assert env.get("AGENT_FOCUS") == "architecture"
