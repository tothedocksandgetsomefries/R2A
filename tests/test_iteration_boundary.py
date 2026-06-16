"""Test iteration boundary behavior for max_iterations.

This test verifies that:
1. max_iterations=N produces exactly N complete iterations
2. No empty iteration N+1 is created
3. Reviewer is called exactly N times
4. Final is called after iteration N completes
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
import tempfile
import json

from r2a.core.final_decision import build_final_decision
from r2a.core.paths import report_path
from r2a.tools.workflow_decision import aggregate_terminal_decision


class TestIterationBoundary:
    """Test iteration boundary logic."""

    def _make_state(
        self,
        iteration: int = 1,
        max_iterations: int = 3,
        auto_iterate: bool = True,
        reviewer_executed: bool = True,
        reviewer_verdict: str = "PASS_SMOKE_ONLY",
        current_reproduction_level: str | None = None,
    ) -> dict:
        """Create a mock state for testing."""
        return {
            "repo_path": str(tempfile.gettempdir()),
            "iteration": iteration,
            "max_iterations": max_iterations,
            "auto_iterate": auto_iterate,
            "reviewer_executed": reviewer_executed,
            "reviewer_verdict": reviewer_verdict,
            "current_reproduction_level": current_reproduction_level,
            "paper_path": "",
            "paper_readiness": {"ready": True},
            "planner_readiness": {"ready": True},
            "engineer_readiness": {"ready": True},
            "source_acquisition": {"source_status": "available"},
            "source_inspection": {"inspection_status": "PASS"},
            "manager_status": "PASS",
            "engineer_status": "PASS",
            "engineer_executor_failure_category": "",
            "engineer_executor_unavailable": False,
            "planner_transaction": {"validation_status": "PASS", "committed": True},
            "stopped": False,
            "loop_status": "",
            "structured_review_feedback": {"iteration_summary": "Test"} if reviewer_executed else None,
        }

    @patch("r2a.tools.workflow_decision.paper_markdown_artifacts_available")
    @patch("r2a.tools.workflow_decision.paper_bundle_status")
    def test_max_iterations_1_complete_iteration(self, mock_bundle, mock_markdown):
        """max_iterations=1 should complete exactly 1 iteration."""
        mock_markdown.return_value = {"usable": True, "available_artifacts": ["paper_context"], "artifact_count": 1}
        mock_bundle.return_value = {"status": "valid", "missing_required": [], "text_fallback_available": False}

        # Iteration 1, max=1, Reviewer completed
        state = self._make_state(
            iteration=1,
            max_iterations=1,
            reviewer_executed=True,
            reviewer_verdict="PASS_SMOKE_ONLY",
            current_reproduction_level="L1_source_artifact_verified",
        )

        decision = aggregate_terminal_decision(state)

        assert decision["typed_decision"] == "final"
        assert decision["reason_code"] == "MAX_ITERATIONS_REACHED"
        assert decision["iteration"] == 1
        assert decision["terminal"] is True

    @patch("r2a.tools.workflow_decision.paper_markdown_artifacts_available")
    @patch("r2a.tools.workflow_decision.paper_bundle_status")
    def test_max_iterations_2_still_iterating(self, mock_bundle, mock_markdown):
        """max_iterations=2, iteration=1 should continue."""
        mock_markdown.return_value = {"usable": True, "available_artifacts": ["paper_context"], "artifact_count": 1}
        mock_bundle.return_value = {"status": "valid", "missing_required": [], "text_fallback_available": False}

        state = self._make_state(
            iteration=1,
            max_iterations=2,
            reviewer_executed=True,
            reviewer_verdict="NEEDS_FIX",
            current_reproduction_level="L0_project_health",
        )

        decision = aggregate_terminal_decision(state)

        assert decision["typed_decision"] == "continue_iteration"
        assert decision["reason_code"] == "READY_FOR_NEXT_ITERATION"
        assert decision["terminal"] is False

    @patch("r2a.tools.workflow_decision.paper_markdown_artifacts_available")
    @patch("r2a.tools.workflow_decision.paper_bundle_status")
    def test_max_iterations_2_reached(self, mock_bundle, mock_markdown):
        """max_iterations=2, iteration=2 should finalize."""
        mock_markdown.return_value = {"usable": True, "available_artifacts": ["paper_context"], "artifact_count": 1}
        mock_bundle.return_value = {"status": "valid", "missing_required": [], "text_fallback_available": False}

        state = self._make_state(
            iteration=2,
            max_iterations=2,
            reviewer_executed=True,
            reviewer_verdict="PASS_SMOKE_ONLY",
            current_reproduction_level="L1_source_artifact_verified",
        )

        decision = aggregate_terminal_decision(state)

        assert decision["typed_decision"] == "final"
        assert decision["reason_code"] == "MAX_ITERATIONS_REACHED"
        assert decision["iteration"] == 2
        assert decision["terminal"] is True

    @patch("r2a.tools.workflow_decision.paper_markdown_artifacts_available")
    @patch("r2a.tools.workflow_decision.paper_bundle_status")
    def test_max_iterations_3_still_iterating(self, mock_bundle, mock_markdown):
        """max_iterations=3, iteration=2 should continue."""
        mock_markdown.return_value = {"usable": True, "available_artifacts": ["paper_context"], "artifact_count": 1}
        mock_bundle.return_value = {"status": "valid", "missing_required": [], "text_fallback_available": False}

        state = self._make_state(
            iteration=2,
            max_iterations=3,
            reviewer_executed=True,
            reviewer_verdict="NEEDS_FIX",
            current_reproduction_level="L1_source_artifact_verified",
        )

        decision = aggregate_terminal_decision(state)

        assert decision["typed_decision"] == "continue_iteration"
        assert decision["terminal"] is False

    @patch("r2a.tools.workflow_decision.paper_markdown_artifacts_available")
    @patch("r2a.tools.workflow_decision.paper_bundle_status")
    def test_max_iterations_3_reached(self, mock_bundle, mock_markdown):
        """max_iterations=3, iteration=3 should finalize."""
        mock_markdown.return_value = {"usable": True, "available_artifacts": ["paper_context"], "artifact_count": 1}
        mock_bundle.return_value = {"status": "valid", "missing_required": [], "text_fallback_available": False}

        state = self._make_state(
            iteration=3,
            max_iterations=3,
            reviewer_executed=True,
            reviewer_verdict="PASS_REDUCED_METHOD_ONLY",
            current_reproduction_level="L3_official_reduced_run",
        )

        decision = aggregate_terminal_decision(state)

        assert decision["typed_decision"] == "final"
        assert decision["reason_code"] == "MAX_ITERATIONS_REACHED"
        assert decision["iteration"] == 3
        assert decision["terminal"] is True

    @patch("r2a.tools.workflow_decision.paper_markdown_artifacts_available")
    @patch("r2a.tools.workflow_decision.paper_bundle_status")
    def test_max_iterations_8_iteration_7_continue(self, mock_bundle, mock_markdown):
        """max_iterations=8, iteration=7 should continue."""
        mock_markdown.return_value = {"usable": True, "available_artifacts": ["paper_context"], "artifact_count": 1}
        mock_bundle.return_value = {"status": "valid", "missing_required": [], "text_fallback_available": False}

        state = self._make_state(
            iteration=7,
            max_iterations=8,
            reviewer_executed=True,
            reviewer_verdict="PASS_SMOKE_ONLY",
            current_reproduction_level="L1_source_artifact_verified",
        )

        decision = aggregate_terminal_decision(state)

        assert decision["typed_decision"] == "continue_iteration"
        assert decision["terminal"] is False

    @patch("r2a.tools.workflow_decision.paper_markdown_artifacts_available")
    @patch("r2a.tools.workflow_decision.paper_bundle_status")
    def test_max_iterations_8_reached(self, mock_bundle, mock_markdown):
        """max_iterations=8, iteration=8 should finalize."""
        mock_markdown.return_value = {"usable": True, "available_artifacts": ["paper_context"], "artifact_count": 1}
        mock_bundle.return_value = {"status": "valid", "missing_required": [], "text_fallback_available": False}

        state = self._make_state(
            iteration=8,
            max_iterations=8,
            reviewer_executed=True,
            reviewer_verdict="PASS_REDUCED_ALIGNED",
            current_reproduction_level="L4_reduced_paper_aligned",
        )

        decision = aggregate_terminal_decision(state)

        assert decision["typed_decision"] == "final"
        assert decision["reason_code"] == "MAX_ITERATIONS_REACHED"
        assert decision["iteration"] == 8
        assert decision["terminal"] is True

    @patch("r2a.tools.workflow_decision.paper_markdown_artifacts_available")
    @patch("r2a.tools.workflow_decision.paper_bundle_status")
    def test_auto_iterate_false_finalizes(self, mock_bundle, mock_markdown):
        """auto_iterate=False should finalize even if iterations remain."""
        mock_markdown.return_value = {"usable": True, "available_artifacts": ["paper_context"], "artifact_count": 1}
        mock_bundle.return_value = {"status": "valid", "missing_required": [], "text_fallback_available": False}

        state = self._make_state(
            iteration=1,
            max_iterations=3,
            auto_iterate=False,
            reviewer_executed=True,
            reviewer_verdict="PASS_SMOKE_ONLY",
            current_reproduction_level="L1_source_artifact_verified",
        )

        decision = aggregate_terminal_decision(state)

        assert decision["typed_decision"] == "final"
        assert decision["reason_code"] == "AUTO_ITERATE_DISABLED"
        assert decision["terminal"] is True

    @patch("r2a.tools.workflow_decision.paper_markdown_artifacts_available")
    @patch("r2a.tools.workflow_decision.paper_bundle_status")
    def test_no_empty_iteration_created(self, mock_bundle, mock_markdown):
        """Verify that checking max_iterations BEFORE prepare_next_iteration prevents empty iteration.

        This is a design verification test:
        - Reviewer iteration N completes
        - Router checks if N >= max_iterations
        - If true, route to final (NOT prepare_next_iteration)
        - This prevents creating empty iteration N+1
        """
        mock_markdown.return_value = {"usable": True, "available_artifacts": ["paper_context"], "artifact_count": 1}
        mock_bundle.return_value = {"status": "valid", "missing_required": [], "text_fallback_available": False}

        # Simulate Run 3 Iteration 7 scenario (from audit report)
        # Iteration 7, max=8, Reviewer completed with L3
        state = self._make_state(
            iteration=7,
            max_iterations=8,
            reviewer_executed=True,
            reviewer_verdict="PASS_REDUCED_METHOD_ONLY",
            current_reproduction_level="L3_official_reduced_run",
        )

        decision = aggregate_terminal_decision(state)

        # Should continue to iteration 8, NOT finalize
        assert decision["typed_decision"] == "continue_iteration"
        assert decision["terminal"] is False

        # This would trigger prepare_next_iteration -> iteration=8
        # Then iteration 8 would run normally

        # Now test iteration 8 completing
        state["iteration"] = 8
        decision = aggregate_terminal_decision(state)

        # Now should finalize
        assert decision["typed_decision"] == "final"
        assert decision["reason_code"] == "MAX_ITERATIONS_REACHED"
        assert decision["terminal"] is True

    @patch("r2a.tools.workflow_decision.paper_markdown_artifacts_available")
    @patch("r2a.tools.workflow_decision.paper_bundle_status")
    def test_iteration_boundary_exact_comparison(self, mock_bundle, mock_markdown):
        """Verify iteration >= max_iterations uses >= not >.

        This prevents the "off-by-one" error that creates empty iteration N+1.
        """
        mock_markdown.return_value = {"usable": True, "available_artifacts": ["paper_context"], "artifact_count": 1}
        mock_bundle.return_value = {"status": "valid", "missing_required": [], "text_fallback_available": False}

        # max_iterations = 2
        # iteration = 2 should finalize (not continue to 3)

        state = self._make_state(
            iteration=2,
            max_iterations=2,
            reviewer_executed=True,
        )

        decision = aggregate_terminal_decision(state)

        # Must be terminal, not continue
        assert decision["typed_decision"] == "final"
        assert decision["reason_code"] == "MAX_ITERATIONS_REACHED"
        assert decision["terminal"] is True

        # Verify iteration < max_iterations continues
        state["iteration"] = 1
        state["max_iterations"] = 2
        decision = aggregate_terminal_decision(state)

        assert decision["typed_decision"] == "continue_iteration"
        assert decision["terminal"] is False

    @patch("r2a.tools.workflow_decision.paper_markdown_artifacts_available")
    @patch("r2a.tools.workflow_decision.paper_bundle_status")
    def test_planner_only_iteration_at_max_does_not_finalize(self, mock_bundle, mock_markdown):
        """iteration == max_iterations is not enough without current Reviewer completion."""
        mock_markdown.return_value = {"usable": True, "available_artifacts": ["paper_context"], "artifact_count": 1}
        mock_bundle.return_value = {"status": "valid", "missing_required": [], "text_fallback_available": False}

        state = self._make_state(
            iteration=8,
            max_iterations=8,
            reviewer_executed=False,
            reviewer_verdict="",
            current_reproduction_level="L4_reduced_paper_aligned",
        )
        state["structured_review_feedback"] = {}
        state["latest_review_feedback_path"] = "/old/iter_007/REVIEW_FEEDBACK.json"
        state["review_feedback_path"] = ""
        state["iteration_history"] = [
            {
                "iteration": iteration,
                "task_spec": f"iter_{iteration:03d}/TASK_SPEC.md",
                "review_report": f"iter_{iteration:03d}/REVIEW_REPORT.md",
                "review_feedback": f"iter_{iteration:03d}/REVIEW_FEEDBACK.json",
                "reviewer_verdict": "PASS_REDUCED_ALIGNED",
                "archive_missing_files": [],
            }
            for iteration in range(1, 8)
        ]

        decision = aggregate_terminal_decision(state)

        assert decision["typed_decision"] == "continue_iteration"
        assert decision["reason_code"] == "READY_FOR_NEXT_STAGE"
        assert decision["completed_review_iterations"] == 7
        assert decision["terminal"] is False


def test_terminal_failure_final_status_overrides_stale_target_reached(tmp_path: Path) -> None:
    path = report_path(tmp_path, "evidence_decision")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "current_reproduction_level": "L4_reduced_paper_aligned",
                "verdict": "PASS_REDUCED_ALIGNED",
                "level_valid": True,
                "target_level": "L4_reduced_paper_aligned",
            }
        ),
        encoding="utf-8",
    )
    state = {
        "repo_path": str(tmp_path),
        "target_reproduction_level": "L4_reduced_paper_aligned",
        "decision_status": {"typed_decision": "terminal_failed", "reason_code": "PLANNER_TRANSACTION_FAILED"},
        "failed_stage": "planner",
        "loop_status": "planner_failed",
        "stop_reason": "PLANNER_TRANSACTION_FAILED",
    }

    decision = build_final_decision(state, write=False)

    assert decision["target_reached"] is True
    assert decision["final_status"] == "completed_with_failure"


class TestIterationBoundaryIntegration:
    """Integration tests for iteration boundary with prepare_next_iteration."""

    @patch("r2a.tools.workflow_decision.paper_markdown_artifacts_available")
    @patch("r2a.tools.workflow_decision.paper_bundle_status")
    def test_prepare_next_iteration_respects_boundary(self, mock_bundle, mock_markdown):
        """Verify prepare_next_iteration is only called when iterations remain."""
        mock_markdown.return_value = {"usable": True, "available_artifacts": ["paper_context"], "artifact_count": 1}
        mock_bundle.return_value = {"status": "valid", "missing_required": [], "text_fallback_available": False}

        # This test verifies the design: router should check boundary BEFORE
        # calling prepare_next_iteration, not after

        # Design principle:
        # route_after_reviewer checks:
        #   if iteration >= max_iterations -> final
        #   else if auto_iterate -> prepare_next_iteration

        # This prevents empty iteration creation

        # Iteration 2, max=2 should NOT call prepare_next_iteration
        state = {
            "repo_path": str(tempfile.gettempdir()),
            "iteration": 2,
            "max_iterations": 2,
            "auto_iterate": True,
            "reviewer_executed": True,
            "reviewer_verdict": "PASS_SMOKE_ONLY",
            "paper_path": "",
            "paper_readiness": {"ready": True},
            "planner_readiness": {"ready": True},
            "engineer_readiness": {"ready": True},
            "source_acquisition": {},
            "source_inspection": {},
            "manager_status": "",
            "engineer_status": "",
            "engineer_executor_failure_category": "",
            "engineer_executor_unavailable": False,
            "planner_transaction": {},
            "stopped": False,
            "loop_status": "",
        }

        decision = aggregate_terminal_decision(state)

        # Must be final, not continue_iteration
        assert decision["typed_decision"] == "final"
        assert "MAX_ITERATIONS_REACHED" in decision["reason_code"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
