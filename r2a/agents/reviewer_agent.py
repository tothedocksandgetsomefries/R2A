from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any

from r2a.core.evidence_policy import evaluate_l0_l4
from r2a.core.paths import artifact_dir, iteration_state_path, report_path, require_repo_dir
from r2a.core.review_verdict import (
    build_evidence_decision_from_review_verdict,
    build_review_verdict_payload,
    extract_review_verdict_json,
    load_review_verdict,
    normalize_verdict_token,
    review_verdict_path,
    validate_review_verdict,
    write_review_verdict,
)
from r2a.core.state import R2AState
from r2a.core.user_hints import format_user_hints_markdown, user_hints_from_state
from r2a.core.verdicts import PASS_LIKE_VERDICTS, VALID_VERDICTS, is_pass_like_verdict
from r2a.tools import claude_stage_runner, codex_stage_runner, openclaw_stage_runner
from r2a.tools.csv_sanitizer import sanitized_csv_rows
from r2a.tools.csv_schemas import allowed_values_for_csv
from r2a.tools.markdown_utils import bullet_list
from r2a.tools.evidence_levels import contract_l2_cap_reason, infer_evidence_level
from r2a.tools.input_integrity import rows_have_input_integrity_blocker, summarize_official_input_integrity
from r2a.tools.paper_lookup import paper_lookup
from r2a.tools.prompt_loader import load_prompt, render_prompt
from r2a.tools.reproduction_levels import (
    ITERATION_VERDICTS,
    claim_level_for_verdict,
    current_level,
    infer_level_from_verdict,
    next_level_after_verdict,
    should_continue_after_verdict,
    target_level,
)
from r2a.tools.report_writer import write_report
from r2a.tools.workflow_decision import build_workflow_decision, collect_workflow_blockers, update_state_with_workflow_decision
from r2a.tools.stage_transaction import (
    commit_reviewer_transaction,
    reviewer_allowed_outputs,
    reviewer_staging_dir,
    validate_reviewer_transaction,
    write_reviewer_transaction_metadata,
)
from r2a.tools.stage_env import build_stage_env
from r2a.tools.wsl import windows_to_wsl_path


BLOCKING_STATUS_LABELS = {
    "BLOCKED",
    "FAILED",
    "NEEDS_CLARIFICATION",
    "NEEDS_INPUT",
    "NEEDS_OFFICIAL_INPUT",
    "NEEDS_INPUT_OR_BUDGET",
    "NOT_AVAILABLE",
    "PARTIAL",
    "NOT_RUN",
}
FAILURE_CATEGORY_LABELS = (
    "SAFE_BUILD_COMPATIBILITY",
    "TOOLCHAIN_OR_ENVIRONMENT",
    "MISSING_ARTIFACT_OR_DATA",
    "API_OR_ALGORITHM_SEMANTICS",
    "RESULT_MISMATCH",
    "TIME_BUDGET",
    "TASK_AMBIGUITY",
    "RUNTIME_DLL_COMPATIBILITY",
    "ENGINEER_TIMEOUT_AFTER_BUILD",
    "DEMO_ONLY",
)

PROGRESSION_VERDICTS = (
    "PASS_REDUCED_COMPARISON",
    "PASS_REDUCED_ALIGNED",
    "PASS_REDUCED_METHOD_ONLY",
    "INPUT_CONTRACT_READY",
    "PASS_SMOKE_ONLY",
)

L4_ALIGNMENT_MATCH_STATUSES = set(allowed_values_for_csv("paper_alignment.csv", "match_status"))
L4_REQUIRED_SETTING_GROUPS = {
    "dataset_scale": ("dataset scale", "scale"),
    "hardware": ("hardware",),
    "runtime_budget": ("runtime budget", "runtime", "budget"),
    "parameters": ("parameters", "params", "parameter"),
    "number_of_repeats": ("number of repeats", "repeats", "repeat"),
    "baselines": ("baseline", "baselines"),
    "metric_definition": ("metric definition", "metric"),
    "input_source": ("input source", "source"),
    "known_evidence_gaps": ("evidence gap", "known evidence gap", "gap"),
}


def run_reviewer_agent(state: R2AState, *, force: bool = True) -> R2AState:
    if state.get("reviewer_backend", "rules") in {"codex", "claude", "openclaw"}:
        return _run_codex_reviewer_agent(state, force=force)
    return _run_rules_reviewer_agent(state, force=force)


def _run_rules_reviewer_agent(state: R2AState, *, force: bool = True) -> R2AState:
    repo = require_repo_dir(state["repo_path"])
    output = report_path(repo, "review")
    feedback_path = report_path(repo, "review_feedback")
    output.parent.mkdir(parents=True, exist_ok=True)
    language = state.get("language", "en")
    user_hints = user_hints_from_state(state)
    load_prompt("reviewer_agent")
    check_report = Path(state.get("check_report_path", report_path(repo, "check")))
    execution_report = Path(state.get("execution_report_path", report_path(repo, "execution")))
    task_spec = Path(state.get("task_spec_path", report_path(repo, "task")))
    experiment_contract = Path(state.get("experiment_contract_path", report_path(repo, "experiment_contract")))
    paper_context = Path(state.get("paper_context_path", report_path(repo, "paper_context")))
    paper_analysis = Path(state.get("paper_analysis_path", report_path(repo, "paper_analysis")))
    paper_card = Path(state.get("paper_reproduction_card_path", report_path(repo, "paper_reproduction_card")))
    paper_figures_tables = Path(state.get("paper_figures_tables_path", report_path(repo, "paper_figures_tables")))
    paper_parse_quality = Path(state.get("paper_parse_quality_path", report_path(repo, "paper_parse_quality")))
    paper_brief = Path(state.get("paper_brief_path", report_path(repo, "paper")))
    paper_evidence = Path(state.get("paper_evidence_path", report_path(repo, "paper_evidence")))
    check_text = _read(check_report)
    execution_text = _read(execution_report)
    execution_outcome = _execution_outcome(repo, check_text, execution_text)
    lookups = [paper_lookup(str(repo), term, max_snippets=2) for term in ("metrics", "baselines", "datasets", "gaps")]
    status = _check_status(check_text)
    verdict = _verdict(status, execution_text, state, execution_outcome)
    major_issues = _major_issues(status, state, lookups, execution_outcome)
    should_iterate = _should_iterate(verdict, state)
    suggested_next_action = _suggested_next_action(verdict, language, execution_outcome)

    # 1. 先更新正式等级
    updated = _with_evidence_decision({
        **state,
        "reviewer_verdict": verdict,
        "reviewer_executed": True,
        "reviewer_backend": "rules",
    })

    # 2. 基于更新后的 state 构造 canonical feedback
    feedback = _build_review_feedback(
        updated,
        verdict=verdict,
        should_iterate=should_iterate,
        major_issues=major_issues,
        execution_outcome=execution_outcome,
        suggested_next_action=suggested_next_action,
    )
    should_iterate = bool(feedback.get("should_iterate", should_iterate))

    write_report(
        output,
        "REVIEW_REPORT.md",
        {
            "repo_path": repo,
            "iteration": state.get("iteration", 1),
            "goal": state.get("goal", ""),
            "user_hints": format_user_hints_markdown(user_hints),
            "paper_brief_path": paper_brief,
            "paper_analysis_path": paper_analysis,
            "paper_context_path": paper_context,
            "paper_reproduction_card_path": paper_card,
            "paper_figures_tables_path": paper_figures_tables,
            "paper_parse_quality_path": paper_parse_quality,
            "paper_evidence_path": paper_evidence,
            "task_spec_path": task_spec,
            "experiment_contract_path": experiment_contract,
            "execution_report_path": execution_report,
            "check_report_path": check_report,
            "verdict": verdict,
            "should_iterate": _t(language, "Yes" if should_iterate else "No", "Yes" if should_iterate else "No"),
            "summary": _t(language, "基于 paper artifacts、TASK_SPEC.md、EXECUTION_REPORT.md 和 CHECK_REPORT.md 生成的规则化 MVP 评审。", "Rule-based MVP review generated from paper artifacts, TASK_SPEC.md, EXECUTION_REPORT.md, and CHECK_REPORT.md."),
            "execution_outcome": execution_outcome["summary"],
            "failure_classification": bullet_list(execution_outcome["failure_categories"] or ["No failure category detected."]),
            "paper_alignment": _paper_alignment(lookups, language),
            "l3_satisfaction": _l3_satisfaction_summary(repo, verdict, language),
            "l4_satisfaction": _l4_satisfaction_summary(repo, verdict, language),
            "not_full_reproduction": _not_full_reproduction_summary(verdict, language),
            "major_issues": bullet_list(major_issues),
            "minor_issues": bullet_list(state.get("warnings", []) or [_t(language, "state 中未记录额外次要问题。", "No additional minor issues recorded by state.")]),
            "missing_tests": _t(language, "MVP Reviewer 无法证明科学覆盖充分。应补充与必要指标绑定的定向测试或 reduced experiment 检查。", "MVP reviewer cannot prove scientific coverage. Add targeted tests or reduced experiment checks tied to required metrics."),
            "risky_changes": _t(language, "请人工检查 git diff。Manager 会报告 dirty git status，但 MVP 中不判断语义风险。", "Inspect git diff manually. Manager reports dirty git status but does not classify semantic risk in MVP."),
            "reproduction_limitations": bullet_list(_reproduction_limitations(lookups, language)),
            "required_fixes": bullet_list(_required_fixes(verdict, major_issues, execution_outcome)),
            "suggested_next_action": suggested_next_action,
            "mvp_notes": _t(language, "Reviewer Agent 当前版本不调用 LLM，且不能仅凭代码能运行就认证完整复现。", "Reviewer Agent does not call an LLM in this version and must not certify full reproduction from runnable code alone."),
        },
        force=force,
    )
    _write_review_feedback(feedback_path, feedback)
    # 3. 返回最终 state（已包含正式等级更新）
    return {
        **updated,
        "review_report_path": str(output),
        "review_feedback_path": str(feedback_path),
        "latest_review_report_path": str(output),
        "latest_review_feedback_path": str(feedback_path),
        "need_replan": should_iterate,
        "suggested_next_action": suggested_next_action,
        "workflow_decision": feedback.get("workflow_decision", {}),
        "workflow_blockers": feedback.get("workflow_blockers", []),
    }


def _run_codex_reviewer_agent(state: R2AState, *, force: bool = True) -> R2AState:
    repo = require_repo_dir(state["repo_path"])
    output = report_path(repo, "review")
    feedback_path = report_path(repo, "review_feedback")
    iteration = int(state.get("iteration", 1))
    staging_dir = reviewer_staging_dir(repo, iteration, attempt=1)
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_output = staging_dir / "REVIEW_REPORT.md"
    staging_feedback = staging_dir / "REVIEW_FEEDBACK.json"
    check_report = Path(state.get("check_report_path", report_path(repo, "check")))
    paper_context = Path(state.get("paper_context_path", report_path(repo, "paper_context")))
    paper_analysis = Path(state.get("paper_analysis_path", report_path(repo, "paper_analysis")))
    paper_card = Path(state.get("paper_reproduction_card_path", report_path(repo, "paper_reproduction_card")))
    paper_figures_tables = Path(state.get("paper_figures_tables_path", report_path(repo, "paper_figures_tables")))
    paper_parse_quality = Path(state.get("paper_parse_quality_path", report_path(repo, "paper_parse_quality")))
    paper_brief = Path(state.get("paper_brief_path", report_path(repo, "paper")))
    paper_evidence = Path(state.get("paper_evidence_path", report_path(repo, "paper_evidence")))
    task_spec = Path(state.get("task_spec_path", report_path(repo, "task")))
    experiment_contract = Path(state.get("experiment_contract_path", report_path(repo, "experiment_contract")))
    execution_report = Path(state.get("execution_report_path", report_path(repo, "execution")))
    manager_codex_review = Path(state.get("latest_manager_codex_review_path", report_path(repo, "manager_codex_review")))
    iteration_state = iteration_state_path(repo)
    backend = state.get("reviewer_backend", "rules")
    user_hints = user_hints_from_state(state)
    prompt_path = _openclaw_prompt_path if backend == "openclaw" else str
    prompt = render_prompt(
        "reviewer_codex",
        {
            "repo_path": prompt_path(repo),
            "language": state.get("language", "en"),
            "language_name": _language_name(state.get("language", "en")),
            "review_report_path": prompt_path(staging_output),
            "paper_context_path": prompt_path(paper_context),
            "paper_analysis_path": prompt_path(paper_analysis),
            "paper_reproduction_card_path": prompt_path(paper_card),
            "paper_figures_tables_path": prompt_path(paper_figures_tables),
            "paper_parse_quality_path": prompt_path(paper_parse_quality),
            "paper_brief_path": prompt_path(paper_brief),
            "paper_evidence_path": prompt_path(paper_evidence),
            "task_spec_path": prompt_path(task_spec),
            "experiment_contract_path": prompt_path(experiment_contract),
            "execution_report_path": prompt_path(execution_report),
            "check_report_path": prompt_path(check_report),
            "manager_codex_review_path": prompt_path(manager_codex_review),
            "iteration_state_path": prompt_path(iteration_state),
            "iteration": str(iteration),
            "user_hints": format_user_hints_markdown(user_hints),
            "review_feedback_path": prompt_path(staging_feedback),
            "paper_context_excerpt": _file_excerpt(paper_context),
            "paper_analysis_excerpt": _file_excerpt(paper_analysis),
            "paper_reproduction_card_excerpt": _file_excerpt(paper_card),
            "paper_figures_tables_excerpt": _file_excerpt(paper_figures_tables),
            "paper_parse_quality_excerpt": _file_excerpt(paper_parse_quality),
            "paper_evidence_excerpt": _file_excerpt(paper_evidence),
            "task_spec_excerpt": _file_excerpt(task_spec),
            "experiment_contract_excerpt": _file_excerpt(experiment_contract),
            "execution_report_excerpt": _file_excerpt(execution_report),
            "check_report_excerpt": _file_excerpt(check_report),
        },
    )
    env = build_stage_env(
        stage="reviewer",
        backend=backend,
        stage_api_keys=state.get("stage_api_keys"),
        stage_api_key_env_vars=state.get("stage_api_key_env_vars"),
    )
    allowed_outputs = reviewer_allowed_outputs(repo, staging_dir)
    attempt_started_at = time.time()
    if backend == "claude":
        result = claude_stage_runner.run_claude_stage(
            repo,
            "reviewer",
            prompt,
            allowed_outputs,
            iteration=iteration,
            timeout=int(state.get("codex_stage_timeout", state.get("timeout", 10800))),
            claude_executable_path=state.get("claude_executable_path"),
            language=state.get("language", "en"),
            env=env,
        )
    elif backend == "openclaw":
        openclaw_input = staging_dir / "OPENCLAW_INPUT.md"
        openclaw_input.write_text(
            _build_openclaw_reviewer_input(
                prompt,
                staging_output=staging_output,
                staging_feedback=staging_feedback,
                state=state,
            ),
            encoding="utf-8",
        )
        result = _run_openclaw_reviewer_stage(
            repo,
            openclaw_input,
            allowed_outputs,
            iteration=iteration,
            timeout=int(state.get("codex_stage_timeout", state.get("timeout", 10800))),
            state=state,
            env=env,
        )
    else:
        result = codex_stage_runner.run_codex_stage(
            repo,
            "reviewer",
            prompt,
            allowed_outputs,
            iteration=iteration,
            timeout=int(state.get("codex_stage_timeout", state.get("timeout", 10800))),
            codex_executable_path=state.get("codex_executable_path"),
            language=state.get("language", "en"),
            env=env,
        )
    warnings = list(state.get("warnings", []))
    if not result.get("guard_available", True):
        warnings.append(f"Reviewer {backend} stage guard unavailable: {result.get('stage_guard_error')}")
    if result.get("unexpected_modifications"):
        warnings.append(f"Reviewer {backend} stage modified unexpected files: {result['unexpected_modifications']}")
    check_status = _check_status(_read(check_report))
    transaction = validate_reviewer_transaction(
        repo,
        staging_dir,
        result,
        iteration=iteration,
        attempt=1,
        attempt_started_at=attempt_started_at,
        manager_status=str(state.get("manager_status", "")),
        check_status=check_status,
    )
    if transaction["validation_status"] == "PASS":
        transaction = commit_reviewer_transaction(repo, staging_dir, transaction)
    write_reviewer_transaction_metadata(repo, transaction)
    if transaction["validation_status"] != "PASS" or not output.exists():
        return _write_reviewer_transaction_failure(state, transaction, warnings, force=force)

    review_verdict_validation = _write_review_verdict_from_committed_outputs(
        repo,
        output,
        feedback_path,
        backend=backend,
        target=str(state.get("target_reproduction_level", "") or ""),
    )
    report_verdict = _extract_verdict(_read(output))
    verdict = ""
    if review_verdict_validation.valid:
        verdict = str(review_verdict_validation.payload.get("verdict", "") or "")
        if report_verdict and report_verdict != verdict:
            warnings.append("REVIEW_REPORT text may not match REVIEW_VERDICT.json; structured verdict used.")
    elif report_verdict:
        verdict = report_verdict
        warnings.append("Structured REVIEW_VERDICT.json missing/invalid; legacy Markdown verdict parser was used.")
    elif review_verdict_validation.errors:
        warnings.append("Structured REVIEW_VERDICT.json missing/invalid: " + "; ".join(review_verdict_validation.errors[:3]))
    safety_override_triggered = False
    if not verdict:
        verdict = "NEEDS_FIX"
        safety_override_triggered = True
        _force_verdict(output, verdict)
        _append_safety_override(output, "No valid REVIEW_VERDICT.json or legacy Markdown verdict was available. Safety override set verdict to NEEDS_FIX.")
    classification_conflict = bool(transaction.get("manager_classification_conflict"))
    if classification_conflict:
        original_verdict = verdict
        verdict = "MANAGER_CLASSIFICATION_CONFLICT"
        safety_override_triggered = True
        _force_verdict(output, verdict)
        _append_safety_override(
            output,
            f"Manager/CHECK status is FAIL while Reviewer proposed {original_verdict}; recorded as MANAGER_CLASSIFICATION_CONFLICT for deterministic or human review.",
        )
    elif check_status == "FAIL" and verdict in PASS_LIKE_VERDICTS:
        original_verdict = verdict
        verdict = "NEEDS_FIX"
        safety_override_triggered = True
        _force_verdict(output, verdict)
        _append_safety_override(output, f"CHECK_REPORT.md is FAIL, so Codex Reviewer {original_verdict} was overridden to NEEDS_FIX.")
    review_text = _read(output)
    execution_outcome = _execution_outcome(repo, _read(check_report), _read(execution_report))
    input_integrity = summarize_official_input_integrity(repo)
    if execution_outcome.get("status") in {"FAILED", "BLOCKED", "PARTIAL"} and verdict in PASS_LIKE_VERDICTS:
        original_verdict = verdict
        verdict = "NEEDS_FIX"
        safety_override_triggered = True
        _force_verdict(output, verdict)
        _append_safety_override(output, f"Engineer outcome is {execution_outcome.get('status')}, so Codex Reviewer {original_verdict} was overridden to NEEDS_FIX.")
    elif execution_outcome.get("status") == "NEEDS_CLARIFICATION" and verdict in PASS_LIKE_VERDICTS:
        original_verdict = verdict
        verdict = "BORDERLINE"
        safety_override_triggered = True
        _force_verdict(output, verdict)
        _append_safety_override(output, f"Engineer outcome is NEEDS_CLARIFICATION, so Codex Reviewer {original_verdict} was overridden to BORDERLINE.")
    elif execution_outcome.get("status") == "NEEDS_OFFICIAL_INPUT" and verdict in PASS_LIKE_VERDICTS:
        original_verdict = verdict
        verdict = "NEEDS_OFFICIAL_INPUT"
        safety_override_triggered = True
        _force_verdict(output, verdict)
        _append_safety_override(output, f"Engineer outcome is NEEDS_OFFICIAL_INPUT, so Codex Reviewer {original_verdict} was overridden to NEEDS_OFFICIAL_INPUT.")
    elif input_integrity.get("has_blocking_issue") and verdict in {"PASS_REDUCED_METHOD_ONLY", "PASS_REDUCED_ALIGNED", "PASS_REDUCED_COMPARISON"}:
        original_verdict = verdict
        verdict = "NEEDS_OFFICIAL_INPUT"
        safety_override_triggered = True
        _force_verdict(output, verdict)
        _append_safety_override(output, f"Official Input Integrity Guard blocked {original_verdict}: empty placeholder, missing, or invalid official inputs cannot support L3/L4.")
    elif contract_l2_cap_reason(repo) and verdict in {"PASS_REDUCED_METHOD_ONLY", "PASS_REDUCED_ALIGNED", "PASS_REDUCED_COMPARISON"}:
        original_verdict = verdict
        safety_override_triggered = True
        # 从 state 读取正式等级，不进行文件推断
        current_level = str(state.get("current_reproduction_level", "") or "")
        if current_level == "L2_input_contract_ready":
            verdict = "INPUT_CONTRACT_READY"
        else:
            verdict = "PASS_SMOKE_ONLY"
        _force_verdict(output, verdict)
        _append_safety_override(output, f"Contract L2 cap blocked {original_verdict}: capped at L2 because {contract_l2_cap_reason(repo)}.")
    if safety_override_triggered:
        _write_safety_override_review_verdict(
            repo,
            verdict=verdict,
            backend=backend,
            reason=_extract_section(_read(output), "Safety Override") or "Safety Override triggered.",
            target=str(state.get("target_reproduction_level", "") or ""),
        )
    should_iterate = False if classification_conflict else _should_iterate(verdict, state)
    suggested_next_action = _extract_section(review_text, "Suggested Next Action") or _suggested_next_action(verdict, state.get("language", "en"), execution_outcome)
    major_issues = _extract_list_section(review_text, "Required Fixes") or _extract_list_section(review_text, "Major Issues")
    if classification_conflict:
        conflicts = [str(item) for item in transaction.get("classification_conflicts", []) if str(item).strip()]
        major_issues = conflicts or ["Manager classification conflict requires deterministic recheck or human review."]
    if not major_issues and verdict in {"NEEDS_FIX", "REJECT"}:
        major_issues = _major_issues(check_status, state, [], execution_outcome)

    # 尝试从 AI 生成的 REVIEW_FEEDBACK.json 中提取结构化等级信息
    structured_level_info = _extract_structured_level_info(staging_feedback, feedback_path)

    # 1. 先更新正式等级
    # 注意：如果 safety_override_triggered=True，则本轮 verdict 不是 AI 有效输出，
    # 不能用于更新正式等级或 level_source=ai_backend
    updated = _with_evidence_decision({
        **state,
        "reviewer_verdict": verdict,
        "reviewer_executed": True,
        "reviewer_backend": backend,
        "structured_review_feedback": structured_level_info,
        "warnings": warnings,
        "reviewer_transaction": transaction,
        "safety_override_triggered": safety_override_triggered,
    })

    # 2. 基于更新后的 state 构造 canonical feedback
    feedback = _build_review_feedback(
        updated,
        verdict=verdict,
        should_iterate=should_iterate,
        major_issues=major_issues,
        execution_outcome=execution_outcome,
        suggested_next_action=suggested_next_action,
    )
    should_iterate = bool(feedback.get("should_iterate", should_iterate))
    if classification_conflict:
        feedback["classification_conflicts"] = major_issues
        feedback["reviewer_transaction"] = {
            "validation_status": transaction.get("validation_status", ""),
            "failure_category": transaction.get("failure_category", ""),
            "execution_status": transaction.get("execution_status", ""),
            "candidate_verdict": transaction.get("candidate_verdict", ""),
            "committed": bool(transaction.get("committed", False)),
            "manager_classification_conflict": True,
        }
    _write_review_feedback(feedback_path, feedback)

    # 3. 返回最终 state
    return {
        **updated,
        "review_report_path": str(output),
        "review_feedback_path": str(feedback_path),
        "latest_review_report_path": str(output),
        "latest_review_feedback_path": str(feedback_path),
        "need_replan": should_iterate,
        "suggested_next_action": suggested_next_action,
        "workflow_decision": feedback.get("workflow_decision", {}),
        "workflow_blockers": feedback.get("workflow_blockers", []),
    }


def _extract_structured_level_info(staging_feedback: Path, committed_feedback_path: Path) -> dict[str, Any] | None:
    """从 AI 生成的 REVIEW_FEEDBACK.json 中提取结构化等级信息。

    AI backend 应该在 REVIEW_FEEDBACK.json 中包含：
    - current_reproduction_level: L0-L6 等级
    - level_reasoning: 判断理由
    - supporting_artifacts: 支持证据列表
    - remaining_gaps: 剩余差距
    - next_iteration_guidance: 下一轮建议
    - review_summary: 总结

    如果不存在这些字段，返回 None。
    """
    import json

    # 优先读取 staging 文件（AI 原始输出）
    for path in [staging_feedback, committed_feedback_path]:
        if not path.exists():
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            data = json.loads(content)
        except (OSError, json.JSONDecodeError):
            continue

        if not isinstance(data, dict):
            continue

        # 检查是否包含等级字段
        if "current_reproduction_level" in data:
            return {
                "current_reproduction_level": data.get("current_reproduction_level"),
                "level_reasoning": data.get("level_reasoning", ""),
                "supporting_artifacts": data.get("supporting_artifacts", []),
                "remaining_gaps": data.get("remaining_gaps", []),
                "next_iteration_guidance": data.get("next_iteration_guidance", ""),
                "review_summary": data.get("review_summary", ""),
            }

    return None


def _write_review_verdict_from_committed_outputs(
    repo: Path,
    report: Path,
    feedback: Path,
    *,
    backend: str,
    target: str,
) -> Any:
    path = review_verdict_path(repo)
    report_payload = extract_review_verdict_json(_read(report))
    if report_payload is not None:
        report_payload.setdefault("backend", backend)
        report_payload.setdefault("target_level", target)
        report_validation = validate_review_verdict(report_payload)
        if report_validation.valid:
            return write_review_verdict(path, report_payload)

    feedback_payload = _review_verdict_payload_from_feedback(feedback, backend=backend, target=target)
    if feedback_payload is not None:
        feedback_validation = validate_review_verdict(feedback_payload)
        if feedback_validation.valid:
            return write_review_verdict(path, feedback_payload)

    if report_payload is not None:
        return write_review_verdict(path, report_payload)

    missing_payload = {
        "schema_version": 1,
        "verdict": "",
        "accepted_level": "UNASSESSED",
        "level_valid": False,
        "target_level": target,
        "target_reached": False,
        "evidence_files": [],
        "limitations": [],
        "needs_fix_reasons": ["Structured reviewer verdict was not provided."],
        "backend": backend,
        "source": "missing_structured_verdict",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(missing_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return validate_review_verdict(missing_payload)


def _review_verdict_payload_from_feedback(feedback: Path, *, backend: str, target: str) -> dict[str, Any] | None:
    if not feedback.exists():
        return None
    try:
        data = json.loads(feedback.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    verdict = normalize_verdict_token(data.get("verdict") or data.get("status"))
    if not verdict:
        return None
    accepted_level = (
        data.get("accepted_level")
        or data.get("current_reproduction_level")
        or data.get("current_level")
        or data.get("reproduction_level")
    )
    if not accepted_level and is_pass_like_verdict(verdict):
        accepted_level = infer_level_from_verdict(verdict)
    if verdict == "NEEDS_FIX":
        accepted_level = "UNASSESSED"
    level_valid = bool(data.get("level_valid")) if isinstance(data.get("level_valid"), bool) else bool(is_pass_like_verdict(verdict) and accepted_level)
    evidence_files = data.get("supporting_artifacts")
    if not isinstance(evidence_files, list):
        evidence = data.get("evidence")
        evidence_files = list(evidence.values()) if isinstance(evidence, dict) else []
    limitations = data.get("remaining_gaps", [])
    if not isinstance(limitations, list):
        limitations = [str(limitations)] if str(limitations or "").strip() else []
    needs_fix_reasons = data.get("required_fixes", [])
    if not isinstance(needs_fix_reasons, list):
        needs_fix_reasons = [str(needs_fix_reasons)] if str(needs_fix_reasons or "").strip() else []
    return build_review_verdict_payload(
        verdict=verdict,
        accepted_level=str(accepted_level or "UNASSESSED"),
        level_valid=level_valid,
        target_level=target,
        target_reached=bool(data.get("target_reached", False)),
        evidence_files=[str(item) for item in evidence_files if str(item).strip()],
        limitations=[str(item) for item in limitations if str(item).strip()],
        needs_fix_reasons=[str(item) for item in needs_fix_reasons if str(item).strip()],
        backend=backend,
        source="ai_backend_structured",
    )


def _write_safety_override_review_verdict(
    repo: Path,
    *,
    verdict: str,
    backend: str,
    reason: str,
    target: str,
) -> None:
    accepted_level = infer_level_from_verdict(verdict) if is_pass_like_verdict(verdict) else "UNASSESSED"
    payload = build_review_verdict_payload(
        verdict=verdict,
        accepted_level=accepted_level,
        level_valid=False,
        target_level=target,
        target_reached=False,
        evidence_files=[],
        limitations=[],
        needs_fix_reasons=[reason],
        backend=backend,
        source="reviewer_safety_override",
    )
    write_review_verdict(review_verdict_path(repo), payload)


def _evidence_decision_from_legacy_markdown(
    repo: Path,
    *,
    iteration: int,
    backend: str,
    structured_errors: list[str],
) -> dict[str, Any] | None:
    from r2a.core.reviewer_level_judgment import LEVEL_LABELS, LEVEL_SEMANTICS

    verdict = _extract_verdict(_read(report_path(repo, "review")))
    if not verdict:
        return None
    warnings = ["Structured REVIEW_VERDICT.json missing/invalid; legacy Markdown verdict parser was used."]
    if structured_errors:
        warnings.append("Structured verdict errors: " + "; ".join(structured_errors[:3]))
    level_valid = bool(is_pass_like_verdict(verdict))
    accepted_level = infer_level_from_verdict(verdict) if level_valid else "UNASSESSED"
    return {
        "schema_version": 1,
        "current_reproduction_level": accepted_level,
        "level_label": LEVEL_LABELS.get(accepted_level, accepted_level if level_valid else None),
        "level_semantics": LEVEL_SEMANTICS.get(accepted_level, ""),
        "level_reasoning": "Legacy Markdown parser fallback accepted explicit Reviewer verdict." if level_valid else "Legacy Markdown parser found a non-pass verdict.",
        "supporting_artifacts": [],
        "remaining_gaps": [],
        "verdict": verdict,
        "iteration": int(iteration),
        "level_source": "legacy_markdown_parser" if level_valid else "unassessed",
        "level_valid": level_valid,
        "backend": backend,
        "warnings": warnings,
    }


def _openclaw_prompt_path(path: str | Path) -> str:
    return windows_to_wsl_path(path)


def _build_openclaw_reviewer_input(prompt: str, *, staging_output: Path, staging_feedback: Path, state: R2AState) -> str:
    config = openclaw_stage_runner.openclaw_config_from_state(state, stage="reviewer")
    return (
        "# R2A Reviewer OpenClaw Stage\n\n"
        "This file is the only long instruction bundle for the OpenClaw Reviewer stage.\n"
        "The OpenClaw CLI message must stay short and refer to this file by absolute WSL path.\n\n"
        "Backend contract:\n"
        f"- provider: `{config['provider']}`\n"
        f"- model: `{config['model']}`\n"
        f"- runner: `{config['runner']}`\n"
        f"- agent: `{config['agent']}`\n"
        "- fallbackUsed: `false`\n\n"
        "Write boundary:\n"
        f"- Write only `{windows_to_wsl_path(staging_output)}` and `{windows_to_wsl_path(staging_feedback)}`.\n"
        "- Do not write any other file or directory.\n"
        "- Do not write directly to `.r2a/REVIEW_REPORT.md` or `.r2a/REVIEW_FEEDBACK.json`.\n\n"
        "When finished, return raw JSON only, without Markdown fences:\n"
        '{"status":"PASS","stage":"reviewer"}\n\n'
        "---\n\n"
        f"{prompt}\n"
    )


def _run_openclaw_reviewer_stage(
    repo_path: str | Path,
    input_path: Path,
    allowed_outputs: list[str],
    *,
    iteration: int,
    timeout: int,
    state: R2AState,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    stage_config = openclaw_stage_runner.openclaw_stage_model_config_from_state(state, "reviewer")
    return openclaw_stage_runner.run_openclaw_stage(
        repo_path,
        "reviewer",
        input_path,
        allowed_outputs,
        session_key=_openclaw_reviewer_session_key(state, iteration),
        iteration=iteration,
        timeout=timeout,
        openclaw_executable_path=state.get("openclaw_executable_path"),
        openclaw_config_path=state.get("openclaw_config_path"),
        wsl_distro=str(state.get("wsl_distro", "Ubuntu")),
        env=env,
        provider=stage_config.get("provider") or state.get("openclaw_provider"),
        model=stage_config.get("model") or state.get("openclaw_model"),
        runner=stage_config.get("runner") or state.get("openclaw_runner"),
        agent=stage_config.get("agent") or state.get("openclaw_agent"),
    )


def _openclaw_reviewer_session_key(state: R2AState, iteration: int) -> str:
    run_id = str(state.get("run_id", "run") or "run")
    safe_run_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", run_id).strip("-") or "run"
    return f"r2a-reviewer-{safe_run_id}-{int(iteration)}-{int(time.time())}"


def _write_reviewer_transaction_failure(
    state: R2AState,
    transaction: dict[str, object],
    warnings: list[str],
    *,
    force: bool = True,
) -> R2AState:
    repo = require_repo_dir(state["repo_path"])
    output = report_path(repo, "review")
    feedback_path = report_path(repo, "review_feedback")
    output.parent.mkdir(parents=True, exist_ok=True)
    issues = [str(issue) for issue in transaction.get("issues", [])] or ["Reviewer transaction validation failed."]
    failure_category = str(transaction.get("failure_category") or "REVIEWER_TRANSACTION_FAILED")
    execution_status = str(transaction.get("execution_status") or "REVIEWER_TRANSACTION_FAILED")
    suggested_next_action = "Stop for deterministic recheck or human review of the Reviewer transaction failure."
    check_report = Path(state.get("check_report_path", report_path(repo, "check")))
    execution_report = Path(state.get("execution_report_path", report_path(repo, "execution")))
    execution_outcome = _execution_outcome(repo, _read(check_report), _read(execution_report))
    major_issues = [f"{failure_category}: {issue}" for issue in issues]
    should_iterate = False
    feedback = _build_review_feedback(
        state,
        verdict="NEEDS_FIX",
        should_iterate=should_iterate,
        major_issues=major_issues,
        execution_outcome={**execution_outcome, "status": execution_status},
        suggested_next_action=suggested_next_action,
    )
    feedback["reviewer_transaction"] = {
        "validation_status": transaction.get("validation_status", "FAIL"),
        "failure_category": failure_category,
        "execution_status": execution_status,
        "committed": bool(transaction.get("committed", False)),
        "staging_dir": transaction.get("staging_dir", ""),
        "candidate_verdict": transaction.get("candidate_verdict", ""),
        "issues": issues,
    }
    if force or not output.exists():
        output.write_text(
            "# REVIEW_REPORT\n\n"
            "## Verdict\n\nNEEDS_FIX\n\n"
            "## Reviewer Transaction\n\n"
            f"- Validation Status: {transaction.get('validation_status', 'FAIL')}\n"
            f"- Failure Category: {failure_category}\n"
            f"- Execution Status: {execution_status}\n"
            f"- Committed: {str(bool(transaction.get('committed', False))).lower()}\n"
            f"- Staging Dir: {transaction.get('staging_dir', '')}\n\n"
            "## Required Fixes\n\n"
            f"{bullet_list(issues)}\n\n"
            "## Suggested Next Action\n\n"
            f"{suggested_next_action}\n",
            encoding="utf-8",
        )
    _write_review_feedback(feedback_path, feedback)
    _write_transaction_failure_review_verdict(
        repo,
        state=state,
        transaction=transaction,
        issues=issues,
        failure_category=failure_category,
        execution_status=execution_status,
    )
    warnings = [*warnings, f"Reviewer transaction failed: {failure_category}"]
    updated = {
        **state,
        "review_report_path": str(output),
        "review_feedback_path": str(feedback_path),
        "latest_review_report_path": str(output),
        "latest_review_feedback_path": str(feedback_path),
        "reviewer_verdict": "NEEDS_FIX",
        "reviewer_executed": True,
        "need_replan": should_iterate,
        "suggested_next_action": suggested_next_action,
        "warnings": warnings,
        "reviewer_transaction": transaction,
        "stopped": True,
        "loop_status": "reviewer_transaction_failed",
        "stop_reason": failure_category,
    }
    return _with_evidence_decision(updated)


def _write_transaction_failure_review_verdict(
    repo: Path,
    *,
    state: R2AState,
    transaction: dict[str, object],
    issues: list[str],
    failure_category: str,
    execution_status: str,
) -> None:
    proposed = _proposed_verdict_context_from_transaction(transaction)
    payload = build_review_verdict_payload(
        verdict="NEEDS_FIX",
        accepted_level="UNASSESSED",
        level_valid=False,
        target_level=str(state.get("target_reproduction_level", "") or ""),
        target_reached=False,
        evidence_files=[],
        limitations=[],
        needs_fix_reasons=issues,
        backend=str(state.get("reviewer_backend", "") or ""),
        source="reviewer_transaction_failure",
    )
    payload.update(
        {
            "validation_status": str(transaction.get("validation_status") or "FAIL"),
            "failure_category": failure_category,
            "execution_status": execution_status,
            "committed": bool(transaction.get("committed", False)),
            "proposed_verdict": proposed.get("proposed_verdict", ""),
            "proposed_accepted_level": proposed.get("proposed_accepted_level", ""),
            "proposed_target_reached": proposed.get("proposed_target_reached", False),
            "failure_issues": issues,
        }
    )
    write_review_verdict(review_verdict_path(repo), payload)


def _proposed_verdict_context_from_transaction(transaction: dict[str, object]) -> dict[str, object]:
    data: dict[str, Any] = {}
    staging_dir = Path(str(transaction.get("staging_dir") or ""))
    feedback = staging_dir / "REVIEW_FEEDBACK.json"
    if feedback.exists():
        try:
            parsed = json.loads(feedback.read_text(encoding="utf-8", errors="replace"))
            if isinstance(parsed, dict):
                data = parsed
        except (OSError, json.JSONDecodeError):
            data = {}
    proposed_verdict = normalize_verdict_token(
        data.get("verdict")
        or data.get("status")
        or transaction.get("candidate_verdict")
    )
    proposed_accepted_level = (
        data.get("accepted_level")
        or data.get("current_reproduction_level")
        or data.get("current_level")
        or data.get("reproduction_level")
        or ""
    )
    if not proposed_accepted_level and is_pass_like_verdict(proposed_verdict):
        proposed_accepted_level = infer_level_from_verdict(proposed_verdict)
    return {
        "proposed_verdict": proposed_verdict,
        "proposed_accepted_level": str(proposed_accepted_level or ""),
        "proposed_target_reached": bool(data.get("target_reached", False)),
    }


def _with_evidence_decision(state: R2AState) -> R2AState:
    """Reviewer 写入正式等级字段。

    Reviewer 是 current_reproduction_level 的唯一业务写入者。
    其他模块只能读取。

    核心原则：
    1. AI backend 直接返回等级，Python 只做协议校验
    2. 无效输出不更新等级，保留上一轮有效值
    3. rules backend 不生成正式等级
    4. 不使用文件推断、verdict 映射或硬编码规则
    5. Safety Override 触发时，不更新 level_source=ai_backend
    """
    from r2a.core.reviewer_level_judgment import (
        LEVEL_LABELS,
        LEVEL_SEMANTICS,
        is_valid_level,
        collect_evidence_artifacts,
        normalize_level,
    )

    repo = require_repo_dir(state["repo_path"])
    iteration = int(state.get("iteration", 1) or 1)
    backend = state.get("reviewer_backend", "rules")
    safety_override_triggered = state.get("safety_override_triggered", False)

    # 收集证据产物，供参考（只收集事实，不判断等级）
    evidence_artifacts = collect_evidence_artifacts(repo)

    review_verdict_file = review_verdict_path(repo)
    structured_verdict = load_review_verdict(review_verdict_file)
    if not safety_override_triggered and structured_verdict.valid:
        path = report_path(repo, "evidence_decision")
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = build_evidence_decision_from_review_verdict(
            structured_verdict,
            iteration=iteration,
            backend=str(backend),
        )
        payload["evidence_artifact_count"] = evidence_artifacts.get("count", 0)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        accepted_level = payload.get("current_reproduction_level")
        if payload.get("level_valid") is True:
            return {
                **state,
                "evidence_decision_path": str(path),
                "current_reproduction_level": accepted_level,
                "current_level_iteration": iteration,
                "achieved_reproduction_level": accepted_level,
                "reproduction_level": accepted_level,
                "level_reasoning": payload.get("level_reasoning", ""),
                "supporting_artifacts": list(payload.get("supporting_artifacts", []) or []),
                "remaining_gaps": list(payload.get("remaining_gaps", []) or []),
                "level_source": payload.get("level_source", "reviewer_structured_verdict"),
                "reviewer_level_valid": True,
            }
        return {
            **state,
            "evidence_decision_path": str(path),
            "reviewer_level_valid": False,
            "reviewer_level_rejection_reason": payload.get("level_reasoning", ""),
            "current_reproduction_level": None,
            "current_level_iteration": 0,
            "level_source": payload.get("level_source", "unassessed"),
        }

    legacy_markdown_payload = None
    if not safety_override_triggered and report_path(repo, "review").exists():
        legacy_markdown_payload = _evidence_decision_from_legacy_markdown(
            repo,
            iteration=iteration,
            backend=str(backend),
            structured_errors=structured_verdict.errors if review_verdict_file.exists() else [],
        )
    if legacy_markdown_payload is not None:
        path = report_path(repo, "evidence_decision")
        path.parent.mkdir(parents=True, exist_ok=True)
        legacy_markdown_payload["evidence_artifact_count"] = evidence_artifacts.get("count", 0)
        path.write_text(json.dumps(legacy_markdown_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        accepted_level = legacy_markdown_payload.get("current_reproduction_level")
        if legacy_markdown_payload.get("level_valid") is True:
            return {
                **state,
                "evidence_decision_path": str(path),
                "current_reproduction_level": accepted_level,
                "current_level_iteration": iteration,
                "achieved_reproduction_level": accepted_level,
                "reproduction_level": accepted_level,
                "level_reasoning": legacy_markdown_payload.get("level_reasoning", ""),
                "supporting_artifacts": list(legacy_markdown_payload.get("supporting_artifacts", []) or []),
                "remaining_gaps": list(legacy_markdown_payload.get("remaining_gaps", []) or []),
                "level_source": "legacy_markdown_parser",
                "reviewer_level_valid": True,
            }
        return {
            **state,
            "evidence_decision_path": str(path),
            "reviewer_level_valid": False,
            "reviewer_level_rejection_reason": legacy_markdown_payload.get("level_reasoning", ""),
            "current_reproduction_level": None,
            "current_level_iteration": 0,
            "level_source": "unassessed",
        }

    # 初始化
    reviewer_level: str | None = None
    level_reasoning: str = ""
    supporting_artifacts: list[str] = []
    remaining_gaps: list[str] = []
    level_valid = False
    level_source = "none"

    # 只有 AI backend 才能生成正式等级，且未触发 Safety Override
    if backend in {"codex", "claude", "openclaw"} and not safety_override_triggered:
        structured_feedback = state.get("structured_review_feedback")

        if isinstance(structured_feedback, dict):
            raw_level = structured_feedback.get("current_reproduction_level")
            raw_reasoning = structured_feedback.get("level_reasoning", "")
            raw_supporting = structured_feedback.get("supporting_artifacts", [])
            raw_gaps = structured_feedback.get("remaining_gaps", [])

            # 协议校验：等级是否合法
            if is_valid_level(raw_level):
                # 协议校验：reasoning 是否非空
                reasoning_str = str(raw_reasoning or "").strip()
                if reasoning_str:
                    reviewer_level = normalize_level(raw_level)
                    level_reasoning = reasoning_str
                    supporting_artifacts = list(raw_supporting) if isinstance(raw_supporting, list) else []
                    remaining_gaps = list(raw_gaps) if isinstance(raw_gaps, list) else []
                    level_valid = True
                    level_source = "ai_backend"
                else:
                    # reasoning 为空，无效输出
                    level_reasoning = f"AI backend returned valid level {raw_level} but empty reasoning. Output rejected."
                    level_source = "invalid_empty_reasoning"
            else:
                # 等级非法，无效输出
                level_reasoning = f"AI backend returned invalid level: {raw_level}. Output rejected."
                level_source = "invalid_level"
        else:
            # 没有结构化反馈
            level_reasoning = "AI backend did not return structured feedback. Output rejected."
            level_source = "no_structured_feedback"
    elif backend in {"codex", "claude", "openclaw"} and safety_override_triggered:
        # Safety Override 触发：本轮 verdict 不是 AI 有效输出
        # 不更新正式等级，保留上一轮有效值
        level_reasoning = "Safety Override triggered: verdict was overridden by rules, not AI backend output. Level not updated."
        level_source = state.get("level_source", "unassessed")  # 保留历史值
    else:
        # rules backend 不生成正式等级，不更新任何正式快照字段
        # 保留历史正式等级、iteration、source、reasoning 等
        level_reasoning = "rules/template backend does not perform semantic level judgment. Level not updated."
        # 不设置 level_source，避免覆盖历史正式来源
        level_source = state.get("level_source", "unassessed")  # 保留历史值

    # 无效输出处理：不更新等级，保留上一轮有效值
    # 但必须检查历史等级来源是否有效：
    # - 只有 level_source=ai_backend 且 reviewer_level_valid=true 的历史等级才应保留
    # - 如果历史等级是脏状态（level_source=unassessed 或 reviewer_level_valid=false），
    #   不应保留，应重置为 None
    if not level_valid:
        # 检查历史等级是否有效
        previous_level = state.get("current_reproduction_level")
        previous_iteration = state.get("current_level_iteration", 0)
        previous_level_source = state.get("level_source", "unassessed")
        previous_level_valid = state.get("reviewer_level_valid", False)

        # 只有历史等级来源有效时才保留
        # 有效条件：level_source=ai_backend 且 reviewer_level_valid=true
        if previous_level and previous_level_source == "ai_backend" and previous_level_valid:
            # 保留有效的历史等级
            pass
        else:
            # 历史等级无效或不存在，清空
            previous_level = None
            previous_iteration = 0
            # level_source 保持当前计算的值（invalid_xxx 或 safety_override）

        # 写入 EVIDENCE_DECISION.json 记录失败原因
        path = report_path(repo, "evidence_decision")
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "current_reproduction_level": previous_level,
            "level_label": LEVEL_LABELS.get(previous_level, previous_level) if previous_level else None,
            "level_semantics": LEVEL_SEMANTICS.get(previous_level, "") if previous_level else None,
            "level_reasoning": level_reasoning,
            "supporting_artifacts": [],
            "remaining_gaps": [],
            "verdict": state.get("reviewer_verdict", ""),
            "iteration": iteration,
            "level_source": level_source,
            "level_valid": False,
            "backend": backend,
            "evidence_artifact_count": evidence_artifacts.get("count", 0),
            "previous_level_iteration": previous_iteration,
            "previous_level_source": previous_level_source,
            "previous_level_valid": previous_level_valid,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        # 不更新等级字段，只有历史等级有效时才保留
        return {
            **state,
            "evidence_decision_path": str(path),
            "reviewer_level_valid": False,
            "reviewer_level_rejection_reason": level_reasoning,
            # 只有历史等级有效时才保留，否则清空
            "current_reproduction_level": previous_level,
            "current_level_iteration": previous_iteration,
            "level_source": level_source,
        }

    # 有效输出：写入正式等级
    path = report_path(repo, "evidence_decision")
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "current_reproduction_level": reviewer_level,
        "level_label": LEVEL_LABELS.get(reviewer_level, reviewer_level),
        "level_semantics": LEVEL_SEMANTICS.get(reviewer_level, ""),
        "level_reasoning": level_reasoning,
        "supporting_artifacts": supporting_artifacts,
        "remaining_gaps": remaining_gaps,
        "verdict": state.get("reviewer_verdict", ""),
        "iteration": iteration,
        "level_source": level_source,
        "level_valid": True,
        "backend": backend,
        "evidence_artifact_count": evidence_artifacts.get("count", 0),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        **state,
        "evidence_decision_path": str(path),
        # 新字段：Reviewer 唯一写入的正式等级
        "current_reproduction_level": reviewer_level,
        "current_level_iteration": iteration,
        # 兼容字段：由 Reviewer 同步，其他模块不得独立写入
        "achieved_reproduction_level": reviewer_level,
        "reproduction_level": reviewer_level,
        # 新增：等级推理和证据
        "level_reasoning": level_reasoning,
        "supporting_artifacts": supporting_artifacts,
        "remaining_gaps": remaining_gaps,
        "level_source": level_source,
        "reviewer_level_valid": True,
    }


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _execution_outcome(repo: Path, check_text: str, execution_text: str) -> dict[str, object]:
    result_csvs = _result_csvs(repo)
    statuses: list[str] = []
    blocking_lines: list[str] = []
    text_blob_parts = [check_text, execution_text]
    executor_status = _execution_report_status(execution_text)
    executor_exit_code = _execution_report_exit_code(execution_text)
    for path in result_csvs:
        for row in _read_csv_rows(path):
            status = _first_present(row, ("status", "verdict", "result")).upper()
            if status:
                statuses.append(status)
                if status in BLOCKING_STATUS_LABELS:
                    reason = _first_present(row, ("reason", "notes", "next_action", "message"))
                    blocking_lines.append(f"{path.name}: {status}: {reason or 'no reason field'}")
            text_blob_parts.extend(str(value) for value in row.values())

    artifact_results = repo / ".r2a" / "results"
    for path in (artifact_results / "ENGINEER_DONE.txt", artifact_results / "ENGINEER_NOTES.md"):
        if path.exists():
            text_blob_parts.append(_read(path))

    done_status = "MISSING"
    done_path = artifact_results / "ENGINEER_DONE.txt"
    if done_path.exists() and _read(done_path).strip():
        done_status = _read(done_path).strip().splitlines()[0].strip().upper()

    blocking_statuses = sorted({status for status in statuses if status in BLOCKING_STATUS_LABELS})
    executor_failed = executor_status == "failed"
    effective_blocking_statuses = [*blocking_statuses, *(["FAIL"] if executor_failed else [])]
    status = _combined_execution_status(done_status, effective_blocking_statuses)
    failure_categories = set(_detect_failure_categories("\n".join(text_blob_parts)))
    if executor_failed:
        failure_categories.add("TOOLCHAIN_OR_ENVIRONMENT")
        blocking_lines.append(
            f"EXECUTION_REPORT.md: external Engineer executor failed"
            f"{f' with exit_code={executor_exit_code}' if executor_exit_code else ''}."
        )
    result_names = {path.name.lower() for path in result_csvs}
    if done_status == "MISSING" and "build_smoke.csv" in result_names:
        failure_categories.add("ENGINEER_TIMEOUT_AFTER_BUILD")
    if "reduced_demo_metrics.csv" in result_names:
        failure_categories.add("DEMO_ONLY")
    lines = [
        f"ENGINEER_DONE status: {done_status}",
        f"overall execution status: {status}",
        f"EXECUTION_REPORT status: {executor_status or 'not found'}",
        f"EXECUTION_REPORT exit_code: {executor_exit_code or 'not found'}",
        f"CSV status labels: {', '.join(sorted(set(statuses))) if statuses else 'None detected'}",
        f"blocking status labels: {', '.join(blocking_statuses) if blocking_statuses else 'None detected'}",
    ]
    lines.extend(blocking_lines[:10])
    return {
        "status": status,
        "blocking_statuses": blocking_statuses,
        "blocking_lines": blocking_lines,
        "failure_categories": sorted(failure_categories),
        "summary": bullet_list(lines),
    }


def _execution_report_status(text: str) -> str:
    match = re.search(r"^\s*-\s*status:\s*([A-Za-z0-9_\-]+)\s*$", text, flags=re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip().lower() if match else ""


def _execution_report_exit_code(text: str) -> str:
    match = re.search(r"^\s*-\s*exit_code:\s*([0-9\-]+)\s*$", text, flags=re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else ""


def _build_review_feedback(
    state: R2AState,
    *,
    verdict: str,
    should_iterate: bool,
    major_issues: list[str],
    execution_outcome: dict[str, object],
    suggested_next_action: str,
) -> dict[str, object]:
    """构造 canonical REVIEW_FEEDBACK.json。

    核心原则：
    1. 所有等级字段只来自已校验的正式等级或历史快照
    2. 不调用 infer_evidence_level() 或 next_level_after_verdict()
    3. REVIEW_FEEDBACK 与 EVIDENCE_DECISION 必须一致
    """
    categories = list(execution_outcome.get("failure_categories", []))
    repo = Path(state["repo_path"])

    # 从 state 读取正式等级，不进行文件推断
    evidence_current_level = str(state.get("current_reproduction_level", "") or "")
    if not evidence_current_level:
        evidence_current_level = "UNASSESSED"

    # 从 state 读取正式等级的关联字段
    current_level_iteration = int(state.get("current_level_iteration", 0) or 0)
    level_source = str(state.get("level_source", "unassessed") or "unassessed")
    level_reasoning = str(state.get("level_reasoning", "") or "")
    supporting_artifacts = list(state.get("supporting_artifacts", []) or [])
    remaining_gaps = list(state.get("remaining_gaps", []) or [])

    decision_state = update_state_with_workflow_decision(state, verdict=verdict, should_iterate=should_iterate)
    workflow_blockers = list(decision_state.get("workflow_blockers", []))
    workflow_decision = dict(decision_state.get("workflow_decision", {}) or {})
    should_iterate = bool(should_iterate and workflow_decision.get("should_iterate", True))

    # 不再推断下一轮等级，保持当前正式等级
    next_level = evidence_current_level

    manager_blockers = _active_blockers_from_current_manager(state, major_issues)
    workflow_messages = [] if manager_blockers else _workflow_blocker_messages(workflow_blockers, workflow_decision)
    active_blockers = _dedupe([*manager_blockers, *workflow_messages])
    blocker_source = active_blockers or major_issues
    resolved_issues = _resolved_issues_from_previous_feedback(state, active_blockers)

    return {
        "schema_version": 1,
        "iteration": int(state.get("iteration", 1)),
        "review_stage_status": "PASS",
        "iteration_summary": suggested_next_action,
        "plan_quality_issues": [],
        "engineering_issues": major_issues,
        "evidence_gaps": _missing_l3_requirements(repo) + _missing_l4_alignment(repo),
        "next_iteration_guidance": _recommended_task_scope(categories, execution_outcome),
        "do_not_repeat": _forbidden_next_actions(categories),
        "suggested_plan_constraints": {
            "next_reproduction_level": next_level,
            "max_evidence_level_allowed": evidence_current_level,
        },
        "verdict": verdict,
        "should_iterate": bool(should_iterate),
        "workflow_decision": workflow_decision,
        "workflow_blockers": workflow_blockers,
        "next_planner_mode": _next_planner_mode(verdict) if should_iterate else "none",
        # 正式等级字段（与 EVIDENCE_DECISION 一致）
        "current_reproduction_level": evidence_current_level,
        "current_level_iteration": current_level_iteration,
        "level_source": level_source,
        "level_reasoning": level_reasoning,
        "supporting_artifacts": supporting_artifacts,
        "remaining_gaps": remaining_gaps,
        # 兼容字段（镜像正式等级）
        "current_level": evidence_current_level,
        "next_level": next_level,
        "reproduction_level": evidence_current_level,
        "next_reproduction_level": next_level,
        "max_evidence_level_allowed": evidence_current_level,
        "claim_allowed": _claim_allowed_for_level(verdict, evidence_current_level),
        "missing_l3_requirements": _missing_l3_requirements(repo),
        "missing_l4_alignment": _missing_l4_alignment(repo),
        "l4_alignment_status": _l4_alignment_status(repo, verdict),
        "l4_alignment_summary_path": str(repo / ".r2a" / "results" / "L4_ALIGNMENT_SUMMARY.md"),
        "reproduction_level": evidence_current_level,
        "next_reproduction_level": next_level,
        "target_reproduction_level": target_level(state),
        "claim_level": claim_level_for_verdict(verdict),
        "execution_status": execution_outcome.get("status", "UNKNOWN"),
        "failure_categories": categories,
        "preserve_successful_steps": _preserve_successful_steps(categories, execution_outcome),
        "active_blockers": active_blockers,
        "resolved_issues": resolved_issues,
        "history": _feedback_history(state, active_blockers, resolved_issues),
        "required_fixes": _required_fixes(verdict, blocker_source, execution_outcome),
        "forbidden_next_actions": _forbidden_next_actions(categories),
        "recommended_task_scope": _recommended_task_scope(categories, execution_outcome),
        "suggested_next_action": suggested_next_action,
        "evidence": {
            "check_report_path": state.get("check_report_path", str(report_path(state["repo_path"], "check"))),
            "execution_report_path": state.get("execution_report_path", str(report_path(state["repo_path"], "execution"))),
            "review_report_path": str(report_path(state["repo_path"], "review")),
        },
    }


def _max_evidence_level_allowed_for_verdict(verdict: str) -> str:
    if verdict == "PASS_REDUCED_ALIGNED":
        return "L4_reduced_paper_aligned"
    if verdict == "PASS_REDUCED_METHOD_ONLY":
        return "L4_reduced_paper_aligned"
    if verdict == "INPUT_CONTRACT_READY":
        return "L3_official_reduced_run"
    if verdict in {"PASS_SMOKE_ONLY", "PASS_DEMO_ONLY", "NEEDS_OFFICIAL_INPUT", "NEEDS_INPUT_OR_BUDGET"}:
        return "L2_input_contract_ready"
    return infer_level_from_verdict(verdict)


def _do_not_downgrade_level(current: str, proposed: str) -> str:
    order = (
        "L0_project_health",
        "L1_source_artifact_verified",
        "L2_input_contract_ready",
        "L3_official_reduced_run",
        "L4_reduced_paper_aligned",
        "L5_minimal_baseline_comparison",
        "L6_full_or_near_full_reproduction",
    )
    try:
        return current if order.index(proposed) < order.index(current) else proposed
    except ValueError:
        return current


def _claim_allowed(verdict: str) -> str:
    if verdict in {"MANAGER_CLASSIFICATION_CONFLICT", "NEEDS_DETERMINISTIC_RECHECK", "HUMAN_REVIEW_REQUIRED", "PASS_WITH_REVIEW_CONFLICT"}:
        return "blocked_manager_reviewer_conflict"
    if verdict == "PASS_REDUCED_ALIGNED":
        return "reduced_paper_aligned_only"
    if verdict == "PASS_REDUCED_METHOD_ONLY":
        return "official_reduced_method_only"
    if verdict == "INPUT_CONTRACT_READY":
        return "input_contract_ready_only"
    if verdict == "PASS_SMOKE_ONLY":
        return "source_or_runtime_smoke_only"
    if verdict == "PASS_DEMO_ONLY":
        return "demo_only_not_paper_reproduction"
    if verdict in {"NEEDS_OFFICIAL_INPUT", "NEEDS_INPUT_OR_BUDGET"}:
        return "blocked_no_reproduction_claim"
    return "limited_or_unresolved"


def _claim_allowed_for_level(verdict: str, level: str) -> str:
    if level == "L2_input_contract_ready" and verdict == "PASS_SMOKE_ONLY":
        return "input_contract_ready_or_l2_capped_verification_only"
    return _claim_allowed(verdict)


def _active_blockers_from_current_manager(state: R2AState, fallback_issues: list[str]) -> list[str]:
    repo = Path(state["repo_path"])
    check_path = Path(state.get("check_report_path", report_path(repo, "check")))
    check_text = _read(check_path)
    if _check_status(check_text) != "FAIL":
        return []
    errors = [
        item
        for item in _extract_list_section(check_text, "Errors")
        if item and item not in {"None", "- None"}
    ]
    if errors:
        return _dedupe(errors)
    manager_decision = _manager_blocking_errors(repo)
    if manager_decision:
        return manager_decision
    return _dedupe(fallback_issues)


def _workflow_blocker_messages(blockers: list[dict[str, Any]], decision: dict[str, Any]) -> list[str]:
    if decision.get("kind") != "request_user_input":
        return []
    active_ids = set(decision.get("active_blocker_ids", []) or [])
    messages: list[str] = []
    for blocker in blockers:
        if active_ids and blocker.get("id") not in active_ids:
            continue
        severity = str(blocker.get("severity", "") or "").upper()
        if severity and severity != "BLOCKING":
            continue
        message = str(blocker.get("message", "") or "").strip()
        blocker_id = str(blocker.get("id", "") or "").strip()
        if not message and blocker_id:
            message = blocker_id
        if message:
            messages.append(f"{blocker_id}: {message}" if blocker_id and blocker_id not in message else message)
    return _dedupe(messages)


def _has_user_input_workflow_blocker(state: R2AState) -> bool:
    blockers = collect_workflow_blockers(state)
    decision = build_workflow_decision(state, verdict=str(state.get("reviewer_verdict", "") or ""), blockers=blockers)
    return decision.get("kind") == "request_user_input"


def _manager_blocking_errors(repo: Path) -> list[str]:
    path = report_path(repo, "manager_decision")
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("blocking_errors", [])
    return _dedupe([str(item) for item in items if str(item).strip()]) if isinstance(items, list) else []


def _resolved_issues_from_previous_feedback(state: R2AState, active_blockers: list[str]) -> list[str]:
    previous = _load_previous_feedback(state)
    if not previous:
        return []
    old_items: list[str] = []
    for key in ("active_blockers", "required_fixes"):
        value = previous.get(key)
        if isinstance(value, list):
            old_items.extend(str(item) for item in value if str(item).strip())
    active_fingerprints = {_issue_fingerprint(item) for item in active_blockers}
    return _dedupe([item for item in old_items if _issue_fingerprint(item) not in active_fingerprints])


def _feedback_history(state: R2AState, active_blockers: list[str], resolved_issues: list[str]) -> list[dict[str, object]]:
    previous = _load_previous_feedback(state)
    history = previous.get("history", []) if isinstance(previous, dict) else []
    if not isinstance(history, list):
        history = []
    return [
        *history[-5:],
        {
            "iteration": int(state.get("iteration", 1)),
            "active_blockers": active_blockers,
            "resolved_issues": resolved_issues,
        },
    ]


def _load_previous_feedback(state: R2AState) -> dict[str, object]:
    path_value = str(state.get("latest_review_feedback_path") or state.get("review_feedback_path") or "")
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _issue_fingerprint(value: str) -> str:
    text = str(value).lower()
    text = re.sub(r"row\s+\d+", "row", text)
    text = re.sub(r"\biter(?:ation)?[_ -]?\d+\b", "iter", text)
    return re.sub(r"[^a-z0-9_.]+", " ", text).strip()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _missing_l3_requirements(repo: Path) -> list[str]:
    result_csvs = _result_csvs(repo)
    reduced_rows = _rows_from_named_csv(result_csvs, "reduced_metrics.csv")
    input_rows = _rows_from_named_csv(result_csvs, "input_contract_verification.csv")
    missing: list[str] = []
    cap_reason = contract_l2_cap_reason(repo)
    if cap_reason:
        missing.append(f"contract L2 cap: capped at L2 because {cap_reason}")
    if not _has_verified_source_artifact(result_csvs):
        missing.append("official source/artifact with commit/branch/tag")
    if not _has_smoke_evidence(result_csvs):
        missing.append("auditable build/import/runtime smoke evidence")
    if not _input_contract_ready(input_rows):
        missing.append("official input contract: dataset, query files, ground truth, metric, method, command, parameters")
    if rows_have_input_integrity_blocker(input_rows) or summarize_official_input_integrity(repo).get("has_blocking_issue"):
        missing.append("official input integrity: non-empty and parseable database/query/ground truth inputs")
    if not any(_row_has_l3_reduced_contract(row) and _row_has_real_measured_metric(row) for row in reduced_rows):
        missing.append("measured reduced_metrics.csv row with dataset/method/k/metric/ground truth/input provenance")
    if not any(_has_command_provenance(repo, row, result_csvs, "reduced_metrics.csv") for row in reduced_rows):
        missing.append("command provenance with command_id, command, exit_code, duration_sec, log_path, artifact hash/path, input provenance")
    return missing


def _missing_l4_alignment(repo: Path) -> list[str]:
    result_csvs = _result_csvs(repo)
    if not _has_l4_paper_alignment(result_csvs):
        return [
            "paper_alignment.csv with schema paper_item,setting_name,paper_setting,reduced_setting,match_status,evidence_source,notes",
            "alignment rows for dataset scale, hardware, runtime budget, parameters, repeats, baselines, metric definition, input source, and evidence gaps",
        ]
    return []


def _l4_alignment_status(repo: Path, verdict: str) -> str:
    result_csvs = _result_csvs(repo)
    if verdict == "PASS_REDUCED_ALIGNED" and _has_l4_paper_alignment(result_csvs):
        return "achieved_with_limitations"
    if _has_l4_paper_alignment(result_csvs):
        return "alignment_evidence_present"
    return "not_achieved"


def _l3_satisfaction_summary(repo: Path, verdict: str, language: str = "en") -> str:
    missing = _missing_l3_requirements(repo)
    if verdict in {"PASS_REDUCED_METHOD_ONLY", "PASS_REDUCED_ALIGNED", "PASS_REDUCED_COMPARISON"} and not missing:
        return _t(language, "- L3 is satisfied: official reduced metrics and command provenance are present.", "- L3 is satisfied: official reduced metrics and command provenance are present.")
    return bullet_list(missing or ["L3 has not been claimed by Reviewer."])


def _l4_satisfaction_summary(repo: Path, verdict: str, language: str = "en") -> str:
    missing = _missing_l4_alignment(repo)
    if verdict == "PASS_REDUCED_ALIGNED" and not missing:
        return _t(language, "- L4 is satisfied with limitations: reduced metrics are mapped to paper settings in paper_alignment.csv.", "- L4 is satisfied with limitations: reduced metrics are mapped to paper settings in paper_alignment.csv.")
    return bullet_list(missing or ["L4 has not been claimed by Reviewer."])


def _not_full_reproduction_summary(verdict: str, language: str = "en") -> str:
    if verdict == "PASS_REDUCED_ALIGNED":
        return _t(
            language,
            "- 这只是 reduced paper-aligned evidence，不是完整论文复现；完整复现需要 L6 和用户授权的完整数据/算力/基线矩阵。",
            "- This is reduced paper-aligned evidence, not full-paper reproduction; full reproduction requires L6 plus user-authorized full data, compute, and baseline matrix.",
        )
    return _t(
        language,
        "- Reviewer did not claim full-paper reproduction.",
        "- Reviewer did not claim full-paper reproduction.",
    )


def _next_planner_mode(verdict: str) -> str:
    if verdict in ITERATION_VERDICTS:
        return "iterative_progress"
    return "none"


def _write_review_feedback(path: Path, feedback: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(feedback, indent=2, ensure_ascii=False), encoding="utf-8")


def _preserve_successful_steps(categories: list[str], execution_outcome: dict[str, object]) -> list[str]:
    steps = [
        "preserve existing TASK_SPEC evidence unless Planner explicitly changes scope",
        "preserve source/artifact verification CSVs when schema-valid",
        "preserve source/feature localization evidence",
    ]
    status = str(execution_outcome.get("status", "UNKNOWN"))
    if status in {"PARTIAL", "BLOCKED", "FAILED"}:
        steps.append("reuse successful clone/configure/build-smoke evidence when output files still exist")
    if "SAFE_BUILD_COMPATIBILITY" in categories:
        steps.append("reuse the same failing build command as the next minimal verification target")
    return steps


def _forbidden_next_actions(categories: list[str]) -> list[str]:
    actions = [
        "do not fabricate metrics, datasets, baselines, commands, or figure/table values",
        "do not run full-scale benchmarks unless explicitly authorized by the user",
    ]
    if "API_OR_ALGORITHM_SEMANTICS" in categories:
        actions.append("do not rewrite algorithm/API/query semantics without explicit authorization")
    if "SAFE_BUILD_COMPATIBILITY" in categories:
        actions.append("do not apply compatibility patches outside the cloned artifact/workspace scope")
        actions.append("do not change algorithm logic while fixing portability/build issues")
    if "RUNTIME_DLL_COMPATIBILITY" in categories:
        actions.append("do not copy executables to Temp or retry runtime smoke without fixing PATH/DLL context")
    return actions


def _recommended_task_scope(categories: list[str], execution_outcome: dict[str, object]) -> list[str]:
    scope = []
    if "SAFE_BUILD_COMPATIBILITY" in categories:
        scope.append("apply minimal artifact-only build compatibility patch")
        scope.append("rerun the smallest failing configure/build command")
    if "TOOLCHAIN_OR_ENVIRONMENT" in categories:
        scope.append("record explicit local toolchain paths and versions before expensive commands")
    if "MISSING_ARTIFACT_OR_DATA" in categories:
        scope.append("record missing official sample data/scripts/commands as a limitation; continue only with user-authorized inputs or synthetic_demo")
    if "API_OR_ALGORITHM_SEMANTICS" in categories:
        scope.append("after build success, verify filtered kNN API/command/metric contract before changing code")
    if "TIME_BUDGET" in categories:
        scope.append("reuse successful prior stages and target only the next unfinished command")
    if "RUNTIME_DLL_COMPATIBILITY" in categories:
        scope.append("fix or document runtime DLL/PATH/entry-point blocker with runtime_smoke.csv; do not repeat full build first")
    if "ENGINEER_TIMEOUT_AFTER_BUILD" in categories:
        scope.append("reuse build_smoke evidence and target the unfinished runtime or input-contract step")
    if "DEMO_ONLY" in categories:
        scope.append("treat synthetic demo as harness evidence only; require official inputs before paper metric claims")
    return scope or ["create the next smallest evidence-supported reduced experiment task"]


def _result_csvs(repo: Path) -> list[Path]:
    files: list[Path] = []
    for directory in (repo / "results", repo / ".r2a" / "results"):
        if directory.exists():
            files.extend(sorted(directory.glob("*.csv")))
    return files


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    result = sanitized_csv_rows(path)
    if result.has_error and not result.rows:
        return []
    return result.rows


def _first_present(row: dict[str, str], columns: tuple[str, ...]) -> str:
    for column in columns:
        value = row.get(column)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _combined_execution_status(done_status: str, blocking_statuses: list[str]) -> str:
    if "FAILED" in blocking_statuses or done_status == "FAILED":
        return "FAILED"
    if "NEEDS_CLARIFICATION" in blocking_statuses or done_status == "NEEDS_CLARIFICATION":
        return "NEEDS_CLARIFICATION"
    if done_status in {"PASS", "DONE", "OK"} and not (
        set(blocking_statuses)
        & {
            "FAILED",
            "BLOCKED",
            "PARTIAL",
            "NEEDS_OFFICIAL_INPUT",
            "NEEDS_INPUT_OR_BUDGET",
            "NEEDS_CLARIFICATION",
        }
    ):
        return done_status
    if "NEEDS_INPUT" in blocking_statuses or done_status == "NEEDS_INPUT":
        return "NEEDS_INPUT"
    if "NEEDS_INPUT_OR_BUDGET" in blocking_statuses or done_status == "NEEDS_INPUT_OR_BUDGET":
        return "NEEDS_INPUT_OR_BUDGET"
    if "NEEDS_OFFICIAL_INPUT" in blocking_statuses or done_status == "NEEDS_OFFICIAL_INPUT":
        return "NEEDS_OFFICIAL_INPUT"
    if "NOT_AVAILABLE" in blocking_statuses or done_status == "NOT_AVAILABLE":
        return "NEEDS_INPUT"
    if "BLOCKED" in blocking_statuses or done_status == "BLOCKED":
        return "BLOCKED"
    if "PARTIAL" in blocking_statuses or done_status == "PARTIAL":
        return "PARTIAL"
    if done_status == "MISSING":
        return "UNKNOWN"
    return done_status or "UNKNOWN"


def _detect_failure_categories(text: str) -> list[str]:
    upper = text.upper()
    categories = {label for label in FAILURE_CATEGORY_LABELS if label in upper}
    lowered = text.lower()
    if any(marker in lowered for marker in ("cstdint", "cstddef", "cstring", "compiler", "mingw", "msvc", "cmake", "ninja")):
        categories.add("SAFE_BUILD_COMPATIBILITY")
    if any(marker in lowered for marker in ("missing executable", "not recognized", "not found", "toolchain", "environment", "dependency")):
        categories.add("TOOLCHAIN_OR_ENVIRONMENT")
    if any(marker in lowered for marker in ("artifact unavailable", "dataset unavailable", "missing artifact", "missing dataset", "source unavailable")):
        categories.add("MISSING_ARTIFACT_OR_DATA")
    if any(marker in lowered for marker in ("api", "semantic", "query vector", "type mismatch", "algorithm logic", "index semantics")):
        categories.add("API_OR_ALGORITHM_SEMANTICS")
    if any(marker in lowered for marker in ("timeout", "time budget", "exceeded")):
        categories.add("TIME_BUDGET")
    if any(marker in lowered for marker in ("nanosleep64", "entry point", "missing dll", "loader", "dynamic link library", "runtime_smoke")):
        categories.add("RUNTIME_DLL_COMPATIBILITY")
    if any(marker in lowered for marker in ("demo_only", "synthetic_input", "not_paper_reproduction", "reduced_demo_metrics")):
        categories.add("DEMO_ONLY")
    if "needs_official_input" in lowered:
        categories.add("MISSING_ARTIFACT_OR_DATA")
    if "needs_input_or_budget" in lowered or "download budget" in lowered:
        categories.add("MISSING_ARTIFACT_OR_DATA")
    return sorted(categories)


def _file_excerpt(path: Path, limit: int = 4000) -> str:
    if not path.exists():
        return f"MISSING: {path}"
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return f"EMPTY: {path}"
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...(truncated)"


def _extract_verdict(text: str) -> str:
    """Extract verdict from REVIEW_REPORT.md.

    Supported formats (in priority order):
    1. Independent `## Verdict` section (highest priority)
    2. Explicit labeled verdict lines:
       - `**Verdict: PASS_REDUCED_ALIGNED**`
       - `Verdict: PASS_REDUCED_ALIGNED`
       - `审查判定: PASS_REDUCED_ALIGNED`
       - `裁决: PASS_REDUCED_ALIGNED`
       - `判定: PASS_REDUCED_ALIGNED`
    3. Verdict/conclusion sections with a standalone verdict token.
    4. A standalone bold verdict token as a conservative fallback.

    Safety: Only accepts project-allowed verdict tokens.
    """
    normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Priority 1: Try independent `## Verdict` section first.
    section = _extract_markdown_heading_section(normalized_text, "Verdict")
    verdict = _match_verdict_token(section)
    if verdict:
        return verdict

    # Priority 2: Try explicit labeled lines/headings in the report body.
    verdict = _extract_labeled_verdict(normalized_text)
    if verdict:
        return verdict

    # Priority 3: Try verdict/conclusion sections with standalone token lines.
    for heading in ("最终裁决", "评审结论", "审查判定", "最终判定", "判决", "裁决", "判定"):
        section = _extract_markdown_heading_section(normalized_text, heading)
        verdict = _extract_labeled_verdict(section, anywhere=True)
        if verdict:
            return verdict
        verdict = _extract_standalone_verdict(section)
        if verdict:
            return verdict

    # Priority 4: Conservative fallback for a standalone bold verdict token.
    verdict = _extract_standalone_bold_verdict(normalized_text)
    if verdict:
        return verdict

    return ""


def _match_verdict_token(text: str) -> str:
    """Match a valid verdict token from text.

    Returns the matched verdict token or empty string if no valid token found.
    """
    if not text:
        return ""
    normalized_candidate = normalize_verdict_token(text)
    if normalized_candidate in VALID_VERDICTS:
        return normalized_candidate
    pattern = _verdict_token_pattern()
    match = re.search(pattern, str(text).upper())
    if match:
        return normalize_verdict_token(match.group(1))
    return ""


def _verdict_token_pattern() -> str:
    tokens = "|".join(re.escape(token) for token in VALID_VERDICTS)
    return rf"(?<![A-Z0-9_])({tokens})(?![A-Z0-9_])"


def _extract_labeled_verdict(text: str, *, anywhere: bool = False) -> str:
    labels = (
        "Verdict",
        "审查判定",
        "评审判定",
        "最终判定",
        "评审结论",
        "最终裁决",
        "判决",
        "裁决",
        "判定",
    )
    label_pattern = "|".join(re.escape(label) for label in labels)
    token_pattern = _verdict_token_pattern()
    prefix = r"(?im)" if anywhere else r"(?im)^\s*(?:[-*]\s*)?(?:#{1,6}\s*)?"
    suffix = r"" if anywhere else r"\s*$"
    # Handle formats like:
    # - **Verdict**: PASS_WITH_LIMITATIONS
    # - **Verdict**: `PASS_WITH_LIMITATIONS`
    # - Verdict: PASS_WITH_LIMITATIONS
    # - Verdict: `PASS_WITH_LIMITATIONS`
    pattern = re.compile(
        rf"{prefix}(?:\*\*)?\s*(?:{label_pattern})\s*(?:\*\*)?\s*[：:]\s*(?:\*\*)?\s*(?:[`\"'\u201c\u2018])?({token_pattern})(?:[`\"'\u201d\u2019])?\s*(?:\*\*)?{suffix}"
    )
    for match in pattern.finditer(text):
        verdict = _match_verdict_token(match.group(1))
        if verdict:
            return verdict
    return ""


def _extract_standalone_verdict(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        stripped = stripped.strip("-* \t")
        stripped = _strip_wrapping_bold(stripped)
        stripped = _strip_wrapping_backticks(stripped)
        verdict = _match_verdict_token(stripped)
        if verdict and re.fullmatch(_verdict_token_pattern(), stripped.upper()):
            return verdict
        break
    return ""


def _extract_standalone_bold_verdict(text: str) -> str:
    # Handle both **VERDICT** and **`VERDICT`** formats
    pattern = re.compile(rf"(?im)^\s*\*\*\s*(?:`)?({_verdict_token_pattern()})(?:`)?\s*\*\*\s*$")
    match = pattern.search(text)
    return _match_verdict_token(match.group(1)) if match else ""


def _strip_wrapping_backticks(value: str) -> str:
    """Strip wrapping backticks from a value.

    Handles: `VALUE` but not mid-text backticks.
    """
    stripped = value.strip()
    if stripped.startswith("`") and stripped.endswith("`") and len(stripped) >= 2:
        return stripped[1:-1].strip()
    return stripped


def _strip_wrapping_bold(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("**") and stripped.endswith("**") and len(stripped) >= 4:
        return stripped[2:-2].strip()
    return stripped


def _extract_markdown_heading_section(text: str, heading: str) -> str:
    escaped = re.escape(heading)
    pattern = re.compile(rf"(?im)^#{{1,6}}\s+{escaped}\s*$")
    match = pattern.search(text)
    if not match:
        return ""
    start = match.end()
    next_heading = re.search(r"(?m)^\s*#{1,6}\s+", text[start:])
    end = start + next_heading.start() if next_heading else len(text)
    return text[start:end].strip()


def _extract_section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    if marker not in text:
        return ""
    after = text.split(marker, 1)[1]
    return after.split("\n## ", 1)[0].strip()


def _extract_list_section(text: str, heading: str) -> list[str]:
    section = _extract_section(text, heading)
    items: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
        elif stripped[:1].isdigit() and ". " in stripped[:5]:
            items.append(stripped.split(". ", 1)[1].strip())
    return items


def _append_safety_override(path: Path, note: str) -> None:
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else "# REVIEW_REPORT\n"
    path.write_text(f"{text.rstrip()}\n\n## Safety Override\n\n{note}\n", encoding="utf-8")


def _force_verdict(path: Path, verdict: str) -> None:
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else "# REVIEW_REPORT\n"
    if "## Verdict" not in text:
        path.write_text(f"{text.rstrip()}\n\n## Verdict\n\n{verdict}\n", encoding="utf-8")
        return
    before, after = text.split("## Verdict", 1)
    rest = after.split("\n## ", 1)
    suffix = "" if len(rest) == 1 else "\n## " + rest[1]
    path.write_text(f"{before}## Verdict\n\n{verdict}\n{suffix}", encoding="utf-8")


def _append_note(path: Path, note: str) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    path.write_text(f"{text.rstrip()}\n\n## Codex Stage Note\n\n{note}\n", encoding="utf-8")


def _check_status(check_text: str) -> str:
    upper = check_text.upper()
    if "\nFAIL" in upper or "STATUS\n\nFAIL" in upper:
        return "FAIL"
    if "\nWARNING" in upper or "STATUS\n\nWARNING" in upper:
        return "WARNING"
    if "\nPASS" in upper or "STATUS\n\nPASS" in upper:
        return "PASS"
    return "UNKNOWN"


def _verdict(status: str, execution_text: str, state: R2AState, execution_outcome: dict[str, object]) -> str:
    if status == "FAIL":
        return "NEEDS_FIX"
    outcome_status = str(execution_outcome.get("status", "UNKNOWN"))
    categories = set(execution_outcome.get("failure_categories", []))
    user_input_blocked = _has_user_input_workflow_blocker(state)
    if outcome_status == "NEEDS_OFFICIAL_INPUT":
        return "NEEDS_OFFICIAL_INPUT"
    if outcome_status == "NEEDS_INPUT":
        return "NEEDS_OFFICIAL_INPUT" if user_input_blocked else "NEEDS_INPUT_OR_BUDGET"
    if outcome_status == "NEEDS_INPUT_OR_BUDGET":
        return "NEEDS_INPUT_OR_BUDGET"
    if outcome_status in {"FAILED", "BLOCKED", "PARTIAL"}:
        if user_input_blocked or "MISSING_ARTIFACT_OR_DATA" in categories:
            return "NEEDS_OFFICIAL_INPUT"
        return "NEEDS_FIX"
    if outcome_status == "NEEDS_CLARIFICATION":
        return "BORDERLINE"
    if "DEMO_ONLY" in categories:
        return "PASS_DEMO_ONLY"
    progression = _progression_verdict(Path(state["repo_path"]))
    if progression:
        return progression
    if state.get("clarification_needed") or _execution_requests_clarification(execution_text):
        return "BORDERLINE"
    if status == "WARNING":
        return "PASS_WITH_LIMITATIONS"
    if "Mock executor completed" in execution_text:
        return "PASS_WITH_LIMITATIONS"
    if status == "PASS":
        return "PASS_WITH_LIMITATIONS"
    return "BORDERLINE"


def _progression_verdict(repo: Path) -> str:
    result_csvs = _result_csvs(repo)
    names = {path.name.lower() for path in result_csvs}
    reduced_rows = _rows_from_named_csv(result_csvs, "reduced_metrics.csv")
    input_rows = _rows_from_named_csv(result_csvs, "input_contract_verification.csv")
    contract_cap = contract_l2_cap_reason(repo)
    if not contract_cap and _has_l3_official_reduced_evidence(repo, result_csvs, reduced_rows, input_rows):
        if _has_l5_fair_baseline_comparison(repo, result_csvs):
            return "PASS_REDUCED_COMPARISON"
        if _has_l4_paper_alignment(result_csvs):
            return "PASS_REDUCED_ALIGNED"
        return "PASS_REDUCED_METHOD_ONLY"
    if _input_contract_ready(input_rows):
        return "INPUT_CONTRACT_READY"
    if {"source_verification.csv", "build_smoke.csv"} & names or "runtime_smoke.csv" in names:
        return "PASS_SMOKE_ONLY"
    return ""


def _rows_from_named_csv(paths: list[Path], name: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths:
        if path.name.lower() == name.lower():
            rows.extend(_read_csv_rows(path))
    return rows


def _has_measured_reduced_rows(rows: list[dict[str, str]]) -> bool:
    return any(_row_has_real_measured_metric(row) for row in rows)


def _has_l3_official_reduced_evidence(
    repo: Path,
    result_csvs: list[Path],
    reduced_rows: list[dict[str, str]],
    input_rows: list[dict[str, str]],
) -> bool:
    if contract_l2_cap_reason(repo):
        return False
    if not _has_verified_source_artifact(result_csvs):
        return False
    if not _has_smoke_evidence(result_csvs):
        return False
    if not _input_contract_ready(input_rows):
        return False
    return any(
        _row_has_l3_reduced_contract(row)
        and _row_has_real_measured_metric(row)
        and not _row_is_l2_capped(row)
        and _has_command_provenance(repo, row, result_csvs, "reduced_metrics.csv")
        for row in reduced_rows
    )


def _input_contract_ready(rows: list[dict[str, str]]) -> bool:
    if not rows:
        return False
    if rows_have_input_integrity_blocker(rows):
        return False
    ready = {"OK", "FOUND", "READY", "PASS", "PASSED"}
    required = {
        "dataset": ("dataset", "database", "index", "input"),
        "query": ("query",),
        "ground_truth": ("ground", "truth", "gt"),
        "metric_definition": ("metric",),
        "method": ("method", "algorithm"),
        "command": ("command", "script", "run"),
        "parameters": ("parameter", "params", "k", "topk", "selectivity", "filter", "metadata"),
    }
    found: set[str] = set()
    for row in rows:
        if _row_is_demo_or_synthetic(row):
            return False
        status = _first_present(row, ("status", "verdict", "result")).upper()
        if not status or status not in ready:
            return False
        text = " ".join(str(value).lower() for value in row.values())
        for key, aliases in required.items():
            if any(alias in text for alias in aliases):
                found.add(key)
    return set(required) <= found


def _row_has_real_measured_metric(row: dict[str, str]) -> bool:
    status = _first_present(row, ("status", "verdict", "result")).upper()
    if status and status in (BLOCKING_STATUS_LABELS | {"NOT_RUN"}):
        return False
    if _row_is_demo_or_synthetic(row):
        return False
    metric_columns = ("recall", "latency_ms", "qps", "throughput", "accuracy", "f1", "index_time", "index_size")
    return any(_is_number(_first_present_alias(row, (column,))) for column in metric_columns)


def _has_verified_source_artifact(result_csvs: list[Path]) -> bool:
    rows = _rows_from_named_csv(result_csvs, "source_verification.csv")
    ready = {"OK", "FOUND", "READY", "PASS", "PASSED"}
    for row in rows:
        if _row_is_demo_or_synthetic(row):
            continue
        status = _first_present_alias(row, ("status", "access_status", "verdict", "result")).upper()
        if status and status not in ready:
            continue
        source = _first_present_alias(row, ("artifact_url", "source_url", "source_path", "artifact_path", "repo_url"))
        ref = _first_present_alias(row, ("commit", "branch", "tag"))
        readme = _first_present_alias(row, ("readme_found", "readme"))
        build_docs = _first_present_alias(row, ("build_docs_found", "build_docs"))
        if source and ref and readme and build_docs:
            return True
    return False


def _has_smoke_evidence(result_csvs: list[Path]) -> bool:
    ready = {"OK", "FOUND", "READY", "PASS", "PASSED", "PARTIAL"}
    for name in ("build_smoke.csv", "runtime_smoke.csv"):
        for row in _rows_from_named_csv(result_csvs, name):
            if _row_is_demo_or_synthetic(row):
                continue
            status = _first_present_alias(row, ("status", "verdict", "result")).upper()
            command = _first_present_alias(row, ("command", "symbol_or_command"))
            if status in ready and command:
                return True
    return False


def _row_has_l3_reduced_contract(row: dict[str, str]) -> bool:
    required_groups = (
        ("dataset", "input_id", "reduced_input_id"),
        ("method", "algorithm"),
        ("k", "top_k", "topk"),
        ("ground_truth_source", "ground_truth", "gt_source"),
        ("metric_definition", "metric", "metric_name"),
        ("input_provenance", "input_source", "data_provenance"),
    )
    return _row_has_alias_groups(row, required_groups)


def _has_l4_paper_alignment(result_csvs: list[Path]) -> bool:
    rows = [
        *_rows_from_named_csv(result_csvs, "paper_alignment.csv"),
        *_rows_from_named_csv(result_csvs, "figure_table_mapping.csv"),
    ]
    schema_groups = (
        ("paper_item", "paper item", "figure", "table", "figure_or_table", "paper_target"),
        ("setting_name", "setting"),
        ("paper_setting",),
        ("reduced_setting",),
        ("match_status",),
        ("evidence_source",),
    )
    valid_rows = [
        row
        for row in rows
        if not _row_is_demo_or_synthetic(row)
        and _row_has_alias_groups(row, schema_groups)
        and _first_present_alias(row, ("match_status",)).upper() in L4_ALIGNMENT_MATCH_STATUSES
    ]
    if not valid_rows:
        return False
    if not any(_first_present_alias(row, ("match_status",)).upper() in {"MATCH", "PARTIAL_MATCH"} for row in valid_rows):
        return False
    setting_text = "\n".join(
        f"{_first_present_alias(row, ('setting_name', 'setting'))} {_first_present_alias(row, ('notes',))}".lower()
        for row in valid_rows
    )
    return all(any(alias in setting_text for alias in aliases) for aliases in L4_REQUIRED_SETTING_GROUPS.values())


def _has_l5_fair_baseline_comparison(repo: Path, result_csvs: list[Path]) -> bool:
    rows = _rows_from_named_csv(result_csvs, "baseline_comparison.csv")
    required_groups = (
        ("method",),
        ("baseline_method", "baseline"),
        ("reduced_input_id", "input_id", "dataset"),
        ("metric", "metric_name"),
        ("environment", "env"),
        ("budget_notes", "budget"),
    )
    fair_rows = [
        row
        for row in rows
        if not _row_is_demo_or_synthetic(row)
        and _row_has_alias_groups(row, required_groups)
        and _has_command_provenance(repo, row, result_csvs, "baseline_comparison.csv")
    ]
    if not fair_rows:
        return False
    input_values = {_normalized_value(_first_present_alias(row, ("reduced_input_id", "input_id", "dataset"))) for row in fair_rows}
    metric_values = {_normalized_value(_first_present_alias(row, ("metric", "metric_name"))) for row in fair_rows}
    environment_values = {_normalized_value(_first_present_alias(row, ("environment", "env"))) for row in fair_rows}
    return len(input_values) == 1 and len(metric_values) == 1 and len(environment_values) == 1


def _has_command_provenance(repo: Path, row: dict[str, str], result_csvs: list[Path], artifact_name: str) -> bool:
    command_id = _first_present_alias(row, ("command_id", "cmd_id"))
    manifest_rows = _rows_from_named_csv(result_csvs, "command_manifest.csv")
    if command_id and manifest_rows and artifact_name.lower() == "reduced_metrics.csv":
        return any(
            _first_present_alias(manifest_row, ("command_id", "cmd_id")) == command_id
            and _manifest_references_artifact(manifest_row, artifact_name)
            and _provenance_row_complete(repo, manifest_row)
            for manifest_row in manifest_rows
        )
    if command_id and _provenance_row_complete(repo, row):
        return True
    for manifest_row in manifest_rows:
        manifest_command_id = _first_present_alias(manifest_row, ("command_id", "cmd_id"))
        if command_id and manifest_command_id != command_id:
            continue
        if not manifest_command_id:
            continue
        if not _manifest_references_artifact(manifest_row, artifact_name):
            continue
        if not _provenance_row_complete(repo, manifest_row):
            continue
        return True
    return False


def _provenance_row_complete(repo: Path, row: dict[str, str]) -> bool:
    if not _first_present_alias(row, ("command_id", "cmd_id")):
        return False
    if not _first_present_alias(row, ("command",)):
        return False
    if not _first_present_alias(row, ("exit_code",)):
        return False
    if not _first_present_alias(row, ("duration_sec", "duration")):
        return False
    if not _log_path_exists(repo, _first_present_alias(row, ("log_path", "log"))):
        return False
    if not _first_present_alias(row, ("artifact_hash", "hash", "sha256", "artifact_path", "output_artifact")):
        return False
    if not _first_present_alias(row, ("input_provenance", "input_source", "data_provenance", "dataset")):
        return False
    return True


def _manifest_references_artifact(row: dict[str, str], artifact_name: str) -> bool:
    needle = artifact_name.lower()
    return any(needle in str(value).lower() for value in row.values())


def _log_path_exists(repo: Path, value: str) -> bool:
    if not value:
        return False
    path = Path(value)
    candidates = [path] if path.is_absolute() else [repo / path, repo / ".r2a" / "logs" / path, repo / ".r2a" / "results" / path]
    return any(candidate.exists() for candidate in candidates)


def _row_has_alias_groups(row: dict[str, str], groups: tuple[tuple[str, ...], ...]) -> bool:
    return all(_first_present_alias(row, aliases) for aliases in groups)


def _first_present_alias(row: dict[str, str], columns: tuple[str, ...]) -> str:
    normalized = {_normalize_column(key): value for key, value in row.items()}
    for column in columns:
        value = row.get(column)
        if value is not None and str(value).strip():
            return str(value).strip()
        value = normalized.get(_normalize_column(column))
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _normalize_column(value: str) -> str:
    return str(value).strip().lower().replace(" ", "_").replace("-", "_")


def _normalized_value(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


def _row_is_demo_or_synthetic(row: dict[str, str]) -> bool:
    text = " ".join(str(value).lower() for value in row.values())
    markers = ("synthetic_input", "demo_only", "not_paper_reproduction", "synthetic", "toy", "mock")
    return any(marker in text for marker in markers)


def _row_is_l2_capped(row: dict[str, str]) -> bool:
    text = " ".join(str(value).lower() for value in row.values())
    return "verification_only" in text or "ceiling=l2" in text or "ceiling: l2" in text


def _is_number(value: str) -> bool:
    if not str(value).strip():
        return False
    try:
        parsed = float(str(value).strip())
    except ValueError:
        return False
    return math.isfinite(parsed)


def _rows_are_ready(rows: list[dict[str, str]]) -> bool:
    ready = {"OK", "FOUND", "READY"}
    for row in rows:
        status = _first_present(row, ("status", "verdict", "result")).upper()
        if status and status not in ready:
            return False
    return True


def _execution_requests_clarification(execution_text: str) -> bool:
    clarification_section = _extract_section(execution_text, "Clarification Needed").strip().lower()
    if clarification_section.startswith(("yes", "true", "是")):
        return True
    summary = _extract_section(execution_text, "Summary").lower()
    return "needs_clarification:" in summary or "clarification needed" in summary


def _major_issues(status: str, state: dict[str, Any], lookups: list[dict], execution_outcome: dict[str, object]) -> list[str]:
    """Extract major issues from status and execution outcome.

    Schema/format warnings are NOT major issues - they are advisory only.
    Only actual evidence failures or execution errors are major issues.
    """
    issues: list[str] = []

    # Check for real errors from state
    for error in state.get("errors", []) or []:
        error_lower = str(error).lower()
        # Skip schema/format issues - these are warnings, not major issues
        if any(marker in error_lower for marker in [
            "missing required column",
            "column must be numeric",
            "invalid ",
            "csv parse issue",
            "partial csv read",
            "malformed",
            "schema",
            "notes",
            "comma",
            "field",
            "format",
        ]):
            continue
        issues.append(str(error))

    if status == "FAIL":
        # Only add this if there are real issues, not just schema issues
        if issues:
            issues.append("CHECK_REPORT is FAIL, so Reviewer cannot give PASS.")

    outcome_status = str(execution_outcome.get("status", "UNKNOWN"))
    if outcome_status in {"FAILED", "BLOCKED", "PARTIAL"}:
        categories = execution_outcome.get("failure_categories", [])
        # Filter out schema/format categories
        non_schema_categories = [cat for cat in categories if cat not in {"SCHEMA_ISSUE", "FORMAT_WARNING"}]
        category_text = f" Categories: {', '.join(non_schema_categories)}." if non_schema_categories else ""
        if category_text or non_schema_categories:
            issues.append(f"Engineer execution outcome is {outcome_status}.{category_text}")

    # Check for real blockers from execution outcome
    for blocker_line in execution_outcome.get("blocking_lines", []) or []:
        blocker_lower = str(blocker_line).lower()
        # Skip schema/format issues
        if any(marker in blocker_lower for marker in [
            "missing required column",
            "column must be numeric",
            "invalid ",
            "csv parse issue",
            "partial csv read",
            "malformed",
            "schema",
            "notes",
            "comma",
            "field",
            "format",
        ]):
            continue
        issues.append(str(blocker_line))

    if any(not _is_usable_lookup(lookup) for lookup in lookups):
        issues.append("Paper evidence is incomplete for at least one of metrics, baselines, datasets, or gaps.")

    return issues or ["No blocking issue detected by rule-based MVP reviewer."]


def _paper_alignment(lookups: list[dict], language: str = "en") -> str:
    lines: list[str] = []
    for lookup in lookups:
        if _is_usable_lookup(lookup):
            lines.append(_t(language, f"{lookup['query']}：在 {', '.join(lookup['sources'])} 中找到可用证据。", f"{lookup['query']}: evidence found in {', '.join(lookup['sources'])}."))
        else:
            lines.append(_t(language, f"{lookup['query']}：证据缺口；{lookup.get('limitations', '未在 paper artifacts 中找到')}。", f"{lookup['query']}: Evidence Gap; {lookup.get('limitations', 'not found in paper artifacts')}."))
    return bullet_list(lines)


def _reproduction_limitations(lookups: list[dict], language: str = "en") -> list[str]:
    limitations = _base_limitations(language)
    for lookup in lookups:
        if not _is_usable_lookup(lookup):
            limitations.append(_t(language, f"`{lookup['query']}` 证据缺失：{lookup.get('limitations', '未在 paper artifacts 中找到')}", f"Evidence missing for `{lookup['query']}`: {lookup.get('limitations', 'not found in paper artifacts')}"))
    return limitations


def _is_usable_lookup(lookup: dict) -> bool:
    return bool(lookup.get("found")) and lookup.get("evidence_quality") == "usable"


def _required_fixes(verdict: str, major_issues: list[str], execution_outcome: dict[str, object]) -> list[str]:
    if verdict in {"NEEDS_FIX", "REJECT"}:
        fixes = list(major_issues)
        categories = set(execution_outcome.get("failure_categories", []))
        if "SAFE_BUILD_COMPATIBILITY" in categories:
            fixes.append("Planner should generate a minimal-fix TASK_SPEC that authorizes safe artifact-only compatibility patches and asks Engineer to rerun only the smallest failing build command.")
        if "TOOLCHAIN_OR_ENVIRONMENT" in categories:
            fixes.append("Planner should require explicit local tool paths and a bounded toolchain preflight before any expensive build.")
        if "MISSING_ARTIFACT_OR_DATA" in categories:
            fixes.append("Planner should verify paper/artifact evidence and require BLOCKED or NEEDS_CLARIFICATION if official source/data cannot be confirmed.")
        if "API_OR_ALGORITHM_SEMANTICS" in categories:
            fixes.append("Do not ask Engineer to redesign algorithm/API semantics by default; create a verification or manual-decision task for the failing call.")
        if "TIME_BUDGET" in categories:
            fixes.append("Planner should reuse successful prior stages and target the next smallest unfinished command within the time budget.")
        if "RUNTIME_DLL_COMPATIBILITY" in categories:
            fixes.append("Planner should require runtime smoke from the build/repo directory with PATH set for toolchain/build DLLs and write runtime_smoke.csv on loader errors.")
        if "ENGINEER_TIMEOUT_AFTER_BUILD" in categories:
            fixes.append("Planner should preserve build evidence and generate a narrow task for the unfinished runtime/input-contract step.")
        return fixes
    if verdict == "NEEDS_OFFICIAL_INPUT":
        return ["Official reduced inputs are missing or ambiguous. Planner should request/authorize official inputs, a small paper-linked subset, or an explicitly labeled synthetic_demo; do not fabricate paper metrics."]
    if verdict == "PASS_DEMO_ONLY":
        return ["Synthetic/demo evidence is acceptable only as harness validation. A future paper-aligned run needs official query files, ground truth, database/index inputs, and metric definitions."]
    if verdict == "PASS_SMOKE_ONLY":
        return ["Source/build/smoke is complete. Next step should identify official inputs, dataset subset, query/ground truth, parameters, and commands."]
    if verdict == "INPUT_CONTRACT_READY":
        return ["Input contract is ready. Next step should acquire or prepare the smallest official/paper-linked data within budget and run reduced method metrics."]
    if verdict == "PASS_REDUCED_METHOD_ONLY":
        return ["Reduced method metrics exist. Next step should align dataset, parameters, metrics, and figure/table mapping against the paper."]
    if verdict == "PASS_REDUCED_ALIGNED":
        return ["Reduced paper alignment exists. Next step may run one low-cost baseline comparison if the target level requires it."]
    if verdict == "NEEDS_INPUT_OR_BUDGET":
        return ["Official data/input step needs more information or budget. Do not download beyond the configured budget; ask for authorization or write a clear blocked status."]
    if verdict == "BORDERLINE":
        return ["Clarify task, evidence gaps, or execution status before claiming reproduction progress."]
    return ["No mandatory fix before next reduced iteration, but limitations must stay explicit."]


def _suggested_next_action(verdict: str, language: str = "en", execution_outcome: dict[str, object] | None = None) -> str:
    categories = set((execution_outcome or {}).get("failure_categories", []))
    if verdict in {"MANAGER_CLASSIFICATION_CONFLICT", "NEEDS_DETERMINISTIC_RECHECK", "HUMAN_REVIEW_REQUIRED", "PASS_WITH_REVIEW_CONFLICT"}:
        return _t(language, "Manager 与 Reviewer 出现分类冲突；停止自动迭代，执行确定性重算或人工复核。", "Manager and Reviewer have a classification conflict; stop auto-iteration for deterministic recheck or human review.")
    if verdict == "PASS_DEMO_ONLY":
        return _t(language, "demo-only 链路已验证；下一步需要官方输入或明确授权的小型 paper-linked subset，才能生成论文相关 reduced metrics。", "Demo-only harness is verified; next step needs official inputs or an explicitly authorized small paper-linked subset before paper-aligned reduced metrics.")
    if verdict == "PASS_SMOKE_ONLY":
        return _t(language, "source/build/smoke 已验证；下一轮进入 input_data_contract，确认官方数据、query、ground truth、参数和命令。", "Source/build/smoke is verified; next iteration should enter input_data_contract and confirm official data, query, ground truth, parameters, and commands.")
    if verdict == "INPUT_CONTRACT_READY":
        return _t(language, "输入契约已就绪；下一轮在预算内下载/准备最小官方数据并运行论文方法 reduced metrics。", "Input contract is ready; next iteration should download/prepare the smallest official data within budget and run reduced method metrics.")
    if verdict == "PASS_REDUCED_METHOD_ONLY":
        return _t(language, "论文方法 reduced metrics 已产出；下一轮做 paper alignment，映射数据、参数、指标和图表。", "Reduced method metrics were produced; next iteration should perform paper alignment for data, parameters, metrics, and figures/tables.")
    if verdict == "PASS_REDUCED_ALIGNED":
        return _t(language, "reduced 结果已与论文设置对齐；如目标需要，下一轮运行一个低成本 baseline comparison。", "Reduced results are aligned with paper settings; if the target requires it, run one low-cost baseline comparison next.")
    if verdict == "NEEDS_INPUT_OR_BUDGET":
        return _t(language, "数据或输入超过当前预算/授权；记录候选来源和体量，等待用户授权或选择更小 subset。", "Data/input exceeds the current budget or authorization; record candidate source/size and wait for authorization or choose a smaller subset.")
    if verdict == "NEEDS_OFFICIAL_INPUT":
        return _t(language, "生成下一轮 input-contract TASK_SPEC：确认官方 query/ground truth/Kuzu database/数据 subset，或请求用户授权 synthetic_demo。", "Generate the next input-contract TASK_SPEC: confirm official query/ground truth/Kuzu database/data subset, or request user authorization for synthetic_demo.")
    if verdict == "NEEDS_FIX":
        if "RUNTIME_DLL_COMPATIBILITY" in categories:
            return _t(language, "生成下一轮 minimal-fix TASK_SPEC：复用构建结果，只处理 runtime DLL/PATH/入口点问题，并写 runtime_smoke.csv。", "Generate the next minimal-fix TASK_SPEC: reuse build outputs, address only runtime DLL/PATH/entry-point issues, and write runtime_smoke.csv.")
        if "ENGINEER_TIMEOUT_AFTER_BUILD" in categories:
            return _t(language, "生成下一轮 minimal-fix TASK_SPEC：复用 build_smoke 证据，直接处理未完成的 runtime 或 input-contract 步骤。", "Generate the next minimal-fix TASK_SPEC: reuse build_smoke evidence and target the unfinished runtime or input-contract step.")
        if "API_OR_ALGORITHM_SEMANTICS" in categories:
            return _t(language, "生成下一轮 minimal-fix TASK_SPEC：先验证失败 API/算法语义调用，不默认重写算法；必要时标记 BLOCKED。", "Generate the next minimal-fix TASK_SPEC: verify the failing API/algorithm semantics first, do not rewrite algorithms by default, and mark BLOCKED when needed.")
        if "SAFE_BUILD_COMPATIBILITY" in categories:
            return _t(language, "生成下一轮 minimal-fix TASK_SPEC：复用已成功步骤，只允许 artifact-only 机械兼容补丁并重跑最小失败命令。", "Generate the next minimal-fix TASK_SPEC: reuse successful steps, allow only artifact-only mechanical compatibility patches, and rerun the smallest failing command.")
        return _t(language, "先修复 Manager failures，然后重新运行 r2a check 和 r2a review。", "Fix Manager failures first, then rerun r2a check and r2a review.")
    if verdict == "BORDERLINE":
        return _t(language, "在下一次 Engineer run 前，为 Planner 创建最小澄清任务。", "Create the smallest clarification task for Planner before another Engineer run.")
    return _t(language, "围绕一个已验证指标，规划下一步最小 reduced experiment。", "Plan the next smallest reduced experiment tied to one verified metric.")

def _should_iterate(verdict: str, state: R2AState) -> bool:
    return should_continue_after_verdict(verdict, state)


def _t(language: str, zh: str, en: str) -> str:
    return zh if language == "zh" else en


def _language_name(language: str) -> str:
    return "Simplified Chinese" if language == "zh" else "English"


def _base_limitations(language: str) -> list[str]:
    if language == "zh":
        return [
            "当前仅支持有限 PDF 文本抽取，不支持完整论文理解、表格/图片/公式解析，论文证据仍需人工核验。",
            "paper_lookup 仅对 PAPER_BRIEF.md 和 PAPER_EVIDENCE.md 做关键词检索。",
            "reduced、mock 或 shell-only 运行不等于完整论文复现。",
            "synthetic_demo / DEMO_ONLY 只能说明工程链路或 benchmark harness 可运行，不是论文指标。",
            "Reviewer 是规则化节点，不调用 LLM。",
        ]
    return [
        "Limited PDF text extraction is available; full paper understanding, table/figure parsing, and robust citation extraction are not implemented.",
        "paper_lookup is keyword-only over PAPER_BRIEF.md and PAPER_EVIDENCE.md.",
        "Reduced, mock, or shell-only runs are not complete paper reproductions.",
        "synthetic_demo / DEMO_ONLY only shows the engineering path or benchmark harness can run; it is not paper metric evidence.",
        "Reviewer is rule-based and does not call an LLM.",
    ]
