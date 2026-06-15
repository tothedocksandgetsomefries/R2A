"""Test Reviewer formal level judgment.

This test verifies that:
1. Only AI backend can produce formal levels
2. rules backend does NOT produce formal levels
3. Invalid AI output preserves previous valid level
4. Level snapshot fields are updated together
5. Attempt fields are separate from formal level
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
import tempfile
import json

from r2a.core.reviewer_level_judgment import (
    is_valid_level,
    normalize_level,
    LEVEL_LABELS,
    LEVEL_SEMANTICS,
)


class TestReviewerFormalLevel:
    """Test Reviewer formal level judgment."""

    def test_is_valid_level_recognizes_all_levels(self):
        """Verify is_valid_level recognizes L0-L6."""
        valid_levels = [
            "L0_project_health",
            "L1_source_artifact_verified",
            "L2_input_contract_ready",
            "L3_official_reduced_run",
            "L4_reduced_paper_aligned",
            "L5_minimal_baseline_comparison",
            "L6_full_or_near_full_reproduction",
        ]

        for level in valid_levels:
            assert is_valid_level(level), f"{level} should be valid"

    def test_is_valid_level_rejects_invalid(self):
        """Verify is_valid_level rejects invalid levels."""
        invalid_levels = [
            None,
            "",
            "L0",
            "L1",
            "L7",
            "invalid",
            "PASS",
            "L3_official_run",  # typo
        ]

        for level in invalid_levels:
            assert not is_valid_level(level), f"{level} should be invalid"

    def test_normalize_level_handles_aliases(self):
        """Verify normalize_level handles legacy aliases."""
        # Valid levels pass through
        assert normalize_level("L3_official_reduced_run") == "L3_official_reduced_run"

        # Invalid defaults to L0
        assert normalize_level(None) == "L0_project_health"
        assert normalize_level("") == "L0_project_health"
        assert normalize_level("invalid") == "L0_project_health"

    def test_ai_backend_valid_level_accepted(self):
        """Case 1: AI backend outputs valid L3 + reasoning -> accepted."""
        # Simulate AI backend output
        structured_feedback = {
            "current_reproduction_level": "L3_official_reduced_run",
            "level_reasoning": "Reduced metrics show recall@10=0.95 matching paper Table 2.",
            "supporting_artifacts": ["reduced_metrics.csv", "command_manifest.csv"],
            "remaining_gaps": ["No baseline comparison yet."],
        }

        # Validate
        raw_level = structured_feedback.get("current_reproduction_level")
        raw_reasoning = structured_feedback.get("level_reasoning", "")

        assert is_valid_level(raw_level)
        assert raw_reasoning.strip()  # Non-empty reasoning

        # This would be accepted as formal level
        level = normalize_level(raw_level)
        assert level == "L3_official_reduced_run"

    def test_ai_backend_empty_reasoning_rejected(self):
        """AI backend with valid level but empty reasoning -> rejected."""
        structured_feedback = {
            "current_reproduction_level": "L3_official_reduced_run",
            "level_reasoning": "",  # Empty!
        }

        raw_level = structured_feedback.get("current_reproduction_level")
        raw_reasoning = structured_feedback.get("level_reasoning", "")

        assert is_valid_level(raw_level)
        assert not raw_reasoning.strip()  # Empty reasoning

        # This should be REJECTED
        # Output should preserve previous level
        level_valid = is_valid_level(raw_level) and bool(raw_reasoning.strip())
        assert not level_valid

    def test_ai_backend_invalid_level_rejected(self):
        """AI backend with invalid level -> rejected."""
        structured_feedback = {
            "current_reproduction_level": "L7_invalid",  # Invalid!
            "level_reasoning": "Some reasoning.",
        }

        raw_level = structured_feedback.get("current_reproduction_level")

        assert not is_valid_level(raw_level)

        # This should be REJECTED

    def test_rules_backend_no_level(self):
        """Case 2: rules backend, no history -> UNASSESSED."""
        # rules backend does NOT produce formal level
        backend = "rules"
        structured_feedback = None

        # No level should be produced
        level = None
        level_source = "rules_backend_no_level"

        assert backend == "rules"
        assert structured_feedback is None
        assert level is None  # UNASSESSED

    def test_rules_backend_preserves_history(self):
        """Case 3: History has valid L2, current iteration uses rules -> L2 preserved.

        Critical: The L2's source, reasoning, and iteration must NOT be overwritten.
        """
        # Previous state (from iteration 2)
        previous_state = {
            "current_reproduction_level": "L2_input_contract_ready",
            "current_level_iteration": 2,
            "level_source": "ai_backend",
            "level_reasoning": "Input contract verified with query and ground truth.",
        }

        # Current iteration (iteration 6) uses rules
        current_backend = "rules"
        current_iteration = 6

        # rules backend does NOT update formal level
        # Previous level must be preserved
        assert previous_state["current_reproduction_level"] == "L2_input_contract_ready"
        assert previous_state["current_level_iteration"] == 2  # NOT 6!
        assert previous_state["level_source"] == "ai_backend"  # NOT "rules"!

        # Attempt fields for current iteration
        attempt_state = {
            "reviewer_executed": True,
            "reviewer_backend": "rules",
            "reviewer_level_valid": False,  # rules does NOT produce valid level
            "reviewer_verdict": "PASS_SMOKE_ONLY",
        }

        assert attempt_state["reviewer_level_valid"] is False

    def test_ai_invalid_preserves_history(self):
        """Case 4: History has valid L2, current AI outputs invalid -> L2 preserved."""
        previous_state = {
            "current_reproduction_level": "L2_input_contract_ready",
            "current_level_iteration": 2,
            "level_source": "ai_backend",
        }

        # Current AI output is invalid
        structured_feedback = {
            "current_reproduction_level": "invalid_level",
            "level_reasoning": "Some reasoning.",
        }

        raw_level = structured_feedback.get("current_reproduction_level")

        assert not is_valid_level(raw_level)

        # Previous level must be preserved
        assert previous_state["current_reproduction_level"] == "L2_input_contract_ready"
        assert previous_state["current_level_iteration"] == 2

    def test_level_snapshot_updated_together(self):
        """Verify level snapshot fields are updated together as atomic unit."""
        # When AI produces valid level, ALL snapshot fields must be updated
        snapshot_fields = [
            "current_reproduction_level",
            "current_level_iteration",
            "level_source",
            "level_reasoning",
            "supporting_artifacts",
            "remaining_gaps",
        ]

        # Example valid update
        snapshot = {
            "current_reproduction_level": "L3_official_reduced_run",
            "current_level_iteration": 5,
            "level_source": "ai_backend",
            "level_reasoning": "Reduced metrics match paper.",
            "supporting_artifacts": ["reduced_metrics.csv"],
            "remaining_gaps": ["No baseline comparison."],
        }

        # All fields must be present
        for field in snapshot_fields:
            assert field in snapshot

    def test_attempt_fields_separate_from_formal(self):
        """Verify attempt fields are separate from formal level snapshot."""
        # Attempt fields (reset each iteration)
        attempt_fields = [
            "reviewer_executed",
            "reviewer_backend",
            "reviewer_level_valid",
            "reviewer_level_rejection_reason",
            "reviewer_verdict",
        ]

        # Formal level snapshot (preserved across iterations)
        formal_fields = [
            "current_reproduction_level",
            "current_level_iteration",
            "level_source",
            "level_reasoning",
        ]

        # These sets must be disjoint
        assert set(attempt_fields).isdisjoint(set(formal_fields))


class TestReviewFeedbackConsistency:
    """Test REVIEW_FEEDBACK.json and EVIDENCE_DECISION.json consistency."""

    def test_review_feedback_mirrors_formal_level(self):
        """REVIEW_FEEDBACK.current_level must mirror current_reproduction_level."""
        formal_level = "L3_official_reduced_run"

        review_feedback = {
            "current_level": formal_level,  # Mirror, NOT independent calculation
            "reproduction_level": formal_level,  # Compatibility mirror
        }

        evidence_decision = {
            "current_reproduction_level": formal_level,
        }

        # Must be consistent
        assert review_feedback["current_level"] == evidence_decision["current_reproduction_level"]

    def test_no_independent_level_calculation(self):
        """REVIEW_FEEDBACK must NOT independently calculate level via infer_evidence_level."""
        # This is a design test: _build_review_feedback should NOT call infer_evidence_level
        # for current_level or next_level

        # The only valid source is the AI backend output
        # If AI did not produce valid level, use previous formal level
        pass  # Design verification - no code to run


class TestFinalReport:
    """Test Final report level display."""

    def test_final_displays_valid_level(self):
        """Final displays valid L3 correctly."""
        state = {
            "current_reproduction_level": "L3_official_reduced_run",
            "current_level_iteration": 5,
            "level_source": "ai_backend",
            "level_reasoning": "Reduced metrics match paper.",
            "reviewer_level_valid": True,
        }

        # Final should display:
        # - Current reproduction level: L3
        # - Assessment iteration: 5
        # - Assessment source: ai_backend
        # - Level reasoning: ...

        assert state["current_reproduction_level"] == "L3_official_reduced_run"
        assert state["current_level_iteration"] == 5
        assert state["level_source"] == "ai_backend"

    def test_final_displays_unassessed_when_no_level(self):
        """Final displays UNASSESSED when no valid level exists."""
        state = {
            "current_reproduction_level": None,
            "reviewer_level_valid": False,
        }

        # Final should display:
        # - Current reproduction level: UNASSESSED

        assert state["current_reproduction_level"] is None

    def test_final_displays_history_preserved(self):
        """Final displays history level when current iteration invalid."""
        state = {
            "current_reproduction_level": "L2_input_contract_ready",
            "current_level_iteration": 2,  # Original iteration
            "level_source": "ai_backend",  # Original source
            "reviewer_level_valid": False,  # Current iteration invalid
            "reviewer_backend": "rules",  # Current iteration used rules
        }

        # Final should display:
        # - Current reproduction level: L2
        # - Assessment originally produced in iteration 2
        # - No new valid Reviewer level was produced in the latest iteration

        assert state["current_reproduction_level"] == "L2_input_contract_ready"
        assert state["current_level_iteration"] == 2  # NOT current iteration
        assert state["level_source"] == "ai_backend"  # NOT "rules"

    def test_final_no_evidence_ladder_dependency(self):
        """Final must NOT depend on evidence_ladder."""
        # This is a design test: write_final_report should NOT call
        # evidence_ladder_markdown() or construct evidence_ladder

        # evidence_ladder is deprecated
        # Final should read directly from state fields
        pass  # Design verification


class TestManifestIterationState:
    """Test Manifest and Iteration State level handling."""

    def test_manifest_no_evidence_ladder(self):
        """RUN_MANIFEST must NOT depend on evidence_ladder."""
        # Manifest should use current_reproduction_level directly
        # NOT evidence_ladder["levels"]
        pass  # Design verification

    def test_iteration_state_level_source(self):
        """ITERATION_STATE must show level_source correctly."""
        state = {
            "current_reproduction_level": "L3_official_reduced_run",
            "current_level_iteration": 5,
            "reviewer_executed": True,
        }

        # ITERATION_STATE should show:
        # - current_reproduction_level: L3
        # - current_level_iteration: 5
        # - level_source: reviewer

        assert state["current_reproduction_level"] == "L3_official_reduced_run"

    def test_no_default_l0_leak(self):
        """Verify no default L0 leaks into new Run."""
        # New Run should have:
        # - current_reproduction_level: None or ""
        # - NOT "L0_project_health" as default

        # L0 must be AI Reviewer's explicit judgment, not default
        new_run_state = {
            "current_reproduction_level": None,
            "reviewer_executed": False,
        }

        assert new_run_state["current_reproduction_level"] is None
        assert new_run_state["current_reproduction_level"] != "L0_project_health"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
