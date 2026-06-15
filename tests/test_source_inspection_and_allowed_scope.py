"""Test SourceInspection and allowed_scope changes.

This test verifies:
1. SourceInspection no longer makes hard decisions about L3/L4 support
2. allowed_scope no longer caps max_target_level based on static inspection
3. Planner schema normalizes unknown blocker categories
4. Backend retry/backoff works correctly
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from r2a.core.planner_schema import normalize_blocker_category, BlockingIssue
from r2a.tools.source_inspection import build_source_inspection


class TestSourceInspectionAdvisory:
    """Tests for SourceInspection advisory (not hard) decisions."""

    def test_supports_l3_is_unknown_not_false_when_dataset_blocked(self, tmp_path):
        """When dataset is mentioned but not verified, supports.L3 should be 'unknown', not False."""
        # Create a minimal source structure
        (tmp_path / "README.md").write_text("Dataset: http://example.com/dataset.tar.gz\n")
        (tmp_path / "benchmark.py").write_text("# entrypoint\n")

        state = {
            "repo_path": str(tmp_path),
            "source_acquisition": {"source_status": "available", "local_path": str(tmp_path)},
        }

        result = build_source_inspection(state)

        # L3 should be 'unknown' (not False) because dataset is mentioned but not verified
        assert result["supports"]["L3_reduced_experiment"] == "unknown"
        assert result["supports"]["L4_reduced_paper_aligned"] == "unknown"

    def test_supports_l3_is_true_when_entrypoints_exist(self, tmp_path):
        """When entrypoints exist and no dataset mentioned, supports.L3 should be True."""
        (tmp_path / "benchmark.py").write_text("# entrypoint\n")

        state = {
            "repo_path": str(tmp_path),
            "source_acquisition": {"source_status": "available", "local_path": str(tmp_path)},
        }

        result = build_source_inspection(state)

        # L3 should be True because entrypoints exist and no dataset blocking
        assert result["supports"]["L3_reduced_experiment"] is True


class TestAllowedScopeNoCap:
    """Tests for allowed_scope not capping max_target_level."""

    def test_allowed_scope_respects_user_target(self, tmp_path):
        """User's target_level should be respected, not capped by static inspection."""
        from r2a.tools.iteration import _allowed_scope_context

        state = {
            "target_reproduction_level": "L4_reduced_paper_aligned",
            "allow_official_dataset_download": True,
            "download_budget_gb": 20,
        }

        with patch("r2a.tools.iteration._read_json_dict") as mock_read:
            mock_read.return_value = {
                "supports": {"L3_reduced_experiment": "unknown"},  # Static uncertainty
                "dataset_requirements": [{"required": True, "available": False}],
            }
            result = _allowed_scope_context(state, tmp_path)

        # max_target_level should be user's target, not capped to L2
        assert result["max_target_level"] == "L4_reduced_paper_aligned"
        assert result["contract_mode"] == "official_reduced"

    def test_allowed_scope_verification_only_when_no_download_permission(self, tmp_path):
        """When user doesn't allow download, contract_mode should be verification_only."""
        from r2a.tools.iteration import _allowed_scope_context

        state = {
            "target_reproduction_level": "L4_reduced_paper_aligned",
            "allow_official_dataset_download": False,  # User didn't allow download
            "download_budget_gb": 20,
        }

        result = _allowed_scope_context(state, tmp_path)

        assert result["contract_mode"] == "verification_only"
        # But max_target_level should still be user's target
        assert result["max_target_level"] == "L4_reduced_paper_aligned"

    def test_allowed_scope_full_benchmark_when_allowed(self, tmp_path):
        """When user allows full benchmark, contract_mode should be full_benchmark."""
        from r2a.tools.iteration import _allowed_scope_context

        state = {
            "target_reproduction_level": "L6_full_benchmark_reproduction",
            "allow_full_benchmark": True,
            "download_budget_gb": 100,
        }

        result = _allowed_scope_context(state, tmp_path)

        assert result["contract_mode"] == "full_benchmark"
        assert result["max_target_level"] == "L6_full_benchmark_reproduction"

    def test_allowed_scope_insufficient_budget(self, tmp_path):
        """When download budget is insufficient, should fall back to verification_only."""
        from r2a.tools.iteration import _allowed_scope_context

        state = {
            "target_reproduction_level": "L4_reduced_paper_aligned",
            "allow_official_dataset_download": True,
            "download_budget_gb": 0,  # Insufficient
        }

        result = _allowed_scope_context(state, tmp_path)

        assert result["contract_mode"] == "verification_only"


class TestBlockerCategoryNormalization:
    """Tests for blocker category normalization."""

    def test_unknown_runtime_category_normalized(self):
        """Unknown runtime-related categories should normalize to TOOLCHAIN_OR_ENVIRONMENT."""
        assert normalize_blocker_category("RUNTIME_DLL_COMPATIBILITY") == "TOOLCHAIN_OR_ENVIRONMENT"
        assert normalize_blocker_category("CUDA_ERROR") == "TOOLCHAIN_OR_ENVIRONMENT"
        assert normalize_blocker_category("PYTHON_VERSION_MISMATCH") == "TOOLCHAIN_OR_ENVIRONMENT"

    def test_unknown_data_category_normalized(self):
        """Unknown data-related categories should normalize to MISSING_ARTIFACT_OR_DATA."""
        assert normalize_blocker_category("DATASET_MISSING") == "MISSING_ARTIFACT_OR_DATA"
        assert normalize_blocker_category("FILE_NOT_FOUND") == "MISSING_ARTIFACT_OR_DATA"

    def test_unknown_schema_category_normalized(self):
        """Unknown schema-related categories should normalize to SCHEMA_OR_REPORTING."""
        assert normalize_blocker_category("CSV_PARSE_ERROR") == "SCHEMA_OR_REPORTING"
        assert normalize_blocker_category("FORMAT_INVALID") == "SCHEMA_OR_REPORTING"

    def test_unknown_approval_category_normalized(self):
        """Unknown approval-related categories should normalize to NEEDS_MANUAL_APPROVAL."""
        assert normalize_blocker_category("PERMISSION_DENIED") == "NEEDS_MANUAL_APPROVAL"
        assert normalize_blocker_category("AUTHORIZATION_REQUIRED") == "NEEDS_MANUAL_APPROVAL"

    def test_completely_unknown_category_becomes_other(self):
        """Completely unknown categories should normalize to OTHER."""
        assert normalize_blocker_category("SOME_NEW_ERROR_TYPE") == "OTHER"
        assert normalize_blocker_category("FUTURE_CATEGORY") == "OTHER"

    def test_valid_category_unchanged(self):
        """Valid categories should remain unchanged."""
        assert normalize_blocker_category("TOOLCHAIN_OR_ENVIRONMENT") == "TOOLCHAIN_OR_ENVIRONMENT"
        assert normalize_blocker_category("MISSING_ARTIFACT_OR_DATA") == "MISSING_ARTIFACT_OR_DATA"
        assert normalize_blocker_category("OTHER") == "OTHER"

    def test_blocking_issue_normalizes_category(self):
        """BlockingIssue model should normalize category before validation."""
        issue = BlockingIssue(
            issue_id="test_001",
            category="RUNTIME_DLL_COMPATIBILITY",  # Unknown category
            description="DLL not found",
            evidence_source="runtime_smoke.csv",
            severity="BLOCKING",
        )

        # Should be normalized to TOOLCHAIN_OR_ENVIRONMENT
        assert issue.category == "TOOLCHAIN_OR_ENVIRONMENT"


class TestBackendRetryBackoff:
    """Tests for backend retry/backoff logic."""

    def test_retry_on_timeout(self, tmp_path):
        """Should retry on timeout errors."""
        from r2a.tools.planner_model_client import (
            call_planner_model_with_diagnostics,
            MAX_RETRIES,
            RETRY_DELAY_SECONDS,
        )

        call_count = [0]

        def mock_generate(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise TimeoutError("Connection timed out")
            # Return valid JSON on third attempt
            return '{"schema_version": "2.0", "iteration": 1, "planning_mode": "initial", "iteration_strategy": "PROGRESS_ONLY", "objective": "test", "contract_mode": "verification_only", "max_evidence_level_allowed": "L2", "current_status_summary": "test", "completed_capabilities": [], "blocking_issues": [], "evidence_used": [], "evidence_gaps": [], "tasks": [{"task_id": "T1", "title": "source verification", "actions": ["echo retry ok"], "expected_outputs": [".r2a/results/source_verification.csv"], "stop_conditions": ["source_verification.csv exists"]}], "claim_restrictions": [], "manual_approval_points": [], "preserve_outputs": []}'

        bundle = {"repo_path": str(tmp_path)}

        with patch("r2a.tools.planner_model_client.generate_planner_json", mock_generate):
            with patch("r2a.tools.planner_model_client._build_prompt", return_value="test"):
                with patch("r2a.tools.planner_model_client.parse_planner_json_with_metadata") as mock_parse:
                    mock_parse.return_value = ({
                        "schema_version": "2.0",
                        "iteration": 1,
                        "planning_mode": "initial",
                        "iteration_strategy": "PROGRESS_ONLY",
                        "objective": "test",
                        "contract_mode": "verification_only",
                        "max_evidence_level_allowed": "L2",
                        "current_status_summary": "test",
                        "completed_capabilities": [],
                        "blocking_issues": [],
                        "evidence_used": [],
                        "evidence_gaps": [],
                        "tasks": [
                            {
                                "task_id": "T1",
                                "title": "source verification",
                                "actions": ["echo retry ok"],
                                "expected_outputs": [".r2a/results/source_verification.csv"],
                                "stop_conditions": ["source_verification.csv exists"],
                            }
                        ],
                        "claim_restrictions": [],
                        "manual_approval_points": [],
                        "preserve_outputs": [],
                    }, {"json_parse_passed": True})

                    data, meta = call_planner_model_with_diagnostics(bundle, backend="ccr")

                    # Should have retried
                    assert call_count[0] == 3
                    assert "retries" in meta

    def test_no_retry_on_config_error(self, tmp_path):
        """Should NOT retry on configuration errors."""
        from r2a.tools.planner_model_client import (
            call_planner_model_with_diagnostics,
            PlannerBackendNotConfigured,
        )

        with pytest.raises(PlannerBackendNotConfigured):
            call_planner_model_with_diagnostics({}, backend="unknown_backend")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
