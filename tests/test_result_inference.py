"""Unit tests for infer_result_from_stdout() and its private helpers.

Tests cover:
- Missing / empty stdout.log returns appropriate failure/unknown dict
- _infer_implementer routes correctly based on haiku response
- _infer_gatekeeper routes correctly based on haiku response
- _infer_fixer routes correctly based on haiku response
- infer_result_from_stdout dispatches by agent_role
- infer_result_from_stdout reads only the last 2000 chars (tail)
- Haiku exceptions are caught and return unknown/failure dicts
"""

from pathlib import Path
from unittest.mock import patch

import pytest


# =============================================================================
# infer_result_from_stdout — missing / empty file edge cases
# =============================================================================


class TestInferResultFromStdoutFileEdgeCases:
    """infer_result_from_stdout handles missing / empty files gracefully."""

    def test_missing_file_implementer_returns_unknown(self, tmp_path):
        """Missing stdout.log returns {"outcome": "unknown"} for implementer."""
        from octopoid.result_handler import infer_result_from_stdout

        result = infer_result_from_stdout(tmp_path / "stdout.log", "implement")
        assert result["outcome"] == "unknown"
        assert "stdout.log" in result.get("reason", "").lower() or "stdout" in str(result)

    def test_missing_file_gatekeeper_returns_failure(self, tmp_path):
        """Missing stdout.log returns {"status": "failure"} for gatekeeper."""
        from octopoid.result_handler import infer_result_from_stdout

        result = infer_result_from_stdout(tmp_path / "stdout.log", "gatekeeper")
        assert result["status"] == "failure"

    def test_missing_file_fixer_returns_unknown(self, tmp_path):
        """Missing stdout.log returns {"outcome": "unknown"} for fixer."""
        from octopoid.result_handler import infer_result_from_stdout

        result = infer_result_from_stdout(tmp_path / "stdout.log", "fixer")
        assert result["outcome"] == "unknown"

    def test_empty_file_implementer_returns_unknown(self, tmp_path):
        """Empty stdout.log returns {"outcome": "unknown"} for implementer."""
        from octopoid.result_handler import infer_result_from_stdout

        (tmp_path / "stdout.log").write_text("   ")
        result = infer_result_from_stdout(tmp_path / "stdout.log", "implement")
        assert result["outcome"] == "unknown"

    def test_empty_file_gatekeeper_returns_failure(self, tmp_path):
        """Empty stdout.log returns {"status": "failure"} for gatekeeper."""
        from octopoid.result_handler import infer_result_from_stdout

        (tmp_path / "stdout.log").write_text("")
        result = infer_result_from_stdout(tmp_path / "stdout.log", "gatekeeper")
        assert result["status"] == "failure"

    def test_missing_file_sanity_check_gatekeeper_returns_failure(self, tmp_path):
        """sanity-check-gatekeeper role also returns {"status": "failure"} on missing file."""
        from octopoid.result_handler import infer_result_from_stdout

        result = infer_result_from_stdout(tmp_path / "stdout.log", "sanity-check-gatekeeper")
        assert result["status"] == "failure"


# =============================================================================
# _infer_implementer — haiku classification
# =============================================================================


class TestInferImplementer:
    """_infer_implementer routes based on haiku's one-word response."""

    def test_done_response_returns_done(self):
        """Haiku returning 'done' maps to {"outcome": "done"}."""
        from octopoid.result_handler import _infer_implementer

        with patch("octopoid.result_handler._call_haiku", return_value="done"):
            result = _infer_implementer("Agent finished successfully.")

        assert result == {"outcome": "done"}

    def test_failed_response_returns_failed(self):
        """Haiku returning 'failed' maps to {"outcome": "failed", ...}."""
        from octopoid.result_handler import _infer_implementer

        with patch("octopoid.result_handler._call_haiku", return_value="failed"):
            result = _infer_implementer("Could not complete the task.")

        assert result["outcome"] == "failed"
        assert "reason" in result

    def test_unexpected_response_returns_unknown(self):
        """Unexpected haiku response maps to {"outcome": "unknown"}."""
        from octopoid.result_handler import _infer_implementer

        with patch("octopoid.result_handler._call_haiku", return_value="purple"):
            result = _infer_implementer("Some ambiguous output.")

        assert result["outcome"] == "unknown"
        assert "purple" in result.get("reason", "")

    def test_haiku_exception_returns_unknown(self):
        """When haiku raises, _infer_implementer returns {"outcome": "unknown"}."""
        from octopoid.result_handler import _infer_implementer

        with patch("octopoid.result_handler._call_haiku", side_effect=RuntimeError("API error")):
            result = _infer_implementer("Some stdout text.")

        assert result["outcome"] == "unknown"
        assert "reason" in result


# =============================================================================
# _infer_gatekeeper — haiku classification
# =============================================================================


class TestInferGatekeeper:
    """_infer_gatekeeper routes based on haiku's one-word response."""

    def test_approve_response_returns_approve(self):
        """Haiku returning 'approve' maps to decision=approve."""
        from octopoid.result_handler import _infer_gatekeeper

        tail = "DECISION: APPROVED"
        with patch("octopoid.result_handler._call_haiku", return_value="approve"):
            result = _infer_gatekeeper(tail)

        assert result["status"] == "success"
        assert result["decision"] == "approve"
        assert result["comment"] == tail

    def test_reject_response_returns_reject(self):
        """Haiku returning 'reject' maps to decision=reject."""
        from octopoid.result_handler import _infer_gatekeeper

        tail = "DECISION: REJECTED\n\nNeeds more tests."
        with patch("octopoid.result_handler._call_haiku", return_value="reject"):
            result = _infer_gatekeeper(tail)

        assert result["status"] == "success"
        assert result["decision"] == "reject"
        assert result["comment"] == tail

    def test_unexpected_response_returns_failure(self):
        """Unexpected haiku response maps to status=failure."""
        from octopoid.result_handler import _infer_gatekeeper

        with patch("octopoid.result_handler._call_haiku", return_value="maybe"):
            result = _infer_gatekeeper("Ambiguous review output.")

        assert result["status"] == "failure"
        assert "message" in result

    def test_haiku_exception_returns_failure(self):
        """When haiku raises, _infer_gatekeeper returns status=failure."""
        from octopoid.result_handler import _infer_gatekeeper

        with patch("octopoid.result_handler._call_haiku", side_effect=RuntimeError("timeout")):
            result = _infer_gatekeeper("Some review text.")

        assert result["status"] == "failure"
        assert "message" in result


# =============================================================================
# _infer_fixer — haiku classification
# =============================================================================


class TestInferFixer:
    """_infer_fixer routes based on haiku's one-word response."""

    def test_fixed_response_returns_fixed(self):
        """Haiku returning 'fixed' maps to outcome=fixed."""
        from octopoid.result_handler import _infer_fixer

        tail = "Rebased successfully and pushed."
        with patch("octopoid.result_handler._call_haiku", return_value="fixed"):
            result = _infer_fixer(tail)

        assert result["outcome"] == "fixed"
        assert "diagnosis" in result
        assert "fix_applied" in result
        assert tail[:500] == result["fix_applied"]

    def test_failed_response_returns_failed(self):
        """Haiku returning 'failed' maps to outcome=failed."""
        from octopoid.result_handler import _infer_fixer

        with patch("octopoid.result_handler._call_haiku", return_value="failed"):
            result = _infer_fixer("Could not rebase — conflicts unresolvable.")

        assert result["outcome"] == "failed"
        assert "diagnosis" in result

    def test_unexpected_response_returns_unknown(self):
        """Unexpected haiku response maps to outcome=unknown."""
        from octopoid.result_handler import _infer_fixer

        with patch("octopoid.result_handler._call_haiku", return_value="unclear"):
            result = _infer_fixer("Some fixer output.")

        assert result["outcome"] == "unknown"
        assert "unclear" in result.get("reason", "")

    def test_haiku_exception_returns_unknown(self):
        """When haiku raises, _infer_fixer returns outcome=unknown."""
        from octopoid.result_handler import _infer_fixer

        with patch("octopoid.result_handler._call_haiku", side_effect=ConnectionError("network")):
            result = _infer_fixer("Some fixer output.")

        assert result["outcome"] == "unknown"


# =============================================================================
# infer_result_from_stdout — role dispatch and tail truncation
# =============================================================================


class TestInferResultFromStdoutDispatch:
    """infer_result_from_stdout dispatches to the right helper by agent_role."""

    def test_gatekeeper_role_uses_gatekeeper_inference(self, tmp_path):
        """agent_role='gatekeeper' calls _infer_gatekeeper."""
        from octopoid.result_handler import infer_result_from_stdout

        (tmp_path / "stdout.log").write_text("**DECISION: APPROVED**")
        expected = {"status": "success", "decision": "approve", "comment": "**DECISION: APPROVED**"}

        with patch("octopoid.result_handler._infer_gatekeeper", return_value=expected) as mock_gk:
            result = infer_result_from_stdout(tmp_path / "stdout.log", "gatekeeper")

        mock_gk.assert_called_once()
        assert result == expected

    def test_sanity_check_gatekeeper_uses_gatekeeper_inference(self, tmp_path):
        """agent_role='sanity-check-gatekeeper' also calls _infer_gatekeeper."""
        from octopoid.result_handler import infer_result_from_stdout

        (tmp_path / "stdout.log").write_text("**DECISION: REJECTED**")
        expected = {"status": "success", "decision": "reject", "comment": "**DECISION: REJECTED**"}

        with patch("octopoid.result_handler._infer_gatekeeper", return_value=expected) as mock_gk:
            result = infer_result_from_stdout(tmp_path / "stdout.log", "sanity-check-gatekeeper")

        mock_gk.assert_called_once()
        assert result == expected

    def test_fixer_role_uses_fixer_inference(self, tmp_path):
        """agent_role='fixer' calls _infer_fixer."""
        from octopoid.result_handler import infer_result_from_stdout

        (tmp_path / "stdout.log").write_text("Fixed the rebase conflict.")
        expected = {"outcome": "fixed", "diagnosis": "test", "fix_applied": ""}

        with patch("octopoid.result_handler._infer_fixer", return_value=expected) as mock_fx:
            result = infer_result_from_stdout(tmp_path / "stdout.log", "fixer")

        mock_fx.assert_called_once()
        assert result == expected

    def test_implement_role_uses_implementer_inference(self, tmp_path):
        """agent_role='implement' calls _infer_implementer."""
        from octopoid.result_handler import infer_result_from_stdout

        (tmp_path / "stdout.log").write_text("All done!")
        expected = {"outcome": "done"}

        with patch("octopoid.result_handler._infer_implementer", return_value=expected) as mock_impl:
            result = infer_result_from_stdout(tmp_path / "stdout.log", "implement")

        mock_impl.assert_called_once()
        assert result == expected

    def test_unknown_role_falls_back_to_implementer(self, tmp_path):
        """An unrecognised role falls through to _infer_implementer."""
        from octopoid.result_handler import infer_result_from_stdout

        (tmp_path / "stdout.log").write_text("Some output.")
        expected = {"outcome": "done"}

        with patch("octopoid.result_handler._infer_implementer", return_value=expected) as mock_impl:
            result = infer_result_from_stdout(tmp_path / "stdout.log", "mystery-role")

        mock_impl.assert_called_once()

    def test_only_last_2000_chars_passed_to_helper(self, tmp_path):
        """Helper receives only the last 2000 characters of stdout."""
        from octopoid.result_handler import infer_result_from_stdout

        long_text = "A" * 5000 + "TAIL_MARKER"
        (tmp_path / "stdout.log").write_text(long_text)

        captured: list[str] = []

        def fake_infer_implementer(tail: str) -> dict:
            captured.append(tail)
            return {"outcome": "done"}

        with patch("octopoid.result_handler._infer_implementer", side_effect=fake_infer_implementer):
            infer_result_from_stdout(tmp_path / "stdout.log", "implement")

        assert len(captured) == 1
        tail = captured[0]
        assert len(tail) == 2000
        assert "TAIL_MARKER" in tail
        # First part of long_text should be truncated
        assert tail.startswith("A")

    def test_short_stdout_passes_entire_content(self, tmp_path):
        """When stdout < 2000 chars, the full content is passed to the helper."""
        from octopoid.result_handler import infer_result_from_stdout

        short_text = "Short output"
        (tmp_path / "stdout.log").write_text(short_text)

        captured: list[str] = []

        def fake_infer_implementer(tail: str) -> dict:
            captured.append(tail)
            return {"outcome": "done"}

        with patch("octopoid.result_handler._infer_implementer", side_effect=fake_infer_implementer):
            infer_result_from_stdout(tmp_path / "stdout.log", "implement")

        assert captured[0] == short_text


# =============================================================================
# JSON stdout parsing (--output-format json)
# =============================================================================


class TestJsonStdoutParsing:
    """infer_result_from_stdout handles JSON-format stdout from --output-format json."""

    def _json_stdout(self, subtype: str, result_text: str = "Agent finished.") -> str:
        import json
        return json.dumps({
            "type": "result",
            "subtype": subtype,
            "is_error": subtype != "success",
            "result": result_text,
            "session_id": "sess_test",
            "total_cost_usd": 0.01,
        })

    def test_json_success_subtype_uses_haiku_on_result_text(self, tmp_path):
        """JSON stdout with subtype='success' extracts result text and passes to haiku."""
        from octopoid.result_handler import infer_result_from_stdout

        result_text = "Task complete — all tests pass."
        (tmp_path / "stdout.log").write_text(self._json_stdout("success", result_text))

        captured: list[str] = []

        def fake_infer_implementer(tail: str) -> dict:
            captured.append(tail)
            return {"outcome": "done"}

        with patch("octopoid.result_handler._infer_implementer", side_effect=fake_infer_implementer):
            result = infer_result_from_stdout(tmp_path / "stdout.log", "implement")

        assert result["outcome"] == "done"
        assert len(captured) == 1
        assert captured[0] == result_text

    def test_json_max_turns_exceeded_returns_max_turns_outcome(self, tmp_path):
        """JSON stdout with subtype='error_max_turns_exceeded' returns max_turns_exceeded outcome."""
        from octopoid.result_handler import infer_result_from_stdout

        (tmp_path / "stdout.log").write_text(
            self._json_stdout("error_max_turns_exceeded", "Partial work done.")
        )

        result = infer_result_from_stdout(tmp_path / "stdout.log", "implement")

        assert result["outcome"] == "max_turns_exceeded"
        assert result["outcome"] != "unknown"

    def test_json_max_turns_gatekeeper_returns_failure(self, tmp_path):
        """JSON max_turns_exceeded for gatekeeper role returns status=failure."""
        from octopoid.result_handler import infer_result_from_stdout

        (tmp_path / "stdout.log").write_text(
            self._json_stdout("error_max_turns_exceeded")
        )

        result = infer_result_from_stdout(tmp_path / "stdout.log", "gatekeeper")

        assert result["status"] == "failure"
        assert "max turns" in result.get("message", "").lower()

    def test_plain_text_stdout_still_works(self, tmp_path):
        """Plain-text stdout (pre-json agents) still classifies correctly (backwards compat)."""
        from octopoid.result_handler import infer_result_from_stdout

        plain_text = "Implemented the feature. Fixed: the bug is gone."
        (tmp_path / "stdout.log").write_text(plain_text)

        captured: list[str] = []

        def fake_infer_implementer(tail: str) -> dict:
            captured.append(tail)
            return {"outcome": "done"}

        with patch("octopoid.result_handler._infer_implementer", side_effect=fake_infer_implementer):
            result = infer_result_from_stdout(tmp_path / "stdout.log", "implement")

        assert result["outcome"] == "done"
        assert len(captured) == 1
        assert captured[0] == plain_text

    def test_json_success_with_long_result_truncates_to_2000(self, tmp_path):
        """Result text longer than 2000 chars is truncated to the last 2000 chars."""
        from octopoid.result_handler import infer_result_from_stdout

        result_text = "A" * 5000 + "TAIL_MARKER"
        (tmp_path / "stdout.log").write_text(self._json_stdout("success", result_text))

        captured: list[str] = []

        def fake_infer_implementer(tail: str) -> dict:
            captured.append(tail)
            return {"outcome": "done"}

        with patch("octopoid.result_handler._infer_implementer", side_effect=fake_infer_implementer):
            infer_result_from_stdout(tmp_path / "stdout.log", "implement")

        assert len(captured) == 1
        assert len(captured[0]) == 2000
        assert "TAIL_MARKER" in captured[0]


# =============================================================================
# _parse_json_stdout — unit tests
# =============================================================================


class TestParseJsonStdout:
    """_parse_json_stdout correctly identifies and parses Claude JSON output."""

    def test_valid_json_result_object_parsed(self):
        """Valid Claude JSON result object returns (parsed, result_text)."""
        import json
        from octopoid.result_handler import _parse_json_stdout

        payload = json.dumps({
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "All done!",
        })

        parsed, text = _parse_json_stdout(payload)

        assert parsed is not None
        assert parsed["subtype"] == "success"
        assert text == "All done!"

    def test_plain_text_returns_none(self):
        """Plain-text stdout returns (None, original_text)."""
        from octopoid.result_handler import _parse_json_stdout

        plain = "Finished the task."
        parsed, text = _parse_json_stdout(plain)

        assert parsed is None
        assert text == plain

    def test_invalid_json_returns_none(self):
        """Malformed JSON returns (None, original_text)."""
        from octopoid.result_handler import _parse_json_stdout

        bad = '{"type": "result", broken'
        parsed, text = _parse_json_stdout(bad)

        assert parsed is None
        assert text == bad

    def test_json_without_type_result_returns_none(self):
        """JSON that is not a Claude result object returns (None, original_text)."""
        import json
        from octopoid.result_handler import _parse_json_stdout

        other_json = json.dumps({"foo": "bar"})
        parsed, text = _parse_json_stdout(other_json)

        assert parsed is None
        assert text == other_json

    def test_empty_result_field_returns_empty_string(self):
        """JSON with missing result field yields empty string for text."""
        import json
        from octopoid.result_handler import _parse_json_stdout

        payload = json.dumps({"type": "result", "subtype": "error_max_turns_exceeded"})
        parsed, text = _parse_json_stdout(payload)

        assert parsed is not None
        assert text == ""
