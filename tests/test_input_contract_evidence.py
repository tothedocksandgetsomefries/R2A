from __future__ import annotations

import json
from pathlib import Path

from r2a.agents.manager_agent import run_manager_agent
from r2a.core.paths import ensure_artifact_dir, report_path
from r2a.core.state import make_initial_state
from r2a.tools.input_contract_evidence import (
    INPUT_CONTRACT_PASS_WITH_EMPTY_LOCAL_FILE,
    INPUT_CONTRACT_PASS_WITHOUT_COMMAND_PROVENANCE,
    INPUT_CONTRACT_PASS_WITHOUT_LOCAL_FILE,
    validate_official_input_pass_evidence,
)


def test_official_input_pass_missing_declared_file_reports_issue(tmp_path: Path) -> None:
    csv_path = _write_input_contract(
        tmp_path,
        "component,status,path_or_command,evidence_source,notes\n"
        "query_vectors,PASS,data/query_vectors.fvecs,hf_hub_download,claimed query vectors\n",
    )

    issues = validate_official_input_pass_evidence(tmp_path, csv_path)

    assert _codes(issues) == [INPUT_CONTRACT_PASS_WITHOUT_LOCAL_FILE]


def test_official_input_pass_empty_declared_file_reports_issue(tmp_path: Path) -> None:
    data_file = tmp_path / "data" / "query_vectors.fvecs"
    data_file.parent.mkdir(parents=True)
    data_file.write_bytes(b"")
    csv_path = _write_input_contract(
        tmp_path,
        "component,status,path_or_command,evidence_source,notes\n"
        "query_vectors,PASS,data/query_vectors.fvecs,hf_hub_download,claimed query vectors\n",
    )
    manifest = _write_manifest(
        tmp_path,
        "command_id,command,exit_code,duration_sec,log_path,artifact_path,artifact_hash,input_provenance,notes\n"
        "verify-query,python verify.py data/query_vectors.fvecs,0,1,,data/query_vectors.fvecs,,official query vectors verified\n",
    )

    issues = validate_official_input_pass_evidence(tmp_path, csv_path, command_manifest_csv=manifest)

    assert _codes(issues) == [INPUT_CONTRACT_PASS_WITH_EMPTY_LOCAL_FILE]


def test_official_input_pass_non_empty_file_without_provenance_reports_issue(tmp_path: Path) -> None:
    data_file = tmp_path / "data" / "query_vectors.fvecs"
    data_file.parent.mkdir(parents=True)
    data_file.write_bytes(b"\x04\x00\x00\x00abcd")
    csv_path = _write_input_contract(
        tmp_path,
        "component,status,path_or_command,evidence_source,notes\n"
        "query_vectors,PASS,data/query_vectors.fvecs,hf_hub_download,claimed query vectors\n",
    )

    issues = validate_official_input_pass_evidence(tmp_path, csv_path)

    assert _codes(issues) == [INPUT_CONTRACT_PASS_WITHOUT_COMMAND_PROVENANCE]


def test_official_input_pass_with_file_and_manifest_provenance_passes(tmp_path: Path) -> None:
    data_file = tmp_path / "data" / "query_vectors.fvecs"
    data_file.parent.mkdir(parents=True)
    data_file.write_bytes(b"\x04\x00\x00\x00abcd")
    csv_path = _write_input_contract(
        tmp_path,
        "component,status,path_or_command,evidence_source,notes\n"
        "query_vectors,PASS,data/query_vectors.fvecs,hf_hub_download,claimed query vectors\n",
    )
    manifest = _write_manifest(
        tmp_path,
        "command_id,command,exit_code,duration_sec,log_path,artifact_path,artifact_hash,input_provenance,notes\n"
        "download-query,hf_hub_download SPCL/arxiv-for-fanns-medium query_vectors.fvecs,0,5,,data/query_vectors.fvecs,,downloaded official query vectors\n",
    )

    issues = validate_official_input_pass_evidence(tmp_path, csv_path, command_manifest_csv=manifest)

    assert issues == []


def test_remote_dataset_id_and_benchmark_command_rows_are_not_local_file_checks(tmp_path: Path) -> None:
    csv_path = _write_input_contract(
        tmp_path,
        "component,status,path_or_command,evidence_source,notes\n"
        "dataset_medium,PASS,SPCL/arxiv-for-fanns-medium,hf_hub_download,100k database_vectors public dataset\n"
        "benchmark_cli,PASS,benchmark.py --help,source_inspection,usage command works\n",
    )

    issues = validate_official_input_pass_evidence(tmp_path, csv_path)

    assert issues == []


def test_non_pass_official_input_rows_do_not_report_fabricated_pass(tmp_path: Path) -> None:
    csv_path = _write_input_contract(
        tmp_path,
        "component,status,path_or_command,evidence_source,notes\n"
        "query_vectors,NEEDS_INPUT,data/query_vectors.fvecs,task,missing official query vectors\n"
        "database_vectors,UNKNOWN_NOT_EXECUTED,data/database_vectors.fvecs,task,not executed\n"
        "ground_truth,MISSING,data/ground_truth.ivecs,task,missing ground truth\n",
    )

    issues = validate_official_input_pass_evidence(tmp_path, csv_path)

    assert issues == []


def test_latest_run_fabricated_pass_scenario_is_blocked(tmp_path: Path) -> None:
    csv_path = _write_input_contract(
        tmp_path,
        "component,status,path_or_command,evidence_source,notes\n"
        "dataset_medium,PASS,SPCL/arxiv-for-fanns-medium,hf_hub_download,100k database_vectors public dataset\n"
        "query_vectors,PASS,query_vectors.fvecs,hf_hub_download,10000 queries. Non-empty. Valid .fvecs format.\n"
        "database_vectors,PASS,database_vectors.fvecs,hf_hub_download,100000 database items. Non-empty. Valid .fvecs format.\n"
        "ground_truth_em,PASS,ground_truth_em.ivecs,hf_hub_download,9995 records k=100. Non-empty. Valid .ivecs format.\n"
        "database_attributes,PASS,database_attributes.jsonl,hf_hub_download,100000 lines. Non-empty. Valid JSONL format.\n",
    )

    issues = validate_official_input_pass_evidence(tmp_path, csv_path)

    assert all(issue.code == INPUT_CONTRACT_PASS_WITHOUT_LOCAL_FILE for issue in issues)
    assert {issue.component for issue in issues} == {
        "query_vectors",
        "database_vectors",
        "ground_truth_em",
        "database_attributes",
    }


def test_manager_outputs_input_contract_evidence_diagnostic_without_formal_level(tmp_path: Path) -> None:
    artifact_dir = ensure_artifact_dir(tmp_path)
    results_dir = artifact_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n\n## Expected Outputs\n\n- .csv\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n\n- status: passed\n", encoding="utf-8")
    (results_dir / "ENGINEER_DONE.txt").write_text("PASS\n", encoding="utf-8")
    (results_dir / "input_contract_verification.csv").write_text(
        "component,status,path_or_command,evidence_source,notes\n"
        "query_vectors,PASS,query_vectors.fvecs,hf_hub_download,claimed local official query vectors\n",
        encoding="utf-8",
    )

    result = run_manager_agent(make_initial_state(tmp_path))
    decision = json.loads(Path(result["manager_decision_path"]).read_text(encoding="utf-8"))
    check_report = Path(result["check_report_path"]).read_text(encoding="utf-8")

    assert result["manager_status"] == "FAIL"
    assert any(
        INPUT_CONTRACT_PASS_WITHOUT_LOCAL_FILE in item
        for item in decision["blocking_errors"]
    )
    assert INPUT_CONTRACT_PASS_WITHOUT_LOCAL_FILE in check_report
    assert decision["artifact_invariant_diagnostics"][0]["code"] == INPUT_CONTRACT_PASS_WITHOUT_LOCAL_FILE
    assert "current_reproduction_level" not in decision
    assert "current_level_iteration" not in decision
    assert "level_source" not in decision


def _write_input_contract(repo: Path, text: str) -> Path:
    path = repo / ".r2a" / "results" / "input_contract_verification.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_manifest(repo: Path, text: str) -> Path:
    path = repo / ".r2a" / "results" / "command_manifest.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _codes(issues: list) -> list[str]:
    return [issue.code for issue in issues]
