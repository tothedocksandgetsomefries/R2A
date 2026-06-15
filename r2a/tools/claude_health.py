from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Mapping

from r2a.agents.engineer_agent import run_engineer_agent
from r2a.agents.manager_agent import run_manager_agent
from r2a.agents.paper_agent import run_paper_agent
from r2a.agents.planner_agent import run_planner_agent
from r2a.agents.reviewer_agent import run_reviewer_agent
from r2a.core.config import DEFAULT_CLAUDE_EXECUTABLE
from r2a.core.paths import artifact_dir, ensure_repo_dir, report_path
from r2a.core.state import make_initial_state
from r2a.tools.evidence_levels import infer_evidence_level
from r2a.tools.iteration import archive_current_iteration
from r2a.tools.backend_errors import TOOL_CALL_PARSE_FAILURE, classify_backend_error
from r2a.tools.claude_runner import _allowed_claude_tools, check_claude_code_cli
from r2a.tools.claude_stage_runner import CLAUDE_STAGE_ALLOWED_TOOLS, run_claude_stage
from r2a.tools.stage_env import DEFAULT_STAGE_API_KEY_ENV, build_stage_env
from r2a.workflow.nodes import final_node, human_approval_node

EXECUTABLE_NOT_FOUND = "EXECUTABLE_NOT_FOUND"
EXECUTABLE_VERSION_FAILED = "EXECUTABLE_VERSION_FAILED"
MISSING_API_KEY = "MISSING_API_KEY"
MISSING_PROVIDER_ENV = "MISSING_PROVIDER_ENV"
MISSING_REQUIRED_OUTPUT = "MISSING_REQUIRED_OUTPUT"
STALE_OUTPUT = "STALE_OUTPUT"
VALIDATION_FAILED = "VALIDATION_FAILED"
FORBIDDEN_OUTPUT = "FORBIDDEN_OUTPUT"
STAGE_BOUNDARY_VIOLATION = "STAGE_BOUNDARY_VIOLATION"
TOOL_ALLOWLIST_VIOLATION = "TOOL_ALLOWLIST_VIOLATION"
APPROVAL_REJECTED = "APPROVAL_REJECTED"
NOT_RUN = "not run"

HEALTHCHECK_TOKEN = "R2A_CLAUDE_HEALTHCHECK_OK"
PROVIDER_ENV_CANDIDATES = (
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_API_BASE",
    "OPENAI_API_KEY",
    "CCR_API_KEY",
)


def run_claude_health_check(
    repo_path: str | Path,
    *,
    claude_executable_path: str | None = None,
    run_write_test: bool = False,
    run_planner_smoke: bool = False,
    run_engineer_noop: bool = False,
    run_full_claude_smoke: bool = False,
    timeout: int = 120,
    stage_api_keys: Mapping[str, str] | None = None,
    stage_api_key_env_vars: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    repo = ensure_repo_dir(Path(repo_path))
    executable = claude_executable_path or DEFAULT_CLAUDE_EXECUTABLE
    cli_check = check_claude_code_cli(executable)
    env_report = provider_env_report(
        stage_api_keys=stage_api_keys,
        stage_api_key_env_vars=stage_api_key_env_vars,
    )
    tools_report = allowed_tools_report()

    checks: dict[str, Any] = {
        "claude_executable": executable,
        "attempted_executable": cli_check.attempted_executable,
        "resolved_path": cli_check.resolved_path or "",
        "executable_exists": bool(cli_check.resolved_path or cli_check.available),
        "version_check": "PASS" if cli_check.available else "FAIL",
        "version_output": cli_check.version_output,
        "ccr_detected": _is_ccr(cli_check.attempted_executable),
        "provider_env": env_report,
        "non_engineer_tools": tools_report["non_engineer_tools"],
        "engineer_tools_summary": tools_report["engineer_tools_summary"],
        "non_engineer_tools_contain_bash": tools_report["non_engineer_tools_contain_bash"],
        "engineer_tools_contain_bash": tools_report["engineer_tools_contain_bash"],
        "minimal_write_test": {"status": NOT_RUN},
        "planner_ccr_smoke": {"status": NOT_RUN},
        "engineer_noop_smoke": {"status": NOT_RUN},
        "full_claude_smoke": {"status": NOT_RUN},
        "failure_category": "",
        "failure_reason": "",
        "is_claude_ccr_usable_for_planner": False,
        "is_claude_ccr_usable_for_engineer": bool(cli_check.available and tools_report["engineer_tools_contain_bash"]),
        "recommended_default_backend": "Paper/Planner/Manager/Reviewer rules/template; Engineer claude only when explicitly selected.",
    }

    failures: list[tuple[str, str]] = []
    if not cli_check.available:
        failures.append((_classify_cli_failure(cli_check.error), cli_check.error))
    if not env_report["provider_env_present"]:
        failures.append((MISSING_API_KEY, "No Claude/CCR provider API key env var was detected in the current process."))
    if tools_report["non_engineer_tools_contain_bash"]:
        failures.append((TOOL_ALLOWLIST_VIOLATION, "Non-Engineer Claude stage tools include Bash."))

    if run_write_test:
        write_result = run_minimal_write_test(
            repo,
            claude_executable_path=executable,
            timeout=timeout,
            stage_api_keys=stage_api_keys,
            stage_api_key_env_vars=stage_api_key_env_vars,
        )
        checks["minimal_write_test"] = write_result
        if write_result.get("status") != "write_success":
            failures.append((str(write_result.get("failure_category") or VALIDATION_FAILED), str(write_result.get("failure_reason") or "Minimal write test failed.")))

    if run_planner_smoke:
        planner_result = run_planner_ccr_smoke(
            repo,
            claude_executable_path=executable,
            timeout=timeout,
            stage_api_keys=stage_api_keys,
            stage_api_key_env_vars=stage_api_key_env_vars,
        )
        checks["planner_ccr_smoke"] = planner_result
        if not planner_result.get("approval_passed"):
            failures.append((str(planner_result.get("failure_category") or VALIDATION_FAILED), str(planner_result.get("failure_reason") or "Planner CCR smoke failed.")))

    if run_engineer_noop:
        engineer_result = run_engineer_noop_smoke(
            repo,
            claude_executable_path=executable,
            timeout=timeout,
            stage_api_keys=stage_api_keys,
            stage_api_key_env_vars=stage_api_key_env_vars,
        )
        checks["engineer_noop_smoke"] = engineer_result
        if not engineer_result.get("validation_passed"):
            failures.append((str(engineer_result.get("failure_category") or VALIDATION_FAILED), str(engineer_result.get("failure_reason") or "Engineer no-op smoke failed.")))

    if run_full_claude_smoke:
        full_result = run_full_claude_smoke_workflow(
            repo,
            claude_executable_path=executable,
            timeout=timeout,
            stage_api_keys=stage_api_keys,
            stage_api_key_env_vars=stage_api_key_env_vars,
        )
        checks["full_claude_smoke"] = full_result
        if not full_result.get("validation_passed"):
            failures.append((str(full_result.get("failure_category") or VALIDATION_FAILED), str(full_result.get("failure_reason") or "Full Claude smoke failed.")))

    if failures:
        checks["failure_category"] = failures[0][0]
        checks["failure_reason"] = failures[0][1]

    checks["is_claude_ccr_usable_for_planner"] = bool(
        cli_check.available
        and not tools_report["non_engineer_tools_contain_bash"]
        and (not run_write_test or checks["minimal_write_test"].get("status") == "write_success")
        and (not run_planner_smoke or checks["planner_ccr_smoke"].get("approval_passed"))
    )
    checks["is_claude_ccr_usable_for_engineer_noop"] = bool(
        run_engineer_noop
        and cli_check.available
        and tools_report["engineer_tools_contain_bash"]
        and checks["engineer_noop_smoke"].get("validation_passed")
    )
    return checks


def provider_env_report(
    *,
    stage_api_keys: Mapping[str, str] | None = None,
    stage_api_key_env_vars: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    stage_env = build_stage_env(
        stage="planner",
        backend="claude",
        stage_api_keys=stage_api_keys,
        stage_api_key_env_vars=stage_api_key_env_vars,
    )
    env_sources: dict[str, str] = {}
    for name in PROVIDER_ENV_CANDIDATES:
        value = (stage_env or os.environ).get(name, "") if stage_env else os.environ.get(name, "")
        if value:
            env_sources[name] = _mask_secret(value)
    injected_var = (stage_api_key_env_vars or {}).get("planner", "") or DEFAULT_STAGE_API_KEY_ENV.get("claude", "")
    return {
        "stage_env_injection_supported": injected_var or "",
        "provider_env_present": bool(env_sources),
        "env_vars_present": env_sources,
        "missing_category": "" if env_sources else MISSING_API_KEY,
    }


def allowed_tools_report() -> dict[str, Any]:
    non_engineer_tools = _split_tools(CLAUDE_STAGE_ALLOWED_TOOLS)
    engineer_tools = _split_tools(_allowed_claude_tools())
    return {
        "non_engineer_tools": non_engineer_tools,
        "engineer_tools_summary": {
            "count": len(engineer_tools),
            "contains_bash": any(_is_bash_tool(tool) for tool in engineer_tools),
            "sample": engineer_tools[:12],
        },
        "non_engineer_tools_contain_bash": any(_is_bash_tool(tool) for tool in non_engineer_tools),
        "engineer_tools_contain_bash": any(_is_bash_tool(tool) for tool in engineer_tools),
    }


def run_minimal_write_test(
    repo: str | Path,
    *,
    claude_executable_path: str | None = None,
    timeout: int = 120,
    stage_api_keys: Mapping[str, str] | None = None,
    stage_api_key_env_vars: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    repo_path = ensure_repo_dir(Path(repo))
    _init_health_git_repo(repo_path)
    output = artifact_dir(repo_path) / "health" / "claude_healthcheck.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    started_at = time.time()
    env = build_stage_env(
        stage="planner",
        backend="claude",
        stage_api_keys=stage_api_keys,
        stage_api_key_env_vars=stage_api_key_env_vars,
    )
    result = run_claude_stage(
        repo_path,
        "health",
        (
            "This is an R2A Claude/CCR health check. Do not inspect the wider repository. "
            f"Write exactly `{HEALTHCHECK_TOKEN}` plus one newline to `.r2a/health/claude_healthcheck.txt` and stop."
        ),
        [".r2a/health/claude_healthcheck.txt"],
        iteration=None,
        timeout=timeout,
        claude_executable_path=claude_executable_path,
        language="en",
        env=env,
    )
    prompt_file = result.get("prompt_file_path", "")
    report: dict[str, Any] = {
        "status": "write_failed",
        "prompt_file": prompt_file,
        "prompt_size": result.get("prompt_size_bytes", 0),
        "allowed_tools": result.get("allowed_tools", CLAUDE_STAGE_ALLOWED_TOOLS),
        "output_path": str(output),
        "fresh": False,
        "failure_category": "",
        "failure_reason": "",
        "backend_failure_category": result.get("backend_failure_category", ""),
        "returncode": result.get("returncode", 0),
    }
    if not output.exists():
        report.update(_stage_failure(result, MISSING_REQUIRED_OUTPUT, "Claude/CCR did not write .r2a/health/claude_healthcheck.txt."))
        return report
    stat = output.stat()
    report["fresh"] = stat.st_mtime + 0.001 >= started_at
    if not report["fresh"]:
        report.update(_stage_failure(result, STALE_OUTPUT, "Health check output existed but was stale."))
        return report
    content = output.read_text(encoding="utf-8", errors="replace").strip()
    if content != HEALTHCHECK_TOKEN:
        report.update(_stage_failure(result, VALIDATION_FAILED, f"Health check output content did not match expected token: {content!r}."))
        return report
    if not result.get("success"):
        report.update(_stage_failure(result, str(result.get("backend_failure_category") or VALIDATION_FAILED), str(result.get("error") or "Claude stage returned failure.")))
        return report
    report["status"] = "write_success"
    return report


def run_planner_ccr_smoke(
    repo: str | Path,
    *,
    claude_executable_path: str | None = None,
    timeout: int = 180,
    stage_api_keys: Mapping[str, str] | None = None,
    stage_api_key_env_vars: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    base = ensure_repo_dir(Path(repo))
    smoke_root = Path(tempfile.mkdtemp(prefix="planner_ccr_smoke_", dir=str(base)))
    _init_health_git_repo(smoke_root)
    paper = smoke_root / "mock_paper.txt"
    paper.write_text(
        "Mock paper for Planner CCR smoke only. verification_only, no downloads, no experiments, no L3/L4 claim.\n",
        encoding="utf-8",
    )
    state = make_initial_state(
        smoke_root,
        workspace_dir=smoke_root,
        paper_path=paper,
        goal="Planner CCR smoke only: produce verification_only TASK_SPEC and EXPERIMENT_CONTRACT; no Engineer run.",
        paper_backend="preprocess",
        planner_backend="claude",
        claude_executable_path=claude_executable_path or DEFAULT_CLAUDE_EXECUTABLE,
        codex_stage_timeout=timeout,
        timeout=timeout,
        auto_approve=True,
        allow_official_dataset_download=False,
        allow_full_benchmark=False,
        stage_api_keys=dict(stage_api_keys or {}),
        stage_api_key_env_vars=dict(stage_api_key_env_vars or {}),
    )
    try:
        planned = run_planner_agent(run_paper_agent(state))
        approved = human_approval_node(planned)
    except Exception as exc:
        return {
            "status": "failed",
            "workspace": str(smoke_root),
            "approval_passed": False,
            "failure_category": VALIDATION_FAILED,
            "failure_reason": str(exc),
        }
    transaction = approved.get("planner_transaction", {}) or {}
    diagnostic = dict(transaction.get("diagnostic", {}) or {})
    return {
        "status": "pass" if bool(approved.get("approved")) and bool(transaction.get("committed")) else "failed",
        "workspace": str(smoke_root),
        "prompt_file": diagnostic.get("prompt_file", ""),
        "prompt_size": diagnostic.get("prompt_size", 0),
        "allowed_tools": diagnostic.get("allowed_tools", ""),
        "staging_task_spec_written": diagnostic.get("staging_task_spec_written"),
        "staging_experiment_contract_written": diagnostic.get("staging_experiment_contract_written"),
        "planner_validation_passed": diagnostic.get("planner_validation_passed"),
        "planner_committed": diagnostic.get("planner_committed", transaction.get("committed")),
        "approval_passed": bool(approved.get("approved")),
        "failure_category": diagnostic.get("failure_category", transaction.get("failure_category", "")),
        "failure_reason": diagnostic.get("failure_reason", ""),
        "is_claude_ccr_call_problem": diagnostic.get("is_claude_ccr_call_problem", False),
    }


def run_engineer_noop_smoke(
    repo: str | Path,
    *,
    claude_executable_path: str | None = None,
    timeout: int = 180,
    stage_api_keys: Mapping[str, str] | None = None,
    stage_api_key_env_vars: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    base = ensure_repo_dir(Path(repo))
    smoke_root = Path(tempfile.mkdtemp(prefix="engineer_noop_smoke_", dir=str(base)))
    r2a_dir = artifact_dir(smoke_root)
    results_dir = r2a_dir / "results"
    r2a_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    _write_engineer_noop_inputs(smoke_root)
    _init_health_git_repo(smoke_root)
    started_at = time.time()
    state = make_initial_state(
        smoke_root,
        workspace_dir=smoke_root,
        goal="Claude Engineer no-op health check only. verification_only; no downloads, no training, no real experiments, no L3/L4 claim.",
        executor="claude",
        engineer_executor="claude",
        claude_executable_path=claude_executable_path or DEFAULT_CLAUDE_EXECUTABLE,
        codex_stage_timeout=timeout,
        timeout=timeout,
        approved=True,
        auto_approve=True,
        allow_official_dataset_download=False,
        allow_full_benchmark=False,
        allow_external_baselines=False,
        stage_api_keys=dict(stage_api_keys or {}),
        stage_api_key_env_vars=dict(stage_api_key_env_vars or {}),
    )
    state["task_spec_path"] = str(r2a_dir / "TASK_SPEC.md")
    report: dict[str, Any] = {
        "status": "failed",
        "workspace": str(smoke_root),
        "real_ccr_invoked": False,
        "engineer_done_written": False,
        "project_tests_written": False,
        "source_verification_written": False,
        "build_smoke_written": False,
        "input_contract_verification_written": False,
        "reduced_metrics_present": False,
        "reduced_metrics_json_present": False,
        "paper_alignment_present": False,
        "validation_passed": False,
        "failure_category": "",
        "failure_reason": "",
        "stdout_log": str(r2a_dir / "logs" / "claude_stdout.log"),
        "stderr_log": str(r2a_dir / "logs" / "claude_stderr.log"),
        "prompt_file": str(r2a_dir / "logs" / "claude_engineer_prompt.md"),
    }
    try:
        run_engineer_agent(state)
    except Exception as exc:
        report.update({"failure_category": VALIDATION_FAILED, "failure_reason": str(exc)})
        return report
    validation = validate_engineer_noop_outputs(smoke_root, started_at)
    report.update(validation)
    stdout_text = _read_text(r2a_dir / "logs" / "claude_stdout.log")
    stderr_text = _read_text(r2a_dir / "logs" / "claude_stderr.log")
    report["real_ccr_invoked"] = bool(stdout_text or stderr_text or (r2a_dir / "logs" / "claude_engineer_prompt.md").exists())
    backend_error = classify_backend_error(stdout_text, stderr_text, backend="claude")
    if backend_error.get("failure_category") == TOOL_CALL_PARSE_FAILURE:
        report["failure_category"] = TOOL_CALL_PARSE_FAILURE
        report["failure_reason"] = str(backend_error.get("user_message") or "Claude Code tool-call parse failure.")
        report["validation_passed"] = False
    if not report["failure_category"] and not report["validation_passed"]:
        report["failure_category"] = validation.get("failure_category") or VALIDATION_FAILED
        report["failure_reason"] = validation.get("failure_reason") or "Engineer no-op validation failed."
    if report["validation_passed"]:
        report["status"] = "pass"
    return report


def run_full_claude_smoke_workflow(
    repo: str | Path,
    *,
    claude_executable_path: str | None = None,
    timeout: int = 240,
    stage_api_keys: Mapping[str, str] | None = None,
    stage_api_key_env_vars: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    base = ensure_repo_dir(Path(repo))
    smoke_root = Path(tempfile.mkdtemp(prefix="full_claude_smoke_", dir=str(base)))
    r2a_dir = artifact_dir(smoke_root)
    r2a_dir.mkdir(parents=True, exist_ok=True)
    paper = smoke_root / "mock_paper.txt"
    paper.write_text(
        "Mock paper for full Claude/CCR smoke only.\n"
        "This is verification_only/no-op workflow plumbing. No official data, no downloads, no training, no real experiments, no L3/L4 claim.\n",
        encoding="utf-8",
    )
    _init_health_git_repo(smoke_root)
    state = make_initial_state(
        smoke_root,
        workspace_dir=smoke_root,
        paper_path=paper,
        goal="Full Claude/CCR smoke only: verification_only/no-op, no downloads, no real experiments, no reduced metrics, no L3/L4 claim.",
        extra_context="Health check only. Contract mode must remain verification_only and capped at L2.",
        language="en",
        paper_backend="claude_reader",
        planner_backend="claude",
        executor="claude",
        engineer_executor="claude",
        manager_backend="rules",
        reviewer_backend="claude",
        claude_executable_path=claude_executable_path or DEFAULT_CLAUDE_EXECUTABLE,
        codex_stage_timeout=timeout,
        timeout=timeout,
        approved=True,
        auto_approve=True,
        auto_iterate=False,
        max_iterations=1,
        target_reproduction_level="L2_input_contract_ready",
        download_budget_gb=0,
        allow_official_dataset_download=False,
        allow_full_benchmark=False,
        allow_external_baselines=False,
        stage_api_keys=dict(stage_api_keys or {}),
        stage_api_key_env_vars=dict(stage_api_key_env_vars or {}),
    )
    report: dict[str, Any] = {
        "status": "failed",
        "workspace": str(smoke_root),
        "paper_claude": {"status": NOT_RUN},
        "planner_claude": {"status": NOT_RUN},
        "engineer_claude": {"status": NOT_RUN},
        "manager": {"status": NOT_RUN, "backend": "rules"},
        "reviewer_claude": {"status": NOT_RUN},
        "final": {"status": NOT_RUN},
        "validation_passed": False,
        "failure_category": "",
        "failure_reason": "",
    }
    try:
        state = run_paper_agent(state)
        report["paper_claude"] = _paper_smoke_diagnostics(smoke_root, state)
        if not report["paper_claude"].get("validation_passed"):
            return _full_smoke_fail(report, "paper_claude")

        state = run_planner_agent(state)
        state = human_approval_node(state)
        report["planner_claude"] = _planner_smoke_diagnostics(smoke_root, state)
        if not report["planner_claude"].get("validation_passed"):
            return _full_smoke_fail(report, "planner_claude")
        _write_engineer_noop_inputs(smoke_root)
        report["planner_claude"]["engineer_noop_contract_overlay_applied"] = True

        engineer_started_at = time.time()
        state = run_engineer_agent(state)
        report["engineer_claude"] = validate_engineer_noop_outputs(smoke_root, engineer_started_at)
        report["engineer_claude"]["status"] = "pass" if report["engineer_claude"].get("validation_passed") else "failed"
        if not report["engineer_claude"].get("validation_passed"):
            return _full_smoke_fail(report, "engineer_claude")

        state = run_manager_agent(state)
        report["manager"] = _manager_smoke_diagnostics(state)

        state = run_reviewer_agent(state)
        report["reviewer_claude"] = _reviewer_smoke_diagnostics(smoke_root, state)
        if not report["reviewer_claude"].get("validation_passed"):
            return _full_smoke_fail(report, "reviewer_claude")

        state = archive_current_iteration(state)
        state = final_node(state)
        report["final"] = _final_smoke_diagnostics(smoke_root, state)
        if not report["final"].get("validation_passed"):
            return _full_smoke_fail(report, "final")
    except Exception as exc:
        report["failure_category"] = VALIDATION_FAILED
        report["failure_reason"] = f"{type(exc).__name__}: {exc}"
        return report
    report["status"] = "pass"
    report["validation_passed"] = True
    return report


def validate_engineer_noop_outputs(repo: str | Path, started_at: float) -> dict[str, Any]:
    repo_path = Path(repo)
    results = artifact_dir(repo_path) / "results"
    required = {
        "engineer_done_written": results / "ENGINEER_DONE.txt",
        "project_tests_written": results / "project_tests.csv",
        "source_verification_written": results / "source_verification.csv",
        "build_smoke_written": results / "build_smoke.csv",
        "input_contract_verification_written": results / "input_contract_verification.csv",
    }
    report: dict[str, Any] = {
        "engineer_done_written": False,
        "project_tests_written": False,
        "source_verification_written": False,
        "build_smoke_written": False,
        "input_contract_verification_written": False,
        "fresh_outputs": True,
        "missing_outputs": [],
        "stale_outputs": [],
        "reduced_metrics_present": _any_forbidden(repo_path, "reduced_metrics.csv"),
        "reduced_metrics_json_present": _any_forbidden(repo_path, "reduced_metrics.json"),
        "paper_alignment_present": _any_forbidden(repo_path, "paper_alignment.csv"),
        "validation_passed": False,
        "failure_category": "",
        "failure_reason": "",
    }
    for key, path in required.items():
        exists = path.exists() and path.stat().st_size > 0
        report[key] = exists
        if not exists:
            report["missing_outputs"].append(str(path))
            continue
        if path.stat().st_mtime + 0.001 < started_at:
            report["stale_outputs"].append(str(path))
    report["fresh_outputs"] = not report["stale_outputs"]
    if report["reduced_metrics_present"] or report["reduced_metrics_json_present"] or report["paper_alignment_present"]:
        report["failure_category"] = FORBIDDEN_OUTPUT
        report["failure_reason"] = "Engineer no-op smoke wrote reduced metrics or paper alignment output, which is forbidden for verification_only health checks."
        return report
    if report["missing_outputs"]:
        report["failure_category"] = MISSING_REQUIRED_OUTPUT
        report["failure_reason"] = "Missing required Engineer no-op output(s): " + "; ".join(report["missing_outputs"][:5])
        return report
    if report["stale_outputs"]:
        report["failure_category"] = STALE_OUTPUT
        report["failure_reason"] = "Engineer no-op output(s) were stale: " + "; ".join(report["stale_outputs"][:5])
        return report
    report["validation_passed"] = True
    return report


def render_health_check_table(report: Mapping[str, Any]) -> str:
    minimal = report.get("minimal_write_test", {}) or {}
    planner = report.get("planner_ccr_smoke", {}) or {}
    engineer = report.get("engineer_noop_smoke", {}) or {}
    full = report.get("full_claude_smoke", {}) or {}
    env = report.get("provider_env", {}) or {}
    lines = [
        f"Claude executable: {report.get('attempted_executable') or report.get('claude_executable')}",
        f"Executable exists: {_yes_no(report.get('executable_exists'))}",
        f"Version check: {report.get('version_check')}",
        f"Version output: {report.get('version_output') or 'n/a'}",
        f"CCR detected: {_yes_no(report.get('ccr_detected'))}",
        f"Provider env present: {_yes_no(env.get('provider_env_present'))}",
        f"Provider env vars: {', '.join(env.get('env_vars_present', {}).keys()) or 'none'}",
        f"Non-engineer tools contain Bash: {_yes_no(report.get('non_engineer_tools_contain_bash'))}",
        f"Engineer tools contain Bash: {_yes_no(report.get('engineer_tools_contain_bash'))}",
        f"Prompt file: {minimal.get('prompt_file', 'not run')}",
        f"Prompt size: {minimal.get('prompt_size', 'not run')}",
        f"Minimal write test: {minimal.get('status', NOT_RUN)}",
        f"Planner CCR smoke: {planner.get('status', NOT_RUN)}",
        f"Engineer no-op smoke: {engineer.get('status', NOT_RUN)}",
        f"Full Claude smoke: {full.get('status', NOT_RUN)}",
        f"Real CCR invoked: {_yes_no(engineer.get('real_ccr_invoked')) if engineer.get('status') != NOT_RUN else NOT_RUN}",
        f"Engineer done written: {_yes_no(engineer.get('engineer_done_written')) if engineer.get('status') != NOT_RUN else NOT_RUN}",
        f"project_tests.csv written: {_yes_no(engineer.get('project_tests_written')) if engineer.get('status') != NOT_RUN else NOT_RUN}",
        f"source_verification.csv written: {_yes_no(engineer.get('source_verification_written')) if engineer.get('status') != NOT_RUN else NOT_RUN}",
        f"build_smoke.csv written: {_yes_no(engineer.get('build_smoke_written')) if engineer.get('status') != NOT_RUN else NOT_RUN}",
        f"input_contract_verification.csv written: {_yes_no(engineer.get('input_contract_verification_written')) if engineer.get('status') != NOT_RUN else NOT_RUN}",
        f"reduced_metrics.csv present: {_yes_no(engineer.get('reduced_metrics_present')) if engineer.get('status') != NOT_RUN else NOT_RUN}",
        f"reduced_metrics.json present: {_yes_no(engineer.get('reduced_metrics_json_present')) if engineer.get('status') != NOT_RUN else NOT_RUN}",
        f"paper_alignment.csv present: {_yes_no(engineer.get('paper_alignment_present')) if engineer.get('status') != NOT_RUN else NOT_RUN}",
        f"Validation passed: {_yes_no(engineer.get('validation_passed')) if engineer.get('status') != NOT_RUN else NOT_RUN}",
        f"Failure category: {report.get('failure_category') or minimal.get('failure_category') or planner.get('failure_category') or engineer.get('failure_category') or full.get('failure_category') or 'none'}",
        f"Failure reason: {report.get('failure_reason') or minimal.get('failure_reason') or planner.get('failure_reason') or engineer.get('failure_reason') or full.get('failure_reason') or 'none'}",
        f"Is Claude/CCR usable for Planner: {_yes_no(report.get('is_claude_ccr_usable_for_planner'))}",
        f"Is Claude/CCR usable for Engineer: {_yes_no(report.get('is_claude_ccr_usable_for_engineer'))}",
        f"Is Claude/CCR usable for Engineer no-op: {_yes_no(report.get('is_claude_ccr_usable_for_engineer_noop'))}",
        f"Recommended default backend: {report.get('recommended_default_backend')}",
    ]
    return "\n".join(lines)


def _stage_failure(result: Mapping[str, Any], fallback_category: str, fallback_reason: str) -> dict[str, Any]:
    if result.get("backend_failure_category"):
        category = str(result.get("backend_failure_category"))
        reason = str(result.get("backend_user_message") or result.get("error") or fallback_reason)
    elif not result.get("stage_guard_ok", True):
        category = STAGE_BOUNDARY_VIOLATION
        reason = str(result.get("error") or result.get("stage_guard_error") or fallback_reason)
    elif result.get("stale_output_detected"):
        category = STALE_OUTPUT
        reason = str(result.get("error") or fallback_reason)
    else:
        category = fallback_category
        reason = str(result.get("error") or fallback_reason)
    backend_error = classify_backend_error(str(result.get("stdout_tail", "")), str(result.get("stderr_tail", "")), backend="claude")
    if backend_error.get("failure_category") == TOOL_CALL_PARSE_FAILURE:
        category = TOOL_CALL_PARSE_FAILURE
        reason = str(backend_error.get("user_message") or reason)
    return {"failure_category": category, "failure_reason": reason}


def _classify_cli_failure(error: str) -> str:
    lowered = (error or "").lower()
    if "not found" in lowered or "filenotfounderror" in lowered or "no such file" in lowered:
        return EXECUTABLE_NOT_FOUND
    return EXECUTABLE_VERSION_FAILED


def _mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}...{value[-4:]}"


def _split_tools(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _is_bash_tool(tool: str) -> bool:
    return tool.lower().startswith("bash(")


def _is_ccr(executable: str) -> bool:
    return Path(executable or "").stem.lower() == "ccr"


def _yes_no(value: Any) -> str:
    return "yes" if bool(value) else "no"


def health_check_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False)


def _paper_smoke_diagnostics(repo: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "paper_context": report_path(repo, "paper_context"),
        "paper_brief": report_path(repo, "paper"),
        "paper_evidence": report_path(repo, "paper_evidence"),
        "paper_reproduction_card": report_path(repo, "paper_reproduction_card"),
        "paper_figures_tables": report_path(repo, "paper_figures_tables"),
        "paper_parse_quality": report_path(repo, "paper_parse_quality"),
    }
    missing = [str(path) for path in required.values() if not path.exists() or path.stat().st_size == 0]
    warnings = "\n".join(str(item) for item in state.get("warnings", []))
    backend_failure = _backend_failure_from_logs(repo, "paper")
    fallback_used = "fallback used" in warnings.lower()
    return {
        "status": "pass" if not missing and not fallback_used and not backend_failure.get("failure_category") else "failed",
        "real_ccr_invoked": (artifact_dir(repo) / "logs" / "claude_paper_prompt.md").exists() or (artifact_dir(repo) / "logs" / "paper_stdout.log").exists(),
        "required_artifacts_written": not missing,
        "validation_passed": not missing and not fallback_used and not backend_failure.get("failure_category"),
        "failure_category": backend_failure.get("failure_category") or (MISSING_REQUIRED_OUTPUT if missing else (VALIDATION_FAILED if fallback_used else "")),
        "failure_reason": backend_failure.get("failure_reason") or ("Missing paper artifacts: " + "; ".join(missing[:4]) if missing else ("Paper Claude reader fell back to local preprocess." if fallback_used else "")),
    }


def _planner_smoke_diagnostics(repo: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    tx_path = artifact_dir(repo) / "logs" / "planner_transaction.json"
    data = _read_json(tx_path)
    diag = dict(data.get("diagnostic", {}) or {})
    validation_passed = bool(diag.get("planner_validation_passed") and diag.get("planner_committed") and diag.get("approval_passed"))
    return {
        "status": "pass" if validation_passed else "failed",
        "staging_task_spec_written": bool(diag.get("staging_task_spec_written")),
        "staging_experiment_contract_written": bool(diag.get("staging_experiment_contract_written")),
        "validation_passed": validation_passed,
        "planner_validation_passed": bool(diag.get("planner_validation_passed")),
        "committed": bool(diag.get("planner_committed", data.get("committed"))),
        "approval_passed": bool(diag.get("approval_passed") or state.get("approved")),
        "failure_category": diag.get("failure_category", data.get("failure_category", "")) or ("" if validation_passed else VALIDATION_FAILED),
        "failure_reason": diag.get("failure_reason", "") or _planner_transaction_issue_text(data),
    }


def _manager_smoke_diagnostics(state: Mapping[str, Any]) -> dict[str, Any]:
    status = str(state.get("manager_status", "") or "UNKNOWN")
    return {
        "status": "pass" if status in {"PASS", "WARNING"} else "failed",
        "backend": state.get("manager_backend", "rules") or "rules",
        "verdict": status,
        "failure_category": "" if status in {"PASS", "WARNING"} else VALIDATION_FAILED,
        "failure_reason": "" if status in {"PASS", "WARNING"} else "Manager status is not PASS/WARNING.",
    }


def _reviewer_smoke_diagnostics(repo: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    tx_path = artifact_dir(repo) / "logs" / "reviewer_transaction.json"
    data = _read_json(tx_path)
    committed = bool(data.get("committed") and data.get("validation_status") == "PASS")
    report_written = report_path(repo, "review").exists()
    feedback_written = report_path(repo, "review_feedback").exists()
    validation_passed = committed and report_written and feedback_written
    return {
        "status": "pass" if validation_passed else "failed",
        "staging_review_report_written": bool((Path(data.get("staging_dir", "")) / "REVIEW_REPORT.md").exists()) if data.get("staging_dir") else False,
        "staging_review_feedback_written": bool((Path(data.get("staging_dir", "")) / "REVIEW_FEEDBACK.json").exists()) if data.get("staging_dir") else False,
        "validation_passed": validation_passed,
        "committed": committed,
        "verdict": state.get("reviewer_verdict", ""),
        "failure_category": data.get("failure_category", "") or ("" if validation_passed else VALIDATION_FAILED),
        "failure_reason": _planner_transaction_issue_text(data),
    }


def _final_smoke_diagnostics(repo: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    path = report_path(repo, "final")
    text = _read_text(path)
    level = infer_evidence_level(repo, str(state.get("reproduction_level", "")))
    false_claim = (
        "Current: L3: Official reduced run" in text
        or "Current: L4: Reduced paper-aligned evidence" in text
        or "Full Reproduction Claim: Yes" in text
    )
    forbidden = _any_forbidden(repo, "reduced_metrics.csv") or _any_forbidden(repo, "reduced_metrics.json") or _any_forbidden(repo, "paper_alignment.csv")
    validation_passed = path.exists() and level in {"L0_project_health", "L1_source_artifact_verified", "L2_input_contract_ready"} and "Full Reproduction Claim: No" in text and not false_claim and not forbidden
    return {
        "status": "pass" if validation_passed else "failed",
        "final_report_generated": path.exists(),
        "current_level": level,
        "full_reproduction_claim": "No" if "Full Reproduction Claim: No" in text else "unknown",
        "false_l3_l4_claim": false_claim,
        "forbidden_reduced_outputs_present": forbidden,
        "validation_passed": validation_passed,
        "failure_category": "" if validation_passed else VALIDATION_FAILED,
        "failure_reason": "" if validation_passed else "Final report missing, overclaimed, or forbidden reduced outputs were generated.",
    }


def _full_smoke_fail(report: dict[str, Any], stage: str) -> dict[str, Any]:
    diag = report.get(stage, {}) or {}
    report["failure_category"] = str(diag.get("failure_category") or VALIDATION_FAILED)
    report["failure_reason"] = f"{stage}: {diag.get('failure_reason') or 'validation failed'}"
    report["validation_passed"] = False
    return report


def _backend_failure_from_logs(repo: Path, stage: str) -> dict[str, str]:
    stdout = _read_text(artifact_dir(repo) / "logs" / f"{stage}_stdout.log")
    stderr = _read_text(artifact_dir(repo) / "logs" / f"{stage}_stderr.log")
    classified = classify_backend_error(stdout, stderr, backend="claude")
    if not classified.get("is_backend_failure"):
        return {"failure_category": "", "failure_reason": ""}
    return {
        "failure_category": str(classified.get("failure_category") or VALIDATION_FAILED),
        "failure_reason": str(classified.get("user_message") or classified.get("failure_detail") or "Claude/CCR backend failure."),
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _planner_transaction_issue_text(data: Mapping[str, Any]) -> str:
    issues = data.get("issues", []) if isinstance(data, Mapping) else []
    if issues:
        return "; ".join(str(item) for item in list(issues)[:3])
    return ""


def _write_engineer_noop_inputs(repo: Path) -> None:
    r2a = artifact_dir(repo)
    (r2a / "TASK_SPEC.md").write_text(
        "# TASK_SPEC\n\n"
        "## Goal\n\n"
        "Run an Engineer Claude/CCR health check only. This is a verification_only no-op smoke task.\n\n"
        "## Objective\n\n"
        "Write the required no-op smoke artifacts under `.r2a/results/` and stop. Do not run real experiments.\n\n"
        "## Allowed Files\n\n"
        "- .r2a/results/ENGINEER_DONE.txt\n"
        "- .r2a/results/project_tests.csv\n"
        "- .r2a/results/source_verification.csv\n"
        "- .r2a/results/build_smoke.csv\n"
        "- .r2a/results/input_contract_verification.csv\n"
        "- .r2a/results/ENGINEER_NOTES.md\n\n"
        "## Forbidden Files\n\n"
        "- .r2a/results/reduced_metrics.csv\n"
        "- .r2a/results/reduced_metrics.json\n"
        "- .r2a/results/paper_alignment.csv\n"
        "- results/reduced_metrics.csv\n"
        "- results/reduced_metrics.json\n"
        "- results/paper_alignment.csv\n\n"
        "## Experiment Config\n\n"
        "- mode: verification_only\n"
        "- no-op smoke only\n"
        "- official_reduced is forbidden\n"
        "- network, downloads, training, benchmarking, and real paper experiments are forbidden\n\n"
        "## Required Metrics\n\n"
        "- None. This health check must not write measured paper metrics.\n\n"
        "## Expected Outputs\n\n"
        "- `.r2a/results/ENGINEER_DONE.txt` with `DONE` and a note that this is verification_only no-op smoke.\n"
        "- `.r2a/results/project_tests.csv` with headers `status,command,exit_code,duration_sec,test_scope,log_path,notes` and one PASS row.\n"
        "- `.r2a/results/source_verification.csv` with headers `status,artifact_url,source_path,branch,commit,tag,readme_found,build_docs_found,experiment_scripts_found,data_scripts_found,notes` and one PASS_WITH_LIMITATIONS row.\n"
        "- `.r2a/results/build_smoke.csv` with headers `status,command,exit_code,duration_sec,component,notes` and one PASS row.\n"
        "- `.r2a/results/input_contract_verification.csv` with headers `component,status,path_or_command,evidence_source,notes` and one READY_WITH_GAPS row.\n\n"
        "## Acceptance Criteria\n\n"
        "- All required files exist and are non-empty.\n"
        "- No `reduced_metrics.csv`, `reduced_metrics.json`, or `paper_alignment.csv` exists.\n"
        "- No L3/L4 claim appears in outputs.\n\n"
        "## Stop Conditions\n\n"
        "- Stop immediately after writing required no-op artifacts.\n"
        "- If any real experiment would be needed, write only verification_only notes and stop.\n\n"
        "## Engineer Instructions\n\n"
        "Use deterministic CSV writing with proper quoting. Do not use network. Do not download data. Do not run tests beyond this no-op artifact write.\n",
        encoding="utf-8",
    )
    (r2a / "EXPERIMENT_CONTRACT.md").write_text(
        "# EXPERIMENT_CONTRACT\n\n"
        "## Contract Mode\n\n"
        "verification_only\n\n"
        "## Result Level\n\n"
        "VERIFICATION_ONLY\n\n"
        "## Reproducibility Gate\n\n"
        "This is an Engineer Claude/CCR health check. It cannot support L3/L4.\n\n"
        "## Max Evidence Level Allowed\n\n"
        "L2_input_contract_ready\n\n"
        "## Claim Restrictions\n\n"
        "- Do not claim official_reduced.\n"
        "- Do not write reduced_metrics.csv or paper_alignment.csv.\n"
        "- Do not claim paper reproduction.\n\n"
        "## Data Download Policy\n\n"
        "No network and no downloads are allowed.\n\n"
        "## Required Outputs\n\n"
        "- ENGINEER_DONE.txt\n"
        "- project_tests.csv\n"
        "- source_verification.csv\n"
        "- build_smoke.csv\n"
        "- input_contract_verification.csv\n",
        encoding="utf-8",
    )


def _init_health_git_repo(repo: Path) -> None:
    try:
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=str(repo),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return


def _any_forbidden(repo: Path, name: str) -> bool:
    return any((directory / name).exists() for directory in (artifact_dir(repo) / "results", repo / "results"))


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
