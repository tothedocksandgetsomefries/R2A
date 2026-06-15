from pathlib import Path
import os
import subprocess

import pytest

from r2a.agents.manager_agent import run_manager_agent
from r2a.core.state import make_initial_state
from r2a.tools.git_provenance import read_git_provenance


def test_manager_accepts_readable_csv_without_schema_grading(tmp_path: Path) -> None:
    results_dir = tmp_path / ".r2a" / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "reproduction_status.csv").write_text("status\nFAIL\n", encoding="utf-8")
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    assert result["manager_passed"] is True
    assert result["manager_executed"] is True
    check_report = Path(result["check_report_path"])
    assert check_report.exists()
    text = check_report.read_text(encoding="utf-8").replace("\\", "/")
    assert "current output: .r2a/results/reproduction_status.csv" in text
    assert "Manager only checks if Engineer executed" in text


def test_manager_accepts_artifact_scoped_result_csv(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    artifact_dir.mkdir()
    (artifact_dir / "results").mkdir()
    (artifact_dir / "results" / "result.csv").write_text("dataset,method,qps\nsift,hnsw,42\n", encoding="utf-8")
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Goal\n\nRun test\n\n## Forbidden Files\n\n- .git/\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text("# EXECUTION_REPORT\n", encoding="utf-8")
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    assert result["manager_passed"] is True
    assert ".r2a/results" in text
    assert "result CSV count under .r2a/results/: 1" in text


def test_check_report_template_has_no_unrendered_placeholders(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "result.csv").write_text("dataset,method,qps\nsift,hnsw,42\n", encoding="utf-8")
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Goal\n\nRun smoke\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text("# EXECUTION_REPORT\n\nstatus: pass\n", encoding="utf-8")
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    assert "{{" not in text
    assert "}}" not in text
    assert "Engineer Execution Status" in text
    assert "Current Iteration Outputs" in text


def test_manager_fails_when_external_engineer_executor_failed(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "build_smoke.csv").write_text(
        "status,command,exit_code,duration_sec,component,notes\n"
        "BLOCKED,cmake configure,NA,0,cmake_configure,No CMakeLists.txt found.\n",
        encoding="utf-8",
    )
    (results_dir / "ENGINEER_DONE.txt").write_text("FAIL\n", encoding="utf-8")
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Expected Outputs\n\n- .csv\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text(
        "# EXECUTION_REPORT\n\n"
        "executor failed\n\n"
        "## Raw Executor Status\n\n"
        "- status: failed\n"
        "- exit_code: 1\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    assert result["manager_status"] == "FAIL"
    assert result["manager_passed"] is False
    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    assert "Engineer backend explicitly failed" in text


def test_manager_openclaw_review_writes_supplemental_review(tmp_path: Path, monkeypatch) -> None:
    artifact_dir = tmp_path / ".r2a"
    artifact_dir.mkdir()
    (artifact_dir / "results").mkdir()
    (artifact_dir / "results" / "result.csv").write_text("dataset,method,qps\nsynthetic,hnsw,1\n", encoding="utf-8")
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Goal\n\nRun smoke\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text("# EXECUTION_REPORT\n\nSmoke completed\n", encoding="utf-8")
    captured = {}

    def fake_openclaw(repo_path, stage, input_path, allowed_outputs, **kwargs):
        captured["stage"] = stage
        captured["input_path"] = Path(input_path)
        captured["input_text"] = Path(input_path).read_text(encoding="utf-8")
        captured["allowed_outputs"] = allowed_outputs
        output = Path(repo_path) / allowed_outputs[0]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            "# MANAGER_CODEX_REVIEW\n\n"
            "## Scope\n\nOpenClaw supplemental review.\n\n"
            "## Rule Authority\n\nCHECK_REPORT.md remains authoritative.\n",
            encoding="utf-8",
        )
        return {
            "stage": stage,
            "backend": "openclaw",
            "returncode": 0,
            "stdout_log_path": "",
            "stderr_log_path": "",
            "stdout_tail": "",
            "stderr_tail": "",
            "allowed_outputs": allowed_outputs,
            "success": True,
            "unexpected_modifications": [],
            "stage_guard_ok": True,
            "guard_available": True,
            "stage_guard_error": "",
            "stage_guard_warning": "",
            "stdout_json": True,
            "provider": "deepseek",
            "model": "deepseek-chat",
            "runner": "embedded",
            "fallbackUsed": False,
        }

    monkeypatch.setattr("r2a.agents.manager_agent.openclaw_stage_runner.run_openclaw_stage", fake_openclaw)
    state = make_initial_state(tmp_path, manager_backend="openclaw_review")

    result = run_manager_agent(state)

    assert captured["stage"] == "manager"
    assert captured["input_path"].name == "OPENCLAW_INPUT.md"
    assert "R2A Manager OpenClaw Stage" in captured["input_text"]
    assert "CHECK_REPORT.md remains authoritative" in captured["input_text"]
    assert captured["allowed_outputs"] == [".r2a/MANAGER_CODEX_REVIEW.md"]
    assert result["manager_passed"] is True
    assert Path(result["latest_manager_codex_review_path"]).exists()


def test_manager_ignores_site_packages_dataset_csv(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    site_packages = tmp_path / ".venv" / "Lib" / "site-packages" / "datasets"
    results_dir.mkdir(parents=True)
    site_packages.mkdir(parents=True)
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Expected Outputs\n\n- .csv\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text("# EXECUTION_REPORT\n", encoding="utf-8")
    (results_dir / "result.csv").write_text("dataset,method,qps\nsift,hnsw,42\n", encoding="utf-8")
    (site_packages / "builder.csv").write_text("status\nFAIL\n", encoding="utf-8")
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    assert result["manager_passed"] is True
    assert "builder.csv" not in text
    assert "result CSV count under .r2a/results/: 1" in text


def test_manager_lists_current_outputs_even_when_execution_manifest_is_present(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    results_dir.mkdir(parents=True)
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Expected Outputs\n\n- .csv\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text("# EXECUTION_REPORT\n", encoding="utf-8")
    (results_dir / "good.csv").write_text("dataset,method,qps\nsift,hnsw,42\n", encoding="utf-8")
    (results_dir / "bad.csv").write_text("status\nFAIL\n", encoding="utf-8")
    (results_dir / "execution_manifest.json").write_text('{"result_files":[".r2a/results/good.csv"]}\n', encoding="utf-8")
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    normalized = text.replace("\\", "/")
    assert result["manager_passed"] is True
    assert "current output: .r2a/results/good.csv" in normalized
    assert "current output: .r2a/results/bad.csv" in normalized
    assert "current output: .r2a/results/execution_manifest.json" in normalized


def test_manager_records_blocked_execution_artifacts_without_schema_grading(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    results_dir.mkdir(parents=True)
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Expected Outputs\n\n- .csv\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text("# EXECUTION_REPORT\n\nBlocked by API type mismatch.\n", encoding="utf-8")
    (results_dir / "ENGINEER_DONE.txt").write_text("BLOCKED\n", encoding="utf-8")
    (results_dir / "reproduction_status.csv").write_text(
        "status,reason,evidence_source,next_action\n"
        "BLOCKED,API type mismatch,.r2a/results/ENGINEER_NOTES.md,verify API semantics\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    assert result["manager_passed"] is True
    assert result["manager_status"] == "PASS"
    normalized = text.replace("\\", "/")
    assert "current output: .r2a/results/ENGINEER_DONE.txt" in normalized
    assert "current output: .r2a/results/reproduction_status.csv" in normalized


def test_manager_accepts_source_localization_csv_without_qps(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    results_dir.mkdir(parents=True)
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Expected Outputs\n\n- .csv\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text("# EXECUTION_REPORT\n\nLocated source.\n", encoding="utf-8")
    (results_dir / "source_localization.csv").write_text(
        "component,found,file_path,symbol_or_command,evidence_source,notes\n"
        "NaviX,true,src/vector.cpp,navix,rg navix,located\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    assert result["manager_passed"] is True
    assert "Missing required column: qps" not in text


def test_manager_accepts_timeout_report_when_current_output_exists(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    results_dir.mkdir(parents=True)
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Expected Outputs\n\n- .csv\n", encoding="utf-8")
    (artifact_dir / "EXPERIMENT_CONTRACT.md").write_text("# EXPERIMENT_CONTRACT\n\n## Contract Mode\n\nverification_only\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text("# EXECUTION_REPORT\n\nExecutor timed out after build evidence.\n", encoding="utf-8")
    (results_dir / "build_smoke.csv").write_text(
        "status,command,exit_code,duration_sec,component,notes\n"
        "OK,cmake --build build,0,30,kuzu,build succeeded\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    assert result["manager_passed"] is True
    assert result["manager_status"] == "PASS"
    assert "current output: .r2a\\results\\build_smoke.csv" in text


def test_manager_marks_stale_engineer_done_as_not_current_completion(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    results_dir.mkdir(parents=True)
    done = results_dir / "ENGINEER_DONE.txt"
    task = artifact_dir / "TASK_SPEC.md"
    done.write_text("PASS\n", encoding="utf-8")
    task.write_text("# TASK_SPEC\n\n## Expected Outputs\n\n- .csv\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text("# EXECUTION_REPORT\n", encoding="utf-8")
    (results_dir / "project_tests.csv").write_text(
        "status,command,exit_code,duration_sec,test_scope,log_path,notes\n"
        "NO_TESTS_FOUND,N/A,-1,N/A,N/A,N/A,no formal tests\n",
        encoding="utf-8",
    )
    os.utime(done, (1000, 1000))
    os.utime(task, (2000, 2000))
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    assert result["manager_passed"] is True
    assert "Stale outputs (previous iterations):" in text
    assert "current output: .r2a\\results\\project_tests.csv" in text


def test_manager_accepts_demo_only_result_as_readable_output(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    results_dir.mkdir(parents=True)
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Expected Outputs\n\n- .csv\n", encoding="utf-8")
    (artifact_dir / "EXPERIMENT_CONTRACT.md").write_text("# EXPERIMENT_CONTRACT\n\n## Contract Mode\n\nsmoke\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text("# EXECUTION_REPORT\n\nSynthetic demo completed.\n", encoding="utf-8")
    (results_dir / "ENGINEER_DONE.txt").write_text("DONE\n", encoding="utf-8")
    (results_dir / "reduced_demo_metrics.csv").write_text(
        "dataset,method,k,efs,selectivity,latency_ms,recall,query_count,ground_truth_source,input_level,result_level,notes\n"
        "synthetic_tiny,HNSW,10,40,0.1,3.2,1.0,5,bruteforce,SYNTHETIC_INPUT,DEMO_ONLY,NOT_PAPER_REPRODUCTION\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    assert result["manager_passed"] is True
    assert "current output: .r2a\\results\\reduced_demo_metrics.csv" in text


def test_manager_accepts_no_test_command_csv_as_readable_output(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    results_dir.mkdir(parents=True)
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Expected Outputs\n\n- .csv\n", encoding="utf-8")
    (artifact_dir / "EXPERIMENT_CONTRACT.md").write_text("# EXPERIMENT_CONTRACT\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text("# EXECUTION_REPORT\n", encoding="utf-8")
    (results_dir / "ENGINEER_DONE.txt").write_text("DONE\n", encoding="utf-8")
    (results_dir / "project_tests.csv").write_text(
        "status,command,exit_code,duration_sec,test_scope,log_path,notes\n"
        "NO_TEST_COMMAND_FOUND,N/A,-1,N/A,N/A,N/A,no formal tests\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    assert result["manager_passed"] is True
    assert result["manager_status"] == "PASS"
    assert "Project tests include a non-zero exit_code" not in text
    assert "current output: .r2a\\results\\project_tests.csv" in text


def test_manager_treats_no_tests_found_as_warning(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    results_dir.mkdir(parents=True)
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Expected Outputs\n\n- .csv\n", encoding="utf-8")
    (artifact_dir / "EXPERIMENT_CONTRACT.md").write_text("# EXPERIMENT_CONTRACT\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text("# EXECUTION_REPORT\n", encoding="utf-8")
    (results_dir / "ENGINEER_DONE.txt").write_text("DONE\n", encoding="utf-8")
    (results_dir / "project_tests.csv").write_text(
        "status,command,exit_code,duration_sec,test_scope,log_path,notes\n"
        "NO_TESTS_FOUND,N/A,-1,N/A,N/A,N/A,no tests directory\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    assert result["manager_passed"] is True
    assert "Project tests include a non-zero exit_code" not in text


def test_manager_treats_usage_help_as_non_blocking_project_test(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    results_dir.mkdir(parents=True)
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Expected Outputs\n\n- .csv\n", encoding="utf-8")
    (artifact_dir / "EXPERIMENT_CONTRACT.md").write_text("# EXPERIMENT_CONTRACT\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text("# EXECUTION_REPORT\n", encoding="utf-8")
    (results_dir / "ENGINEER_DONE.txt").write_text("PASS\n", encoding="utf-8")
    (results_dir / "project_tests.csv").write_text(
        "status,command,exit_code,duration_sec,test_scope,log_path,notes\n"
        "PASS_USAGE_HELP,./app --help,1,0.1,cli_usage,help.log,usage text shown as expected\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    assert result["manager_passed"] is True
    assert "Project tests failed according to project_tests.csv" not in text
    assert "Project tests include a non-zero exit_code" not in text


def test_manager_records_input_contract_artifacts_without_level_cap(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    results_dir.mkdir(parents=True)
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Expected Outputs\n\n- .csv\n", encoding="utf-8")
    (artifact_dir / "EXPERIMENT_CONTRACT.md").write_text(
        "# EXPERIMENT_CONTRACT\n\n## Contract Mode\n\nverification_only\n",
        encoding="utf-8",
    )
    (artifact_dir / "EXECUTION_REPORT.md").write_text(
        "# EXECUTION_REPORT\n\n## Raw Executor Status\n\n- status: passed\n- exit_code: 0\n",
        encoding="utf-8",
    )
    (results_dir / "ENGINEER_DONE.txt").write_text("PASS\n", encoding="utf-8")
    (results_dir / "project_tests.csv").write_text(
        "status,command,exit_code,duration_sec,test_scope,log_path,notes\n"
        "PASS,./test_l2_benchmark,0,0.01,unit,test.log,standalone binary passed\n"
        "NO_TEST_COMMAND_FOUND,ctest,-1,0,discovery,,no formal test command\n",
        encoding="utf-8",
    )
    (results_dir / "source_verification.csv").write_text(
        "status,artifact_url,branch,commit,readme_found,build_docs_found,notes\n"
        "FOUND,https://example.test/repo,main,abc123,yes,yes,source documented\n",
        encoding="utf-8",
    )
    (results_dir / "build_smoke.csv").write_text(
        "status,command,exit_code,duration_sec,component,notes\n"
        "PASS,cmake --build build,0,1.0,all,built\n",
        encoding="utf-8",
    )
    (results_dir / "runtime_smoke.csv").write_text(
        "status,command,exit_code,duration_sec,component,evidence_source,notes\n"
        "PASS,./app --help,0,0.1,app,app,usage shown\n",
        encoding="utf-8",
    )
    (results_dir / "input_contract_verification.csv").write_text(
        "component,status,path_or_command,evidence_source,notes\n"
        "dataset,NEEDS_INPUT,,task,official dataset requires approval\n"
        "query,NEEDS_INPUT,,task,official query requires approval\n"
        "ground_truth,NEEDS_INPUT,,task,ground truth requires approval\n"
        "metric,AVAILABLE,recall@10,paper,metric documented\n"
        "command,DOCUMENTED,./app --help,runtime_smoke.csv,command documented\n"
        "parameters,READY,k=10,paper,parameters documented\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    decision_text = Path(result["manager_decision_path"]).read_text(encoding="utf-8")
    assert result["manager_status"] == "PASS"
    assert result["manager_passed"] is True
    assert "Project tests failed according to project_tests.csv" not in text
    assert "current output: .r2a\\results\\input_contract_verification.csv" in text
    assert '"manager_stage_status": "PASS"' in decision_text


@pytest.mark.xfail(
    reason="Current Manager does not parse project_tests.csv status; Reviewer/evidence layers own formal outcome judgment.",
    strict=True,
)
def test_manager_still_fails_real_project_test_failure(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    results_dir.mkdir(parents=True)
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Expected Outputs\n\n- .csv\n", encoding="utf-8")
    (artifact_dir / "EXPERIMENT_CONTRACT.md").write_text("# EXPERIMENT_CONTRACT\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text("# EXECUTION_REPORT\n", encoding="utf-8")
    (results_dir / "ENGINEER_DONE.txt").write_text("PASS\n", encoding="utf-8")
    (results_dir / "project_tests.csv").write_text(
        "status,command,exit_code,duration_sec,test_scope,log_path,notes\n"
        "FAIL,pytest,1,0.2,unit,pytest.log,assertion failed\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    assert result["manager_status"] == "FAIL"
    assert result["manager_passed"] is False
    assert "Project tests failed according to project_tests.csv" in text


def test_manager_ignores_root_cmake_not_applicable_as_blocker(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    results_dir.mkdir(parents=True)
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Expected Outputs\n\n- .csv\n", encoding="utf-8")
    (artifact_dir / "EXPERIMENT_CONTRACT.md").write_text("# EXPERIMENT_CONTRACT\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text("# EXECUTION_REPORT\n", encoding="utf-8")
    (results_dir / "ENGINEER_DONE.txt").write_text("DONE\n", encoding="utf-8")
    (results_dir / "build_smoke.csv").write_text(
        "status,command,exit_code,duration_sec,component,notes\n"
        "BLOCKED,cmake configure,NA,0,cmake_configure,No CMakeLists.txt found in repo root; root CMake not required for Python repo.\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    assert result["manager_passed"] is True
    assert "current output: .r2a\\results\\build_smoke.csv" in text


def test_manager_does_not_promote_historical_planner_error_to_current_error(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    logs_dir = artifact_dir / "logs"
    results_dir.mkdir(parents=True)
    logs_dir.mkdir(parents=True)
    old_log = logs_dir / "planner_stderr.log"
    old_log.write_text("ERROR old planner failure\n", encoding="utf-8")
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Expected Outputs\n\n- .csv\n", encoding="utf-8")
    (artifact_dir / "EXPERIMENT_CONTRACT.md").write_text("# EXPERIMENT_CONTRACT\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text("# EXECUTION_REPORT\n", encoding="utf-8")
    (results_dir / "ENGINEER_DONE.txt").write_text("DONE\n", encoding="utf-8")
    (results_dir / "project_tests.csv").write_text(
        "status,command,exit_code,duration_sec,test_scope,log_path,notes\n"
        "NO_TESTS_FOUND,N/A,-1,N/A,N/A,N/A,no formal tests\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    assert result["manager_passed"] is True
    assert "ERROR old planner failure" not in "\n".join(result["errors"])
    assert "## Errors\n\n- None" in text


def test_git_provenance_reads_actual_checkout_head(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path / "artifact")

    provenance = read_git_provenance(repo)
    actual = _git(repo, "rev-parse", "HEAD")

    assert provenance is not None
    assert provenance.commit == actual
    assert provenance.origin == "https://example.test/demo.git"
    assert provenance.branch in {"main", "master"}


@pytest.mark.xfail(
    reason="Current Manager does not enforce source_verification.csv commit provenance; this is a follow-up product decision.",
    strict=True,
)
def test_manager_fails_verified_source_commit_mismatch(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    source_repo = _init_git_repo(artifact_dir / "artifacts" / "demo")
    actual = _git(source_repo, "rev-parse", "HEAD")
    wrong = "0" * 40 if actual != "0" * 40 else "1" * 40
    results_dir.mkdir(parents=True)
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Expected Outputs\n\n- .csv\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text("# EXECUTION_REPORT\n", encoding="utf-8")
    (results_dir / "source_verification.csv").write_text(
        "status,artifact_url,source_path,branch,commit,tag,readme_found,build_docs_found,experiment_scripts_found,data_scripts_found,notes\n"
        f"PASS,https://example.test/demo.git,.r2a/artifacts/demo,main,{wrong},,yes,yes,yes,yes,verified source\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path)

    result = run_manager_agent(state)

    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    assert result["manager_status"] == "FAIL"
    assert "source_verification.csv commit mismatch" in text
    assert actual in text


def _init_git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "r2a@example.test")
    _git(path, "config", "user.name", "R2A Test")
    _git(path, "remote", "add", "origin", "https://example.test/demo.git")
    (path / "README.md").write_text("# demo\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "init")
    if not _git(path, "rev-parse", "HEAD"):
        pytest.skip("git is unavailable for provenance tests")
    return path


def _git(path: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git is unavailable for provenance tests")
    return completed.stdout.strip()
