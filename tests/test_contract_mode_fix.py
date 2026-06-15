"""Test that allow_official_dataset_download is respected in allowed_scope.

This test verifies the fix for the contract_mode bug where:
- UI sets target_level = L4 and allow_official_dataset_download = True
- But internal contract_mode was forced to verification_only
- Even when user explicitly allowed dataset download

UPDATED 2026-06-09:
- max_target_level is NO LONGER capped by static inspection uncertainty
- User's target level is preserved
- contract_mode is determined by user permissions only
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from r2a.core.state import R2AState, make_initial_state
from r2a.tools.iteration import _allowed_scope_context
from r2a.tools.readiness_gate import _allowed_scope
from r2a.tools.planner_input_builder import _allowed_scope_from_readiness


def make_test_state(
    target_level: str = "L4_reduced_paper_aligned",
    allow_download: bool = False,
) -> R2AState:
    """Create a minimal test state."""
    return {
        "run_id": "test-run",
        "repo_path": "/tmp/test-repo",
        "iteration": 1,
        "target_reproduction_level": target_level,
        "allow_official_dataset_download": allow_download,
        "download_budget_gb": 20,
        "max_iterations": 12,
    }


def make_source_inspection(
    supports_l3: bool = True,
    dataset_missing: bool = False,
) -> dict:
    """Create a minimal source inspection result."""
    return {
        "inspection_status": "completed",
        "supports": {
            "L3_reduced_experiment": supports_l3,
        },
        "dataset_requirements": [
            {"name": "test_dataset", "required": True, "available": not dataset_missing}
        ] if dataset_missing else [],
    }


class TestAllowedScopeContext:
    """Tests for _allowed_scope_context in iteration.py"""

    def test_no_dataset_missing_no_download_allowed(self, tmp_path):
        """When dataset is available and user didn't allow download, should be verification_only."""
        state = make_test_state(allow_download=False)
        inspection = make_source_inspection(supports_l3=True, dataset_missing=False)

        with patch("r2a.tools.iteration._read_json_dict") as mock_read:
            mock_read.return_value = inspection
            result = _allowed_scope_context(state, tmp_path)

        assert result["contract_mode"] == "verification_only"
        assert result["target_level"] == "L4_reduced_paper_aligned"

    def test_no_dataset_missing_download_allowed(self, tmp_path):
        """When dataset is available and user allowed download, should be official_reduced."""
        state = make_test_state(allow_download=True)
        inspection = make_source_inspection(supports_l3=True, dataset_missing=False)

        with patch("r2a.tools.iteration._read_json_dict") as mock_read:
            mock_read.return_value = inspection
            result = _allowed_scope_context(state, tmp_path)

        assert result["contract_mode"] == "official_reduced"
        assert result["target_level"] == "L4_reduced_paper_aligned"

    def test_dataset_missing_no_download_allowed(self, tmp_path):
        """When dataset is missing and user didn't allow download, should be verification_only.

        UPDATED: max_target_level is NO LONGER capped - user's target is preserved.
        """
        state = make_test_state(allow_download=False)
        inspection = make_source_inspection(supports_l3=True, dataset_missing=True)

        with patch("r2a.tools.iteration._read_json_dict") as mock_read:
            mock_read.return_value = inspection
            result = _allowed_scope_context(state, tmp_path)

        assert result["contract_mode"] == "verification_only"
        # NEW: max_target_level preserves user's target, not capped to L2
        assert result["max_target_level"] == "L4_reduced_paper_aligned"

    def test_dataset_missing_download_allowed(self, tmp_path):
        """When dataset is missing and user allowed download, should be official_reduced.

        THIS IS THE KEY FIX: Previously this would return verification_only,
        ignoring the user's explicit allow_official_dataset_download=True.
        """
        state = make_test_state(allow_download=True)
        inspection = make_source_inspection(supports_l3=True, dataset_missing=True)

        with patch("r2a.tools.iteration._read_json_dict") as mock_read:
            mock_read.return_value = inspection
            result = _allowed_scope_context(state, tmp_path)

        # KEY FIX: Should be official_reduced, not verification_only
        assert result["contract_mode"] == "official_reduced"
        assert result["target_level"] == "L4_reduced_paper_aligned"

    def test_source_does_not_support_l3(self, tmp_path):
        """When source doesn't support L3, contract_mode still respects user permission.

        UPDATED: Static inspection uncertainty does NOT force verification_only.
        The actual feasibility is determined by execution results.
        """
        state = make_test_state(allow_download=True)
        inspection = make_source_inspection(supports_l3=False, dataset_missing=False)

        with patch("r2a.tools.iteration._read_json_dict") as mock_read:
            mock_read.return_value = inspection
            result = _allowed_scope_context(state, tmp_path)

        # NEW: contract_mode respects user permission, not static inspection
        assert result["contract_mode"] == "official_reduced"
        # NEW: max_target_level preserves user's target
        assert result["max_target_level"] == "L4_reduced_paper_aligned"


class TestAllowedScopeReadinessGate:
    """Tests for _allowed_scope in readiness_gate.py"""

    def test_dataset_missing_download_allowed(self):
        """When dataset is missing and user allowed download, should be official_reduced."""
        state = make_test_state(allow_download=True)
        inspection = make_source_inspection(supports_l3=True, dataset_missing=True)

        result = _allowed_scope(state, inspection)

        assert result["contract_mode"] == "official_reduced"
        assert result["target_level"] == "L4_reduced_paper_aligned"

    def test_dataset_missing_no_download_allowed(self):
        """When dataset is missing and user didn't allow download, should be verification_only.

        UPDATED: max_target_level preserves user's target.
        """
        state = make_test_state(allow_download=False)
        inspection = make_source_inspection(supports_l3=True, dataset_missing=True)

        result = _allowed_scope(state, inspection)

        assert result["contract_mode"] == "verification_only"
        # NEW: max_target_level preserves user's target, not capped
        assert result["max_target_level"] == "L4_reduced_paper_aligned"

    def test_fallback_forces_verification_only(self):
        """When fallback=True, should be verification_only regardless of other settings."""
        state = make_test_state(allow_download=True)
        inspection = make_source_inspection(supports_l3=True, dataset_missing=False)

        result = _allowed_scope(state, inspection, fallback=True)

        assert result["contract_mode"] == "verification_only"


class TestAllowedScopeFromReadiness:
    """Tests for _allowed_scope_from_readiness in planner_input_builder.py"""

    def test_dataset_missing_download_allowed(self):
        """When dataset is missing and user allowed download, should be official_reduced."""
        state = make_test_state(allow_download=True)
        inspection = make_source_inspection(supports_l3=True, dataset_missing=True)

        result = _allowed_scope_from_readiness({}, inspection, state)

        assert result["contract_mode"] == "official_reduced"
        assert result["target_level"] == "L4_reduced_paper_aligned"

    def test_dataset_missing_no_download_allowed(self):
        """When dataset is missing and user didn't allow download, should be verification_only.

        UPDATED: max_target_level preserves user's target.
        """
        state = make_test_state(allow_download=False)
        inspection = make_source_inspection(supports_l3=True, dataset_missing=True)

        result = _allowed_scope_from_readiness({}, inspection, state)

        assert result["contract_mode"] == "verification_only"
        # NEW: max_target_level preserves user's target, not capped
        assert result["max_target_level"] == "L4_reduced_paper_aligned"

    def test_planner_readiness_constraints_take_precedence(self):
        """When planner_readiness has constraints, they should be used directly."""
        state = make_test_state(allow_download=True)
        inspection = make_source_inspection(supports_l3=True, dataset_missing=True)

        readiness = {
            "constraints": {
                "contract_mode": "full_benchmark",
                "target_level": "L6_full_benchmark_reproduction",
            }
        }

        result = _allowed_scope_from_readiness(readiness, inspection, state)

        assert result["contract_mode"] == "full_benchmark"


class TestContractModeIntegration:
    """Integration tests for the full contract_mode flow."""

    def test_ui_l4_with_download_should_allow_official_reduced(self, tmp_path):
        """Simulate the full UI flow: L4 target + allow download → official_reduced."""
        # This simulates what happens when user sets in UI:
        # - target_reproduction_level = L4_reduced_paper_aligned
        # - allow_official_dataset_download = True

        state = make_test_state(
            target_level="L4_reduced_paper_aligned",
            allow_download=True,
        )

        # Simulate source inspection finding missing dataset
        inspection = make_source_inspection(supports_l3=True, dataset_missing=True)

        with patch("r2a.tools.iteration._read_json_dict") as mock_read:
            mock_read.return_value = inspection
            result = _allowed_scope_context(state, tmp_path)

        # KEY FIX: Should be official_reduced, not verification_only
        assert result["contract_mode"] == "official_reduced"
        assert result["target_level"] == "L4_reduced_paper_aligned"
        assert result["max_target_level"] == "L4_reduced_paper_aligned"

    def test_ui_l4_without_download_should_be_verification_only(self, tmp_path):
        """Simulate the full UI flow: L4 target + no download → verification_only.

        UPDATED: max_target_level preserves user's target.
        """
        state = make_test_state(
            target_level="L4_reduced_paper_aligned",
            allow_download=False,
        )

        inspection = make_source_inspection(supports_l3=True, dataset_missing=True)

        with patch("r2a.tools.iteration._read_json_dict") as mock_read:
            mock_read.return_value = inspection
            result = _allowed_scope_context(state, tmp_path)

        assert result["contract_mode"] == "verification_only"
        # NEW: max_target_level preserves user's target, not capped to L2
        assert result["max_target_level"] == "L4_reduced_paper_aligned"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
