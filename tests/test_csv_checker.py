from pathlib import Path

from r2a.tools.csv_checker import check_csv_file, check_csv_tree
from r2a.tools.csv_schemas import allowed_values_for_csv, csv_header


def test_csv_checker_accepts_numeric_qps(tmp_path: Path) -> None:
    csv_path = tmp_path / "good.csv"
    csv_path.write_text("dataset,method,qps\nsift,hnsw,12.5\n", encoding="utf-8")

    issues = check_csv_file(csv_path)

    assert issues == []


def test_csv_checker_accepts_generic_csv_without_qps(tmp_path: Path) -> None:
    csv_path = tmp_path / "generic.csv"
    csv_path.write_text("dataset,method,recall\nsift,hnsw,0.9\n", encoding="utf-8")

    issues = check_csv_file(csv_path)

    assert issues == []


def test_csv_checker_warns_and_skips_non_numeric_qps_when_present(tmp_path: Path) -> None:
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("dataset,method,qps\nsift,hnsw,fast\n", encoding="utf-8")

    issues = check_csv_file(csv_path)

    assert any(issue.level == "warning" and "Column should be numeric" in issue.message and "qps" in issue.message for issue in issues)
    assert any(issue.level == "error" and "no valid data rows" in issue.message.lower() for issue in issues)


def test_csv_tree_skips_r2a_artifacts(tmp_path: Path) -> None:
    (tmp_path / ".r2a").mkdir()
    (tmp_path / ".r2a" / "artifact.csv").write_text("not,qps\nx,1\n", encoding="utf-8")
    (tmp_path / "result.csv").write_text("dataset,method,qps\nsift,hnsw,12\n", encoding="utf-8")

    report = check_csv_tree(tmp_path)

    assert report.passed
    assert len(report.checked_files) == 1


def test_csv_checker_accepts_reproduction_status_schema(tmp_path: Path) -> None:
    csv_path = tmp_path / "reproduction_status.csv"
    csv_path.write_text(
        "status,reason,evidence_source,next_action\nBLOCKED,no source,.r2a/PAPER_EVIDENCE.md,provide source\n",
        encoding="utf-8",
    )

    issues = check_csv_file(csv_path)

    assert issues == []


def test_csv_checker_accepts_source_verification_schema(tmp_path: Path) -> None:
    csv_path = tmp_path / "source_verification.csv"
    csv_path.write_text(
        "status,artifact_url,source_path,branch,commit,tag,readme_found,build_docs_found,experiment_scripts_found,data_scripts_found,notes\n"
        "OK,https://example.test/repo,repo,main,abc,,true,true,false,false,checked\n",
        encoding="utf-8",
    )

    issues = check_csv_file(csv_path)

    assert issues == []


def test_csv_checker_accepts_legacy_source_verification_schema(tmp_path: Path) -> None:
    csv_path = tmp_path / "source_verification.csv"
    csv_path.write_text(
        "artifact_url,access_status,branch,commit,license,readme_found,build_docs_found,navix_indicators,evidence_source,notes\n"
        "https://example.test/repo,OK,main,abc,MIT,true,true,true,README,checked\n",
        encoding="utf-8",
    )

    issues = check_csv_file(csv_path)

    assert issues == []


def test_csv_checker_accepts_dependency_setup_schema(tmp_path: Path) -> None:
    csv_path = tmp_path / "dependency_setup.csv"
    csv_path.write_text(
        "package,command,status,version,evidence_source,notes\n"
        "ninja,python -m pip install ninja,OK,1.13.0,pip show ninja,installed for reduced smoke\n",
        encoding="utf-8",
    )

    issues = check_csv_file(csv_path)

    assert issues == []


def test_csv_checker_accepts_feature_and_figure_verification_schemas(tmp_path: Path) -> None:
    feature_csv = tmp_path / "feature_localization.csv"
    feature_csv.write_text(
        "component,status,path,symbol_or_command,evidence_source,notes\n"
        "NaviX,FOUND,src/vector.cpp,navix,rg navix,located\n",
        encoding="utf-8",
    )
    figure_csv = tmp_path / "figure_table_verification.csv"
    figure_csv.write_text(
        "item,status,evidence_source,notes,next_action\n"
        "Table 1,VERIFIED,src/vector.cpp,strategies found,none\n",
        encoding="utf-8",
    )

    assert check_csv_file(feature_csv) == []
    assert check_csv_file(figure_csv) == []


def test_csv_checker_accepts_source_localization_without_metric_columns(tmp_path: Path) -> None:
    csv_path = tmp_path / "source_localization.csv"
    csv_path.write_text(
        "component,found,file_path,symbol_or_command,evidence_source,notes\n"
        "NaviX,true,src/vector.cpp,navix,rg navix,located\n",
        encoding="utf-8",
    )

    issues = check_csv_file(csv_path)

    assert issues == []


def test_csv_checker_accepts_runtime_and_input_contract_schemas(tmp_path: Path) -> None:
    runtime_csv = tmp_path / "runtime_smoke.csv"
    runtime_csv.write_text(
        "status,command,exit_code,duration_sec,component,evidence_source,notes\n"
        "PARTIAL,build/kuzu.exe --help,1,2.5,kuzu_cli,.r2a/logs/runtime.log,nanosleep64 loader error\n",
        encoding="utf-8",
    )
    input_csv = tmp_path / "input_contract_verification.csv"
    input_csv.write_text(
        "component,status,path_or_command,evidence_source,notes\n"
        "query_files,NEEDS_OFFICIAL_INPUT,not found,README,official query files missing\n",
        encoding="utf-8",
    )

    assert check_csv_file(runtime_csv) == []
    assert check_csv_file(input_csv) == []


def test_csv_checker_accepts_na_for_not_run_project_tests(tmp_path: Path) -> None:
    csv_path = tmp_path / "project_tests.csv"
    csv_path.write_text(
        "status,command,exit_code,duration_sec,test_scope,log_path,notes\n"
        "NO_TEST_COMMAND_FOUND,N/A,N/A,N/A,N/A,N/A,no test infrastructure discovered\n",
        encoding="utf-8",
    )

    assert check_csv_file(csv_path) == []


def test_csv_checker_warns_and_skips_non_numeric_project_test_exit_code(tmp_path: Path) -> None:
    csv_path = tmp_path / "project_tests.csv"
    csv_path.write_text(
        "status,command,exit_code,duration_sec,test_scope,log_path,notes\n"
        "FAILED,pytest,failed,1.2,unit,.r2a/logs/test.log,test failed\n",
        encoding="utf-8",
    )

    issues = check_csv_file(csv_path)

    assert any(issue.level == "warning" and "Column should be numeric" in issue.message and "exit_code" in issue.message for issue in issues)
    assert any(issue.level == "error" and "no valid data rows" in issue.message.lower() for issue in issues)


def test_csv_checker_accepts_reduced_demo_metrics_schema(tmp_path: Path) -> None:
    csv_path = tmp_path / "reduced_demo_metrics.csv"
    csv_path.write_text(
        "dataset,method,k,efs,selectivity,latency_ms,recall,query_count,ground_truth_source,input_level,result_level,notes\n"
        "synthetic_tiny,HNSW,10,40,0.1,3.2,1.0,5,bruteforce,SYNTHETIC_INPUT,DEMO_ONLY,NOT_PAPER_REPRODUCTION\n",
        encoding="utf-8",
    )

    assert check_csv_file(csv_path) == []


def test_csv_checker_accepts_command_manifest_with_input_provenance(tmp_path: Path) -> None:
    csv_path = tmp_path / "command_manifest.csv"
    csv_path.write_text(
        "command_id,command,exit_code,duration_sec,log_path,artifact_path,artifact_hash,input_provenance,notes\n"
        "cmd-1,python run.py,0,1.2,.r2a/logs/run.log,.r2a/results/reduced_metrics.csv,sha256:abc,official sample,ok\n",
        encoding="utf-8",
    )

    issues = check_csv_file(csv_path)

    assert all(issue.level == "warning" for issue in issues)
    assert any("recommended field missing" in issue.message for issue in issues)


def test_csv_checker_sanitizes_duplicate_header_metadata_and_bad_numeric_rows(tmp_path: Path) -> None:
    csv_path = tmp_path / "reduced_metrics.csv"
    csv_path.write_text(
        "command_id,dataset,method,k,recall,qps,notes\n"
        "command_id,dataset,method,k,recall,qps,notes\n"
        "# generated by previous tool\n"
        "cmd-bad,sift,hnsw,10,0.9,fast,bad numeric\n"
        "cmd-good,sift,hnsw,10,0.91,12.5,measured\n",
        encoding="utf-8",
    )

    issues = check_csv_file(csv_path)

    assert any("Skipped duplicate header row" in issue.message for issue in issues)
    assert any("Skipped metadata or explanatory row" in issue.message for issue in issues)
    assert any("Column should be numeric" in issue.message and "qps" in issue.message for issue in issues)
    assert not any(issue.level == "error" for issue in issues)


def test_csv_checker_accepts_paper_alignment_schema(tmp_path: Path) -> None:
    csv_path = tmp_path / "paper_alignment.csv"
    csv_path.write_text(
        "paper_item,setting_name,paper_setting,reduced_setting,match_status,evidence_source,notes\n"
        "Table 1,dataset scale,full,reduced,PARTIAL_MATCH,paper,scale differs\n",
        encoding="utf-8",
    )

    assert check_csv_file(csv_path) == []


def test_csv_schema_registry_defines_paper_alignment_match_status_values() -> None:
    assert allowed_values_for_csv("paper_alignment.csv", "match_status") == (
        "MATCH",
        "PARTIAL_MATCH",
        "MISMATCH",
        "NOT_AVAILABLE",
        "NEEDS_HUMAN_VERIFICATION",
    )


def test_csv_schema_registry_uses_reduced_setting_for_paper_alignment() -> None:
    assert csv_header("paper_alignment.csv") == (
        "paper_item,setting_name,paper_setting,reduced_setting,match_status,evidence_source,notes"
    )


def test_csv_checker_warns_and_maps_legacy_paper_alignment_alias(tmp_path: Path) -> None:
    csv_path = tmp_path / "paper_alignment.csv"
    csv_path.write_text(
        "paper_item,setting_name,paper_setting,verified_setting,match_status,evidence_source,notes\n"
        "Table 1,dataset scale,full,reduced,PARTIAL_MATCH,paper,legacy alias\n",
        encoding="utf-8",
    )

    issues = check_csv_file(csv_path)

    assert not any(issue.level == "error" and "reduced_setting" in issue.message for issue in issues)
    assert any(issue.level == "warning" and "verified_setting" in issue.message and "reduced_setting" in issue.message for issue in issues)


def test_csv_checker_warns_and_maps_legacy_partial_match_status(tmp_path: Path) -> None:
    csv_path = tmp_path / "paper_alignment.csv"
    csv_path.write_text(
        "paper_item,setting_name,paper_setting,reduced_setting,match_status,evidence_source,notes\n"
        "Table 1,dataset scale,full,reduced,PARTIAL,paper,legacy value\n",
        encoding="utf-8",
    )

    issues = check_csv_file(csv_path)

    assert any(issue.level == "warning" and "Invalid match_status" in issue.message and "PARTIAL" in issue.message for issue in issues)


def test_csv_checker_warns_and_maps_legacy_gap_match_status(tmp_path: Path) -> None:
    csv_path = tmp_path / "paper_alignment.csv"
    csv_path.write_text(
        "paper_item,setting_name,paper_setting,reduced_setting,match_status,evidence_source,notes\n"
        "Table 1,dataset scale,full,missing,GAP,paper,reduced setting unavailable\n",
        encoding="utf-8",
    )

    issues = check_csv_file(csv_path)

    assert any(issue.level == "warning" and "Legacy value `GAP`" in issue.message for issue in issues)
    assert not any(issue.level == "error" and "GAP" in issue.message for issue in issues)


def test_csv_checker_rejects_disallowed_paper_alignment_status_values(tmp_path: Path) -> None:
    csv_path = tmp_path / "paper_alignment.csv"
    csv_path.write_text(
        "paper_item,setting_name,paper_setting,reduced_setting,match_status,evidence_source,notes\n"
        "Table 1,scope,full,reduced,REDUCED,paper,belongs in notes\n"
        "Table 1,gap,full,missing,GAP,paper,belongs in notes\n"
        "Table 1,inference,full,unknown,INFERRED,paper,belongs in notes\n"
        "Table 1,input,full,downloaded,DOWNLOADED,paper,belongs in notes\n",
        encoding="utf-8",
    )

    issues = check_csv_file(csv_path)

    invalid = " ".join(issue.message for issue in issues if issue.level == "warning")
    for value in ("REDUCED", "INFERRED", "DOWNLOADED"):
        assert value in invalid
    assert "Legacy value `GAP`" in invalid


def test_csv_checker_warns_on_paper_alignment_metadata_rows(tmp_path: Path) -> None:
    csv_path = tmp_path / "paper_alignment.csv"
    csv_path.write_text(
        "paper_item,setting_name,paper_setting,reduced_setting,match_status,evidence_source,notes\n"
        "Generated at: 2026-06-14\n"
        "Table 1,dataset scale,full,reduced,PARTIAL_MATCH,paper,scale differs\n",
        encoding="utf-8",
    )

    issues = check_csv_file(csv_path)

    assert any(issue.level == "warning" and "Skipped metadata or explanatory row" in issue.message for issue in issues)


def test_csv_checker_rejects_invalid_paper_alignment_status(tmp_path: Path) -> None:
    csv_path = tmp_path / "paper_alignment.csv"
    csv_path.write_text(
        "paper_item,setting_name,paper_setting,reduced_setting,match_status,evidence_source,notes\n"
        "Table 1,dataset scale,full,reduced,ALMOST,paper,invalid\n",
        encoding="utf-8",
    )

    issues = check_csv_file(csv_path)

    assert any(issue.level == "warning" and "Invalid match_status" in issue.message for issue in issues)


def test_csv_checker_rejects_empty_required_paper_alignment_fields(tmp_path: Path) -> None:
    csv_path = tmp_path / "paper_alignment.csv"
    csv_path.write_text(
        "paper_item,setting_name,paper_setting,reduced_setting,match_status,evidence_source,notes\n"
        ",dataset scale,full,reduced,PARTIAL_MATCH,,missing fields\n",
        encoding="utf-8",
    )

    issues = check_csv_file(csv_path)

    assert any(issue.level == "warning" and "paper_item" in issue.message for issue in issues)
    assert any(issue.level == "warning" and "evidence_source" in issue.message for issue in issues)


def test_csv_checker_warns_when_paper_alignment_has_no_match_or_partial(tmp_path: Path) -> None:
    csv_path = tmp_path / "paper_alignment.csv"
    csv_path.write_text(
        "paper_item,setting_name,paper_setting,reduced_setting,match_status,evidence_source,notes\n"
        "Table 1,dataset scale,full,unknown,NOT_AVAILABLE,paper,unavailable\n"
        "Table 1,hardware,paper server,unknown,NEEDS_HUMAN_VERIFICATION,review,needs check\n",
        encoding="utf-8",
    )

    issues = check_csv_file(csv_path)

    assert any(issue.level == "warning" and "no MATCH or PARTIAL_MATCH" in issue.message for issue in issues)


def test_csv_checker_rejects_reduced_metrics_command_id_missing_from_manifest(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    (results / "reduced_metrics.csv").write_text(
        "command_id,dataset,method,k,notes\n"
        "missing-cmd,sample,method,10,measured\n",
        encoding="utf-8",
    )
    (results / "command_manifest.csv").write_text(
        "command_id,command,exit_code,duration_sec,log_path,artifact_path,artifact_hash,input_provenance,notes\n"
        "other-cmd,python run.py,0,1.2,run.log,.r2a/results/reduced_metrics.csv,sha256:abc,official,ok\n",
        encoding="utf-8",
    )

    report = check_csv_tree(tmp_path)

    assert any(issue.level == "error" and "missing-cmd" in issue.message for issue in report.issues)
