"""Tests for evidence recognition fixes - status normalization and command provenance."""
from __future__ import annotations

from pathlib import Path

from r2a.tools.evidence_levels import (
    is_success_status,
    normalize_status,
)


class TestStatusNormalization:
    """Test unified status normalization."""

    def test_supported_status_normalizes_to_pass(self) -> None:
        """SUPPORTED → PASS"""
        assert normalize_status("SUPPORTED") == "PASS"
        assert is_success_status("SUPPORTED")

    def test_generated_status_normalizes_to_pass(self) -> None:
        """GENERATED → PASS"""
        assert normalize_status("GENERATED") == "PASS"
        assert is_success_status("GENERATED")

    def test_built_status_normalizes_to_pass(self) -> None:
        """BUILT → PASS"""
        assert normalize_status("BUILT") == "PASS"
        assert is_success_status("BUILT")

    def test_present_status_normalizes_to_pass(self) -> None:
        """PRESENT → PASS"""
        assert normalize_status("PRESENT") == "PASS"
        assert is_success_status("PRESENT")

    def test_available_status_normalizes_to_pass(self) -> None:
        """AVAILABLE → PASS"""
        assert normalize_status("AVAILABLE") == "PASS"
        assert is_success_status("AVAILABLE")

    def test_not_run_status_normalizes_correctly(self) -> None:
        """NOT_RUN/SKIPPED/PENDING → NOT_RUN"""
        assert normalize_status("NOT_RUN") == "NOT_RUN"
        assert normalize_status("SKIPPED") == "NOT_RUN"
        assert normalize_status("PENDING") == "NOT_RUN"
        assert not is_success_status("NOT_RUN")
        assert not is_success_status("SKIPPED")

    def test_blocked_status_normalizes_to_fail(self) -> None:
        """BLOCKED → FAIL (not NOT_RUN)"""
        assert normalize_status("BLOCKED") == "FAIL"
        assert not is_success_status("BLOCKED")

    def test_case_insensitive_normalization(self) -> None:
        """Status normalization is case-insensitive."""
        assert normalize_status("supported") == "PASS"
        assert normalize_status("Supported") == "PASS"
        assert normalize_status("SUPPORTED") == "PASS"

    def test_empty_status_returns_empty(self) -> None:
        """Empty status returns empty string, not FAIL."""
        assert normalize_status("") == ""
        assert normalize_status(None) == ""


class TestInputContractWithSupportedStatus:
    """Test input contract recognition with SUPPORTED/GENERATED status."""

    def test_input_contract_with_supported_query_and_ground_truth(self, tmp_path: Path) -> None:
        """Input contract with SUPPORTED status should be recognized as ready."""
        from r2a.tools.evidence_levels import infer_evidence_level

        results = tmp_path / ".r2a" / "results"
        results.mkdir(parents=True)

        (results / "source_verification.csv").write_text(
            "status,artifact_url,source_path,branch,commit,tag,readme_found,build_docs_found,experiment_scripts_found,data_scripts_found,notes\n"
            "PASS,url,path,main,abc,,yes,yes,no,no,ok\n",
            encoding="utf-8",
        )
        (results / "build_smoke.csv").write_text(
            "status,command,exit_code,duration_sec,component,notes\nPASS,build,0,1,target,ok\n",
            encoding="utf-8",
        )
        (results / "input_contract_verification.csv").write_text(
            "component,status,path_or_command,evidence_source,notes\n"
            "dataset,GENERATED,../datasets/arxiv-for-fanns-medium,artifact,100k items\n"
            "query_vectors,SUPPORTED,../datasets/arxiv-for-fanns-medium/query_vectors.fvecs,read_fvecs,10k queries\n"
            "ground_truth_em,SUPPORTED,../datasets/arxiv-for-fanns-medium/gt_em.ivecs,read_ivecs,10k entries\n"
            "ground_truth_r,SUPPORTED,../datasets/arxiv-for-fanns-medium/gt_r.ivecs,read_ivecs,10k entries\n",
            encoding="utf-8",
        )

        # Should reach L2 with SUPPORTED/GENERATED status
        level = infer_evidence_level(tmp_path)
        assert level == "L2_input_contract_ready"


class TestL3WithDistributedEvidence:
    """Test L3 recognition with evidence distributed across multiple CSVs."""

    def test_l3_with_distributed_evidence_no_command_manifest(self, tmp_path: Path) -> None:
        """L3 should pass with distributed evidence, even without command_manifest.csv."""
        from r2a.tools.evidence_levels import infer_evidence_level

        results = tmp_path / ".r2a" / "results"
        logs = tmp_path / ".r2a" / "logs"
        results.mkdir(parents=True)
        logs.mkdir(parents=True)

        # Source and build verification
        (results / "source_verification.csv").write_text(
            "status,artifact_url,source_path,branch,commit,tag,readme_found,build_docs_found,experiment_scripts_found,data_scripts_found,notes\n"
            "PASS,url,path,main,abc,,yes,yes,no,no,ok\n",
            encoding="utf-8",
        )
        (results / "build_smoke.csv").write_text(
            "status,command,exit_code,duration_sec,component,notes\nPASS,build,0,1,target,ok\n",
            encoding="utf-8",
        )

        # Input contract with SUPPORTED status
        (results / "input_contract_verification.csv").write_text(
            "component,status,path_or_command,evidence_source,notes\n"
            "dataset,SUPPORTED,arxiv-for-fanns-medium,artifact,100k items\n"
            "query_vectors,SUPPORTED,query_vectors.fvecs,read_fvecs,10k queries\n"
            "ground_truth_em,SUPPORTED,gt_em.ivecs,read_ivecs,10k entries\n",
            encoding="utf-8",
        )

        # Reduced metrics WITHOUT ground_truth_source, metric_definition, input_provenance
        # But WITH actual measured values
        (results / "reduced_metrics.csv").write_text(
            "algorithm,filter_type,dataset,method,efs,k,M,M_beta,gamma,reps,recall_mean,recall_std,qps_mean,qps_std,notes\n"
            "ACORN,EM,arxiv-for-fanns-medium,direct,100,10,16,24,10,5,0.954,0.000,240.5,4.5,Paper parameters\n",
            encoding="utf-8",
        )

        # Runtime smoke with command and exit_code (alternative to command_manifest)
        (results / "runtime_smoke.csv").write_text(
            "status,command,exit_code,duration_sec,component,notes\n"
            "PASS,python reduced_benchmark.py,0,120,reduced,ok\n",
            encoding="utf-8",
        )

        # Engineer log
        (logs / "engineer_stdout.log").write_text(
            "Running: python reduced_benchmark.py\n"
            "Exit code: 0\n"
            "Success\n",
            encoding="utf-8",
        )

        # Should reach L3 with distributed evidence
        level = infer_evidence_level(tmp_path)
        assert level == "L3_official_reduced_run"


class TestCommandProvenance:
    """Test command provenance from multiple sources."""

    def test_command_provenance_from_runtime_csv(self, tmp_path: Path) -> None:
        """Runtime CSV with command + exit_code should satisfy command provenance."""
        from r2a.tools.evidence_levels import _has_command_provenance, _rows_from_named_csv, _result_csvs

        results = tmp_path / ".r2a" / "results"
        results.mkdir(parents=True)

        # Runtime smoke with successful command
        (results / "runtime_smoke.csv").write_text(
            "status,command,exit_code,duration_sec,component,notes\n"
            "PASS,python reduced_benchmark.py,0,120,reduced,ok\n",
            encoding="utf-8",
        )

        csvs = _result_csvs(tmp_path)
        rows_by_name = {"runtime_smoke.csv": _rows_from_named_csv(csvs, "runtime_smoke.csv")}

        # Should accept command provenance from runtime CSV
        assert _has_command_provenance(tmp_path, {}, rows_by_name, "reduced_metrics.csv")

    def test_command_provenance_from_build_smoke(self, tmp_path: Path) -> None:
        """Build smoke with successful command should NOT satisfy reduced metrics provenance.

        Build commands (cmake/make) cannot prove benchmark execution.
        This test validates that we don't accept unrelated commands.
        """
        from r2a.tools.evidence_levels import _has_command_provenance, _rows_from_named_csv, _result_csvs

        results = tmp_path / ".r2a" / "results"
        results.mkdir(parents=True)

        # Build command - should NOT prove reduced metrics
        (results / "build_smoke.csv").write_text(
            "status,command,exit_code,duration_sec,component,notes\n"
            "PASS,make -j4,0,60,build,ok\n",
            encoding="utf-8",
        )

        csvs = _result_csvs(tmp_path)
        rows_by_name = {"build_smoke.csv": _rows_from_named_csv(csvs, "build_smoke.csv")}

        # Should reject build commands for reduced metrics provenance
        assert not _has_command_provenance(tmp_path, {}, rows_by_name, "reduced_metrics.csv")

    def test_command_provenance_from_engineer_log(self, tmp_path: Path) -> None:
        """Engineer log with explicit command record should satisfy command provenance."""
        from r2a.tools.evidence_levels import _has_command_provenance

        logs = tmp_path / ".r2a" / "logs"
        logs.mkdir(parents=True)

        (logs / "engineer_stdout.log").write_text(
            "Running: python benchmark.py --filter EM\n"
            "Completed successfully\n"
            "Exit code: 0\n",
            encoding="utf-8",
        )

        assert _has_command_provenance(tmp_path, {}, {}, "reduced_metrics.csv")

    def test_command_provenance_rejects_empty_or_placeholder(self, tmp_path: Path) -> None:
        """Empty or placeholder commands should not satisfy provenance."""
        from r2a.tools.evidence_levels import _has_command_provenance

        # Empty runtime CSV
        results = tmp_path / ".r2a" / "results"
        results.mkdir(parents=True)
        (results / "runtime_smoke.csv").write_text(
            "status,command,exit_code,duration_sec,component,notes\n"
            "NOT_RUN,,0,0,,placeholder\n",
            encoding="utf-8",
        )

        # Should reject empty/placeholder commands
        assert not _has_command_provenance(tmp_path, {}, {}, "reduced_metrics.csv")


class TestRegressionWithActualRunData:
    """Regression tests using actual Run data patterns."""

    def test_actual_run_pattern_succeeds(self, tmp_path: Path) -> None:
        """Test with actual Run data pattern: SUPPORTED status, no command_manifest."""
        from r2a.tools.evidence_levels import infer_evidence_level

        results = tmp_path / ".r2a" / "results"
        logs = tmp_path / ".r2a" / "logs"
        results.mkdir(parents=True)
        logs.mkdir(parents=True)

        # Actual pattern from run_20260609_150408_86419fe2
        (results / "source_verification.csv").write_text(
            "status,artifact_url,source_path,branch,commit,tag,readme_found,build_docs_found,experiment_scripts_found,data_scripts_found,notes\n"
            "PASS,url,path,main,abc,,yes,yes,no,no,ok\n",
            encoding="utf-8",
        )
        (results / "build_smoke.csv").write_text(
            "status,command,exit_code,duration_sec,component,notes\nPASS,build,0,1,target,ok\n",
            encoding="utf-8",
        )
        (results / "input_contract_verification.csv").write_text(
            "component,status,path_or_command,evidence_source,notes\n"
            "database_vectors,SUPPORTED,../datasets/arxiv-for-fanns-medium/database_vectors.fvecs,read_fvecs,100000 items\n"
            "query_vectors,SUPPORTED,../datasets/arxiv-for-fanns-medium/query_vectors.fvecs,read_fvecs,10000 items\n"
            "ground_truth_em,SUPPORTED,../datasets/arxiv-for-fanns-medium/gt_em.ivecs,read_ivecs,10000 entries\n",
            encoding="utf-8",
        )
        (results / "reduced_metrics.csv").write_text(
            "algorithm,filter_type,dataset,method,efs,k,M,M_beta,gamma,reps,recall_mean,recall_std,qps_mean,qps_std,build_time_s,index_size_bytes,notes\n"
            "ACORN,EM,arxiv-for-fanns-medium,direct,100,10,16,24,10,5,0.954,0.000,240.5,4.5,102.9,1663130542,Paper parameters from Table 5\n",
            encoding="utf-8",
        )
        (results / "runtime_smoke.csv").write_text(
            "status,command,exit_code,duration_sec,component,notes\nPASS,python reduced_benchmark.py,0,120,reduced,ok\n",
            encoding="utf-8",
        )
        (results / "paper_alignment.csv").write_text(
            "paper_item,setting_name,paper_setting,reduced_setting,match_status,evidence_source,notes\n"
            "Table 5,dataset scale,1M,100k,PARTIAL_MATCH,paper,scale differs\n"
            "Table 5,hardware,GPU,CPU,PARTIAL_MATCH,paper,hardware differs\n"
            "Table 5,parameters,k=10,k=10,MATCH,command,parameters match\n"
            "Table 5,metric definition,Recall@10,Recall@10,MATCH,paper,metric match\n",
            encoding="utf-8",
        )

        # Engineer log
        (logs / "engineer_stdout.log").write_text(
            "Running ACORN reduced benchmark\n"
            "python reduced_benchmark.py --filter EM\n"
            "Exit code: 0\n"
            "Completed\n",
            encoding="utf-8",
        )

        # Should reach L3 (or L4 with paper alignment)
        level = infer_evidence_level(tmp_path)
        # At minimum L3, ideally L4
        assert level in ("L3_official_reduced_run", "L4_reduced_paper_aligned")


class TestNotRunStatusNotMistakenForPass:
    """Ensure NOT_RUN/SKIPPED/BLOCKED are never mistaken for PASS."""

    def test_not_run_not_success(self) -> None:
        """NOT_RUN should not be success."""
        assert not is_success_status("NOT_RUN")

    def test_skipped_not_success(self) -> None:
        """SKIPPED should not be success."""
        assert not is_success_status("SKIPPED")

    def test_blocked_not_success(self) -> None:
        """BLOCKED should not be success."""
        assert not is_success_status("BLOCKED")

    def test_pending_not_success(self) -> None:
        """PENDING should not be success."""
        assert not is_success_status("PENDING")
