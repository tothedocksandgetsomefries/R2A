from __future__ import annotations

import json
import time
from pathlib import Path

from r2a.core.evidence_policy import evaluate_l0_l4
from r2a.core.paths import artifact_dir, report_path, require_repo_dir
from r2a.core.state import R2AState
from r2a.tools import claude_stage_runner, codex_stage_runner, openclaw_stage_runner
from r2a.tools.evidence_levels import infer_evidence_level
from r2a.tools.git_guard import inspect_repo
from r2a.tools.input_contract_evidence import validate_official_input_pass_evidence
from r2a.tools.markdown_utils import bullet_list
from r2a.tools.prompt_loader import load_prompt, render_prompt
from r2a.tools.report_writer import write_report
from r2a.tools.stage_env import build_stage_env
from r2a.tools.wsl import windows_to_wsl_path


def run_manager_agent(state: R2AState, *, force: bool = True) -> R2AState:
    """Manager Agent: Engineer 阶段的基础交付检查器。

    Manager 不再判断复现质量、Evidence Level、CSV schema 是否标准、
    列名是否完整、input contract 是否充分、paper alignment 是否合格。

    Manager 只回答以下三个问题：
    1. Engineer 本轮是否真实执行过；
    2. Engineer 是否产生了属于本轮的新输出；
    3. 输出是否至少存在且非空、可以被基本读取，而不是损坏文件或上一轮遗留文件。

    只要满足以上基本条件，Manager 就应通过，并进入 Reviewer。
    """
    repo = require_repo_dir(state["repo_path"])
    language = state.get("language", "en")
    load_prompt("manager_agent")

    task_spec = Path(state.get("task_spec_path", report_path(repo, "task")))
    execution_report = Path(state.get("execution_report_path", report_path(repo, "execution")))
    experiment_contract = Path(state.get("experiment_contract_path", report_path(repo, "experiment_contract")))

    # 收集候选输出文件
    artifact_results_dir = artifact_dir(repo) / "results"
    candidate_outputs = _collect_candidate_outputs(repo, artifact_results_dir)

    # 检查 Engineer 执行状态
    engineer_status = _check_engineer_execution_status(repo, execution_report, state)

    # 检查本轮产物
    current_iteration_outputs = _check_current_iteration_outputs(
        repo,
        candidate_outputs,
        task_spec,
        execution_report,
        state
    )

    # 读取文件内容用于报告
    task_spec_text = task_spec.read_text(encoding="utf-8", errors="replace") if task_spec.exists() else ""

    # Git 状态报告（仅用于信息展示，不影响判断）
    git_report = inspect_repo(repo)
    input_contract_evidence_issues = validate_official_input_pass_evidence(
        repo=repo,
        input_contract_csv=artifact_results_dir / "input_contract_verification.csv",
        command_manifest_csv=artifact_results_dir / "command_manifest.csv",
        logs_dir=artifact_dir(repo) / "logs",
    )

    # 判断 Manager 状态
    status, blocking_errors, warnings = _determine_manager_status(
        engineer_status=engineer_status,
        current_iteration_outputs=current_iteration_outputs,
        task_spec_exists=task_spec.exists(),
        execution_report_exists=execution_report.exists(),
        language=language,
    )
    input_contract_evidence_diagnostics = _format_input_contract_evidence_issues(input_contract_evidence_issues)
    if input_contract_evidence_diagnostics:
        blocking_errors.extend(input_contract_evidence_diagnostics)
        status = "FAIL"

    passed = status in {"PASS", "WARNING"}

    # 生成 MANAGER_DECISION.json
    manager_decision = _build_manager_decision(
        repo=repo,
        status=status,
        blocking_errors=blocking_errors,
        warnings=warnings,
        engineer_status=engineer_status,
        current_iteration_outputs=current_iteration_outputs,
        candidate_outputs=candidate_outputs,
        input_contract_evidence_issues=input_contract_evidence_issues,
        state=state,
    )

    manager_decision_path = report_path(repo, "manager_decision")
    manager_decision_path.parent.mkdir(parents=True, exist_ok=True)
    manager_decision_path.write_text(
        json.dumps(manager_decision, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    # 生成 CHECK_REPORT.md
    output = report_path(repo, "check")
    write_report(
        output,
        "CHECK_REPORT.md",
        {
            "repo_path": repo,
            "iteration": state.get("iteration", 1),
            "auto_iteration_context": _t(
                language,
                f"auto_iterate={state.get('auto_iterate', False)}, max_iterations={state.get('max_iterations', 1)}",
                f"auto_iterate={state.get('auto_iterate', False)}, max_iterations={state.get('max_iterations', 1)}",
            ),
            "status": status,
            "engineer_execution_status": engineer_status["summary"],
            "current_iteration_outputs": _format_outputs_summary(current_iteration_outputs, candidate_outputs),
            "git_checks": bullet_list(
                [
                    f"is_git_repo: {git_report.is_git_repo}",
                    f"clean: {git_report.clean}",
                    f"changed_files: {len(git_report.changed_files)}",
                ]
            ),
            "errors": bullet_list(blocking_errors),
            "warnings": bullet_list(warnings),
            "artifact_invariant_diagnostics": bullet_list(input_contract_evidence_diagnostics),
            "final_decision": status,
            "suggested_next_action": _suggested_next_action(status, blocking_errors, warnings, language),
            "manager_role_note": _t(
                language,
                "Manager 仅检查 Engineer 是否执行并产生可用的本轮输出。证据质量和复现等级由 Reviewer 判断。",
                "Manager only checks if Engineer executed and produced usable current-iteration outputs. Evidence quality and reproduction level are judged by Reviewer."
            ),
        },
        force=force,
    )

    # 更新 state
    merged_errors = [*state.get("errors", []), *blocking_errors]
    merged_warnings = [*state.get("warnings", []), *warnings]

    updated = {
        **state,
        "check_report_path": str(output),
        "latest_check_report_path": str(output),
        "manager_status": status,
        "manager_passed": passed,
        "manager_executed": True,
        "manager_max_level_allowed": str(manager_decision["max_level_allowed"]),
        "manager_decision_path": str(manager_decision_path),
        "input_contract_evidence_issues": [issue.to_dict() for issue in input_contract_evidence_issues],
        "errors": merged_errors,
        "warnings": merged_warnings,
    }

    # 如果配置了 OpenClaw/Codex review backend，运行补充 review
    if state.get("manager_backend", "rules") in {"codex_review", "claude_review", "openclaw_review"}:
        updated = _run_manager_codex_review(updated, output)

    return updated


def _collect_candidate_outputs(repo: Path, artifact_results_dir: Path) -> list[dict]:
    """收集所有候选输出文件。"""
    outputs = []

    # 收集 .r2a/results/ 下的所有文件
    if artifact_results_dir.exists():
        for path in sorted(artifact_results_dir.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                outputs.append(_inspect_file(path, repo))

    # 收集 repo/results/ 下的所有文件（兼容旧位置）
    repo_results_dir = repo / "results"
    if repo_results_dir.exists():
        for path in sorted(repo_results_dir.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                outputs.append(_inspect_file(path, repo))

    # 收集 EXECUTION_REPORT.md
    execution_report = report_path(repo, "execution")
    if execution_report.exists():
        outputs.append(_inspect_file(execution_report, repo))

    # 收集 ENGINEER_NOTES.md
    engineer_notes = artifact_results_dir / "ENGINEER_NOTES.md"
    if engineer_notes.exists():
        outputs.append(_inspect_file(engineer_notes, repo))

    # 收集 ENGINEER_DONE.txt
    for done_path in [
        artifact_results_dir / "ENGINEER_DONE.txt",
        repo / "results" / "ENGINEER_DONE.txt",
    ]:
        if done_path.exists():
            outputs.append(_inspect_file(done_path, repo))

    return outputs


def _inspect_file(path: Path, repo: Path) -> dict:
    """检查单个文件的基本属性。"""
    result = {
        "path": str(path.relative_to(repo) if path.is_relative_to(repo) else path),
        "name": path.name,
        "exists": True,
        "size_bytes": 0,
        "non_empty": False,
        "readable": False,
        "extension": path.suffix.lower(),
        "current_iteration": None,  # 稍后判断
    }

    try:
        stat = path.stat()
        result["size_bytes"] = stat.st_size
        result["non_empty"] = stat.st_size > 0

        # 尝试读取文件
        if path.suffix.lower() in {".csv", ".json", ".md", ".txt"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            result["readable"] = bool(text.strip())
            result["line_count"] = len(text.splitlines())
        else:
            # 二进制文件，只检查大小
            result["readable"] = stat.st_size > 0

    except Exception as e:
        result["readable"] = False
        result["error"] = str(e)

    return result


def _check_engineer_execution_status(repo: Path, execution_report: Path, state: dict) -> dict:
    """检查 Engineer 执行状态。

    返回：
    - executed: bool - Engineer 是否执行
    - backend_failed: bool - backend 是否明确失败
    - status: str - 状态标签
    - summary: list[str] - 状态摘要
    """
    result = {
        "executed": False,
        "backend_failed": False,
        "status": "UNKNOWN",
        "summary": [],
    }

    # 检查 state 中的 engineer 状态
    engineer_status = str(state.get("engineer_status", "") or "").upper()
    engineer_executor_failed = state.get("engineer_executor_failed", False)
    engineer_executor_failure_category = str(state.get("engineer_executor_failure_category", "") or "")

    # 检查 EXECUTION_REPORT.md
    if execution_report.exists():
        text = execution_report.read_text(encoding="utf-8", errors="replace").lower()

        # 检查 backend failure 标记
        if "backend failure" in text or "backend_failed" in text or "executor failed" in text:
            result["backend_failed"] = True
            result["status"] = "BACKEND_FAILED"
            result["summary"].append("Engineer backend failed according to EXECUTION_REPORT.md")

        # 检查执行标记
        if "engineer_executed" in text or "status: pass" in text or "status: done" in text:
            result["executed"] = True
            if result["status"] == "UNKNOWN":
                result["status"] = "EXECUTED"

    # 使用 state 中的信息
    if engineer_executor_failed:
        result["backend_failed"] = True
        result["executed"] = False
        result["status"] = "BACKEND_FAILED"
        result["summary"].append(f"Engineer backend failed: {engineer_executor_failure_category or 'unknown reason'}")
    elif engineer_status in {"PASS", "WARNING", "DONE", "EXECUTED"}:
        result["executed"] = True
        if result["status"] == "UNKNOWN":
            result["status"] = "EXECUTED"
    elif engineer_status in {"FAIL", "FAILED", "BLOCKED"}:
        result["executed"] = True
        result["status"] = engineer_status
        result["summary"].append(f"Engineer status: {engineer_status}")
    elif engineer_status in {"NOT_RUN", "NOT_STARTED", "PENDING", "SKIPPED"}:
        # 明确未执行
        result["executed"] = False
        result["status"] = "NOT_EXECUTED"
        result["summary"].append(f"Engineer not executed: {engineer_status}")

    # 检查是否有任何输出产物
    artifact_results_dir = artifact_dir(repo) / "results"
    has_any_output = False
    if artifact_results_dir.exists():
        has_any_output = any(
            path.is_file() and path.stat().st_size > 0
            for path in artifact_results_dir.iterdir()
            if not path.name.startswith(".")
        )

    if has_any_output:
        result["executed"] = True
        if result["status"] == "UNKNOWN":
            result["status"] = "EXECUTED"

    if not result["summary"]:
        result["summary"].append(f"Engineer execution status: {result['status']}")
        result["summary"].append(f"Has any output: {has_any_output}")

    return result


def _check_current_iteration_outputs(
    repo: Path,
    candidate_outputs: list[dict],
    task_spec: Path,
    execution_report: Path,
    state: dict,
) -> dict:
    """检查属于当前轮的输出。

    返回：
    - has_outputs: bool - 是否有本轮输出
    - non_empty_outputs: list[dict] - 非空输出的列表
    - readable_outputs: list[dict] - 可读输出的列表
    - stale_outputs: list[dict] - 明确属于旧轮的输出
    - current_iteration_files: list[dict] - 属于当前轮的文件
    """
    result = {
        "has_outputs": False,
        "non_empty_outputs": [],
        "readable_outputs": [],
        "stale_outputs": [],
        "current_iteration_files": [],
    }

    iteration = int(state.get("iteration", 1) or 1)

    # 获取当前迭代的起始时间参考
    iteration_start_mtime = _get_iteration_start_mtime(task_spec, execution_report, iteration)

    for output in candidate_outputs:
        path = Path(repo / output["path"]) if not Path(output["path"]).is_absolute() else Path(output["path"])

        # 检查是否属于当前轮
        is_current = _is_current_iteration_file(path, iteration_start_mtime, iteration)
        output["current_iteration"] = is_current

        if is_current:
            result["current_iteration_files"].append(output)

            if output["non_empty"]:
                result["non_empty_outputs"].append(output)

            if output["readable"]:
                result["readable_outputs"].append(output)
        else:
            # 明确属于旧轮
            result["stale_outputs"].append(output)

    result["has_outputs"] = bool(result["current_iteration_files"])
    result["has_non_empty_outputs"] = bool(result["non_empty_outputs"])
    result["has_readable_outputs"] = bool(result["readable_outputs"])

    return result


def _get_iteration_start_mtime(task_spec: Path, execution_report: Path, iteration: int) -> float | None:
    """获取当前迭代的起始修改时间参考。"""
    candidates = []

    # TASK_SPEC.md 的修改时间是迭代开始的好参考
    if task_spec.exists():
        try:
            candidates.append(task_spec.stat().st_mtime)
        except OSError:
            pass

    # EXECUTION_REPORT.md 的修改时间
    if execution_report.exists():
        try:
            candidates.append(execution_report.stat().st_mtime)
        except OSError:
            pass

    if not candidates:
        return None

    # 使用最早的时间作为参考，减去缓冲时间
    return min(candidates) - 2.0


def _is_current_iteration_file(path: Path, iteration_start_mtime: float | None, iteration: int) -> bool:
    """判断文件是否属于当前迭代。

    使用文件修改时间作为判断依据：
    - 如果文件修改时间晚于迭代开始时间，认为是当前轮
    - 如果无法判断，默认认为是当前轮（保守策略）
    """
    if not path.exists():
        return False

    # 如果无法获取迭代开始时间，默认认为是当前轮
    if iteration_start_mtime is None:
        return True

    try:
        file_mtime = path.stat().st_mtime
        # 文件修改时间晚于迭代开始时间，认为是当前轮
        return file_mtime >= iteration_start_mtime
    except OSError:
        # 无法获取修改时间，默认认为是当前轮
        return True


def _determine_manager_status(
    engineer_status: dict,
    current_iteration_outputs: dict,
    task_spec_exists: bool,
    execution_report_exists: bool,
    language: str,
) -> tuple[str, list[str], list[str]]:
    """判断 Manager 状态。

    只允许在以下情况下返回非通过状态：
    1. Engineer backend 明确执行失败
    2. Engineer 根本没有执行
    3. Engineer 执行结束，但没有产生任何结果、报告、日志或有效产物
    4. 所有输出文件都是空文件
    5. 所有候选输出都无法进行最基本的读取
    6. 检测到的全部结果明显是旧迭代遗留
    7. Manager 自身发生程序异常

    返回：(status, blocking_errors, warnings)
    """
    blocking_errors = []
    warnings = []

    # 1. Engineer backend 明确执行失败
    if engineer_status["backend_failed"]:
        blocking_errors.append(_t(
            language,
            "Engineer backend 明确执行失败",
            "Engineer backend explicitly failed"
        ))
        return "FAIL", blocking_errors, warnings

    # 2. Engineer 根本没有执行
    if not engineer_status["executed"]:
        blocking_errors.append(_t(
            language,
            "Engineer 未执行",
            "Engineer did not execute"
        ))
        return "FAIL", blocking_errors, warnings

    # 3. 缺少必要文件（仅作为 warning）
    if not task_spec_exists:
        warnings.append(_t(
            language,
            "缺少 TASK_SPEC.md",
            "Missing TASK_SPEC.md"
        ))

    if not execution_report_exists:
        warnings.append(_t(
            language,
            "缺少 EXECUTION_REPORT.md",
            "Missing EXECUTION_REPORT.md"
        ))

    # 4. Engineer 执行结束，但没有产生任何结果
    if not current_iteration_outputs["has_outputs"]:
        # 检查是否所有输出都是旧轮残留
        if current_iteration_outputs["stale_outputs"]:
            blocking_errors.append(_t(
                language,
                "所有输出文件都是旧迭代遗留，无法证明属于当前轮",
                "All outputs are from previous iterations, no evidence of current iteration"
            ))
            return "FAIL", blocking_errors, warnings
        else:
            blocking_errors.append(_t(
                language,
                "Engineer 执行后没有任何输出",
                "Engineer produced no outputs after execution"
            ))
            return "FAIL", blocking_errors, warnings

    # 5. 所有输出文件都是空文件
    # 检查当前轮的所有文件是否都为空
    current_files = current_iteration_outputs.get("current_iteration_files", [])
    all_empty = all(not f.get("non_empty", False) for f in current_files)
    if current_files and all_empty:
        blocking_errors.append(_t(
            language,
            "所有输出文件为空",
            "All output files are empty"
        ))
        return "FAIL", blocking_errors, warnings

    # 6. 所有候选输出都无法读取
    # 检查当前轮的所有文件是否都不可读
    all_unreadable = all(not f.get("readable", False) for f in current_files)
    if current_files and all_unreadable:
        blocking_errors.append(_t(
            language,
            "所有候选输出均不可读取",
            "All candidate outputs are not readable"
        ))
        return "FAIL", blocking_errors, warnings

    # 7. 所有结果都是旧轮残留（但有空文件或其他情况）
    if current_iteration_outputs["stale_outputs"] and not current_iteration_outputs["current_iteration_files"]:
        blocking_errors.append(_t(
            language,
            "所有结果都是旧迭代遗留",
            "All results are from previous iterations"
        ))
        return "FAIL", blocking_errors, warnings

    # 通过：有当前轮的非空可读输出
    if warnings:
        return "WARNING", blocking_errors, warnings

    return "PASS", blocking_errors, warnings


def _build_manager_decision(
    repo: Path,
    status: str,
    blocking_errors: list[str],
    warnings: list[str],
    engineer_status: dict,
    current_iteration_outputs: dict,
    candidate_outputs: list[dict],
    input_contract_evidence_issues: list,
    state: dict,
) -> dict:
    """构建 MANAGER_DECISION.json。

    保持接口兼容，但简化内部逻辑。
    """
    # 从实际文件推断 evidence level（不依赖 tiered cap）
    inferred_level = infer_evidence_level(repo)

    # 构建输出文件列表（简化版）
    outputs_summary = [
        {
            "path": output["path"],
            "exists": output["exists"],
            "non_empty": output["non_empty"],
            "readable": output["readable"],
            "current_iteration": output.get("current_iteration"),
        }
        for output in candidate_outputs
    ]

    return {
        "status": status,
        # 保持兼容：max_level_allowed 设为推断的 evidence level（不降低）
        "max_level_allowed": inferred_level,
        "blocking_errors": blocking_errors,
        "warnings": warnings,
        # 新增字段：基础交付信息
        "manager_executed": True,
        "engineer_executed": engineer_status["executed"],
        "engineer_backend_failed": engineer_status["backend_failed"],
        "has_current_iteration_output": current_iteration_outputs["has_outputs"],
        "has_non_empty_output": current_iteration_outputs.get("has_non_empty_outputs", False),
        "has_readable_output": current_iteration_outputs.get("has_readable_outputs", False),
        "outputs": outputs_summary,
        "artifact_invariant_diagnostics": [issue.to_dict() for issue in input_contract_evidence_issues],
        "input_contract_evidence_issues": [issue.to_dict() for issue in input_contract_evidence_issues],
        "current_iteration_files_count": len(current_iteration_outputs.get("current_iteration_files", [])),
        "stale_files_count": len(current_iteration_outputs.get("stale_outputs", [])),
        # 兼容字段
        "checks": {
            "engineer_executed": engineer_status["executed"],
            "has_current_output": current_iteration_outputs["has_outputs"],
            "has_non_empty_output": current_iteration_outputs.get("has_non_empty_outputs", False),
            "has_readable_output": current_iteration_outputs.get("has_readable_outputs", False),
        },
        "manager_stage_status": status,
        # 兼容字段：evidence 信息（从实际文件推断，不依赖 tiered cap）
        "evidence_observed": inferred_level,
        "evidence_accepted": inferred_level,
        "target_level": str(state.get("target_reproduction_level", "L4_reduced_paper_aligned")),
        "fatal_failures": blocking_errors,
        "non_fatal_warnings": warnings,
        "blockers": [],  # Manager 不再生成 blockers
        "evidence_gaps": [],  # Manager 不再判断 evidence gaps
    }


def _format_input_contract_evidence_issues(issues: list) -> list[str]:
    diagnostics: list[str] = []
    for issue in issues:
        component = getattr(issue, "component", "")
        path_or_command = getattr(issue, "path_or_command", "")
        row_number = getattr(issue, "row_number", "")
        code = getattr(issue, "code", "INPUT_CONTRACT_PASS_WITHOUT_EVIDENCE")
        message = getattr(issue, "message", "")
        diagnostics.append(
            f"{code}: row={row_number}; component={component}; path={path_or_command}; {message}"
        )
    return diagnostics


def _format_outputs_summary(current_iteration_outputs: dict, candidate_outputs: list[dict]) -> str:
    """格式化输出摘要用于报告。"""
    current_paths = [str(output.get("path", "")) for output in current_iteration_outputs.get("current_iteration_files", []) if output.get("path")]
    artifact_result_csv_count = sum(
        1
        for output in candidate_outputs
        if str(output.get("path", "")).replace("\\", "/").startswith(".r2a/results/")
        and str(output.get("path", "")).lower().endswith(".csv")
    )
    lines = [
        f"Total candidate outputs: {len(candidate_outputs)}",
        f"Current iteration outputs: {len(current_iteration_outputs.get('current_iteration_files', []))}",
        f"Non-empty current outputs: {len(current_iteration_outputs.get('non_empty_outputs', []))}",
        f"Readable current outputs: {len(current_iteration_outputs.get('readable_outputs', []))}",
        f"Stale outputs (previous iterations): {len(current_iteration_outputs.get('stale_outputs', []))}",
        f"result CSV count under .r2a/results/: {artifact_result_csv_count}",
    ]
    lines.extend(f"current output: {path}" for path in current_paths[:10])
    return bullet_list(lines)


def _run_manager_codex_review(state: R2AState, check_report_path: Path) -> R2AState:
    """运行 Manager 的补充 AI review（如果配置）。"""
    repo = require_repo_dir(state["repo_path"])
    output = report_path(repo, "manager_codex_review")
    output.parent.mkdir(parents=True, exist_ok=True)
    backend = state.get("manager_backend", "rules")
    prompt_path = windows_to_wsl_path if backend == "openclaw_review" else str
    prompt = render_prompt(
        "manager_codex_review",
        {
            "repo_path": prompt_path(repo),
            "language": state.get("language", "en"),
            "language_name": _language_name(state.get("language", "en")),
            "check_report_path": prompt_path(check_report_path),
            "manager_codex_review_path": prompt_path(output),
            "iteration": str(state.get("iteration", 1)),
        },
    )
    env = build_stage_env(
        stage="manager",
        backend=backend,
        stage_api_keys=state.get("stage_api_keys"),
        stage_api_key_env_vars=state.get("stage_api_key_env_vars"),
    )
    if backend == "claude_review":
        result = claude_stage_runner.run_claude_stage(
            repo,
            "manager",
            prompt,
            [".r2a/MANAGER_CODEX_REVIEW.md"],
            iteration=int(state.get("iteration", 1)),
            timeout=int(state.get("codex_stage_timeout", state.get("timeout", 10800))),
            claude_executable_path=state.get("claude_executable_path"),
            language=state.get("language", "en"),
            env=env,
        )
    elif backend == "openclaw_review":
        iteration = int(state.get("iteration", 1))
        staging_dir = artifact_dir(repo) / "staging" / "manager" / f"iter_{iteration:03d}" / "attempt_001"
        staging_dir.mkdir(parents=True, exist_ok=True)
        input_path = staging_dir / "OPENCLAW_INPUT.md"
        input_path.write_text(_build_openclaw_manager_input(prompt, output, state), encoding="utf-8")
        stage_config = openclaw_stage_runner.openclaw_stage_model_config_from_state(state, "manager")
        result = openclaw_stage_runner.run_openclaw_stage(
            repo,
            "manager",
            input_path,
            [".r2a/MANAGER_CODEX_REVIEW.md"],
            session_key=_openclaw_manager_session_key(state, iteration),
            iteration=iteration,
            timeout=int(state.get("codex_stage_timeout", state.get("timeout", 10800))),
            openclaw_executable_path=state.get("openclaw_executable_path"),
            openclaw_config_path=state.get("openclaw_config_path"),
            wsl_distro=str(state.get("wsl_distro", "Ubuntu")),
            env=env,
            provider=stage_config.get("provider") or state.get("openclaw_provider"),
            model=stage_config.get("model") or state.get("openclaw_model"),
            runner=stage_config.get("runner") or state.get("openclaw_runner"),
            agent=stage_config.get("agent") or state.get("openclaw_agent"),
        )
    else:
        result = codex_stage_runner.run_codex_stage(
            repo,
            "manager",
            prompt,
            [".r2a/MANAGER_CODEX_REVIEW.md"],
            iteration=int(state.get("iteration", 1)),
            timeout=int(state.get("codex_stage_timeout", state.get("timeout", 10800))),
            codex_executable_path=state.get("codex_executable_path"),
            language=state.get("language", "en"),
            env=env,
        )
    warnings = list(state.get("warnings", []))
    if not result.get("guard_available", True):
        warnings.append(f"Manager {backend} stage guard unavailable: {result.get('stage_guard_error')}")
    if result.get("unexpected_modifications"):
        warnings.append(f"Manager {backend} modified unexpected files: {result['unexpected_modifications']}")
    if not result.get("success"):
        warnings.append(f"Manager {backend} failed; CHECK_REPORT.md rules output remains authoritative.")
        output.write_text(
            "# MANAGER_CODEX_REVIEW\n\n"
            f"{backend} was requested but failed or was unavailable.\n\n"
            "CHECK_REPORT.md remains the authoritative Manager result.\n",
            encoding="utf-8",
        )
    return {**state, "latest_manager_codex_review_path": str(output), "warnings": warnings}


def _build_openclaw_manager_input(prompt: str, output: Path, state: R2AState) -> str:
    config = openclaw_stage_runner.openclaw_config_from_state(state, stage="manager")
    return (
        "# R2A Manager OpenClaw Stage\n\n"
        "This file is the only long instruction bundle for the OpenClaw Manager supplemental review stage.\n"
        "CHECK_REPORT.md remains authoritative; OpenClaw writes only the supplemental Manager review.\n\n"
        "Backend contract:\n"
        f"- provider: `{config['provider']}`\n"
        f"- model: `{config['model']}`\n"
        f"- runner: `{config['runner']}`\n"
        f"- agent: `{config['agent']}`\n"
        "- fallbackUsed: `false`\n\n"
        "Write boundary:\n"
        f"- Write only `{windows_to_wsl_path(output)}`.\n"
        "- Do not write any other file or directory.\n\n"
        "When finished, return raw JSON only, without Markdown fences:\n"
        '{"status":"PASS","stage":"manager"}\n\n'
        "---\n\n"
        f"{prompt}\n"
    )


def _openclaw_manager_session_key(state: R2AState, iteration: int) -> str:
    import re
    run_id = str(state.get("run_id", "run") or "run")
    safe_run_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", run_id).strip("-") or "run"
    return f"r2a-manager-{safe_run_id}-{int(iteration)}-{int(time.time())}"


def _suggested_next_action(status: str, errors: list[str], warnings: list[str], language: str = "en") -> str:
    if status == "PASS":
        return _t(language, "进入 Reviewer Stage。", "Proceed to Reviewer Stage.")
    if errors:
        return _t(language, "Engineer 执行或输出存在问题，需要修复后再进入 Reviewer。", "Engineer execution or outputs have issues; fix before proceeding to Reviewer.")
    if warnings:
        return _t(language, "检查 warning，并决定是否需要更严格地重新运行。", "Inspect warnings and decide whether a stricter rerun is needed.")
    return _t(language, "检查 Manager report。", "Inspect Manager report.")


def _t(language: str, zh: str, en: str) -> str:
    return zh if language == "zh" else en


def _language_name(language: str) -> str:
    return "Simplified Chinese" if language == "zh" else "English"
