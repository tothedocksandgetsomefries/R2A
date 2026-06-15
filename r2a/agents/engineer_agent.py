from __future__ import annotations

import json
from pathlib import Path
import re
import time

from r2a.core.paths import artifact_dir, report_path, require_repo_dir
from r2a.core.state import R2AState
from r2a.tools.claude_runner import _ensure_engineer_done_from_terminal_results, run_claude_code_exec
from r2a.tools import openclaw_stage_runner
from r2a.tools.codex_runner import CodexRunResult, build_codex_exec_prompt, mock_codex_exec, run_codex_exec
from r2a.tools.csv_checker import check_csv_files
from r2a.tools.csv_writer import CSV_PARSE_ERROR, write_csv_rows
from r2a.tools.engineer_runtime import EngineerRuntimeResult, run_engineer_runtime
from r2a.tools.markdown_utils import bullet_list
from r2a.tools.prompt_loader import load_prompt
from r2a.tools.report_writer import write_report
from r2a.tools.stage_env import build_stage_env
from r2a.tools.wsl import DEFAULT_WSL_CACHE_DIR, DEFAULT_WSL_DISTRO


def run_engineer_agent(state: R2AState, *, force: bool = True) -> R2AState:
    repo = require_repo_dir(state["repo_path"])
    task_spec = state.get("task_spec_path") or str(report_path(repo, "task"))
    executor = state.get("engineer_executor", state.get("executor", "shell"))
    language = state.get("language", "en")
    iteration = int(state.get("iteration", 1))
    timeout = int(state.get("codex_stage_timeout", state.get("timeout", 10800)))
    engineer_execution_environment = state.get("engineer_execution_environment", "windows")
    wsl_distro = state.get("wsl_distro", DEFAULT_WSL_DISTRO)
    wsl_cache_dir = state.get("wsl_cache_dir", DEFAULT_WSL_CACHE_DIR)
    load_prompt("engineer_agent")
    logs_dir = artifact_dir(repo) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (repo / "results").mkdir(exist_ok=True)
    clarification = _clarification_needed(task_spec)
    result_csv_signatures_before = _result_csv_signatures(repo)
    runtime_result: EngineerRuntimeResult | None = None
    stage_env = build_stage_env(
        stage="engineer",
        backend=executor,
        stage_api_keys=state.get("stage_api_keys"),
        stage_api_key_env_vars=state.get("stage_api_key_env_vars"),
    )

    if executor == "codex":
        result = run_codex_exec(
            repo,
            task_spec,
            timeout=timeout,
            language=language,
            iteration=iteration,
            auto_iterate=bool(state.get("auto_iterate", False)),
            codex_executable_path=state.get("codex_executable_path"),
            env=stage_env,
        )
        if clarification and result.ok:
            summary = _t(language, "Codex 执行器已运行；TASK_SPEC.md 格式不完整，请查看执行报告中的限制。", "Codex executor ran; TASK_SPEC.md is incomplete, so inspect execution limitations.")
        else:
            summary = _t(language, "Codex 执行器已完成。", "Codex executor completed.") if result.ok else _t(language, "Codex 执行器失败或不可用。", "Codex executor failed or was unavailable.")
    elif executor in {"claude", "claude_code"}:
        runtime_result = run_engineer_runtime(
            repo,
            timeout=timeout,
            iteration=iteration,
            execution_environment=engineer_execution_environment,
            wsl_distro=wsl_distro,
            wsl_cache_dir=wsl_cache_dir,
        )
        result = run_claude_code_exec(
            repo,
            task_spec,
            timeout=timeout,
            language=language,
            iteration=iteration,
            auto_iterate=bool(state.get("auto_iterate", False)),
            claude_executable_path=state.get("claude_executable_path"),
            execution_environment=engineer_execution_environment,
            wsl_distro=wsl_distro,
            wsl_cache_dir=wsl_cache_dir,
        )
        _ensure_engineer_done_from_terminal_results(repo)
        if clarification and result.ok:
            summary = _t(language, "Claude Code 执行器已运行；TASK_SPEC.md 格式不完整，请查看执行报告中的限制。", "Claude Code executor ran; TASK_SPEC.md is incomplete, so inspect execution limitations.")
        else:
            summary = _t(language, "Claude Code 执行器已完成。", "Claude Code executor completed.") if result.ok else _t(language, "Claude Code 执行器失败或不可用。", "Claude Code executor failed or was unavailable.")
    elif executor == "openclaw":
        stage_config = openclaw_stage_runner.openclaw_stage_model_config_from_state(state, "engineer")
        result = _run_openclaw_engineer_exec(
            repo,
            task_spec,
            timeout=timeout,
            language=language,
            iteration=iteration,
            auto_iterate=bool(state.get("auto_iterate", False)),
            openclaw_executable_path=state.get("openclaw_executable_path"),
            openclaw_config_path=state.get("openclaw_config_path"),
            # Support stage-level provider/model configuration for Engineer
            # Priority: engineer_provider/engineer_model > openclaw_provider/openclaw_model
            provider=state.get("engineer_provider") or stage_config.get("provider") or state.get("openclaw_provider"),
            model=state.get("engineer_model") or stage_config.get("model") or state.get("openclaw_model"),
            runner=stage_config.get("runner") or state.get("openclaw_runner"),
            agent=stage_config.get("agent") or state.get("openclaw_agent"),
            session_key=_openclaw_engineer_session_key(state, iteration),
            env=stage_env,
        )
        _ensure_engineer_done_from_terminal_results(repo)
        if clarification and result.ok:
            summary = _t(language, "OpenClaw executor ran; TASK_SPEC.md is incomplete.", "OpenClaw executor ran; TASK_SPEC.md is incomplete, so inspect execution limitations.")
        else:
            summary = _t(language, "OpenClaw executor completed.", "OpenClaw executor completed.") if result.ok else _t(language, "OpenClaw executor failed or was unavailable.", "OpenClaw executor failed or was unavailable.")
    elif clarification:
        result = mock_codex_exec(repo, task_spec, language=language)
        summary = _t(language, "NEEDS_CLARIFICATION: TASK_SPEC.md 缺少必要执行信息。", "NEEDS_CLARIFICATION: TASK_SPEC.md is missing required execution detail.")
    elif executor in {"mock", "shell"}:
        result = mock_codex_exec(repo, task_spec, language=language)
        summary = _t(language, f"{executor} 执行器已作为安全 MVP 演示完成。这不是真实完整实验。", f"{executor} executor completed as a safe MVP demo. This is not a real experiment.")
    else:
        raise ValueError(f"Unsupported executor: {executor}")

    if executor in {"mock", "shell"} and not clarification:
        _write_verification_only_smoke_outputs(repo, result.stdout_log_path, executor)

    output = report_path(repo, "execution")
    result_csvs = _result_csvs(repo)
    csv_postprocess_issues = _csv_postprocess_issues(result_csvs)
    if csv_postprocess_issues:
        _write_csv_parse_reproduction_status(repo, csv_postprocess_issues)
        result_csvs = _result_csvs(repo)
    changed_result_csvs = _changed_result_csvs(repo, result_csv_signatures_before)
    runtime_made_new_progress = _runtime_made_new_progress(runtime_result)
    recovered_from_executor_error = _recovered_from_executor_error(executor, result, changed_result_csvs, runtime_made_new_progress)
    effective_ok = result.ok or recovered_from_executor_error
    executor_unavailable = _executor_unavailable(result)
    if recovered_from_executor_error:
        summary = _t(
            language,
            "Claude Code 返回非零状态，但本轮 deterministic Engineer runtime 生成了新进展；继续交给 Manager 按警告状态验证。",
            "Claude Code returned non-zero, but deterministic Engineer runtime generated new progress; continuing as passed_with_warnings for Manager validation.",
        )
    if csv_postprocess_issues:
        summary = (
            f"{summary}\n\nCSV postprocess detected malformed or schema-invalid result CSVs. "
            "R2A wrote `reproduction_status.csv` with `NEEDS_FIX` / `CSV_PARSE_ERROR`; Manager must not treat these outputs as clean L3/L4 evidence."
        )
    if executor_unavailable:
        summary = (
            f"{summary}\n\nExternal Engineer executor is unavailable: "
            f"{result.backend_failure_category or result.error or result.stderr_tail}. "
            "R2A will not auto-iterate this Engineer failure until the executor is usable."
        )
    runtime_generated_files = runtime_result.generated_files if runtime_result else []
    runtime_commands = _runtime_commands(runtime_result)
    commands_run = [*runtime_commands, " ".join(result.command)]
    generated_files = [str(output), result.stdout_log_path, result.stderr_log_path, str(repo / "results"), str(artifact_dir(repo) / "results"), *runtime_generated_files, *result_csvs]
    write_report(
        output,
        "EXECUTION_REPORT.md",
        {
            "repo_path": repo,
            "iteration": iteration,
            "auto_iteration_context": _auto_iteration_context(state, language),
            "executor": executor,
            "execution_environment": _execution_environment_summary(engineer_execution_environment, wsl_distro, wsl_cache_dir),
            "task_spec_path": task_spec,
            "command": " ".join(result.command),
            "exit_code": result.returncode,
            "status": "passed_with_warnings" if csv_postprocess_issues else ("passed" if result.ok else ("passed_with_warnings" if recovered_from_executor_error else "failed")),
            "summary": summary,
            "modified_files": _t(language, "MVP 执行器包装层暂不能可靠列出修改文件；请以 git diff 为准。", "Not available from executor wrapper in MVP. Inspect git diff for authoritative changes."),
            "commands_run": bullet_list(commands_run),
            "generated_files": bullet_list(generated_files),
            "result_summary": _t(language, "R2A 不伪造实验结论；真实输出必须存在于 results/ 或 .r2a/results/，并由 Manager 检查。", "No result claims are made by R2A. Real outputs must exist under results/ and be checked by Manager."),
            "errors_warnings": (result.stderr or _t(language, "无", "None")) if effective_ok else (result.stderr or _t(language, "执行器失败，但没有 stderr。", "Executor failed without stderr.")),
            "clarification_needed": _t(language, "是", "Yes") if clarification else _t(language, "否", "No"),
            "acceptance_checklist": bullet_list(_engineer_checklist(language)),
            "stdout": result.stdout_tail or "(empty)",
            "stderr": result.stderr_tail or "(empty)",
            "skipped": result.skipped,
        },
        force=force,
    )
    errors = list(state.get("errors", []))
    if not effective_ok:
        errors.append(_t(language, f"Engineer 执行器失败，退出码 {result.returncode}。", f"Engineer executor failed with exit code {result.returncode}."))
    if clarification:
        errors.append(_t(language, "真实执行前需要澄清任务。", "Engineer needs clarification before real execution."))
    warnings = list(state.get("warnings", []))
    if executor_unavailable:
        warnings.append(result.backend_user_message or "Engineer executor is unavailable to this R2A process.")
    return {
        **state,
        "execution_report_path": str(output),
        "latest_execution_report_path": str(output),
        "errors": errors,
        "warnings": warnings,
        "clarification_needed": clarification,
        "engineer_status": "PASS" if effective_ok else "FAIL",
        "engineer_passed": bool(effective_ok),
        "engineer_executor_failed": not effective_ok,
        "engineer_executor_failure_category": result.backend_failure_category,
        "engineer_executor_unavailable": executor_unavailable,
        "engineer_backend_provider": getattr(result, "backend_provider", ""),
        "engineer_backend_model": getattr(result, "backend_model", ""),
        "engineer_backend_runner": getattr(result, "backend_runner", ""),
        "engineer_backend_agent": getattr(result, "backend_agent", ""),
        "auto_iterate": False if executor_unavailable else state.get("auto_iterate", False),
        "stop_reason": "engineer_executor_unavailable" if executor_unavailable else state.get("stop_reason", ""),
    }


def _write_verification_only_smoke_outputs(repo: Path, log_path: str, executor: str) -> None:
    results = artifact_dir(repo) / "results"
    results.mkdir(parents=True, exist_ok=True)
    if not (results / "project_tests.csv").exists():
        write_csv_rows(
            results / "project_tests.csv",
            ("status", "command", "exit_code", "duration_sec", "test_scope", "log_path", "notes"),
            [
                {
                    "status": "PASS",
                    "command": f"{executor} verification-only no-op smoke",
                    "exit_code": 0,
                    "duration_sec": 0,
                    "test_scope": "workflow_smoke_no_real_experiment",
                    "log_path": log_path,
                    "notes": "Verification-only no-op smoke artifact; not a paper reproduction result.",
                }
            ],
        )
    _write_result_metadata(results / "project_tests.csv", result_type="verification_only", max_evidence_level="L1_source_artifact_verified", data_source="none", command_id="workflow_smoke_no_real_experiment")
    if not (results / "source_verification.csv").exists():
        write_csv_rows(
            results / "source_verification.csv",
            (
                "status",
                "artifact_url",
                "source_path",
                "branch",
                "commit",
                "tag",
                "readme_found",
                "build_docs_found",
                "experiment_scripts_found",
                "data_scripts_found",
                "notes",
            ),
            [
                {
                    "status": "PASS",
                    "artifact_url": "local_mock_workspace",
                    "source_path": str(repo),
                    "branch": "",
                    "commit": "",
                    "tag": "",
                    "readme_found": "not_checked",
                    "build_docs_found": "not_checked",
                    "experiment_scripts_found": "not_checked",
                    "data_scripts_found": "not_checked",
                    "notes": "Local smoke source/artifact presence only; not official source verification.",
                }
            ],
        )
    _write_result_metadata(results / "source_verification.csv", result_type="verification_only", max_evidence_level="L1_source_artifact_verified", data_source="local_workspace", command_id="workflow_smoke_no_real_experiment")
    if not (results / "build_smoke.csv").exists():
        write_csv_rows(
            results / "build_smoke.csv",
            ("status", "command", "exit_code", "duration_sec", "component", "notes"),
            [
                {
                    "status": "PASS",
                    "command": f"{executor} no-op build smoke",
                    "exit_code": 0,
                    "duration_sec": 0,
                    "component": "workflow_smoke",
                    "notes": "No build or experiment was run; this only proves the R2A smoke executor completed.",
                }
            ],
        )
    _write_result_metadata(results / "build_smoke.csv", result_type="verification_only", max_evidence_level="L1_source_artifact_verified", data_source="none", command_id="workflow_smoke_no_real_experiment")
    if not (results / "input_contract_verification.csv").exists():
        write_csv_rows(
            results / "input_contract_verification.csv",
            ("component", "status", "path_or_command", "evidence_source", "notes"),
            [
                {
                    "component": "dataset",
                    "status": "NEEDS_INPUT",
                    "path_or_command": "",
                    "evidence_source": ".r2a/EXPERIMENT_CONTRACT.md",
                    "notes": "Official reduced dataset was not acquired in this verification-only smoke.",
                },
                {
                    "component": "query",
                    "status": "NEEDS_INPUT",
                    "path_or_command": "",
                    "evidence_source": ".r2a/EXPERIMENT_CONTRACT.md",
                    "notes": "Official query input was not acquired in this verification-only smoke.",
                },
                {
                    "component": "ground_truth",
                    "status": "NEEDS_INPUT",
                    "path_or_command": "",
                    "evidence_source": ".r2a/EXPERIMENT_CONTRACT.md",
                    "notes": "Official ground truth was not acquired in this verification-only smoke.",
                },
                {
                    "component": "metric",
                    "status": "NEEDS_INPUT",
                    "path_or_command": "",
                    "evidence_source": ".r2a/EXPERIMENT_CONTRACT.md",
                    "notes": "Metric must be verified from paper or official scripts before reduced reproduction.",
                },
                {
                    "component": "command",
                    "status": "NEEDS_INPUT",
                    "path_or_command": "",
                    "evidence_source": ".r2a/EXPERIMENT_CONTRACT.md",
                    "notes": "Reduced reproduction command must be verified before L3.",
                },
            ],
        )
    _write_result_metadata(results / "input_contract_verification.csv", result_type="verification_only", max_evidence_level="L1_source_artifact_verified", data_source="none", command_id="workflow_smoke_no_real_experiment")
    done = results / "ENGINEER_DONE.txt"
    done.write_text("PASS\nverification_only no-op smoke; no real experiment executed.\n", encoding="utf-8")


def _run_openclaw_engineer_exec(
    repo: Path,
    task_spec_path: str | Path,
    *,
    timeout: int,
    language: str,
    iteration: int,
    auto_iterate: bool,
    openclaw_executable_path: str | None,
    openclaw_config_path: str | None,
    provider: str | None,
    model: str | None,
    runner: str | None,
    agent: str | None,
    session_key: str,
    env: dict[str, str] | None,
) -> CodexRunResult:
    logs_dir = artifact_dir(repo) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    input_path = logs_dir / "openclaw_engineer_input.md"
    prompt = build_codex_exec_prompt(repo, task_spec_path, language=language)
    prompt += (
        "\nOpenClaw Engineer mode:\n"
        "- You are running through OpenClaw local embedded mode.\n"
        "- Execute only the Engineer task from TASK_SPEC.md and the R2A protocol files.\n"
        "- Do not write `.r2a/EXECUTION_REPORT.md`; R2A writes it after you exit.\n"
        "- Write `.r2a/results/ENGINEER_DONE.txt` after CSV/status artifacts are complete.\n"
        "- Return only a short raw JSON status when the work is complete.\n"
        f"\nCurrent iteration: {iteration}\n"
        f"Auto iterate: {auto_iterate}\n"
    )
    input_path.write_text(prompt, encoding="utf-8")
    raw = openclaw_stage_runner.run_openclaw_stage(
        repo,
        "engineer",
        input_path,
        ["*"],
        session_key=session_key,
        iteration=iteration,
        timeout=timeout,
        openclaw_executable_path=openclaw_executable_path,
        openclaw_config_path=openclaw_config_path,
        provider=provider,
        model=model,
        runner=runner,
        agent=agent,
        env=env,
    )
    success = bool(raw.get("success"))
    raw_returncode = int(raw.get("returncode") or 0)
    returncode = raw_returncode if success or raw_returncode != 0 else 1
    stdout = str(raw.get("payload") or raw.get("stdout_tail") or "")
    stderr = str(raw.get("stderr_tail") or "")
    if raw.get("timed_out"):
        stderr = f"{stderr.rstrip()}\nTimeoutExpired: OpenClaw Engineer exceeded {timeout} seconds and the process tree was terminated.".strip()
    return CodexRunResult(
        command=[str(part) for part in raw.get("command", [])],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        stdout_log_path=str(raw.get("stdout_log_path", "")),
        stderr_log_path=str(raw.get("stderr_log_path", "")),
        stdout_tail=str(raw.get("stdout_tail") or stdout),
        stderr_tail=str(raw.get("stderr_tail") or stderr),
        attempted_executable=str(raw.get("attempted_executable") or raw.get("resolved_executable") or "openclaw"),
        skipped=False,
        error="" if success else str(raw.get("error") or stderr or "OpenClaw Engineer failed."),
        backend_failure_category=str(raw.get("failure_category") or ""),
        backend_failure_scope=str(raw.get("execution_status") or ""),
        backend_user_message=str(raw.get("error") or ""),
        backend_provider=str(raw.get("provider") or raw.get("configured_provider") or ""),
        backend_model=str(raw.get("model") or raw.get("configured_model") or ""),
        backend_runner=str(raw.get("runner") or raw.get("configured_runner") or ""),
        backend_agent=str(raw.get("configured_agent") or ""),
    )


def _openclaw_engineer_session_key(state: R2AState, iteration: int) -> str:
    raw = f"r2a-engineer-{iteration}-{state.get('runtime_run_id') or state.get('run_id') or int(time.time())}"
    return re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw).strip("-")[:120]


def _write_result_metadata(
    csv_path: Path,
    *,
    result_type: str,
    max_evidence_level: str,
    data_source: str,
    command_id: str,
) -> None:
    meta_path = csv_path.with_suffix(csv_path.suffix + ".meta.json")
    payload = {
        "artifact": str(csv_path),
        "result_type": result_type,
        "max_evidence_level": max_evidence_level,
        "reproduction_claim": False,
        "not_reproduction": True,
        "data_source": data_source,
        "uses_official_data": False,
        "uses_synthetic_data": False,
        "command_id": command_id,
    }
    meta_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _clarification_needed(task_spec_path: str) -> bool:
    path = Path(task_spec_path)
    if not path.exists():
        return True
    text = path.read_text(encoding="utf-8", errors="replace")
    required_sections = ("## Allowed Files", "## Forbidden Files", "## Acceptance Criteria", "## Stop Conditions")
    has_objective = any(section in text for section in ("## Goal", "## Objective", "## Purpose"))
    return (not has_objective) or any(section not in text for section in required_sections)


def _t(language: str, zh: str, en: str) -> str:
    return zh if language == "zh" else en


def _engineer_checklist(language: str) -> list[str]:
    if language == "zh":
        return [
            "TASK_SPEC.md 是唯一任务来源。",
            "Forbidden Files 必须由 Manager 验证。",
            "不得删除已有 results。",
            "不得伪造生成结果。",
            "真实执行应至少生成一个 .r2a/results/*.csv；无法执行时应写入 FAIL、NOT_RUN 或 NEEDS_INPUT 状态 CSV。",
            "reduced / mock 运行必须标注为有限证据。",
        ]
    return [
        "TASK_SPEC.md was the only task source.",
        "Forbidden Files must be verified by Manager.",
        "Existing results must not be deleted.",
        "Generated results must not be fabricated.",
        "Real execution should generate at least one .r2a/results/*.csv; blocked runs must write a FAIL, NOT_RUN, or NEEDS_INPUT status CSV.",
        "Reduced or mock runs must be labeled as limited evidence.",
    ]


def _execution_environment_summary(environment: str, wsl_distro: str, wsl_cache_dir: str) -> str:
    if environment == "wsl":
        return f"wsl (distro={wsl_distro}, cache_dir={wsl_cache_dir})"
    return "windows"


def _auto_iteration_context(state: R2AState, language: str) -> str:
    auto_iterate = bool(state.get("auto_iterate", False))
    max_iterations = int(state.get("max_iterations", 1))
    iteration = int(state.get("iteration", 1))
    if state.get("executor") in {"codex", "claude", "claude_code"} and auto_iterate:
        quota_note = _t(language, "此轮可能消耗 AI coding agent 额度。", "This iteration may consume AI coding agent quota.")
    else:
        quota_note = _t(language, "无额外自动迭代额度提示。", "No additional auto-iteration quota notice.")
    return _t(
        language,
        f"iteration={iteration}, max_iterations={max_iterations}, auto_iterate={auto_iterate}. {quota_note}",
        f"iteration={iteration}, max_iterations={max_iterations}, auto_iterate={auto_iterate}. {quota_note}",
    )


def _result_csvs(repo: Path) -> list[str]:
    files: list[Path] = []
    for directory in (repo / "results", artifact_dir(repo) / "results"):
        if directory.exists():
            files.extend(sorted(directory.glob("*.csv")))
    return [str(path) for path in files]


def _csv_postprocess_issues(result_csvs: list[str]) -> list[str]:
    if not result_csvs:
        return []
    report = check_csv_files([Path(path) for path in result_csvs])
    critical: list[str] = []
    for issue in report.issues:
        if issue.level != "error":
            continue
        name = Path(issue.file).name.lower()
        if CSV_PARSE_ERROR in issue.message or name in {"command_manifest.csv", "input_contract_verification.csv"}:
            critical.append(f"{issue.file}: {issue.message}")
    return critical


def _write_csv_parse_reproduction_status(repo: Path, issues: list[str]) -> None:
    write_csv_rows(
        artifact_dir(repo) / "results" / "reproduction_status.csv",
        ("status", "reason", "evidence_source", "next_action"),
        [
            {
                "status": "NEEDS_FIX",
                "reason": f"CSV_PARSE_ERROR: {'; '.join(issues[:3])}",
                "evidence_source": ".r2a/results/*.csv",
                "next_action": "rewrite malformed CSV with deterministic writer before Manager/Reviewer can accept evidence",
            }
        ],
    )


def _result_csv_signatures(repo: Path) -> dict[Path, tuple[int, int]]:
    signatures: dict[Path, tuple[int, int]] = {}
    for directory in (repo / "results", artifact_dir(repo) / "results"):
        if not directory.exists():
            continue
        for path in directory.glob("*.csv"):
            try:
                stat = path.stat()
            except OSError:
                continue
            signatures[path] = (stat.st_mtime_ns, stat.st_size)
    return signatures


def _changed_result_csvs(repo: Path, before: dict[Path, tuple[int, int]]) -> list[Path]:
    changed: list[Path] = []
    for path, signature in _result_csv_signatures(repo).items():
        if before.get(path) != signature:
            changed.append(path)
    return sorted(changed)


def _recovered_from_executor_error(executor: str, result, changed_result_csvs: list[Path], runtime_made_new_progress: bool = False) -> bool:
    if executor not in {"claude", "claude_code"}:
        return False
    if result.ok or result.skipped and not runtime_made_new_progress:
        return False
    if getattr(result, "is_backend_failure", False) and not getattr(result, "transient_backend_failure", False):
        return False
    if result.returncode in {126, 127}:
        return False
    if result.returncode == 124 and runtime_made_new_progress:
        return True
    return bool(changed_result_csvs)


def _executor_unavailable(result) -> bool:
    category = str(getattr(result, "backend_failure_category", "") or "")
    scope = str(getattr(result, "backend_failure_scope", "") or "")
    if category == "AUTHENTICATION_FAILURE" or scope == "BACKEND_AUTH_FAILURE":
        return True
    return getattr(result, "returncode", 0) in {126, 127}


def _runtime_made_new_progress(runtime_result: EngineerRuntimeResult | None) -> bool:
    if runtime_result is None:
        return False
    for command in runtime_result.commands:
        if command.returncode == 0 and command.command and command.command[0] != "reuse":
            return True
    return False


def _runtime_commands(runtime_result: EngineerRuntimeResult | None) -> list[str]:
    if runtime_result is None:
        return []
    return [f"runtime:{command.stage}: {' '.join(command.command)}" for command in runtime_result.commands]
