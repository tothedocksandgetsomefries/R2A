"""Tests for paper_alignment.csv value normalization."""

import pytest
from r2a.tools.csv_schemas import (
    canonicalize_cell_value,
    canonicalize_row,
    allowed_values_for_csv,
    legacy_value_aliases_for_csv,
)


class TestPaperAlignmentCanonicalValues:
    """Tests for canonical match_status values in paper_alignment.csv."""

    def test_match_is_valid(self):
        """MATCH is a canonical value."""
        assert "MATCH" in allowed_values_for_csv("paper_alignment.csv", "match_status")

    def test_partial_match_is_valid(self):
        """PARTIAL_MATCH is a canonical value."""
        assert "PARTIAL_MATCH" in allowed_values_for_csv("paper_alignment.csv", "match_status")

    def test_mismatch_is_valid(self):
        """MISMATCH is a canonical value."""
        assert "MISMATCH" in allowed_values_for_csv("paper_alignment.csv", "match_status")

    def test_not_available_is_valid(self):
        """NOT_AVAILABLE is a canonical value."""
        assert "NOT_AVAILABLE" in allowed_values_for_csv("paper_alignment.csv", "match_status")

    def test_needs_human_verification_is_valid(self):
        """NEEDS_HUMAN_VERIFICATION is a canonical value."""
        assert "NEEDS_HUMAN_VERIFICATION" in allowed_values_for_csv("paper_alignment.csv", "match_status")

    def test_different_is_not_canonical(self):
        """DIFFERENT is NOT a canonical value."""
        assert "DIFFERENT" not in allowed_values_for_csv("paper_alignment.csv", "match_status")

    def test_none_is_not_canonical(self):
        """None is NOT a canonical value."""
        assert "None" not in allowed_values_for_csv("paper_alignment.csv", "match_status")


class TestPaperAlignmentValueNormalization:
    """Tests for match_status value normalization via legacy_value_aliases."""

    def test_different_mapped_to_mismatch(self):
        """DIFFERENT should be mapped to MISMATCH."""
        assert canonicalize_cell_value("paper_alignment.csv", "match_status", "DIFFERENT") == "MISMATCH"

    def test_different_lowercase_mapped_to_mismatch(self):
        """lowercase 'different' should be mapped to MISMATCH."""
        assert canonicalize_cell_value("paper_alignment.csv", "match_status", "different") == "MISMATCH"

    def test_none_mapped_to_not_available(self):
        """None should be mapped to NOT_AVAILABLE."""
        assert canonicalize_cell_value("paper_alignment.csv", "match_status", "None") == "NOT_AVAILABLE"

    def test_none_uppercase_mapped_to_not_available(self):
        """NONE should be mapped to NOT_AVAILABLE."""
        assert canonicalize_cell_value("paper_alignment.csv", "match_status", "NONE") == "NOT_AVAILABLE"

    def test_null_mapped_to_not_available(self):
        """NULL should be mapped to NOT_AVAILABLE."""
        assert canonicalize_cell_value("paper_alignment.csv", "match_status", "NULL") == "NOT_AVAILABLE"

    def test_empty_mapped_to_not_available(self):
        """Empty string should be mapped to NOT_AVAILABLE."""
        assert canonicalize_cell_value("paper_alignment.csv", "match_status", "") == "NOT_AVAILABLE"

    def test_canonical_value_unchanged(self):
        """Canonical values should be returned unchanged (uppercased)."""
        assert canonicalize_cell_value("paper_alignment.csv", "match_status", "MATCH") == "MATCH"
        assert canonicalize_cell_value("paper_alignment.csv", "match_status", "match") == "MATCH"
        assert canonicalize_cell_value("paper_alignment.csv", "match_status", "MISMATCH") == "MISMATCH"

    def test_unknown_value_unchanged(self):
        """Unknown values should be returned uppercased but not mapped."""
        assert canonicalize_cell_value("paper_alignment.csv", "match_status", "UNKNOWN_STATUS") == "UNKNOWN_STATUS"


class TestPaperAlignmentRowCanonicalization:
    """Tests for canonicalize_row with paper_alignment.csv."""

    def test_row_with_different_normalized(self):
        """Row with DIFFERENT match_status should be normalized to MISMATCH."""
        row = {
            "paper_item": "hardware",
            "setting_name": "machine",
            "paper_setting": "AWS m5d.24xlarge",
            "reduced_setting": "WSL Ubuntu x86-64",
            "match_status": "DIFFERENT",
            "evidence_source": "toolchain_context.csv",
            "notes": "Different hardware environment",
        }
        normalized = canonicalize_row("paper_alignment.csv", row)
        assert normalized["match_status"] == "MISMATCH"

    def test_row_with_none_normalized(self):
        """Row with None match_status should be normalized to NOT_AVAILABLE."""
        row = {
            "paper_item": "metric",
            "setting_name": "recall",
            "paper_setting": "Recall@10",
            "reduced_setting": "NOT_MEASURED",
            "match_status": "None",
            "evidence_source": "reduced_metrics.csv",
            "notes": "Not measured",
        }
        normalized = canonicalize_row("paper_alignment.csv", row)
        assert normalized["match_status"] == "NOT_AVAILABLE"

    def test_row_with_match_unchanged(self):
        """Row with MATCH match_status should be unchanged."""
        row = {
            "paper_item": "parameters",
            "setting_name": "M",
            "paper_setting": "32",
            "reduced_setting": "32",
            "match_status": "MATCH",
            "evidence_source": "build_smoke.csv",
            "notes": "Paper Table 2 parameter",
        }
        normalized = canonicalize_row("paper_alignment.csv", row)
        assert normalized["match_status"] == "MATCH"

    def test_row_preserves_other_fields(self):
        """Row canonicalization should preserve non-match_status fields."""
        row = {
            "paper_item": "dataset",
            "setting_name": "name",
            "paper_setting": "SIFT1M",
            "reduced_setting": "SIFT1M_subset",
            "match_status": "PARTIAL_MATCH",
            "evidence_source": "input_contract_verification.csv",
            "notes": "Reduced to 100k base vectors",
        }
        normalized = canonicalize_row("paper_alignment.csv", row)
        assert normalized["paper_item"] == "dataset"
        assert normalized["setting_name"] == "name"
        assert normalized["paper_setting"] == "SIFT1M"
        assert normalized["reduced_setting"] == "SIFT1M_subset"
        assert normalized["match_status"] == "PARTIAL_MATCH"
        assert normalized["evidence_source"] == "input_contract_verification.csv"
        assert normalized["notes"] == "Reduced to 100k base vectors"


class TestLegacyValueAliasesExist:
    """Tests to verify legacy_value_aliases are defined."""

    def test_match_status_aliases_exist(self):
        """match_status should have legacy_value_aliases defined."""
        aliases = legacy_value_aliases_for_csv("paper_alignment.csv")
        assert "match_status" in aliases

    def test_different_alias_exists(self):
        """DIFFERENT alias should exist for match_status."""
        aliases = legacy_value_aliases_for_csv("paper_alignment.csv")
        assert aliases.get("match_status", {}).get("DIFFERENT") == "MISMATCH"

    def test_none_alias_exists(self):
        """NONE alias should exist for match_status."""
        aliases = legacy_value_aliases_for_csv("paper_alignment.csv")
        assert aliases.get("match_status", {}).get("NONE") == "NOT_AVAILABLE"

    def test_gap_alias_exists(self):
        """Legacy GAP should map to NOT_AVAILABLE instead of hard failing."""
        aliases = legacy_value_aliases_for_csv("paper_alignment.csv")
        assert aliases.get("match_status", {}).get("GAP") == "NOT_AVAILABLE"
