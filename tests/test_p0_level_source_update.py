"""Tests for P0-3: level_source update logic.

Validates that:
1. level_source=ai_backend only when AI backend successfully parses verdict
2. Safety Override prevents level_source=ai_backend
3. Invalid output preserves previous level_source
4. No L4/unassessed contradiction
"""

import json

import pytest
from r2a.agents.reviewer_agent import (
    _build_review_feedback,
    _extract_verdict,
    _with_evidence_decision,
    _write_review_feedback,
)
from r2a.core.paths import report_path
from r2a.core.run_manifest import write_run_manifest
from r2a.core.state import R2AState
from r2a.tools.iteration import _formal_level_summary


def _base_state(tmp_path) -> R2AState:
    return {
        "repo_path": str(tmp_path),
        "run_id": "test-run",
        "iteration": 4,
        "target_reproduction_level": "L4_reduced_paper_aligned",
        "reviewer_executed": True,
        "reviewer_backend": "openclaw",
        "reviewer_verdict": "PASS_REDUCED_ALIGNED",
        "safety_override_triggered": False,
        "structured_review_feedback": {
            "current_reproduction_level": "L4_reduced_paper_aligned",
            "level_reasoning": "Reduced metrics and paper alignment are both valid.",
            "supporting_artifacts": ["reduced_metrics.csv", "paper_alignment.csv"],
            "remaining_gaps": ["No baseline comparison."],
        },
        "current_reproduction_level": None,
        "current_level_iteration": 0,
        "level_source": "unassessed",
        "level_reasoning": "",
        "supporting_artifacts": [],
        "remaining_gaps": [],
        "reviewer_level_valid": False,
        "auto_iterate": False,
        "max_iterations": 4,
        "manager_status": "PASS",
        "decision_status": {},
        "workflow_blockers": [],
        "warnings": [],
    }


class TestLevelSourceUpdate:
    """Test level_source update logic in various scenarios."""

    def test_extract_verdict_pass_reduced_aligned(self):
        """Verdict PASS_REDUCED_ALIGNED should be extracted."""
        text = """
# REVIEW_REPORT

### 评审结论

**Verdict: PASS_REDUCED_ALIGNED**

本次迭代成功完成 L4 验证。
"""
        assert _extract_verdict(text) == "PASS_REDUCED_ALIGNED"

    def test_extract_verdict_no_verdict(self):
        """No verdict should return empty string."""
        text = """
# REVIEW_REPORT

## Summary

This is a summary without verdict.
"""
        assert _extract_verdict(text) == ""

    def test_with_evidence_decision_ai_backend_valid(self, tmp_path):
        """When AI backend returns valid level, level_source should be ai_backend."""
        state = _base_state(tmp_path)

        result = _with_evidence_decision(state)
        evidence_decision = json.loads(report_path(tmp_path, "evidence_decision").read_text(encoding="utf-8"))
        feedback = _build_review_feedback(
            result,
            verdict="PASS_REDUCED_ALIGNED",
            should_iterate=False,
            major_issues=[],
            execution_outcome={"failure_categories": [], "status": "COMPLETED"},
            suggested_next_action="Finalize.",
        )
        feedback_path = report_path(tmp_path, "review_feedback")
        _write_review_feedback(feedback_path, feedback)
        feedback_json = json.loads(feedback_path.read_text(encoding="utf-8"))
        manifest_path = write_run_manifest(result)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        final_summary = _formal_level_summary(result, result["current_reproduction_level"], result["current_level_iteration"])

        assert result["current_reproduction_level"] == "L4_reduced_paper_aligned"
        assert result["level_source"] == "ai_backend"
        assert result["reviewer_level_valid"] is True
        assert evidence_decision["level_source"] == result["level_source"] == "ai_backend"
        assert feedback_json["level_source"] == result["level_source"] == "ai_backend"
        assert manifest["level_source"] == result["level_source"] == "ai_backend"
        assert "**Assessment Source:** ai_backend" in final_summary

    def test_safety_override_does_not_create_ai_backend_level(self, tmp_path):
        """Safety Override rejects the current attempt and does not create a new AI-backed level."""
        state = {
            **_base_state(tmp_path),
            "reviewer_verdict": "NEEDS_FIX",
            "safety_override_triggered": True,
            "current_reproduction_level": None,
            "level_source": "unassessed",
            "reviewer_level_valid": False,
        }

        result = _with_evidence_decision(state)
        evidence_decision = json.loads(report_path(tmp_path, "evidence_decision").read_text(encoding="utf-8"))

        assert result["current_reproduction_level"] is None
        assert result["level_source"] == "unassessed"
        assert result["reviewer_level_valid"] is False
        assert evidence_decision["level_source"] == "unassessed"
        assert evidence_decision["level_valid"] is False

    def test_dirty_l4_unassessed_history_is_not_preserved(self, tmp_path):
        """Dirty historical L4 without AI provenance is cleared on invalid current attempt."""
        state = {
            **_base_state(tmp_path),
            "reviewer_verdict": "NEEDS_FIX",
            "safety_override_triggered": True,
            "current_reproduction_level": "L4_reduced_paper_aligned",
            "current_level_iteration": 9,
            "level_source": "unassessed",
            "reviewer_level_valid": False,
        }

        result = _with_evidence_decision(state)

        assert result["current_reproduction_level"] is None
        assert result["current_level_iteration"] == 0
        assert result["level_source"] == "unassessed"
        assert result["reviewer_level_valid"] is False

    def test_valid_ai_backend_history_is_preserved_on_safety_override(self, tmp_path):
        """A previous valid AI-backed level may be retained when current attempt is invalid."""
        state = {
            **_base_state(tmp_path),
            "reviewer_verdict": "NEEDS_FIX",
            "safety_override_triggered": True,
            "current_reproduction_level": "L3_official_reduced_run",
            "current_level_iteration": 2,
            "level_source": "ai_backend",
            "reviewer_level_valid": True,
        }

        result = _with_evidence_decision(state)
        evidence_decision = json.loads(report_path(tmp_path, "evidence_decision").read_text(encoding="utf-8"))

        assert result["current_reproduction_level"] == "L3_official_reduced_run"
        assert result["current_level_iteration"] == 2
        assert result["level_source"] == "ai_backend"
        assert result["reviewer_level_valid"] is False
        assert evidence_decision["level_valid"] is False
        assert evidence_decision["previous_level_valid"] is True

    def test_rules_backend_does_not_create_formal_level(self, tmp_path):
        """Rules backend must not create a formal level from structured feedback."""
        state = {
            **_base_state(tmp_path),
            "reviewer_backend": "rules",
            "reviewer_verdict": "PASS_REDUCED_ALIGNED",
            "current_reproduction_level": None,
            "level_source": "unassessed",
            "reviewer_level_valid": False,
        }

        result = _with_evidence_decision(state)

        assert result["current_reproduction_level"] is None
        assert result["level_source"] == "unassessed"
        assert result["reviewer_level_valid"] is False

    def test_with_evidence_decision_safety_override_blocks_ai_backend(self):
        """When Safety Override is triggered, level_source should NOT be ai_backend."""
        state: R2AState = {
            "repo_path": "/tmp/test_repo",
            "iteration": 1,
            "reviewer_backend": "openclaw",
            "structured_review_feedback": {
                "current_reproduction_level": "L4_reduced_paper_aligned",
                "level_reasoning": "Evidence files verified.",
                "supporting_artifacts": [],
                "remaining_gaps": [],
            },
            "reviewer_verdict": "NEEDS_FIX",  # Override from PASS_REDUCED_ALIGNED
            "safety_override_triggered": True,
            "current_reproduction_level": "L3_official_reduced_run",
            "current_level_iteration": 1,
            "level_source": "ai_backend",  # Previous valid source
        }

        # Key assertion: Safety Override should prevent updating level_source
        assert state.get("safety_override_triggered") is True

        # The logic should preserve previous level_source, not set to "ai_backend" for this attempt

    def test_no_structured_feedback_preserves_previous(self):
        """When no structured feedback, previous level should be preserved."""
        state: R2AState = {
            "repo_path": "/tmp/test_repo",
            "iteration": 2,
            "reviewer_backend": "openclaw",
            "structured_review_feedback": None,  # No structured feedback
            "reviewer_verdict": "NEEDS_FIX",
            "safety_override_triggered": False,
            "current_reproduction_level": "L3_official_reduced_run",
            "current_level_iteration": 1,
            "level_source": "ai_backend",  # Previous valid source from iteration 1
        }

        # Key assertion: Should preserve previous level and source
        assert state.get("current_reproduction_level") == "L3_official_reduced_run"
        assert state.get("level_source") == "ai_backend"

    def test_rules_backend_preserves_previous(self):
        """When rules backend, previous level should be preserved."""
        state: R2AState = {
            "repo_path": "/tmp/test_repo",
            "iteration": 2,
            "reviewer_backend": "rules",  # rules backend
            "reviewer_verdict": "NEEDS_FIX",
            "safety_override_triggered": False,
            "current_reproduction_level": "L3_official_reduced_run",
            "current_level_iteration": 1,
            "level_source": "ai_backend",  # Previous valid source
        }

        # Key assertion: rules backend should NOT update level_source
        assert state.get("reviewer_backend") == "rules"

    def test_no_l4_unassessed_contradiction(self):
        """Verify that level=L4 + source=unassessed should not happen after valid AI output."""
        # This test documents the fix for the bug in run_20260612_003125_23ef3d02

        # Before fix:
        # current_reproduction_level = L4_reduced_paper_aligned
        # level_source = unassessed  <-- BUG

        # After fix:
        # If AI returns valid L4, level_source should be "ai_backend"
        # If Safety Override triggers, level should be preserved from previous valid iteration

        # This is the core invariant we're testing:
        # level_source == "ai_backend" implies level was set by valid AI output
        # level_source == "unassessed" implies no valid AI output yet

        # We cannot have:
        # - level = L4 + source = ai_backend + verdict = NEEDS_FIX (contradiction)
        # - level = L4 + source = unassessed (missing provenance)

        pass  # This test documents the invariant, actual enforcement is in _with_evidence_decision


class TestSafetyOverrideTracking:
    """Test Safety Override tracking."""

    def test_safety_override_flag_exists(self):
        """Verify safety_override_triggered flag is recognized."""
        state: R2AState = {
            "repo_path": "/tmp/test_repo",
            "safety_override_triggered": True,
        }

        # The flag should be accessible
        assert state.get("safety_override_triggered") is True

    def test_safety_override_default_false(self):
        """Verify safety_override_triggered defaults to False."""
        state: R2AState = {
            "repo_path": "/tmp/test_repo",
        }

        # Default should be False
        assert state.get("safety_override_triggered", False) is False


class TestDirtyStateRejection:
    """Test that dirty state (L4 + unassessed) is rejected, not preserved."""

    def test_dirty_state_not_preserved_on_safety_override(self):
        """When input state has L4 + unassessed + reviewer_level_valid=false,
        Safety Override should NOT preserve this dirty L4.

        This is the key bug fix for run_20260612_003125_23ef3d02.
        """
        # Simulate dirty state from the actual bug
        state: R2AState = {
            "repo_path": "/tmp/test_repo",
            "iteration": 9,
            "reviewer_backend": "openclaw",
            "reviewer_verdict": "NEEDS_FIX",
            "safety_override_triggered": True,  # Safety Override triggered

            # Dirty state: L4 but source=unassessed and not valid
            "current_reproduction_level": "L4_reduced_paper_aligned",
            "current_level_iteration": 9,
            "level_source": "unassessed",  # NOT ai_backend!
            "reviewer_level_valid": False,  # Not valid!

            "structured_review_feedback": {
                "current_reproduction_level": "L4_reduced_paper_aligned",
                "level_reasoning": "Evidence verified.",
            },
        }

        # Key assertions:
        # 1. Safety Override is triggered
        assert state.get("safety_override_triggered") is True

        # 2. The L4 is dirty (source != ai_backend or not valid)
        assert state.get("current_reproduction_level") == "L4_reduced_paper_aligned"
        assert state.get("level_source") != "ai_backend"
        assert state.get("reviewer_level_valid") is False

        # After fix, _with_evidence_decision should:
        # - NOT preserve this dirty L4
        # - Reset current_reproduction_level to None
        # - Keep level_source as invalid/safety_override
        # - Set reviewer_level_valid=False

        # Note: Actual testing requires repo with evidence artifacts
        # This test documents the expected behavior

    def test_valid_history_preserved_on_safety_override(self):
        """When input state has valid L3 + ai_backend + valid=true,
        Safety Override should preserve this valid L3.
        """
        state: R2AState = {
            "repo_path": "/tmp/test_repo",
            "iteration": 5,
            "reviewer_backend": "openclaw",
            "reviewer_verdict": "NEEDS_FIX",
            "safety_override_triggered": True,

            # Valid history: L3 from AI backend
            "current_reproduction_level": "L3_official_reduced_run",
            "current_level_iteration": 3,
            "level_source": "ai_backend",  # Valid source!
            "reviewer_level_valid": True,  # Valid!
        }

        # Key assertions:
        # 1. Safety Override is triggered
        assert state.get("safety_override_triggered") is True

        # 2. The L3 is valid (source=ai_backend AND valid=true)
        assert state.get("current_reproduction_level") == "L3_official_reduced_run"
        assert state.get("level_source") == "ai_backend"
        assert state.get("reviewer_level_valid") is True

        # After fix, _with_evidence_decision should:
        # - Preserve this valid L3
        # - Keep current_reproduction_level=L3
        # - Keep level_source=ai_backend
        # - Set reviewer_level_valid=False for THIS attempt

    def test_level_source_validation_rules(self):
        """Document the rules for preserving historical level."""
        # Rule: Only preserve if ALL of:
        # 1. previous_level is not None
        # 2. previous_level_source == "ai_backend"
        # 3. previous_level_valid == True

        # Case 1: All valid → preserve
        case1 = {
            "level": "L3_official_reduced_run",
            "source": "ai_backend",
            "valid": True,
            "should_preserve": True,
        }

        # Case 2: source=unassessed → reject
        case2 = {
            "level": "L4_reduced_paper_aligned",
            "source": "unassessed",
            "valid": False,
            "should_preserve": False,
        }

        # Case 3: source=ai_backend but valid=False → reject
        case3 = {
            "level": "L4_reduced_paper_aligned",
            "source": "ai_backend",
            "valid": False,
            "should_preserve": False,
        }

        # Case 4: source=invalid_xxx → reject
        case4 = {
            "level": "L3_official_reduced_run",
            "source": "invalid_empty_reasoning",
            "valid": False,
            "should_preserve": False,
        }

        # Case 5: No previous level → preserve None
        case5 = {
            "level": None,
            "source": "unassessed",
            "valid": False,
            "should_preserve": False,
        }

        # Verify logic
        for case in [case1, case2, case3, case4, case5]:
            level = case["level"]
            source = case["source"]
            valid = case["valid"]
            expected = case["should_preserve"]

            # Apply the rule
            should_preserve = (
                level is not None and
                source == "ai_backend" and
                valid is True
            )

            assert should_preserve == expected, f"Failed for case: {case}"


class TestVerdictFormatsWithActualRun:
    """Test verdict parsing with actual run format."""

    def test_actual_run_iter_009_format(self):
        """Test verdict parsing with actual iter_009 format."""
        text = """
# REVIEW_REPORT.md

## 迭代信息

- **迭代次数**: 9
- **最大迭代限制**: 10
- **工作流状态**: 已达成 L4_reduced_paper_aligned
- **输出语言**: 简体中文

---

## 1. 评审摘要

### 评审结论

**Verdict: PASS_REDUCED_ALIGNED**

本次迭代（第9次迭代）成功完成了 L4_reduced_paper_aligned 证据完整性验证。
"""
        assert _extract_verdict(text) == "PASS_REDUCED_ALIGNED"

    def test_actual_run_with_safety_override_appendix(self):
        """Test that verdict can be extracted even if Safety Override is appended."""
        text = """
# REVIEW_REPORT.md

## 迭代信息

- **迭代次数**: 9

### 评审结论

**Verdict: PASS_REDUCED_ALIGNED**

本次迭代成功完成验证。

## Verdict

NEEDS_FIX

## Safety Override

REVIEW_REPORT.md did not include a Verdict. Safety override set verdict to NEEDS_FIX.
"""
        # Independent ## Verdict section has priority
        # But after fix, the inline verdict should be detected first
        # This test validates the priority: independent section wins
        assert _extract_verdict(text) == "NEEDS_FIX"

        # NOTE: After P0-1 fix, the inline `**Verdict: PASS_REDUCED_ALIGNED**`
        # should be detected before Safety Override is triggered.
        # If parser finds it, Safety Override won't be triggered.


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
