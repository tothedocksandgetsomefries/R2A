"""Test reviewer_backend configuration preservation across iterations.

This test verifies that:
1. reviewer_backend is NOT reset to "rules" in prepare_next_iteration
2. User's backend choice is preserved across all iterations
3. All supported backends (openclaw, codex, claude, rules) work correctly
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
import tempfile

from r2a.tools.iteration import prepare_next_iteration


class TestReviewerBackendPreservation:
    """Test reviewer_backend configuration preservation."""

    def _make_state(
        self,
        iteration: int = 1,
        reviewer_backend: str = "openclaw",
        current_reproduction_level: str | None = None,
    ) -> dict:
        """Create a mock state for testing."""
        return {
            "repo_path": str(tempfile.gettempdir()),
            "iteration": iteration,
            "max_iterations": 8,
            "reviewer_backend": reviewer_backend,
            "current_reproduction_level": current_reproduction_level,
            "current_level_iteration": 0 if not current_reproduction_level else iteration,
            "level_source": "unassessed",
            "level_reasoning": "",
            "supporting_artifacts": [],
            "remaining_gaps": [],
            "reviewer_executed": True,
            "reviewer_level_valid": False,
            "reviewer_verdict": "PASS_SMOKE_ONLY",
            "auto_iterate": True,
            "metadata": {},
        }

    @patch("r2a.tools.iteration.ensure_iteration_dirs")
    @patch("r2a.tools.iteration.write_iteration_state")
    @patch("r2a.tools.iteration.report_path")
    @patch("r2a.tools.iteration.runs_dir")
    def test_openclaw_backend_preserved_across_iterations(
        self, mock_runs_dir, mock_report_path, mock_write_state, mock_ensure_dirs
    ):
        """openclaw backend should be preserved across iterations."""
        mock_ensure_dirs.return_value = Path(tempfile.mkdtemp())
        mock_report_path.return_value = Path(tempfile.mkdtemp()) / "test.json"
        mock_runs_dir.return_value = Path(tempfile.mkdtemp())

        state = self._make_state(iteration=1, reviewer_backend="openclaw")

        # First iteration
        result = prepare_next_iteration(state)
        assert result["reviewer_backend"] == "openclaw", \
            f"Expected openclaw, got {result['reviewer_backend']}"

        # Second iteration
        result = prepare_next_iteration(result)
        assert result["reviewer_backend"] == "openclaw", \
            f"Expected openclaw, got {result['reviewer_backend']}"

        # Third iteration
        result = prepare_next_iteration(result)
        assert result["reviewer_backend"] == "openclaw", \
            f"Expected openclaw, got {result['reviewer_backend']}"

    @patch("r2a.tools.iteration.ensure_iteration_dirs")
    @patch("r2a.tools.iteration.write_iteration_state")
    @patch("r2a.tools.iteration.report_path")
    @patch("r2a.tools.iteration.runs_dir")
    def test_codex_backend_preserved(
        self, mock_runs_dir, mock_report_path, mock_write_state, mock_ensure_dirs
    ):
        """codex backend should be preserved."""
        mock_ensure_dirs.return_value = Path(tempfile.mkdtemp())
        mock_report_path.return_value = Path(tempfile.mkdtemp()) / "test.json"
        mock_runs_dir.return_value = Path(tempfile.mkdtemp())

        state = self._make_state(iteration=1, reviewer_backend="codex")

        result = prepare_next_iteration(state)
        assert result["reviewer_backend"] == "codex"

        result = prepare_next_iteration(result)
        assert result["reviewer_backend"] == "codex"

    @patch("r2a.tools.iteration.ensure_iteration_dirs")
    @patch("r2a.tools.iteration.write_iteration_state")
    @patch("r2a.tools.iteration.report_path")
    @patch("r2a.tools.iteration.runs_dir")
    def test_claude_backend_preserved(
        self, mock_runs_dir, mock_report_path, mock_write_state, mock_ensure_dirs
    ):
        """claude backend should be preserved."""
        mock_ensure_dirs.return_value = Path(tempfile.mkdtemp())
        mock_report_path.return_value = Path(tempfile.mkdtemp()) / "test.json"
        mock_runs_dir.return_value = Path(tempfile.mkdtemp())

        state = self._make_state(iteration=1, reviewer_backend="claude")

        result = prepare_next_iteration(state)
        assert result["reviewer_backend"] == "claude"

        result = prepare_next_iteration(result)
        assert result["reviewer_backend"] == "claude"

    @patch("r2a.tools.iteration.ensure_iteration_dirs")
    @patch("r2a.tools.iteration.write_iteration_state")
    @patch("r2a.tools.iteration.report_path")
    @patch("r2a.tools.iteration.runs_dir")
    def test_rules_backend_preserved(
        self, mock_runs_dir, mock_report_path, mock_write_state, mock_ensure_dirs
    ):
        """rules backend should be preserved (not reset to rules again)."""
        mock_ensure_dirs.return_value = Path(tempfile.mkdtemp())
        mock_report_path.return_value = Path(tempfile.mkdtemp()) / "test.json"
        mock_runs_dir.return_value = Path(tempfile.mkdtemp())

        state = self._make_state(iteration=1, reviewer_backend="rules")

        result = prepare_next_iteration(state)
        assert result["reviewer_backend"] == "rules"

        result = prepare_next_iteration(result)
        assert result["reviewer_backend"] == "rules"

    @patch("r2a.tools.iteration.ensure_iteration_dirs")
    @patch("r2a.tools.iteration.write_iteration_state")
    @patch("r2a.tools.iteration.report_path")
    @patch("r2a.tools.iteration.runs_dir")
    def test_backend_not_reset_after_ai_level(
        self, mock_runs_dir, mock_report_path, mock_write_state, mock_ensure_dirs
    ):
        """Backend should not be reset even after AI produced valid level."""
        mock_ensure_dirs.return_value = Path(tempfile.mkdtemp())
        mock_report_path.return_value = Path(tempfile.mkdtemp()) / "test.json"
        mock_runs_dir.return_value = Path(tempfile.mkdtemp())

        state = self._make_state(
            iteration=1,
            reviewer_backend="openclaw",
            current_reproduction_level="L3_official_reduced_run",
        )

        result = prepare_next_iteration(state)
        assert result["reviewer_backend"] == "openclaw"
        assert result["current_reproduction_level"] == "L3_official_reduced_run"


class TestAttemptFieldsReset:
    """Test that attempt fields are reset correctly."""

    @patch("r2a.tools.iteration.ensure_iteration_dirs")
    @patch("r2a.tools.iteration.write_iteration_state")
    @patch("r2a.tools.iteration.report_path")
    @patch("r2a.tools.iteration.runs_dir")
    def test_attempt_fields_reset(
        self, mock_runs_dir, mock_report_path, mock_write_state, mock_ensure_dirs
    ):
        """Verify all attempt fields are reset in prepare_next_iteration."""
        mock_ensure_dirs.return_value = Path(tempfile.mkdtemp())
        mock_report_path.return_value = Path(tempfile.mkdtemp()) / "test.json"
        mock_runs_dir.return_value = Path(tempfile.mkdtemp())

        state = {
            "repo_path": str(tempfile.gettempdir()),
            "iteration": 1,
            "reviewer_backend": "openclaw",
            "reviewer_executed": True,
            "reviewer_verdict": "PASS_L3",
            "reviewer_level_valid": True,
            "reviewer_level_rejection_reason": "",
            "structured_review_feedback": {"test": "data"},
            "current_reproduction_level": "L3_official_reduced_run",
            "current_level_iteration": 1,
            "level_source": "ai_backend",
            "level_reasoning": "Test reasoning",
            "manager_status": "PASS",
            "manager_executed": True,
            "engineer_status": "PASS",
            "engineer_passed": True,
            "errors": ["error1"],
            "warnings": ["warning1"],
            "metadata": {},
        }

        result = prepare_next_iteration(state)

        # Attempt fields should be reset
        assert result["reviewer_executed"] == False
        assert result["reviewer_verdict"] == ""
        assert result["reviewer_level_valid"] == False
        assert result["reviewer_level_rejection_reason"] == ""
        assert result["structured_review_feedback"] == {}
        assert result["manager_status"] == ""
        assert result["manager_executed"] == False
        assert result["engineer_status"] == ""
        assert result["engineer_passed"] == False
        assert result["errors"] == []
        assert result["warnings"] == []

        # Formal snapshot fields should be preserved
        assert result["current_reproduction_level"] == "L3_official_reduced_run"
        assert result["current_level_iteration"] == 1
        assert result["level_source"] == "ai_backend"
        assert result["level_reasoning"] == "Test reasoning"

        # Backend should be preserved
        assert result["reviewer_backend"] == "openclaw"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
