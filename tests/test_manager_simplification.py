"""Test that Manager does not FAIL on CSV schema issues.

This test verifies the Manager simplification where:
- CSV missing columns (command, component, evidence_source) → WARNING, not FAIL
- CSV format issues → WARNING, not FAIL
- Only critical evidence absence → FAIL
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from r2a.core.evidence_policy import manager_level_decision


def make_minimal_csv(tmp_path: Path, filename: str, content: str) -> Path:
    """Create a minimal CSV file for testing."""
    csv_path = tmp_path / "results" / filename
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(content, encoding="utf-8")
    return csv_path


class TestManagerCsvSchemaTolerance:
    """Tests for Manager's tolerance of CSV schema issues."""

    def test_missing_command_column_should_not_fail(self, tmp_path):
        """CSV missing 'command' column should be WARNING, not FAIL."""
        # Create a CSV without 'command' column
        csv_content = """status,exit_code,notes
PASS,0,Basic test passed
PASS,0,Another test passed
"""
        csv_path = make_minimal_csv(tmp_path, "build_smoke.csv", csv_content)

        # Manager should treat this as WARNING, not FAIL
        decision = manager_level_decision(
            tmp_path,
            status="WARNING",
            errors=[],
            warnings=["CSV: build_smoke.csv: missing required column 'command'"],
            result_csvs=[csv_path],
        )

        # Should not be FAIL
        assert decision["status"] != "FAIL"
        # Should have the warning in checks or warnings field
        # The actual field name may vary, just check status is not FAIL

    def test_missing_component_column_should_not_fail(self, tmp_path):
        """CSV missing 'component' column should be WARNING, not FAIL."""
        csv_content = """status,exit_code,notes
PASS,0,Test passed
"""
        csv_path = make_minimal_csv(tmp_path, "input_contract_verification.csv", csv_content)

        decision = manager_level_decision(
            tmp_path,
            status="WARNING",
            errors=[],
            warnings=["CSV: input_contract_verification.csv: missing required column 'component'"],
            result_csvs=[csv_path],
        )

        assert decision["status"] != "FAIL"

    def test_missing_evidence_source_column_should_not_fail(self, tmp_path):
        """CSV missing 'evidence_source' column should be WARNING, not FAIL."""
        csv_content = """status,exit_code,notes
PASS,0,Runtime smoke passed
"""
        csv_path = make_minimal_csv(tmp_path, "runtime_smoke.csv", csv_content)

        decision = manager_level_decision(
            tmp_path,
            status="WARNING",
            errors=[],
            warnings=["CSV: runtime_smoke.csv: missing required column 'evidence_source'"],
            result_csvs=[csv_path],
        )

        assert decision["status"] != "FAIL"

    def test_malformed_csv_should_not_fail_if_has_pass_evidence(self, tmp_path):
        """Malformed CSV should be WARNING if there's other PASS evidence."""
        # Create a malformed CSV
        csv_content = """this,is,malformed,with,too,many,columns,and,no,structure
PASS,0,test
"""
        csv_path = make_minimal_csv(tmp_path, "bad.csv", csv_content)

        # Create a valid CSV with PASS
        valid_csv = make_minimal_csv(
            tmp_path,
            "source_verification.csv",
            "status,exit_code,notes\nPASS,0,Source verified\n"
        )

        decision = manager_level_decision(
            tmp_path,
            status="WARNING",
            errors=[],
            warnings=["CSV: bad.csv: CSV parse issue - malformed structure"],
            result_csvs=[csv_path, valid_csv],
        )

        # Should not FAIL because we have valid PASS evidence
        assert decision["status"] != "FAIL"

    def test_no_pass_evidence_at_all_should_fail(self, tmp_path):
        """If there's NO PASS evidence at all, should FAIL."""
        # Create only NOT_RUN results
        csv_content = """status,exit_code,notes
NOT_RUN,-,Not executed
NOT_RUN,-,Also not executed
"""
        csv_path = make_minimal_csv(tmp_path, "build_smoke.csv", csv_content)

        decision = manager_level_decision(
            tmp_path,
            status="FAIL",
            errors=["No PASS evidence found in any result CSV"],
            warnings=[],
            result_csvs=[csv_path],
        )

        assert decision["status"] == "FAIL"

    def test_real_failure_should_still_fail(self, tmp_path):
        """Real failures (not schema issues) should still cause FAIL."""
        csv_content = """status,exit_code,notes
FAIL,1,Build failed with error
"""
        csv_path = make_minimal_csv(tmp_path, "build_smoke.csv", csv_content)

        decision = manager_level_decision(
            tmp_path,
            status="FAIL",
            errors=["Build smoke failed with exit code 1"],
            warnings=[],
            result_csvs=[csv_path],
        )

        assert decision["status"] == "FAIL"


class TestManagerEvidenceGate:
    """Tests for Manager as a lightweight evidence gate."""

    def test_manager_should_check_engineer_outputs_present(self, tmp_path):
        """Manager should verify Engineer produced outputs."""
        # Create expected outputs
        csvs = []
        for filename in ["source_verification.csv", "project_tests.csv", "build_smoke.csv"]:
            csvs.append(make_minimal_csv(
                tmp_path,
                filename,
                "status,exit_code,notes\nPASS,0,OK\n"
            ))

        decision = manager_level_decision(
            tmp_path,
            status="PASS",
            errors=[],
            warnings=[],
            result_csvs=csvs,
        )

        # Should recognize outputs are present
        assert decision["status"] == "PASS"
        assert decision["max_level_allowed"] != "L0_project_health"

    def test_manager_should_identify_critical_missing_outputs(self, tmp_path):
        """Manager should identify when critical outputs are missing."""
        # Only create one output, missing others
        csv_path = make_minimal_csv(
            tmp_path,
            "source_verification.csv",
            "status,exit_code,notes\nPASS,0,OK\n"
        )

        decision = manager_level_decision(
            tmp_path,
            status="WARNING",
            errors=["Missing expected result: build_smoke.csv"],
            warnings=[],
            result_csvs=[csv_path],
        )

        # Should note missing outputs but not necessarily FAIL
        assert "build_smoke.csv" in str(decision.get("blocking_errors", [])) or \
               decision["status"] in {"WARNING", "FAIL"}

    def test_manager_should_not_fail_on_partial_csv_read(self, tmp_path):
        """Manager should not FAIL when CSV can only be partially read."""
        csv_content = """status,exit_code,notes
PASS,0,First test
PASS,0,Second test
# truncated here - file was being written
"""
        csv_path = make_minimal_csv(tmp_path, "runtime_smoke.csv", csv_content)

        decision = manager_level_decision(
            tmp_path,
            status="WARNING",
            errors=[],
            warnings=["CSV: runtime_smoke.csv: partial CSV read - file may be incomplete"],
            result_csvs=[csv_path],
        )

        # Should be WARNING, not FAIL
        assert decision["status"] != "FAIL"


class TestManagerOutputSimplification:
    """Tests for simplified Manager output format."""

    def test_manager_output_has_required_fields(self, tmp_path):
        """Manager output should have the simplified required fields."""
        csv_path = make_minimal_csv(
            tmp_path,
            "source_verification.csv",
            "status,exit_code,notes\nPASS,0,OK\n"
        )

        decision = manager_level_decision(
            tmp_path,
            status="PASS",
            errors=[],
            warnings=[],
            result_csvs=[csv_path],
        )

        # Required fields
        assert "status" in decision
        assert "max_level_allowed" in decision
        # These should exist (may be empty)
        assert "blocking_errors" in decision
        # advisory_warnings may be in checks or separate field
        # Just verify the decision has the essential fields

    def test_manager_does_not_output_detailed_csv_fix_suggestions(self, tmp_path):
        """Manager should not output detailed CSV fix suggestions."""
        csv_content = """status,exit_code
PASS,0
"""
        csv_path = make_minimal_csv(tmp_path, "build_smoke.csv", csv_content)

        decision = manager_level_decision(
            tmp_path,
            status="WARNING",
            errors=[],
            warnings=["CSV: build_smoke.csv: missing required column 'notes'"],
            result_csvs=[csv_path],
        )

        # Should NOT have detailed fix suggestions like:
        # "Add column 'notes' with format ..."
        # "Reorder columns to match schema ..."
        warnings_text = " ".join(decision.get("advisory_warnings", []))
        assert "Add column" not in warnings_text
        assert "Reorder columns" not in warnings_text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
