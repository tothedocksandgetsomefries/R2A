from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from r2a.cli import app
from r2a.tools.backend_errors import TOOL_CALL_PARSE_FAILURE
from r2a.tools.claude_health import (
    EXECUTABLE_NOT_FOUND,
    EXECUTABLE_VERSION_FAILED,
    MISSING_API_KEY,
    MISSING_REQUIRED_OUTPUT,
    FORBIDDEN_OUTPUT,
    allowed_tools_report,
    run_claude_health_check,
    run_engineer_noop_smoke,
    run_full_claude_smoke_workflow,
    run_planner_ccr_smoke,
)
from r2a.tools.codex_cli import CodexCliCheckResult


def _available_check(executable: str = "ccr.cmd") -> CodexCliCheckResult:
    return CodexCliCheckResult(True, executable, executable, "claude-code-router version: 2.0.0", "", "ok")


def test_claude_health_reports_executable_not_found(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        "r2a.tools.claude_health.check_claude_code_cli",
        lambda executable: CodexCliCheckResult(False, executable or "ccr", None, "", "FileNotFoundError: missing", "install ccr"),
    )

    report = run_claude_health_check(tmp_path, claude_executable_path="missing-ccr")

    assert report["failure_category"] == EXECUTABLE_NOT_FOUND
    assert report["version_check"] == "FAIL"


def test_claude_health_reports_version_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-api-key-placeholder")
    monkeypatch.setattr(
        "r2a.tools.claude_health.check_claude_code_cli",
        lambda executable: CodexCliCheckResult(False, executable or "ccr", "C:/bin/ccr.cmd", "bad", "`ccr version` exited with code 1.", "bad version"),
    )

    report = run_claude_health_check(tmp_path, claude_executable_path="ccr.cmd")

    assert report["failure_category"] == EXECUTABLE_VERSION_FAILED
    assert report["executable_exists"] is True


def test_claude_health_reports_missing_api_key(tmp_path: Path, monkeypatch) -> None:
    for name in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "DEEPSEEK_API_BASE", "OPENAI_API_KEY", "CCR_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("r2a.tools.claude_health.check_claude_code_cli", lambda executable: _available_check(executable or "ccr.cmd"))

    report = run_claude_health_check(tmp_path, claude_executable_path="ccr.cmd")

    assert report["failure_category"] == MISSING_API_KEY
    assert report["provider_env"]["provider_env_present"] is False


def test_non_engineer_allowed_tools_do_not_include_bash() -> None:
    report = allowed_tools_report()

    assert report["non_engineer_tools_contain_bash"] is False
    assert report["engineer_tools_contain_bash"] is True


def test_minimal_write_test_success(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-api-key-placeholder")
    monkeypatch.setattr("r2a.tools.claude_health.check_claude_code_cli", lambda executable: _available_check(executable or "ccr.cmd"))

    def fake_stage(repo, stage, prompt, allowed_outputs, **kwargs):
        output = Path(repo) / ".r2a" / "health" / "claude_healthcheck.txt"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("R2A_CLAUDE_HEALTHCHECK_OK\n", encoding="utf-8")
        return {
            "success": True,
            "returncode": 0,
            "prompt_file_path": str(Path(repo) / ".r2a" / "logs" / "claude_health_prompt.md"),
            "prompt_size_bytes": len(prompt),
            "allowed_tools": "Read,Write,Edit,MultiEdit",
            "stage_guard_ok": True,
        }

    monkeypatch.setattr("r2a.tools.claude_health.run_claude_stage", fake_stage)

    report = run_claude_health_check(tmp_path, claude_executable_path="ccr.cmd", run_write_test=True)

    assert report["minimal_write_test"]["status"] == "write_success"
    assert report["is_claude_ccr_usable_for_planner"] is True


def test_minimal_write_test_missing_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-api-key-placeholder")
    monkeypatch.setattr("r2a.tools.claude_health.check_claude_code_cli", lambda executable: _available_check(executable or "ccr.cmd"))
    monkeypatch.setattr(
        "r2a.tools.claude_health.run_claude_stage",
        lambda repo, stage, prompt, allowed_outputs, **kwargs: {"success": True, "returncode": 0, "stage_guard_ok": True},
    )

    report = run_claude_health_check(tmp_path, claude_executable_path="ccr.cmd", run_write_test=True)

    assert report["minimal_write_test"]["failure_category"] == MISSING_REQUIRED_OUTPUT
    assert report["failure_category"] == MISSING_REQUIRED_OUTPUT


def test_minimal_write_test_tool_call_parse_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-api-key-placeholder")
    monkeypatch.setattr("r2a.tools.claude_health.check_claude_code_cli", lambda executable: _available_check(executable or "ccr.cmd"))
    monkeypatch.setattr(
        "r2a.tools.claude_health.run_claude_stage",
        lambda repo, stage, prompt, allowed_outputs, **kwargs: {
            "success": False,
            "returncode": 1,
            "stage_guard_ok": True,
            "backend_failure_category": TOOL_CALL_PARSE_FAILURE,
            "backend_user_message": "Claude Code tool-call parse failure",
        },
    )

    report = run_claude_health_check(tmp_path, claude_executable_path="ccr.cmd", run_write_test=True)

    assert report["minimal_write_test"]["failure_category"] == TOOL_CALL_PARSE_FAILURE


def test_planner_ccr_smoke_diagnostic_fields(tmp_path: Path, monkeypatch) -> None:
    def fake_paper(state):
        return state

    def fake_planner(state):
        return {
            **state,
            "planner_transaction": {
                "committed": True,
                "diagnostic": {
                    "prompt_file": "prompt.md",
                    "prompt_size": 123,
                    "allowed_tools": "Read,Write,Edit,MultiEdit",
                    "staging_task_spec_written": True,
                    "staging_experiment_contract_written": True,
                    "planner_validation_passed": True,
                    "planner_committed": True,
                    "failure_category": "",
                    "failure_reason": "",
                    "is_claude_ccr_call_problem": False,
                },
            },
        }

    def fake_approval(state):
        return {**state, "approved": True}

    monkeypatch.setattr("r2a.tools.claude_health.run_paper_agent", fake_paper)
    monkeypatch.setattr("r2a.tools.claude_health.run_planner_agent", fake_planner)
    monkeypatch.setattr("r2a.tools.claude_health.human_approval_node", fake_approval)

    report = run_planner_ccr_smoke(tmp_path, claude_executable_path="ccr.cmd")

    assert report["prompt_file"] == "prompt.md"
    assert report["planner_validation_passed"] is True
    assert report["planner_committed"] is True
    assert report["approval_passed"] is True


def test_check_claude_cli_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "r2a.cli.run_claude_health_check",
        lambda *args, **kwargs: {
            "attempted_executable": "ccr.cmd",
            "executable_exists": True,
            "version_check": "PASS",
            "ccr_detected": True,
            "provider_env": {"provider_env_present": True, "env_vars_present": {"ANTHROPIC_API_KEY": "dum...lder"}},
            "non_engineer_tools_contain_bash": False,
            "engineer_tools_contain_bash": True,
            "minimal_write_test": {"status": "not run"},
            "planner_ccr_smoke": {"status": "not run"},
            "engineer_noop_smoke": {"status": "not run"},
            "is_claude_ccr_usable_for_planner": True,
            "is_claude_ccr_usable_for_engineer": True,
            "is_claude_ccr_usable_for_engineer_noop": True,
            "recommended_default_backend": "rules/template by default",
        },
    )

    result = CliRunner().invoke(app, ["check-claude", "--repo", str(tmp_path)])

    assert result.exit_code == 0
    assert "Claude executable: ccr.cmd" in result.output
    assert "Version check: PASS" in result.output
    assert "reduced_metrics.csv present: not run" in result.output


def test_engineer_noop_success_path(tmp_path: Path, monkeypatch) -> None:
    def fake_engineer(state):
        repo = Path(state["repo_path"])
        results = repo / ".r2a" / "results"
        logs = repo / ".r2a" / "logs"
        results.mkdir(parents=True, exist_ok=True)
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "claude_stdout.log").write_text("ccr engineer ok\n", encoding="utf-8")
        (logs / "claude_stderr.log").write_text("", encoding="utf-8")
        (logs / "claude_engineer_prompt.md").write_text("prompt", encoding="utf-8")
        _write_engineer_outputs(results)
        return {**state, "execution_report_path": str(repo / ".r2a" / "EXECUTION_REPORT.md")}

    monkeypatch.setattr("r2a.tools.claude_health.run_engineer_agent", fake_engineer)

    report = run_engineer_noop_smoke(tmp_path, claude_executable_path="ccr.cmd")

    assert report["status"] == "pass"
    assert report["validation_passed"] is True
    assert report["real_ccr_invoked"] is True
    assert report["engineer_done_written"] is True
    assert report["reduced_metrics_present"] is False


def test_engineer_noop_missing_done_fails(tmp_path: Path, monkeypatch) -> None:
    def fake_engineer(state):
        repo = Path(state["repo_path"])
        results = repo / ".r2a" / "results"
        logs = repo / ".r2a" / "logs"
        results.mkdir(parents=True, exist_ok=True)
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "claude_stdout.log").write_text("ccr engineer ok\n", encoding="utf-8")
        _write_engineer_outputs(results, done=False)
        return state

    monkeypatch.setattr("r2a.tools.claude_health.run_engineer_agent", fake_engineer)

    report = run_engineer_noop_smoke(tmp_path, claude_executable_path="ccr.cmd")

    assert report["validation_passed"] is False
    assert report["failure_category"] == MISSING_REQUIRED_OUTPUT
    assert "ENGINEER_DONE.txt" in report["failure_reason"]


def test_engineer_noop_missing_smoke_artifact_fails(tmp_path: Path, monkeypatch) -> None:
    def fake_engineer(state):
        repo = Path(state["repo_path"])
        results = repo / ".r2a" / "results"
        logs = repo / ".r2a" / "logs"
        results.mkdir(parents=True, exist_ok=True)
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "claude_stdout.log").write_text("ccr engineer ok\n", encoding="utf-8")
        _write_engineer_outputs(results, input_contract=False)
        return state

    monkeypatch.setattr("r2a.tools.claude_health.run_engineer_agent", fake_engineer)

    report = run_engineer_noop_smoke(tmp_path, claude_executable_path="ccr.cmd")

    assert report["validation_passed"] is False
    assert report["failure_category"] == MISSING_REQUIRED_OUTPUT
    assert "input_contract_verification.csv" in report["failure_reason"]


def test_engineer_noop_reduced_metrics_fails(tmp_path: Path, monkeypatch) -> None:
    def fake_engineer(state):
        repo = Path(state["repo_path"])
        results = repo / ".r2a" / "results"
        logs = repo / ".r2a" / "logs"
        results.mkdir(parents=True, exist_ok=True)
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "claude_stdout.log").write_text("ccr engineer ok\n", encoding="utf-8")
        _write_engineer_outputs(results)
        (results / "reduced_metrics.csv").write_text("command_id,dataset,method,k,notes\nx,y,z,1,forbidden\n", encoding="utf-8")
        return state

    monkeypatch.setattr("r2a.tools.claude_health.run_engineer_agent", fake_engineer)

    report = run_engineer_noop_smoke(tmp_path, claude_executable_path="ccr.cmd")

    assert report["validation_passed"] is False
    assert report["failure_category"] == FORBIDDEN_OUTPUT
    assert report["reduced_metrics_present"] is True


def test_engineer_noop_tool_call_parse_failure(tmp_path: Path, monkeypatch) -> None:
    def fake_engineer(state):
        repo = Path(state["repo_path"])
        logs = repo / ".r2a" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "claude_stdout.log").write_text("The model's tool call could not be parsed (retry also failed).", encoding="utf-8")
        (logs / "claude_stderr.log").write_text("", encoding="utf-8")
        return state

    monkeypatch.setattr("r2a.tools.claude_health.run_engineer_agent", fake_engineer)

    report = run_engineer_noop_smoke(tmp_path, claude_executable_path="ccr.cmd")

    assert report["validation_passed"] is False
    assert report["failure_category"] == TOOL_CALL_PARSE_FAILURE


def test_claude_health_runs_engineer_noop_option(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-api-key-placeholder")
    monkeypatch.setattr("r2a.tools.claude_health.check_claude_code_cli", lambda executable: _available_check(executable or "ccr.cmd"))
    monkeypatch.setattr(
        "r2a.tools.claude_health.run_engineer_noop_smoke",
        lambda *args, **kwargs: {
            "status": "pass",
            "validation_passed": True,
            "real_ccr_invoked": True,
            "engineer_done_written": True,
            "project_tests_written": True,
            "source_verification_written": True,
            "build_smoke_written": True,
            "input_contract_verification_written": True,
            "reduced_metrics_present": False,
            "reduced_metrics_json_present": False,
            "paper_alignment_present": False,
        },
    )

    report = run_claude_health_check(tmp_path, claude_executable_path="ccr.cmd", run_engineer_noop=True)

    assert report["engineer_noop_smoke"]["status"] == "pass"
    assert report["is_claude_ccr_usable_for_engineer_noop"] is True


def test_full_claude_smoke_mock_success(tmp_path: Path, monkeypatch) -> None:
    _patch_full_smoke_success(monkeypatch)

    report = run_full_claude_smoke_workflow(tmp_path, claude_executable_path="ccr.cmd")

    assert report["status"] == "pass"
    assert report["paper_claude"]["required_artifacts_written"] is True
    assert report["planner_claude"]["approval_passed"] is True
    assert report["planner_claude"]["engineer_noop_contract_overlay_applied"] is True
    assert report["engineer_claude"]["reduced_metrics_present"] is False
    assert report["reviewer_claude"]["committed"] is True
    assert report["final"]["current_level"] == "L2_input_contract_ready"
    assert report["final"]["false_l3_l4_claim"] is False


def test_full_claude_smoke_paper_missing_artifact_fails(tmp_path: Path, monkeypatch) -> None:
    def fake_paper(state):
        return state

    monkeypatch.setattr("r2a.tools.claude_health.run_paper_agent", fake_paper)

    report = run_full_claude_smoke_workflow(tmp_path, claude_executable_path="ccr.cmd")

    assert report["validation_passed"] is False
    assert report["failure_category"] == MISSING_REQUIRED_OUTPUT
    assert "paper_claude" in report["failure_reason"]


def test_full_claude_smoke_planner_missing_staging_fails(tmp_path: Path, monkeypatch) -> None:
    _patch_full_smoke_success(monkeypatch, planner_pass=False)

    report = run_full_claude_smoke_workflow(tmp_path, claude_executable_path="ccr.cmd")

    assert report["validation_passed"] is False
    assert report["failure_category"] == "PLANNER_MISSING_REQUIRED_OUTPUT"


def test_full_claude_smoke_engineer_reduced_metrics_fails(tmp_path: Path, monkeypatch) -> None:
    _patch_full_smoke_success(monkeypatch, engineer_reduced=True)

    report = run_full_claude_smoke_workflow(tmp_path, claude_executable_path="ccr.cmd")

    assert report["validation_passed"] is False
    assert report["failure_category"] == FORBIDDEN_OUTPUT


def test_full_claude_smoke_reviewer_malformed_feedback_safe_fails(tmp_path: Path, monkeypatch) -> None:
    _patch_full_smoke_success(monkeypatch, reviewer_pass=False)

    report = run_full_claude_smoke_workflow(tmp_path, claude_executable_path="ccr.cmd")

    assert report["validation_passed"] is False
    assert report["failure_category"] == "REVIEWER_FEEDBACK_VALIDATION_FAILED"


def test_full_claude_smoke_approval_rejected_skips_engineer(tmp_path: Path, monkeypatch) -> None:
    _patch_full_smoke_success(monkeypatch, approval=False)

    report = run_full_claude_smoke_workflow(tmp_path, claude_executable_path="ccr.cmd")

    assert report["validation_passed"] is False
    assert report["planner_claude"]["approval_passed"] is False
    assert "planner_claude" in report["failure_reason"]


def _write_engineer_outputs(
    results: Path,
    *,
    done: bool = True,
    project_tests: bool = True,
    source_verification: bool = True,
    build_smoke: bool = True,
    input_contract: bool = True,
) -> None:
    if done:
        (results / "ENGINEER_DONE.txt").write_text("DONE\nverification_only no-op\n", encoding="utf-8")
    if project_tests:
        (results / "project_tests.csv").write_text(
            "status,command,exit_code,duration_sec,test_scope,log_path,notes\n"
            "PASS,no-op,0,0,health,claude_stdout.log,verification only\n",
            encoding="utf-8",
        )
    if source_verification:
        (results / "source_verification.csv").write_text(
            "status,artifact_url,source_path,branch,commit,tag,readme_found,build_docs_found,experiment_scripts_found,data_scripts_found,notes\n"
            "PASS_WITH_LIMITATIONS,local,.,,,,,not_checked,not_checked,not_checked,not official\n",
            encoding="utf-8",
        )
    if build_smoke:
        (results / "build_smoke.csv").write_text(
            "status,command,exit_code,duration_sec,component,notes\n"
            "PASS,no-op,0,0,health,verification only\n",
            encoding="utf-8",
        )
    if input_contract:
        (results / "input_contract_verification.csv").write_text(
            "component,status,path_or_command,evidence_source,notes\n"
            "contract_mode,READY_WITH_GAPS,.r2a/EXPERIMENT_CONTRACT.md,health,verification_only\n",
            encoding="utf-8",
        )


def _patch_full_smoke_success(
    monkeypatch,
    *,
    planner_pass: bool = True,
    approval: bool = True,
    engineer_reduced: bool = False,
    reviewer_pass: bool = True,
) -> None:
    def fake_paper(state):
        repo = Path(state["repo_path"])
        _write_paper_outputs(repo)
        logs = repo / ".r2a" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "claude_paper_prompt.md").write_text("prompt", encoding="utf-8")
        return {
            **state,
            "paper_context_path": str(repo / ".r2a" / "PAPER_CONTEXT.md"),
            "paper_brief_path": str(repo / ".r2a" / "PAPER_BRIEF.md"),
        }

    def fake_planner(state):
        repo = Path(state["repo_path"])
        logs = repo / ".r2a" / "logs"
        staging = repo / ".r2a" / "staging" / "planner" / "iter_001" / "attempt_001"
        logs.mkdir(parents=True, exist_ok=True)
        staging.mkdir(parents=True, exist_ok=True)
        committed = bool(planner_pass)
        if planner_pass:
            (staging / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Goal\nnoop\n", encoding="utf-8")
            (staging / "EXPERIMENT_CONTRACT.md").write_text("# EXPERIMENT_CONTRACT\n\n## Contract Mode\n\nverification_only\n", encoding="utf-8")
            (repo / ".r2a" / "TASK_SPEC.md").write_text((staging / "TASK_SPEC.md").read_text(encoding="utf-8"), encoding="utf-8")
            (repo / ".r2a" / "EXPERIMENT_CONTRACT.md").write_text((staging / "EXPERIMENT_CONTRACT.md").read_text(encoding="utf-8"), encoding="utf-8")
        tx = {
            "committed": committed,
            "validation_status": "PASS" if planner_pass else "FAIL",
            "failure_category": "" if planner_pass else "PLANNER_MISSING_REQUIRED_OUTPUT",
            "issues": [] if planner_pass else ["Missing required planner candidate output: TASK_SPEC.md."],
            "diagnostic": {
                "staging_task_spec_written": planner_pass,
                "staging_experiment_contract_written": planner_pass,
                "planner_validation_passed": planner_pass,
                "planner_committed": planner_pass,
                "approval_passed": False,
                "failure_category": "" if planner_pass else "PLANNER_MISSING_REQUIRED_OUTPUT",
            },
        }
        (logs / "planner_transaction.json").write_text(json.dumps(tx), encoding="utf-8")
        return {**state, "planner_transaction": tx, "task_spec_path": str(repo / ".r2a" / "TASK_SPEC.md")}

    def fake_approval(state):
        tx = dict(state.get("planner_transaction", {}) or {})
        diag = dict(tx.get("diagnostic", {}) or {})
        diag["approval_passed"] = approval
        tx["diagnostic"] = diag
        repo = Path(state["repo_path"])
        (repo / ".r2a" / "logs" / "planner_transaction.json").write_text(json.dumps(tx), encoding="utf-8")
        return {**state, "approved": approval, "stopped": not approval, "planner_transaction": tx}

    def fake_engineer(state):
        repo = Path(state["repo_path"])
        results = repo / ".r2a" / "results"
        results.mkdir(parents=True, exist_ok=True)
        _write_engineer_outputs(results)
        if engineer_reduced:
            (results / "reduced_metrics.csv").write_text("command_id,dataset,method,k,notes\nx,y,z,1,bad\n", encoding="utf-8")
        return {**state, "execution_report_path": str(repo / ".r2a" / "EXECUTION_REPORT.md")}

    def fake_manager(state):
        repo = Path(state["repo_path"])
        (repo / ".r2a" / "CHECK_REPORT.md").write_text("# CHECK_REPORT\n\n## Status\n\nWARNING\n", encoding="utf-8")
        return {**state, "manager_status": "WARNING", "manager_passed": True, "check_report_path": str(repo / ".r2a" / "CHECK_REPORT.md")}

    def fake_reviewer(state):
        repo = Path(state["repo_path"])
        logs = repo / ".r2a" / "logs"
        staging = repo / ".r2a" / "staging" / "reviewer" / "iter_001" / "attempt_001"
        logs.mkdir(parents=True, exist_ok=True)
        staging.mkdir(parents=True, exist_ok=True)
        if reviewer_pass:
            (staging / "REVIEW_REPORT.md").write_text("# REVIEW_REPORT\n\n## Verdict\n\nPASS_SMOKE_ONLY\n", encoding="utf-8")
            (staging / "REVIEW_FEEDBACK.json").write_text('{"verdict":"PASS_SMOKE_ONLY","should_iterate":false}', encoding="utf-8")
            (repo / ".r2a" / "REVIEW_REPORT.md").write_text((staging / "REVIEW_REPORT.md").read_text(encoding="utf-8"), encoding="utf-8")
            (repo / ".r2a" / "REVIEW_FEEDBACK.json").write_text((staging / "REVIEW_FEEDBACK.json").read_text(encoding="utf-8"), encoding="utf-8")
        tx = {
            "staging_dir": str(staging),
            "committed": reviewer_pass,
            "validation_status": "PASS" if reviewer_pass else "FAIL",
            "failure_category": "" if reviewer_pass else "REVIEWER_FEEDBACK_VALIDATION_FAILED",
            "issues": [] if reviewer_pass else ["REVIEW_FEEDBACK.json is not valid JSON."],
        }
        (logs / "reviewer_transaction.json").write_text(json.dumps(tx), encoding="utf-8")
        return {**state, "reviewer_verdict": "PASS_SMOKE_ONLY" if reviewer_pass else "NEEDS_FIX"}

    def fake_archive(state):
        return {**state, "iteration_history": [{"iteration": 1, "reviewer_verdict": state.get("reviewer_verdict", "")}]}

    def fake_final(state):
        repo = Path(state["repo_path"])
        (repo / ".r2a" / "FINAL_REPORT.md").write_text(
            "# FINAL_REPORT\n\n"
            "## Reproduction Level\n\n"
            "- Current: L2: Input contract ready (L2_input_contract_ready)\n"
            "- Full Reproduction Claim: No. This is not a full reproduction.\n",
            encoding="utf-8",
        )
        return {**state, "final_report_path": str(repo / ".r2a" / "FINAL_REPORT.md"), "reproduction_level": "L2_input_contract_ready"}

    monkeypatch.setattr("r2a.tools.claude_health.run_paper_agent", fake_paper)
    monkeypatch.setattr("r2a.tools.claude_health.run_planner_agent", fake_planner)
    monkeypatch.setattr("r2a.tools.claude_health.human_approval_node", fake_approval)
    monkeypatch.setattr("r2a.tools.claude_health.run_engineer_agent", fake_engineer)
    monkeypatch.setattr("r2a.tools.claude_health.run_manager_agent", fake_manager)
    monkeypatch.setattr("r2a.tools.claude_health.run_reviewer_agent", fake_reviewer)
    monkeypatch.setattr("r2a.tools.claude_health.archive_current_iteration", fake_archive)
    monkeypatch.setattr("r2a.tools.claude_health.final_node", fake_final)


def _write_paper_outputs(repo: Path) -> None:
    r2a = repo / ".r2a"
    r2a.mkdir(parents=True, exist_ok=True)
    for name in (
        "PAPER_CONTEXT.md",
        "PAPER_BRIEF.md",
        "PAPER_EVIDENCE.md",
        "PAPER_REPRODUCTION_CARD.md",
        "PAPER_FIGURES_TABLES.md",
        "PAPER_PARSE_QUALITY.md",
    ):
        (r2a / name).write_text(f"# {name}\nmock\n", encoding="utf-8")
