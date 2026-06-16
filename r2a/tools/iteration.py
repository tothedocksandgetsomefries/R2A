from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
from typing import Any

from r2a.core.final_decision import UNASSESSED as FINAL_UNASSESSED, build_final_decision
from r2a.core.evidence_level_compat import (
    read_current_reproduction_level,
    read_current_level_iteration,
    is_reviewer_executed,
    UNASSESSED,
)
from r2a.core.evidence_policy import blocking_reasons_markdown
from r2a.core.paths import artifact_dir, iteration_dir, iteration_state_path, report_path, runs_dir
from r2a.core.state import R2AState
from r2a.core.user_hints import format_user_hints_markdown, user_hints_from_state
from r2a.core.verdicts import PASS_LIKE_VERDICTS
from r2a.tools.backend_errors import TOOL_CALL_PARSE_FAILURE, classify_backend_error
from r2a.tools.csv_sanitizer import sanitized_csv_rows
from r2a.tools.evidence_levels import contract_l2_cap_reason, evidence_level_summary
from r2a.tools.final_writer import build_template_final_narrative_cn, final_writer_metadata_markdown, run_final_writer
from r2a.tools.input_integrity import summarize_official_input_integrity
from r2a.tools.markdown_utils import bullet_list
from r2a.tools.reproduction_levels import claim_level_for_verdict
from r2a.tools.report_writer import write_report
from r2a.tools.workflow_decision import aggregate_terminal_decision

# Reviewer verdicts that indicate PASS.
PASS_VERDICTS = PASS_LIKE_VERDICTS

ITERATION_REPORT_KEYS = (
    "planner_output",
    "task",
    "experiment_contract",
    "execution",
    "check",
    "manager_decision",
    "review",
    "review_feedback",
    "evidence_decision",
    "source_acquisition",
    "source_inspection",
    "next_planner_context",
)

FINAL_ITERATION_REPORT_KEYS = (*ITERATION_REPORT_KEYS, "final_decision", "final_narrative", "final_writer_metadata", "final")

LEVEL_DISPLAY = {
    "L0_project_health": "L0: Project health",
    "L1_source_artifact_verified": "L1: Source / artifact verified",
    "L2_input_contract_ready": "L2: Input contract ready",
    "L3_official_reduced_run": "L3: Official reduced run",
    "L4_reduced_paper_aligned": "L4: Reduced paper-aligned evidence",
    "L5_minimal_baseline_comparison": "L5: Minimal baseline comparison",
    "L6_full_or_near_full_reproduction": "L6: Full or near-full reproduction",
}

VERDICT_DISPLAY = {
    "PASS_WITH_LIMITATIONS": "Pass with limitations",
    "PASS_SMOKE_ONLY": "Source/artifact smoke evidence only",
    "VERIFICATION_REDUCED_RUN_RECORDED": "Verification-only reduced benchmark evidence recorded",
    "INPUT_CONTRACT_READY": "Input contract ready",
    "PASS_DEMO_ONLY": "Demo only, not paper reproduction",
    "PASS_REDUCED_METHOD_ONLY": "Official reduced method evidence completed",
    "PASS_REDUCED_ALIGNED": "Reduced paper-aligned evidence completed",
    "PASS_REDUCED_COMPARISON": "Reduced baseline comparison completed",
    "NEEDS_FIX": "Needs cleanup / needs fix",
    "NEEDS_INPUT": "Needs input",
    "NEEDS_OFFICIAL_INPUT": "Needs official input",
    "NEEDS_INPUT_OR_BUDGET": "Needs input or budget authorization",
    "BORDERLINE": "Borderline / needs clarification",
    "REJECT": "Rejected",
}


def ensure_iteration_dirs(repo_path: str | Path, iteration: int) -> Path:
    iter_dir = iteration_dir(repo_path, iteration)
    (iter_dir / "logs").mkdir(parents=True, exist_ok=True)
    (iter_dir / "results").mkdir(parents=True, exist_ok=True)
    runs_dir(repo_path).mkdir(parents=True, exist_ok=True)
    return iter_dir


def archive_current_iteration(state: R2AState) -> R2AState:
    repo = Path(state["repo_path"])
    iteration = int(state.get("iteration", 1))
    iter_dir = ensure_iteration_dirs(repo, iteration)

    for key in ITERATION_REPORT_KEYS:
        source = report_path(repo, key)
        if source.exists():
            shutil.copy2(source, iter_dir / source.name)

    _copy_tree_contents(artifact_dir(repo) / "logs", iter_dir / "logs")
    _copy_tree_contents(repo / "results", iter_dir / "results")
    _copy_tree_contents(artifact_dir(repo) / "results", iter_dir / "results")

    entry = _iteration_entry(state, iter_dir)
    history = [item for item in state.get("iteration_history", []) if item.get("iteration") != iteration]
    history.append(entry)
    history = sorted(history, key=lambda item: int(item.get("iteration", 0)))

    updated = {
        **state,
        "iteration_dir": str(iter_dir),
        "runs_dir": str(runs_dir(repo)),
        "iteration_history": history,
        "latest_task_spec_path": str(report_path(repo, "task")),
        "latest_planner_output_path": str(report_path(repo, "planner_output")),
        "latest_experiment_contract_path": str(report_path(repo, "experiment_contract")),
        "latest_execution_report_path": str(report_path(repo, "execution")),
        "latest_check_report_path": str(report_path(repo, "check")),
        "latest_review_report_path": str(report_path(repo, "review")),
        "latest_review_feedback_path": str(report_path(repo, "review_feedback")),
    }
    write_iteration_state(updated)
    return updated


def archive_final_iteration(state: R2AState) -> R2AState:
    repo = Path(state["repo_path"])
    iteration = int(state.get("iteration", 1))
    if not _current_iteration_has_reviewer_cycle(state):
        final_dir = runs_dir(repo) / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        (final_dir / "logs").mkdir(parents=True, exist_ok=True)
        (final_dir / "results").mkdir(parents=True, exist_ok=True)
        for key in FINAL_ITERATION_REPORT_KEYS:
            source = report_path(repo, key)
            if source.exists():
                shutil.copy2(source, final_dir / source.name)
        _copy_tree_contents(artifact_dir(repo) / "logs", final_dir / "logs")
        _copy_tree_contents(repo / "results", final_dir / "results")
        _copy_tree_contents(artifact_dir(repo) / "results", final_dir / "results")
        updated = {
            **state,
            "final_archive_dir": str(final_dir),
            "runs_dir": str(runs_dir(repo)),
        }
        write_iteration_state(updated)
        return updated

    iter_dir = ensure_iteration_dirs(repo, iteration)

    for key in FINAL_ITERATION_REPORT_KEYS:
        source = report_path(repo, key)
        if source.exists():
            shutil.copy2(source, iter_dir / source.name)

    _copy_tree_contents(artifact_dir(repo) / "logs", iter_dir / "logs")
    _copy_tree_contents(repo / "results", iter_dir / "results")
    _copy_tree_contents(artifact_dir(repo) / "results", iter_dir / "results")

    entry = _iteration_entry(state, iter_dir)
    entry["final_report"] = _existing_archive_path(iter_dir / "FINAL_REPORT.md")
    history = [item for item in state.get("iteration_history", []) if item.get("iteration") != iteration]
    history.append(entry)
    history = sorted(history, key=lambda item: int(item.get("iteration", 0)))
    updated = {
        **state,
        "iteration_dir": str(iter_dir),
        "runs_dir": str(runs_dir(repo)),
        "iteration_history": history,
    }
    write_iteration_state(updated)
    return updated


def prepare_next_iteration(state: R2AState) -> R2AState:
    """推进到下一轮迭代。

    核心原则：
    1. 不判断、预测或升级等级
    2. 只归档、推进轮次、重置本轮状态
    3. 保留正式等级快照
    4. 重置 attempt 状态
    """
    next_iteration = int(state.get("iteration", 1)) + 1
    repo = Path(state["repo_path"])
    iter_dir = ensure_iteration_dirs(repo, next_iteration)
    review_feedback = _review_feedback_for_next_iteration(state)
    active_blockers = list((state.get("decision_status", {}) or {}).get("active_blockers", []) or state.get("workflow_blockers", []) or [])
    metadata = dict(state.get("metadata", {}) or {})
    next_context = {
        "schema_version": 1,
        "next_iteration": next_iteration,
        "previous_iteration": int(state.get("iteration", 1)),
        "previous_iteration_summary": str(state.get("final_report", "") or review_feedback.get("iteration_summary", "") or ""),
        "reviewer_guidance": review_feedback.get("next_iteration_guidance", review_feedback.get("recommended_task_scope", [])),
        "do_not_repeat": review_feedback.get("do_not_repeat", review_feedback.get("forbidden_next_actions", [])),
        "suggested_plan_constraints": review_feedback.get("suggested_plan_constraints", {}),
        "active_blockers": active_blockers,
        "resolved_issues": review_feedback.get("resolved_issues", []),
        "source_status": _source_status_context(repo),
        "source_inspection_summary": _source_inspection_context(repo),
        "evidence_status": (state.get("decision_status", {}) or {}).get("evidence_summary", {}),
        "missing_metrics": _missing_metrics_context(repo),
        "not_measured_items": _not_measured_context(repo),
        "missing_command_provenance": _missing_command_provenance_context(repo),
        "paper_alignment_gaps": _paper_alignment_gap_context(repo),
        "next_priority": _next_priority_context(repo, review_feedback, active_blockers),
        "allowed_scope": _allowed_scope_context(state, repo),
    }
    metadata["next_iteration_context"] = next_context
    next_context_path = report_path(repo, "next_planner_context")
    next_context_path.parent.mkdir(parents=True, exist_ok=True)
    next_context_path.write_text(json.dumps(next_context, indent=2, ensure_ascii=False), encoding="utf-8")
    if iter_dir.exists():
        (iter_dir / "NEXT_PLANNER_CONTEXT.json").write_text(json.dumps(next_context, indent=2, ensure_ascii=False), encoding="utf-8")

    # 保留正式等级快照（不更新）
    current_reproduction_level = state.get("current_reproduction_level")
    current_level_iteration = state.get("current_level_iteration", 0)
    level_source = state.get("level_source", "unassessed")
    level_reasoning = state.get("level_reasoning", "")
    supporting_artifacts = state.get("supporting_artifacts", [])
    remaining_gaps = state.get("remaining_gaps", [])

    updated = {
        **state,
        "iteration": next_iteration,
        # 保留正式等级快照
        "reproduction_level": current_reproduction_level or "",  # 兼容字段，镜像正式等级
        "current_reproduction_level": current_reproduction_level,
        "current_level_iteration": current_level_iteration,
        "level_source": level_source,
        "level_reasoning": level_reasoning,
        "supporting_artifacts": supporting_artifacts,
        "remaining_gaps": remaining_gaps,
        # 推进轮次
        "iteration_dir": str(iter_dir),
        "runs_dir": str(runs_dir(repo)),
        "need_replan": True,
        "approved": bool(state.get("auto_approve") or state.get("approved")),
        "loop_status": "replanning",
        # 重置本轮 attempt 状态
        "clarification_needed": False,
        "errors": [],
        "warnings": [],
        "manager_status": "",
        "manager_passed": False,
        "manager_executed": False,
        "manager_max_level_allowed": "",
        "manager_decision_path": "",
        "reviewer_verdict": "",
        "reviewer_executed": False,
        # reviewer_backend 是运行配置，不重置，保留用户选择
        # "reviewer_backend": state.get("reviewer_backend", "rules"),
        "reviewer_level_valid": False,
        "reviewer_level_rejection_reason": "",
        "structured_review_feedback": {},
        "suggested_next_action": "",
        "engineer_status": "",
        "engineer_passed": False,
        "engineer_executor_failed": False,
        "engineer_executor_failure_category": "",
        "check_report_path": "",
        "execution_report_path": "",
        "review_report_path": "",
        "review_feedback_path": "",
        "decision_status": {},
        "workflow_decision": {},
        "workflow_blockers": [],
        "planner_readiness": {},
        "engineer_readiness": {},
        "next_planner_context_path": str(next_context_path),
        "metadata": metadata,
    }
    write_iteration_state(updated)
    return updated


def _review_feedback_for_next_iteration(state: R2AState) -> dict[str, Any]:
    direct = state.get("structured_review_feedback")
    if isinstance(direct, dict):
        return direct
    for key in ("latest_review_feedback_path", "review_feedback_path"):
        value = str(state.get(key, "") or "")
        if not value:
            continue
        path = Path(value)
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def _source_status_context(repo: Path) -> dict[str, Any]:
    data = _read_json_dict(report_path(repo, "source_acquisition"))
    if not data:
        return {}
    return {
        "source_status": data.get("source_status", ""),
        "source_type": data.get("source_type", ""),
        "repo_url": data.get("repo_url", ""),
        "local_path": data.get("local_path", ""),
        "commit": data.get("commit", ""),
        "branch": data.get("branch", ""),
        "blockers": data.get("blockers", []),
        "warnings": data.get("warnings", []),
    }


def _source_inspection_context(repo: Path) -> dict[str, Any]:
    data = _read_json_dict(report_path(repo, "source_inspection"))
    if not data:
        return {}
    return {
        "inspection_status": data.get("inspection_status", ""),
        "repo_root": data.get("repo_root", ""),
        "language": data.get("language", []),
        "frameworks": data.get("frameworks", []),
        "environment_files": data.get("environment_files", []),
        "entrypoints": data.get("entrypoints", []),
        "test_commands": data.get("test_commands", []),
        "dataset_requirements": data.get("dataset_requirements", []),
        "supports": data.get("supports", {}),
        "planner_hints": data.get("planner_hints", []),
    }


def _allowed_scope_context(state: R2AState, repo: Path) -> dict[str, Any]:
    """
    Compute allowed scope based on user permissions and safety boundaries.

    NOTE: This function NO longer caps max_target_level based on SourceInspection.supports.
    Static inspection uncertainty should become Planner notes or runtime_probe tasks,
    not hard caps. The actual evidence level should be determined by execution results.
    """
    planner_readiness = state.get("planner_readiness")
    if isinstance(planner_readiness, dict):
        constraints = planner_readiness.get("constraints")
        if isinstance(constraints, dict) and constraints:
            return dict(constraints)

    target = str(state.get("target_reproduction_level", "") or "L4_reduced_paper_aligned")
    allow_download = bool(state.get("allow_official_dataset_download", False))
    allow_full_benchmark = bool(state.get("allow_full_benchmark", False))
    download_budget = int(state.get("download_budget_gb", 0) or 0)

    # Only check user permissions and safety boundaries
    # Do NOT cap max_target_level based on static inspection uncertainty

    # Determine contract_mode based on user permissions
    if allow_full_benchmark:
        contract_mode = "full_benchmark"
    elif allow_download:
        contract_mode = "official_reduced"
    else:
        contract_mode = "verification_only"

    # Safety check: download budget must be sufficient if download is allowed
    if allow_download and download_budget <= 0:
        return {
            "target_level": target,
            "contract_mode": "verification_only",
            "max_target_level": target,  # Don't cap, let Planner try
            "reason": "Download budget insufficient for official dataset download.",
        }

    return {
        "target_level": target,
        "contract_mode": contract_mode,
        "max_target_level": target,  # User's target, not capped by static inspection
        "reason": "User permissions satisfied; actual feasibility determined by execution.",
    }


def write_iteration_state(state: R2AState) -> Path:
    """写入 ITERATION_STATE.json。

    只从 state 读取等级，不进行文件推断。
    新 Run 的等级为空字符串（Reviewer 未执行）。
    """
    repo = Path(state["repo_path"])
    path = iteration_state_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)

    # 使用兼容读取函数
    # 只读取已有等级，不进行文件推断
    reviewer_executed = is_reviewer_executed(state)
    current_level = read_current_reproduction_level(state, reviewer_executed=reviewer_executed)
    current_level_iteration = read_current_level_iteration(state, reviewer_executed=reviewer_executed)

    # 如果 Reviewer 未执行或等级为 None，使用空字符串
    if not current_level:
        current_level = ""  # Reviewer 未执行
    elif current_level == UNASSESSED:
        current_level = ""  # 未评估

    completed_review_iterations = _completed_review_iterations_count(state)
    data = {
        "run_id": state.get("run_id", ""),
        "current_iteration": int(state.get("iteration", 1)),
        "current_iteration_complete": _current_iteration_has_reviewer_cycle(state),
        "completed_review_iterations": completed_review_iterations,
        "max_iterations": int(state.get("max_iterations", 1)),
        "auto_iterate": bool(state.get("auto_iterate", False)),
        # 新字段：正式等级
        "current_reproduction_level": current_level,
        "current_level_iteration": current_level_iteration,
        # 兼容字段（compatibility-only）
        "reproduction_level": current_level,
        "state_reproduction_level": state.get("reproduction_level", ""),
        "target_reproduction_level": state.get("target_reproduction_level", ""),
        "achieved_reproduction_level": current_level,
        # 其他字段
        "download_budget_gb": state.get("download_budget_gb", 20),
        "stage_backends": _stage_backends(state),
        "stop_reason": state.get("stop_reason", ""),
        "decision_status": state.get("decision_status", {}),
        "paper_readiness": state.get("paper_readiness", {}),
        "planner_readiness": state.get("planner_readiness", {}),
        "engineer_readiness": state.get("engineer_readiness", {}),
        "source_acquisition": state.get("source_acquisition", {}),
        "source_inspection": state.get("source_inspection", {}),
        "next_planner_context_path": state.get("next_planner_context_path", ""),
        "workflow_decision": state.get("workflow_decision", {}),
        "workflow_blockers": state.get("workflow_blockers", []),
        "iterations": state.get("iteration_history", []),
        # 标记来源
        "level_source": "reviewer" if reviewer_executed else "unassessed",
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_final_report(state: R2AState) -> Path:
    """写入 FINAL_REPORT.md。

    只从 state 读取等级，不进行文件推断。
    新 Run 的等级为空字符串（Reviewer 未执行）。
    """
    repo = Path(state["repo_path"])
    language = str(state.get("language", "en"))
    if not isinstance(state.get("decision_status"), dict) or not state.get("decision_status"):
        state = {**state, "decision_status": aggregate_terminal_decision(state)}
    history = state.get("iteration_history", [])
    completed_review_iterations = _completed_review_iterations_count(state)

    final_decision = build_final_decision(state, write=True)
    user_hints = user_hints_from_state(state)

    # 使用兼容读取函数只读取已有正式等级；accepted_level 以 FINAL_DECISION 为准。
    reviewer_executed = is_reviewer_executed(state)
    state_current_level = read_current_reproduction_level(state, reviewer_executed=reviewer_executed)
    current_level_iteration = read_current_level_iteration(state, reviewer_executed=reviewer_executed)

    accepted_level = str(final_decision.get("accepted_level", FINAL_UNASSESSED) or FINAL_UNASSESSED)
    observed_level = str(final_decision.get("observed_level", FINAL_UNASSESSED) or FINAL_UNASSESSED)
    current_level = "" if accepted_level == FINAL_UNASSESSED else accepted_level
    if current_level and current_level != state_current_level:
        current_level_iteration = int(state.get("iteration", current_level_iteration) or current_level_iteration or 0)
    observed_level_for_cards = "" if observed_level == FINAL_UNASSESSED else observed_level
    check_status = _check_status(repo)
    display = _final_display_state(state, repo, current_level, check_status)
    final_warnings = list(final_decision.get("warnings", []) or [])
    if final_warnings:
        display["remaining_issues"] = _dedupe([*final_warnings, *display.get("remaining_issues", [])])
    display = {
        **display,
        "current_level": accepted_level,
        "current_level_display": _level_display(accepted_level),
        "current_level_iteration": current_level_iteration,
        "observed_level": observed_level,
        "observed_level_display": _level_display(observed_level),
        "accepted_level": accepted_level,
        "accepted_level_display": _level_display(accepted_level),
        "quality_gate_level": accepted_level,
        "quality_gate_level_display": _level_display(accepted_level),
        "cap_reason": "",
        "target_level": str(final_decision.get("target_level", "") or state.get("target_reproduction_level", "")),
        "target_level_display": _level_display(str(final_decision.get("target_level", "") or state.get("target_reproduction_level", ""))),
        "final_verdict": str(final_decision.get("formal_verdict", "") or display["final_verdict"]),
        "stop_reason": str(final_decision.get("stop_reason", "") or display["stop_reason"]),
    }
    if accepted_level == FINAL_UNASSESSED and observed_level != FINAL_UNASSESSED:
        display["result_type"] = f"observed candidate {observed_level}; not formally accepted"
        display["cap_reason"] = "EVIDENCE_DECISION.json is missing or invalid; accepted_level remains UNASSESSED."
    l4_summary_path = _write_l4_alignment_summary(repo, observed_level_for_cards or current_level, display)
    latest_reports = _key_outputs(repo, state, l4_summary_path)
    evidence_decision_for_report = {
        "target_level": display["target_level"],
        "target_label": display["target_level_display"],
        "observed_level": observed_level,
        "observed_label": _level_display(observed_level),
        "accepted_level": accepted_level,
        "accepted_label": _level_display(accepted_level),
        "achieved_level": accepted_level,
        "achieved_label": _level_display(accepted_level),
        "quality_gate_level": accepted_level,
        "quality_gate_label": _level_display(accepted_level),
        "status": str(final_decision.get("final_status", "") or "completed_with_failure"),
        "blocking_reasons": list(state.get("evidence_blocking_reasons", []) or []),
        "cap_reason": "",
        # evidence_ladder 已废弃，不再填充
        "levels": {},
    }
    # 构造正式等级摘要（不使用 evidence_ladder）
    formal_summary_state = {
        **state,
        "reviewer_level_valid": bool(final_decision.get("accepted_level_valid", False)),
        "level_source": final_decision.get("accepted_level_source", state.get("level_source", "unassessed")),
    }
    level_summary = _formal_level_summary(formal_summary_state, current_level, current_level_iteration)
    source_acquisition_summary = _source_acquisition_summary(repo)
    input_data_summary = _input_data_summary(repo)
    executed_reduced_experiments = _executed_reduced_experiments(repo)
    experiment_summary = _experiment_summary(repo, language)
    paper_alignment_summary = _paper_alignment_summary(repo, language)
    command_manifest_summary = _command_manifest_summary(repo)
    limitations = [
        "Auto iteration is conservative and bounded by max_iterations.",
        "Reviewer does not edit code; Planner converts review feedback into the next TASK_SPEC.",
        "Shell executor is a safe demo path and does not prove full reproduction.",
        "Reproduction Level is read from FINAL_DECISION/EVIDENCE_DECISION, not re-judged by Final Writer.",
    ]
    narrative_context = {
        "final_decision": final_decision,
        "evidence_decision": _read_json_dict(report_path(repo, "evidence_decision")),
        "review_verdict": _read_json_dict(report_path(repo, "review_verdict")),
        "reduced_metrics_summary": executed_reduced_experiments,
        "paper_alignment_summary": paper_alignment_summary,
        "l4_alignment_excerpt": _l4_alignment_excerpt(l4_summary_path),
        "command_manifest_summary": command_manifest_summary,
        "decision_status_summary": _decision_status_markdown(state.get("decision_status", {})),
        "warnings": final_warnings,
        "limitations": limitations,
    }
    template_narrative = build_template_final_narrative_cn(narrative_context)
    final_writer_result = run_final_writer(state, narrative_context, template_narrative=template_narrative)
    final_writer_metadata = dict(final_writer_result.get("metadata", {}) or {})
    final_narrative_cn = str(final_writer_result.get("narrative", "") or template_narrative)
    return write_report(
        report_path(repo, "final"),
        "FINAL_REPORT.md",
        {
            **_report_labels(language),
            "run_summary": _run_summary(evidence_decision_for_report, display),
            "decision_status_summary": _decision_status_markdown(state.get("decision_status", {})),
            "user_hints_summary": format_user_hints_markdown(user_hints),
            "source_acquisition_summary": source_acquisition_summary,
            "input_data_summary": input_data_summary,
            "executed_reduced_experiments": executed_reduced_experiments,
            # evidence_ladder 已废弃，使用正式等级摘要
            "evidence_ladder": level_summary,
            "evidence_blocking_reasons": blocking_reasons_markdown(evidence_decision_for_report),
            "key_outputs": bullet_list(latest_reports),
            "final_decision_summary": _final_decision_markdown(final_decision),
            "final_writer_summary": final_writer_metadata_markdown(final_writer_metadata),
            "final_narrative_cn": final_narrative_cn,
            "final_status": str(final_decision.get("final_status", "") or state.get("loop_status", "completed")),
            "total_iterations": completed_review_iterations,
            "stop_reason": display["stop_reason"],
            "final_verdict": display["final_verdict"],
            "detailed_status": display["detailed_status"],
            "reproduction_level": accepted_level,
            "reproduction_level_display": _level_display(accepted_level),
            "observed_reproduction_level": observed_level,
            "observed_reproduction_level_display": _level_display(observed_level),
            "accepted_reproduction_level": accepted_level,
            "accepted_reproduction_level_display": _level_display(accepted_level),
            "quality_gate_level": display["quality_gate_level"],
            "quality_gate_level_display": display["quality_gate_level_display"],
            "cap_reason": display["cap_reason"] or "None",
            "target_reproduction_level": display["target_level"],
            "target_reproduction_level_display": display["target_level_display"],
            "claim_level": display["claim_level"],
            "claim_level_display": display["claim_level_display"],
            "result_type": display["result_type"],
            "full_reproduction_claim": display["full_reproduction_claim"],
            "next_action": display["next_action"],
            "download_budget_gb": state.get("download_budget_gb", 20),
            "executive_summary": _executive_summary(display, language),
            "progress_cards": _progress_cards(repo, observed_level_for_cards or current_level, language),
            "what_was_done": _what_was_done(state, check_status, display, language),
            "planner_approval_diagnostics": _planner_approval_diagnostics(repo, state, language),
            "experiment_summary": experiment_summary,
            "paper_alignment_summary": paper_alignment_summary,
            "command_manifest_summary": command_manifest_summary,
            "remaining_issues": bullet_list(display["remaining_issues"]),
            "evidence_files": bullet_list(_evidence_files(repo, l4_summary_path)),
            "warnings_summary": bullet_list(final_warnings or ["None"]),
            "recommendations": bullet_list(_recommendations(final_decision)),
            "iteration_summary": _format_iteration_summary(history),
            "latest_reports": bullet_list(latest_reports),
            "limitations": bullet_list(limitations),
            "evidence_level_summary": evidence_level_summary(repo),
            "raw_engineer_results_note": _t(
                language,
                "原始 CSV 表格、日志和历史迭代详情仍保留在 `.r2a/results/`、`.r2a/logs/` 和 `.r2a/runs/` 下。Web UI 会把这些内容放在摘要之后，完整溯源文件仍可审计。",
                "Raw CSV tables, logs, and archived iteration details remain available under `.r2a/results/`, `.r2a/logs/`, and `.r2a/runs/`. The Web UI shows these after the summary sections and keeps full artifacts available for audit.",
            ),
        },
    )


def _key_outputs(repo: Path, state: R2AState, l4_summary_path: Path | None) -> list[str]:
    stages = _manifest_stages(repo, state)
    candidates = [
        ("paper", "PAPER_CONTEXT.md", report_path(repo, "paper_context")),
        ("paper", "PAPER_TEXT.md", report_path(repo, "paper_text")),
        ("paper", "SOURCE_ACQUISITION.json", report_path(repo, "source_acquisition")),
        ("paper", "SOURCE_INSPECTION.json", report_path(repo, "source_inspection")),
        ("planner", "PLANNER_OUTPUT.json", report_path(repo, "planner_output")),
        ("planner", "NEXT_PLANNER_CONTEXT.json", report_path(repo, "next_planner_context")),
        ("planner", "TASK_SPEC.md", report_path(repo, "task")),
        ("planner", "EXPERIMENT_CONTRACT.md", report_path(repo, "experiment_contract")),
        ("engineer", "EXECUTION_REPORT.md", report_path(repo, "execution")),
        ("manager", "CHECK_REPORT.md", report_path(repo, "check")),
        ("manager", "MANAGER_DECISION.json", report_path(repo, "manager_decision")),
        ("reviewer", "REVIEW_REPORT.md", report_path(repo, "review")),
        ("reviewer", "REVIEW_VERDICT.json", report_path(repo, "review_verdict")),
        ("reviewer", "REVIEW_FEEDBACK.json", report_path(repo, "review_feedback")),
        ("reviewer", "EVIDENCE_DECISION.json", report_path(repo, "evidence_decision")),
        ("final", "FINAL_DECISION.json", report_path(repo, "final_decision")),
        ("final", "FINAL_NARRATIVE_CN.md", report_path(repo, "final_narrative")),
        ("final", "FINAL_WRITER_METADATA.json", report_path(repo, "final_writer_metadata")),
    ]
    outputs: list[str] = []
    for stage, label, path in candidates:
        if path.exists() and (_stage_has_real_record(stages, stage) or (not state.get("stopped") and stage != "approval")):
            outputs.append(f"{label}: {path}")
    tx_path = artifact_dir(repo) / "logs" / "planner_transaction.json"
    if tx_path.exists():
        outputs.append(f"planner_transaction.json: {tx_path}")
    if l4_summary_path and l4_summary_path.exists() and (_stage_has_real_record(stages, "engineer") or not state.get("stopped")):
        outputs.append(f"L4_ALIGNMENT_SUMMARY.md: {l4_summary_path}")
    manifest_path = Path(state.get("latest_run_manifest_path", artifact_dir(repo) / "latest" / "RUN_MANIFEST.json"))
    if manifest_path.exists():
        outputs.append(f"RUN_MANIFEST.json: {manifest_path}")
    if not outputs:
        outputs.append("No durable stage outputs were committed for this run.")
    return outputs


def _final_decision_markdown(final_decision: dict[str, Any]) -> str:
    lines = [
        f"formal_verdict: {final_decision.get('formal_verdict', '') or 'UNASSESSED'}",
        f"accepted_level: {final_decision.get('accepted_level', '') or FINAL_UNASSESSED}",
        f"accepted_level_valid: {bool(final_decision.get('accepted_level_valid', False))}",
        f"accepted_level_source: {final_decision.get('accepted_level_source', '') or 'UNASSESSED'}",
        f"observed_level: {final_decision.get('observed_level', '') or FINAL_UNASSESSED}",
        f"observed_level_source: {final_decision.get('observed_level_source', '') or 'none'}",
        f"target_level: {final_decision.get('target_level', '') or 'unknown'}",
        f"target_reached: {bool(final_decision.get('target_reached', False))}",
        f"final_status: {final_decision.get('final_status', '') or 'completed_with_failure'}",
        f"stop_reason: {final_decision.get('stop_reason', '') or 'unknown'}",
    ]
    warnings = [str(item) for item in final_decision.get("warnings", []) or [] if str(item).strip()]
    if warnings:
        lines.append("warnings: " + "; ".join(warnings))
    return bullet_list(lines)


def _evidence_files(repo: Path, l4_summary_path: Path | None) -> list[str]:
    items: list[str] = []
    for label, path in (
        ("reduced_metrics.csv", _result_path(repo, "reduced_metrics.csv")),
        ("paper_alignment.csv", _result_path(repo, "paper_alignment.csv")),
        ("L4_ALIGNMENT_SUMMARY.md", l4_summary_path or artifact_dir(repo) / "results" / "L4_ALIGNMENT_SUMMARY.md"),
        ("L4_EVIDENCE_SUMMARY.md", artifact_dir(repo) / "results" / "L4_EVIDENCE_SUMMARY.md"),
        ("REVIEW_REPORT.md", report_path(repo, "review")),
        ("REVIEW_VERDICT.json", report_path(repo, "review_verdict")),
        ("EVIDENCE_DECISION.json", report_path(repo, "evidence_decision")),
        ("FINAL_DECISION.json", report_path(repo, "final_decision")),
        ("RUN_MANIFEST.json", Path()),
    ):
        if label == "RUN_MANIFEST.json":
            manifest = artifact_dir(repo) / "latest" / "RUN_MANIFEST.json"
            if manifest.exists():
                items.append(f"{label}: {manifest}")
            continue
        if path and path.exists():
            items.append(f"{label}: {path}")
    command_manifest = _result_path(repo, "command_manifest.csv")
    if command_manifest.exists():
        items.append(f"command_manifest.csv: {command_manifest}")
    else:
        items.append("command_manifest.csv: missing warning")
    return items


def _recommendations(final_decision: dict[str, Any]) -> list[str]:
    accepted = str(final_decision.get("accepted_level", "") or "")
    observed = str(final_decision.get("observed_level", "") or "")
    target = str(final_decision.get("target_level", "") or "")
    if accepted and accepted != FINAL_UNASSESSED and _level_at_least(accepted, target or accepted):
        return [
            "Accepted level has reached the target; preserve the reduced evidence package and strengthen command provenance if needed.",
            "If moving beyond L4 is desired, authorize a separate low-cost baseline or full benchmark scope explicitly.",
        ]
    if observed and observed != FINAL_UNASSESSED and accepted == FINAL_UNASSESSED:
        return [
            "Observed candidate evidence exists but is not formally accepted; inspect EVIDENCE_DECISION.json and Reviewer Safety Override first.",
            "Do not relabel observed L4 artifacts as accepted until EVIDENCE_DECISION.json is valid.",
        ]
    return ["Resolve the listed blockers, then rerun the narrowest affected stage."]


def _manifest_stages(repo: Path, state: R2AState) -> dict[str, dict[str, Any]]:
    manifest_path = Path(state.get("latest_run_manifest_path", artifact_dir(repo) / "latest" / "RUN_MANIFEST.json"))
    data = _read_json_dict(manifest_path)
    stages = data.get("stages", {}) if isinstance(data, dict) else {}
    return {str(key): dict(value) for key, value in stages.items() if isinstance(value, dict)}


def _stage_has_real_record(stages: dict[str, dict[str, Any]], stage: str) -> bool:
    status = _stage_status_from_manifest(stages, stage)
    if not status:
        return False
    return status not in {"PENDING", "SKIPPED", "NOT_RUN", "BLOCKED", "RUNNING"}


def _stage_status_from_manifest(stages: dict[str, dict[str, Any]], stage: str) -> str:
    return str(stages.get(stage, {}).get("status", "") or "").strip().upper()


def _stage_blocker(stage: str, stages: dict[str, dict[str, Any]], state: R2AState) -> str:
    order = ("paper", "planner", "approval", "engineer", "manager", "reviewer", "final")
    try:
        stage_index = order.index(stage)
    except ValueError:
        stage_index = len(order)
    for upstream in order[:stage_index]:
        status = _stage_status_from_manifest(stages, upstream)
        if status in {"FAIL", "FAILED", "REJECT", "REJECTED"}:
            reason = _planner_failure_category(Path(state["repo_path"]), state) if upstream == "planner" else str(state.get("stop_reason", "") or status)
            return f"{upstream} ({reason or status})"
    if state.get("stopped"):
        return str(state.get("stop_reason", "") or "upstream stop")
    return ""


def _what_was_done_from_manifest(repo: Path, state: R2AState, language: str = "en") -> str:
    stages = _manifest_stages(repo, state)
    stage_defs = [
        ("paper", "Paper", [report_path(repo, "paper_context"), report_path(repo, "paper_text")]),
        ("planner", "Planner", [report_path(repo, "planner_output"), report_path(repo, "task"), report_path(repo, "experiment_contract")]),
        ("approval", "Approval", []),
        ("engineer", "Engineer", [report_path(repo, "execution")]),
        ("manager", "Manager", [report_path(repo, "check"), report_path(repo, "manager_decision")]),
        ("reviewer", "Reviewer", [report_path(repo, "review"), report_path(repo, "review_feedback")]),
        ("final", "Final aggregation", [report_path(repo, "final")]),
    ]
    lines: list[str] = []
    for index, (stage, label, paths) in enumerate(stage_defs, start=1):
        status = _stage_status_from_manifest(stages, stage) or "NOT_RUN"
        existing = [str(path) for path in paths if path.exists()]
        if status in {"PENDING", "SKIPPED", "NOT_RUN", "BLOCKED"}:
            blocker = _stage_blocker(stage, stages, state)
            reason = f"Skipped/not run; blocked by {blocker}." if blocker else "Not run in this workflow."
            lines.append(f"{index}. {label} stage: {status}. {reason}")
        elif status in {"FAIL", "FAILED", "REJECT", "REJECTED"}:
            evidence = "; ".join(existing) if existing else "see stage diagnostics."
            lines.append(f"{index}. {label} stage: FAILED. Evidence: {evidence}")
        else:
            evidence = "; ".join(existing) if existing else "no durable artifact recorded."
            lines.append(f"{index}. {label} stage: {status}. Evidence: {evidence}")
    if _csv_exists(repo, "reduced_metrics.csv"):
        lines.append(_t(language, "- Engineer result: real reduced metrics file is present.", "- Engineer result: real reduced metrics file is present."))
    if _csv_exists(repo, "paper_alignment.csv"):
        lines.append(_t(language, "- Engineer result: paper alignment file is present.", "- Engineer result: paper alignment file is present."))
    if _csv_exists(repo, "command_manifest.csv"):
        lines.append(_t(language, "- Engineer result: command provenance manifest is present.", "- Engineer result: command provenance manifest is present."))
    return "\n".join(lines)


def _planner_failure_category(repo: Path, state: R2AState) -> str:
    transaction = dict(state.get("planner_transaction", {}) or {})
    if not transaction:
        transaction = _read_json_dict(artifact_dir(repo) / "logs" / "planner_transaction.json")
    diagnostic = dict(transaction.get("diagnostic", {}) or {})
    failed = bool(
        transaction
        and (
            transaction.get("validation_status") == "FAIL"
            or transaction.get("committed") is False
            or diagnostic.get("planner_validation_passed") is False
            or diagnostic.get("planner_committed") is False
        )
    )
    if not failed and state.get("loop_status") != "planner_failed":
        return ""
    for value in (
        transaction.get("failure_category"),
        transaction.get("execution_status"),
        diagnostic.get("failure_category"),
        state.get("failure_category"),
        state.get("stop_reason"),
    ):
        reason = str(value or "").strip()
        if reason.startswith("PLANNER_") or reason in {"BACKEND_TRANSIENT_FAILURE", "planner_stage_failed"}:
            return reason
    return "PLANNER_TRANSACTION_FAILED"


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _final_display_state(state: R2AState, repo: Path, evidence_level: str, check_status: str) -> dict[str, Any]:
    detailed_status = _status_from_evidence_level(evidence_level)
    final_verdict = detailed_status
    remaining_issues = _remaining_issues(repo, check_status)
    stop_reason = str(state.get("stop_reason", "") or "")
    verification_reduced = has_verification_only_reduced_evidence(repo)
    contract_cap_reason = contract_l2_cap_reason(repo)
    reviewer_verdict = str(state.get("reviewer_verdict", "") or "").upper()
    planner_failure = _planner_failure_category(repo, state)
    decision_status = dict(state.get("decision_status", {}) or {})

    if decision_status:
        final_verdict = str(decision_status.get("typed_decision", "") or final_verdict).upper()
        detailed_status = str(decision_status.get("reason_code", "") or final_verdict)
        stop_reason = str(decision_status.get("reason_code", "") or stop_reason or final_verdict)
        reason = str(decision_status.get("reason", "") or "").strip()
        if reason:
            remaining_issues.insert(0, f"Decision reason: {reason}")
        for blocker in decision_status.get("active_blockers", []) or []:
            if isinstance(blocker, dict):
                message = str(blocker.get("last_message") or blocker.get("reason_code") or blocker.get("blocker_id") or "").strip()
                if message:
                    remaining_issues.append(f"Active blocker: {message}")
    elif planner_failure:
        final_verdict = "FAILED"
        detailed_status = "PLANNER_VALIDATION_FAILED"
        stop_reason = planner_failure
        remaining_issues.insert(
            0,
            f"Workflow stopped before Engineer Stage because Planner transaction failed: {planner_failure}.",
        )
    elif state.get("stopped"):
        final_verdict = "REJECTED"
        detailed_status = "APPROVAL_REJECTED"
        stop_reason = stop_reason or "human_approval_rejected"
        remaining_issues.insert(0, f"Workflow stopped before Engineer Stage: {stop_reason}.")
    elif check_status == "FAIL" or str(state.get("manager_status", "") or "").upper() == "FAIL":
        final_verdict = "NEEDS_FIX"
        remaining_issues.insert(0, "Manager structural checks failed; cleanup is required before any pass claim.")
    elif _reviewer_blocks_success(reviewer_verdict):
        final_verdict = reviewer_verdict
        detailed_status = reviewer_verdict
        remaining_issues.insert(0, _reviewer_blocking_issue(reviewer_verdict))
        if not stop_reason:
            stop_reason = _reviewer_stop_reason(reviewer_verdict)
    elif _level_at_least(evidence_level, "L4_reduced_paper_aligned"):
        final_verdict = "PASS_WITH_LIMITATIONS"
        detailed_status = "PASS_REDUCED_ALIGNED_WITH_LIMITATIONS"
    elif verification_reduced and evidence_level == "L2_input_contract_ready":
        final_verdict = "PASS_WITH_LIMITATIONS"
        detailed_status = "VERIFICATION_REDUCED_RUN_RECORDED"
        if stop_reason == "smoke_only_accepted":
            stop_reason = "verification_reduced_l2_accepted"
        remaining_issues.insert(
            0,
            f"Evidence note: a reduced benchmark was recorded, but it is capped at L2 because {contract_cap_reason or 'contract mode is verification_only'}; official L3 is not claimed.",
        )
        if not _csv_exists(repo, "paper_alignment.csv"):
            remaining_issues.insert(1, "No `paper_alignment.csv` was produced, so L4 reduced_paper_aligned is not claimed.")
    elif contract_cap_reason and not _level_at_least(evidence_level, "L3_official_reduced_run"):
        remaining_issues.insert(
            0,
            f"Contract cap: capped at L2 because {contract_cap_reason}; no L3/L4 claim is allowed for this run.",
        )

    if decision_status and verification_reduced and evidence_level == "L2_input_contract_ready":
        note = f"Evidence note: a reduced benchmark was recorded, but it is capped at L2 because {contract_cap_reason or 'contract mode is verification_only'}; official L3 is not claimed."
        if not any(note in issue for issue in remaining_issues):
            remaining_issues.insert(0, note)
    elif decision_status and contract_cap_reason and not _level_at_least(evidence_level, "L3_official_reduced_run"):
        note = f"Contract cap: capped at L2 because {contract_cap_reason}; no L3/L4 claim is allowed for this run."
        if not any(note in issue for issue in remaining_issues):
            remaining_issues.insert(0, note)

    if planner_failure:
        result_type = "workflow stopped before evidence evaluation"
    elif state.get("stopped"):
        result_type = "workflow stopped before Engineer execution"
    elif verification_reduced and evidence_level == "L2_input_contract_ready":
        result_type = "verification-only reduced benchmark evidence, capped at L2"
    elif contract_cap_reason and not _level_at_least(evidence_level, "L3_official_reduced_run"):
        result_type = "verification-only/no-op smoke evidence, capped at L2"
    elif final_verdict == "PASS_WITH_LIMITATIONS" and _level_at_least(evidence_level, "L4_reduced_paper_aligned"):
        result_type = "reduced paper-aligned evidence with limitations"
    else:
        result_type = _result_type(final_verdict, evidence_level)

    claim_verdict = _status_from_evidence_level(evidence_level) if _level_at_least(evidence_level, "L3_official_reduced_run") else final_verdict
    claim_level = claim_level_for_verdict(claim_verdict)
    if verification_reduced and evidence_level == "L2_input_contract_ready":
        claim_level = "input contract ready with verification-only reduced evidence; capped at L2"
    next_action = _workflow_next_action(state) or _next_action(final_verdict, detailed_status, remaining_issues)
    return {
        "final_verdict": final_verdict,
        "detailed_status": detailed_status,
        "stop_reason": stop_reason,
        "claim_level": claim_level,
        "claim_level_display": _claim_display(claim_level),
        "result_type": result_type,
        "full_reproduction_claim": "No. This is not a full reproduction unless L6 is reached with explicit user authorization.",
        "next_action": next_action,
        "remaining_issues": remaining_issues or ["No blocking issue detected in final aggregation; limitations still apply to reduced evidence."],
    }


def _workflow_next_action(state: R2AState) -> str:
    decision_status = state.get("decision_status")
    if isinstance(decision_status, dict) and decision_status:
        typed = str(decision_status.get("typed_decision", "") or "")
        required = [str(item) for item in decision_status.get("required_inputs", []) or [] if str(item).strip()]
        if required:
            return "Provide required input before the workflow can continue: " + ", ".join(required) + "."
        if typed == "stop_success":
            return "Target evidence level reached; preserve artifacts and provenance."
        if typed == "stop_evidence_cap":
            return "Evidence is capped for this run; provide missing official inputs or raise scope only if higher evidence is required."
        if typed == "retry_backend":
            return "Retry the same backend stage once or switch backend if the retry limit is reached."
        if typed == "terminal_failed":
            return "Inspect the active blockers and stage diagnostics before rerunning."
    decision = state.get("workflow_decision")
    if not isinstance(decision, dict):
        return ""
    if decision.get("kind") != "request_user_input":
        return ""
    required = [str(item) for item in decision.get("required_inputs", []) if str(item).strip()]
    if required:
        return "Provide the required official input before auto-iteration can continue: " + ", ".join(required) + "."
    reason = str(decision.get("reason", "") or "").strip()
    return f"Resolve the required user input before auto-iteration can continue: {reason}." if reason else ""


def _formal_level_summary(state: R2AState, current_level: str, current_level_iteration: int) -> str:
    """构造正式等级摘要（替代 evidence_ladder）。

    核心原则：
    1. 直接从 state 读取正式等级字段
    2. 不进行文件推断
    3. 无等级时显示 UNASSESSED
    """
    level_source = str(state.get("level_source", "unassessed") or "unassessed")
    level_reasoning = str(state.get("level_reasoning", "") or "")
    supporting_artifacts = list(state.get("supporting_artifacts", []) or [])
    remaining_gaps = list(state.get("remaining_gaps", []) or [])
    reviewer_level_valid = state.get("reviewer_level_valid", False)
    reviewer_backend = str(state.get("reviewer_backend", "rules") or "rules")

    lines = []

    if not current_level or current_level == "UNASSESSED":
        # 无正式等级
        if not state.get("reviewer_executed", False):
            lines.append("**Current Level:** UNASSESSED — Reviewer was not executed")
        elif reviewer_backend == "rules":
            lines.append("**Current Level:** UNASSESSED — rules backend does not perform semantic level judgment")
        elif not reviewer_level_valid:
            rejection = str(state.get("reviewer_level_rejection_reason", "") or "output rejected")
            lines.append(f"**Current Level:** UNASSESSED — Reviewer output was rejected: {rejection}")
        else:
            lines.append("**Current Level:** UNASSESSED")
    else:
        # 有正式等级
        lines.append(f"**Current Level:** `{current_level}`")
        if current_level_iteration > 0:
            lines.append(f"**Assessment Iteration:** {current_level_iteration}")
        lines.append(f"**Assessment Source:** {level_source}")
        if level_reasoning:
            lines.append(f"**Level Reasoning:** {level_reasoning}")
        if supporting_artifacts:
            lines.append(f"**Supporting Artifacts:** {', '.join(supporting_artifacts[:5])}")
        if remaining_gaps:
            lines.append(f"**Remaining Gaps:** {', '.join(remaining_gaps[:3])}")

    # 如果本轮无效但存在历史正式等级
    if current_level and not reviewer_level_valid and state.get("reviewer_executed", False):
        lines.append("")
        lines.append("*Note: No new valid level was produced in the latest iteration.*")
        lines.append(f"*Latest attempt status: backend={reviewer_backend}, valid={reviewer_level_valid}*")

    return "\n".join(lines)


def _run_summary(evidence_decision: dict[str, Any], display: dict[str, Any]) -> str:
    lines = [
        f"Target level: {evidence_decision['target_label']} (`{evidence_decision['target_level']}`)",
        f"Observed evidence level: {evidence_decision['observed_label']} (`{evidence_decision['observed_level']}`)",
        f"Accepted level after quality gates: {evidence_decision['accepted_label']} (`{evidence_decision['accepted_level']}`)",
        f"Status: {evidence_decision['status']}",
        f"Final verdict: {display['final_verdict']}",
        f"Result type: {display['result_type']}",
        f"Next action: {display['next_action']}",
    ]
    if evidence_decision.get("cap_reason"):
        lines.insert(3, f"Cap reason: {evidence_decision['cap_reason']}")
    return bullet_list(lines)


def _decision_status_markdown(decision: Any) -> str:
    if not isinstance(decision, dict) or not decision:
        return "- Decision status: not available."
    evidence = decision.get("evidence_summary", {}) if isinstance(decision.get("evidence_summary"), dict) else {}
    blockers = decision.get("active_blockers", []) if isinstance(decision.get("active_blockers"), list) else []
    lines = [
        f"Outcome: {str(decision.get('typed_decision', '')).upper() or 'UNKNOWN'}",
        f"typed_decision: {decision.get('typed_decision', '') or 'unknown'}",
        f"reason_code: {decision.get('reason_code', '') or 'unknown'}",
        f"terminal: {bool(decision.get('terminal', False))}",
        f"requires_user_input: {bool(decision.get('requires_user_input', False))}",
        f"retryable: {bool(decision.get('retryable', False))}",
        f"accepted_evidence_level: {evidence.get('accepted_level', '') or 'unknown'}",
        f"target_reproduction_level: {evidence.get('target_level', '') or 'unknown'}",
    ]
    required = [str(item) for item in decision.get("required_inputs", []) or [] if str(item).strip()]
    if required:
        lines.append("required_inputs: " + ", ".join(required))
    reason = str(decision.get("reason", "") or "").strip()
    if reason:
        lines.append(f"reason: {reason}")
    if blockers:
        lines.append("active_blockers:")
        for blocker in blockers[:10]:
            if isinstance(blocker, dict):
                lines.append(
                    f"{blocker.get('blocker_id', '')}: type={blocker.get('type', '')}; "
                    f"reason_code={blocker.get('reason_code', '')}; count={blocker.get('count', 1)}; "
                    f"message={blocker.get('last_message', '')}"
                )
    else:
        lines.append("active_blockers: none")
    return bullet_list(lines)


def _executive_summary(display: dict[str, Any], language: str = "en") -> str:
    lines = [
        f"{_t(language, 'Observed Evidence Level', 'Observed Evidence Level')}: {display['observed_level_display']} (`{display['observed_level']}`).",
        f"{_t(language, 'Accepted Level', 'Accepted Level')}: {display['accepted_level_display']} (`{display['accepted_level']}`).",
        f"{_t(language, 'Current Level', 'Current Level')}: {display['accepted_level_display']} (`{display['accepted_level']}`).",
        f"{_t(language, 'Quality Gate Cap Reason', 'Quality Gate Cap Reason')}: {display.get('cap_reason') or 'None'}.",
        f"{_t(language, 'Target Level', 'Target Level')}: {display['target_level_display']} (`{display['target_level']}`).",
        f"{_t(language, 'Final Verdict', 'Final Verdict')}: {_verdict_display(str(display['final_verdict']))} (`{display['final_verdict']}`).",
        f"{_t(language, 'Detailed Status', 'Detailed status')}: {_verdict_display(str(display['detailed_status']))} (`{display['detailed_status']}`).",
        f"{_t(language, 'Result Type', 'Result Type')}: {display['result_type']}.",
        f"{_t(language, 'Full Reproduction Claim', 'Full Reproduction Claim')}: {display['full_reproduction_claim']}",
        f"{_t(language, 'Next Action', 'Next action')}: {display['next_action']}",
    ]
    if display.get("detailed_status") == "VERIFICATION_REDUCED_RUN_RECORDED":
        lines.append(
            _t(
                language,
                "当前结论：本次运行已完成 L2 input-contract evidence，并额外记录了一次 reduced benchmark。由于 contract mode 是 `verification_only`，本次 capped at L2，不能正式声明 L3；由于没有 `paper_alignment.csv`，也不能声明 L4。",
                "Evidence note: the run recorded a reduced benchmark, but it is capped at L2 because contract mode is verification_only. Official L3 is not claimed; without `paper_alignment.csv`, L4 is not claimed.",
            )
        )
    if any("Claude Code tool-call parse error" in issue for issue in display.get("remaining_issues", [])):
        lines.append("Backend note: Claude Code tool-call parse failure is a backend execution issue, not a paper reproduction failure.")
    return bullet_list(lines)


def _progress_cards(repo: Path, evidence_level: str, language: str = "en") -> str:
    summaries = {
        "L0_project_health": ("Project health", "project_tests.csv or workspace health artifacts"),
        "L1_source_artifact_verified": ("Source / artifact", "source_verification.csv plus build/runtime smoke"),
        "L2_input_contract_ready": ("Input contract", "input_contract_verification.csv"),
        "L3_official_reduced_run": ("Official reduced run", "reduced_metrics.csv and command_manifest.csv"),
        "L4_reduced_paper_aligned": ("Paper alignment", "paper_alignment.csv"),
        "L5_minimal_baseline_comparison": ("Baseline comparison", "baseline_comparison.csv"),
        "L6_full_or_near_full_reproduction": ("Full reproduction", "manual budget-gated full_reproduction.csv"),
    }
    lines = []
    for level, (label, evidence) in summaries.items():
        if _level_at_least(evidence_level, level):
            status = "done"
        elif level == "L6_full_or_near_full_reproduction":
            status = "not attempted"
        else:
            status = "missing"
        if level == "L5_minimal_baseline_comparison" and not _csv_exists(repo, "baseline_comparison.csv"):
            status = "not attempted"
        lines.append(f"- {label}: {status}. {_t(language, '证据', 'Evidence')}: {evidence}.")
    return "\n".join(lines)


def _what_was_done(state: R2AState, check_status: str, display: dict[str, Any], language: str = "en") -> str:
    repo = Path(state["repo_path"])
    manifest_summary = _what_was_done_from_manifest(repo, state, language)
    if manifest_summary:
        return manifest_summary
    if language == "zh":
        stage_lines = [
            f"1. Paper 阶段：生成论文上下文和 paper artifacts。证据：{report_path(repo, 'paper_context')}。",
            f"2. Planner 阶段：生成 TASK_SPEC.md 和 EXPERIMENT_CONTRACT.md。证据：{report_path(repo, 'task')}。",
            f"3. Engineer 阶段：执行有界任务，或写入 blocked/status artifacts。证据：{report_path(repo, 'execution')}。",
            f"4. Manager 阶段：检查 CSV schema、provenance、日志和结构状态 `{check_status or 'UNKNOWN'}`。证据：{report_path(repo, 'check')}。",
            f"5. Reviewer 阶段：判断当前证据等级和限制为 `{display['detailed_status']}`。证据：{report_path(repo, 'review')}。",
            "6. Final aggregation：合并 Reviewer verdict、Manager 状态和结果证据，不修改原始 artifacts。",
        ]
    else:
        stage_lines = [
            f"1. Paper stage: generated paper context/artifacts when available. Evidence: {report_path(repo, 'paper_context')}.",
            f"2. Planner stage: generated TASK_SPEC.md and EXPERIMENT_CONTRACT.md with the target evidence level. Evidence: {report_path(repo, 'task')}.",
            f"3. Engineer stage: executed the bounded task or wrote blocked/status artifacts. Evidence: {report_path(repo, 'execution')}.",
            f"4. Manager stage: checked CSV schemas, provenance, logs, and structural status `{check_status or 'UNKNOWN'}`. Evidence: {report_path(repo, 'check')}.",
            f"5. Reviewer stage: judged current evidence level and limitations as `{display['detailed_status']}`. Evidence: {report_path(repo, 'review')}.",
            "6. Final aggregation: combined Reviewer verdict, Manager status, and result evidence without changing raw artifacts.",
        ]
    if _csv_exists(repo, "reduced_metrics.csv"):
        stage_lines.append(_t(language, "- Engineer 结果：存在 reduced_metrics.csv。", "- Engineer result: real reduced metrics file is present."))
    if _csv_exists(repo, "paper_alignment.csv"):
        stage_lines.append(_t(language, "- Engineer 结果：存在 paper_alignment.csv。", "- Engineer result: paper alignment file is present."))
    if _csv_exists(repo, "command_manifest.csv"):
        stage_lines.append(_t(language, "- Engineer 结果：存在 command_manifest.csv provenance。", "- Engineer result: command provenance manifest is present."))
    return "\n".join(stage_lines)


def _planner_approval_diagnostics(repo: Path, state: R2AState, language: str = "en") -> str:
    path = artifact_dir(repo) / "logs" / "planner_transaction.json"
    if not path.exists():
        return bullet_list(
            [
                f"Planner backend: {state.get('planner_backend', 'unknown') or 'unknown'}",
                "Planner committed: yes" if (artifact_dir(repo) / "TASK_SPEC.md").exists() and (artifact_dir(repo) / "EXPERIMENT_CONTRACT.md").exists() else "Planner committed: no",
                f"Approval passed: {_yes_no(bool(state.get('approved')))}",
                _t(
                    language,
                    "Planner transaction metadata: not applicable for deterministic template Planner paths.",
                    "Planner transaction metadata: not applicable for deterministic template Planner paths.",
                )
            ]
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return bullet_list(["Planner transaction metadata could not be parsed."])
    diagnostic = dict(data.get("diagnostic", {}) or {})
    allowed_tools = diagnostic.get("allowed_tools", data.get("allowed_tools", ""))
    if isinstance(allowed_tools, list):
        allowed_tools = ", ".join(str(item) for item in allowed_tools)
    lines = [
        f"Planner backend: {diagnostic.get('planner_backend', data.get('backend', 'unknown')) or 'unknown'}",
        f"Prompt file: {diagnostic.get('prompt_file', '') or 'n/a'}",
        f"Prompt size: {diagnostic.get('prompt_size', '') or 'n/a'}",
        f"Allowed tools: {allowed_tools or 'n/a'}",
        f"Staging TASK_SPEC written: {_yes_no(diagnostic.get('staging_task_spec_written'))}",
        f"Staging EXPERIMENT_CONTRACT written: {_yes_no(diagnostic.get('staging_experiment_contract_written'))}",
        f"Planner validation passed: {_yes_no(diagnostic.get('planner_validation_passed', data.get('validation_status') == 'PASS'))}",
        f"Planner committed: {_yes_no(diagnostic.get('planner_committed', data.get('committed')))}",
        f"Approval passed: {_yes_no(diagnostic.get('approval_passed'))}",
        f"Failure category: {diagnostic.get('failure_category', data.get('failure_category', '')) or 'none'}",
        f"Failure reason: {diagnostic.get('failure_reason', '') or _planner_failure_reason(data)}",
        f"Is Claude/CCR call problem: {_yes_no(diagnostic.get('is_claude_ccr_call_problem'))}",
    ]
    return bullet_list(lines)


def _planner_failure_reason(data: dict[str, Any]) -> str:
    issues = data.get("issues", [])
    if issues:
        return "; ".join(str(item) for item in issues[:3])
    return "none"


def _yes_no(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def _experiment_summary(repo: Path, language: str = "en") -> str:
    rows = _rows_from_csv(repo, "reduced_metrics.csv")
    if not rows:
        return _t(language, "没有完成真实 reduced experiment。", "No real reduced experiment was completed.")
    columns = ["dataset", "method", "filter_type", "k", "selectivity", "efs", "ef_search", "recall", "qps", "latency_ms", "query_count", "repetitions"]
    present = [column for column in columns if any(str(row.get(column, "")).strip() for row in rows)]
    if not present:
        return f"`reduced_metrics.csv` exists with {len(rows)} row(s), but no standard summary columns were found."
    lines = ["| " + " | ".join(present) + " |", "| " + " | ".join("---" for _ in present) + " |"]
    for row in rows[:5]:
        lines.append("| " + " | ".join(str(row.get(column, "")).strip() for column in present) + " |")
    if len(rows) > 5:
        lines.append(f"\nShowing first 5 of {len(rows)} rows. Full CSV remains in provenance artifacts.")
    return "\n".join(lines)


def _source_acquisition_summary(repo: Path) -> str:
    data = _read_json_dict(report_path(repo, "source_acquisition"))
    if not data:
        return "- SOURCE_ACQUISITION.json not present."
    selected = data.get("selected_source") if isinstance(data.get("selected_source"), dict) else {}
    candidates = data.get("candidates") if isinstance(data.get("candidates"), list) else []
    selected_ref = (
        str(selected.get("url") or selected.get("repo_url") or selected.get("local_path") or "").strip()
        or str(data.get("repo_url") or data.get("local_path") or "").strip()
        or "None"
    )
    lines = [
        f"source_status: {data.get('source_status') or 'unknown'}",
        f"source_type: {data.get('source_type') or 'unknown'}",
        f"reason_code: {data.get('reason_code') or 'None'}",
        f"selected_source: {selected_ref}",
        f"candidate_count: {len(candidates)}",
    ]
    candidate_types = _candidate_type_counts(candidates)
    if candidate_types:
        lines.append(f"candidate_types: {candidate_types}")
    blockers = _string_items(data.get("blockers"))
    if blockers:
        lines.append("blockers: " + "; ".join(blockers[:3]))
    warnings = _string_items(data.get("warnings"))
    if warnings:
        lines.append("warnings: " + "; ".join(warnings[:3]))
    return bullet_list(lines)


def _input_data_summary(repo: Path) -> str:
    rows = _rows_from_csv(repo, "input_contract_verification.csv")
    if not rows:
        return "- input_contract_verification.csv not present."
    statuses: dict[str, int] = {}
    components: list[str] = []
    for row in rows:
        status = str(row.get("status", "")).strip() or "UNSPECIFIED"
        statuses[status] = statuses.get(status, 0) + 1
        component = str(row.get("component", "")).strip()
        if component and component not in components:
            components.append(component)
    lines = [
        f"input_contract_rows: {len(rows)}",
        "statuses: " + ", ".join(f"{status}: {count}" for status, count in sorted(statuses.items())),
        "components: " + (", ".join(components[:8]) if components else "Not specified"),
    ]
    if len(components) > 8:
        lines.append(f"additional_components: {len(components) - 8}")
    return bullet_list(lines)


def _executed_reduced_experiments(repo: Path) -> str:
    rows = _rows_from_csv(repo, "reduced_metrics.csv")
    if not rows:
        return "- reduced_metrics.csv not present; no executed reduced experiment recorded."
    command_ids = _unique_csv_values(rows, "command_id")
    datasets = _unique_csv_values(rows, "dataset")
    methods = _unique_csv_values(rows, "method")
    metric_columns = [
        column
        for column in ("recall", "qps", "latency_ms", "query_count", "repetitions", "build_time", "index_size")
        if any(str(row.get(column, "")).strip() for row in rows)
    ]
    lines = [
        f"reduced_metrics_rows: {len(rows)}",
        "command_ids: " + (", ".join(command_ids[:5]) if command_ids else "Not recorded"),
        "datasets: " + (", ".join(datasets[:5]) if datasets else "Not recorded"),
        "methods: " + (", ".join(methods[:5]) if methods else "Not recorded"),
        "metrics: " + (", ".join(metric_columns) if metric_columns else "No standard metric columns recorded"),
        f"command_manifest.csv present: {_yes_no(_csv_exists(repo, 'command_manifest.csv'))}",
    ]
    return bullet_list(lines)


def _command_manifest_summary(repo: Path) -> str:
    path = _result_path(repo, "command_manifest.csv")
    if not path.exists():
        return "- command_manifest.csv missing warning; this does not hard-block accepted_level."
    rows = _rows_from_csv(repo, "command_manifest.csv")
    if not rows:
        return "- command_manifest.csv exists but no readable command rows were found."
    success = sum(1 for row in rows if str(row.get("exit_code", "")).strip() == "0")
    failed = len(rows) - success
    recommended = (
        "cwd",
        "start_time",
        "end_time",
        "stdout_path",
        "stderr_path",
        "observed_outputs",
        "declared_outputs",
        "artifact_hash",
        "network_used",
        "stage",
        "iteration",
    )
    missing = [column for column in recommended if not any(str(row.get(column, "")).strip() for row in rows)]
    lines = [
        f"command_manifest_rows: {len(rows)}",
        f"successful_commands: {success}",
        f"failed_commands: {failed}",
        "missing_recommended_fields_warning: " + (", ".join(missing) if missing else "none"),
    ]
    return bullet_list(lines)


def _missing_metrics_context(repo: Path) -> list[str]:
    rows = _rows_from_csv(repo, "reduced_metrics.csv")
    if not rows:
        return ["reduced_metrics.csv missing or has no valid data rows"]
    metric_columns = ("recall", "recall_at_10", "distance", "runtime_sec", "latency_ms", "qps")
    missing: list[str] = []
    for column in metric_columns:
        if not any(str(row.get(column, "")).strip() for row in rows):
            missing.append(column)
    return missing


def _not_measured_context(repo: Path) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for name in ("reduced_metrics.csv", "paper_alignment.csv", "input_contract_verification.csv"):
        for row in _rows_from_csv(repo, name):
            values = " ".join(str(value) for value in row.values()).upper()
            if "NOT_MEASURED" not in values:
                continue
            items.append(
                {
                    "file": name,
                    "command_id": str(row.get("command_id", "")),
                    "setting_name": str(row.get("setting_name", "")),
                    "component": str(row.get("component", "")),
                    "notes": str(row.get("notes", ""))[:300],
                }
            )
    return items


def _missing_command_provenance_context(repo: Path) -> dict[str, object]:
    rows = _rows_from_csv(repo, "command_manifest.csv")
    if not rows:
        return {
            "missing": True,
            "summary": "command_manifest.csv missing or has no valid data rows",
            "warning_only": True,
        }
    recommended = (
        "cwd",
        "command",
        "start_time",
        "end_time",
        "returncode",
        "stdout_path",
        "stderr_path",
        "observed_outputs",
        "declared_outputs",
        "artifact_hash",
        "network_used",
        "stage",
        "iteration",
    )
    missing = [column for column in recommended if not any(str(row.get(column, "")).strip() for row in rows)]
    return {
        "missing": False,
        "recommended_field_missing": missing,
        "warning_only": True,
    }


def _paper_alignment_gap_context(repo: Path) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    for row in _rows_from_csv(repo, "paper_alignment.csv"):
        status = str(row.get("match_status", "")).strip().upper()
        reduced_setting = str(row.get("reduced_setting", "")).strip().upper()
        if status not in {"NOT_AVAILABLE", "NEEDS_HUMAN_VERIFICATION"} and reduced_setting != "NOT_MEASURED":
            continue
        gaps.append(
            {
                "paper_item": str(row.get("paper_item", "")),
                "setting_name": str(row.get("setting_name", "")),
                "match_status": status,
                "notes": str(row.get("notes", ""))[:300],
            }
        )
    return gaps


def _next_priority_context(repo: Path, review_feedback: dict[str, Any], active_blockers: list[Any]) -> list[str]:
    priorities = [str(item) for item in review_feedback.get("recommended_task_scope", []) or [] if str(item).strip()]
    if priorities:
        return priorities[:5]
    if active_blockers:
        return [str(active_blockers[0])]
    if _missing_command_provenance_context(repo).get("missing"):
        return ["add command_manifest.csv provenance for measured reduced outputs"]
    if _missing_metrics_context(repo):
        return ["fill missing measured reduced metrics or document why_not_measured"]
    if _paper_alignment_gap_context(repo):
        return ["resolve paper_alignment.csv NOT_AVAILABLE or NEEDS_HUMAN_VERIFICATION rows"]
    return ["preserve current evidence package and close the smallest remaining documented gap"]


def _l4_alignment_excerpt(path: Path | None) -> str:
    if not path or not path.exists():
        return "- L4_ALIGNMENT_SUMMARY.md not present."
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return "- L4_ALIGNMENT_SUMMARY.md is empty."
    lines = text.splitlines()
    return "\n".join(lines[:40])


def _candidate_type_counts(candidates: list[Any]) -> str:
    counts: dict[str, int] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_type = str(candidate.get("candidate_type") or candidate.get("source_type") or "unknown_candidate").strip()
        counts[candidate_type] = counts.get(candidate_type, 0) + 1
    return ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))


def _unique_csv_values(rows: list[dict[str, str]], column: str) -> list[str]:
    values: list[str] = []
    for row in rows:
        value = str(row.get(column, "")).strip()
        if value and value not in values:
            values.append(value)
    return values


def _string_items(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _paper_alignment_summary(repo: Path, language: str = "en") -> str:
    rows = _rows_from_csv(repo, "paper_alignment.csv")
    if not rows:
        return _t(language, "No paper alignment evidence was completed.", "No paper alignment evidence was completed.")
    statuses: dict[str, int] = {}
    paper_items: list[str] = []
    for row in rows:
        status = str(row.get("match_status", "")).strip() or "UNSPECIFIED"
        statuses[status] = statuses.get(status, 0) + 1
        item = str(row.get("paper_item", "")).strip()
        if item and item not in paper_items:
            paper_items.append(item)
    status_text = ", ".join(f"{key}: {value}" for key, value in sorted(statuses.items()))
    items_text = ", ".join(paper_items[:6]) if paper_items else "Not specified"
    allowed = {"MATCH", "PARTIAL_MATCH", "MISMATCH", "NOT_AVAILABLE", "NEEDS_HUMAN_VERIFICATION"}
    invalid = sorted(status for status in statuses if status not in allowed and status != "UNSPECIFIED")
    lines = [
        f"Mapped paper item(s): {items_text}.",
        f"Alignment statuses: {status_text}.",
        f"MATCH rows: {statuses.get('MATCH', 0)}; PARTIAL_MATCH rows: {statuses.get('PARTIAL_MATCH', 0)}; MISMATCH rows: {statuses.get('MISMATCH', 0)}; NOT_AVAILABLE rows: {statuses.get('NOT_AVAILABLE', 0)}.",
        "This is reduced paper-aligned evidence, not full reproduction; scale, hardware, budget, repeats, and baseline coverage may differ.",
    ]
    if invalid:
        lines.append("Warning: paper_alignment.csv contains non-canonical match_status values: " + ", ".join(invalid))
    return bullet_list(lines)


def _write_l4_alignment_summary(repo: Path, evidence_level: str, display: dict[str, Any]) -> Path | None:
    alignment_rows = _rows_from_csv(repo, "paper_alignment.csv")
    metrics_rows = _rows_from_csv(repo, "reduced_metrics.csv")
    if not alignment_rows or not metrics_rows:
        return None
    path = artifact_dir(repo) / "results" / "L4_ALIGNMENT_SUMMARY.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    accepted_level = str(display.get("accepted_level", "") or "")
    observed_level = str(display.get("observed_level", "") or evidence_level or "")
    accepted_l4 = _level_at_least(accepted_level, "L4_reduced_paper_aligned") if accepted_level and accepted_level != FINAL_UNASSESSED else False
    observed_l4 = _level_at_least(observed_level, "L4_reduced_paper_aligned") if observed_level and observed_level != FINAL_UNASSESSED else False
    if accepted_l4 and display.get("final_verdict") == "PASS_REDUCED_ALIGNED":
        status = "accepted"
    elif accepted_l4:
        status = "accepted_with_limitations"
    elif observed_l4:
        status = "observed_candidate_not_formally_accepted"
    else:
        status = "not_achieved"
    by_status = _alignment_rows_by_status(alignment_rows)
    first_alignment = alignment_rows[0]
    provenance_items = [
        str(_result_path(repo, "reduced_metrics.csv")),
        str(_result_path(repo, "paper_alignment.csv")),
        str(_result_path(repo, "input_contract_verification.csv")),
        str(artifact_dir(repo) / "logs"),
        str(report_path(repo, "task")),
        str(report_path(repo, "experiment_contract")),
        str(report_path(repo, "review")),
        str(report_path(repo, "evidence_decision")),
        str(report_path(repo, "final_decision")),
    ]
    command_manifest_path = _result_path(repo, "command_manifest.csv")
    if command_manifest_path.exists():
        provenance_items.insert(2, str(command_manifest_path))
    limitation_items = [
        "This is not full-paper reproduction.",
        "This is a reduced, paper-aligned reproduction evidence package.",
        "Remaining gaps are recorded in paper_alignment.csv and REVIEW_REPORT.md.",
    ]
    if not command_manifest_path.exists():
        limitation_items.append("command_manifest.csv is missing; it is not listed as existing provenance.")
    if observed_l4 and not accepted_l4:
        limitation_items.append("L4 evidence is observed as a candidate only; it is not formally accepted without a valid EVIDENCE_DECISION.json.")
    content = [
        "# L4_ALIGNMENT_SUMMARY",
        "",
        "## Verdict",
        "",
        f"- L4 status: {status}",
        "- Claim: reduced paper-aligned evidence, not full reproduction",
        f"- Accepted level: {accepted_level or FINAL_UNASSESSED}",
        f"- Observed level: {observed_level or FINAL_UNASSESSED}",
        "",
        "## Linked Paper Experiment",
        "",
        f"- Paper item: {_cell(first_alignment, 'paper_item') or 'Not specified'}",
        f"- Paper setting summary: {_cell(first_alignment, 'paper_setting') or 'See paper_alignment.csv'}",
        f"- Reduced setting summary: {_cell(first_alignment, 'reduced_setting') or 'See paper_alignment.csv'}",
        "",
        "## Matched Settings",
        "",
        _alignment_bullets(by_status.get("MATCH", [])),
        "",
        "## Partially Matched Settings",
        "",
        _alignment_bullets(by_status.get("PARTIAL_MATCH", [])),
        "",
        "## Mismatched / Missing Settings",
        "",
        _alignment_bullets(by_status.get("MISMATCH", []) + by_status.get("NOT_AVAILABLE", []) + by_status.get("NEEDS_HUMAN_VERIFICATION", [])),
        "",
        "## Reduced Metrics Summary",
        "",
        _metrics_table(metrics_rows),
        "",
        "## Provenance",
        "",
        bullet_list([item for item in provenance_items if Path(item).exists()]),
        "",
        "## Limitations",
        "",
        bullet_list(limitation_items),
        "",
        "## Suggested Next Step",
        "",
        display.get("next_action", "Perform closure cleanup or extend the reduced alignment when authorized."),
        "",
    ]
    path.write_text("\n".join(content), encoding="utf-8")
    return path


def _alignment_rows_by_status(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("match_status", "")).strip() or "UNSPECIFIED", []).append(row)
    return grouped


def _alignment_bullets(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "- None recorded."
    return bullet_list(
        [
            f"{_cell(row, 'setting_name') or 'setting'}: paper={_cell(row, 'paper_setting') or 'n/a'}; reduced={_cell(row, 'reduced_setting') or 'n/a'}; evidence={_cell(row, 'evidence_source') or 'n/a'}; notes={_cell(row, 'notes') or 'n/a'}"
            for row in rows
        ]
    )


def _metrics_table(rows: list[dict[str, str]]) -> str:
    columns = ["command_id", "dataset", "method", "k", "selectivity", "efs", "ef_search", "recall", "qps", "latency_ms", "notes"]
    present = [column for column in columns if any(_cell(row, column) for row in rows)]
    if not present:
        return "- reduced_metrics.csv exists, but no standard summary columns were found."
    lines = ["| " + " | ".join(present) + " |", "| " + " | ".join("---" for _ in present) + " |"]
    for row in rows[:5]:
        lines.append("| " + " | ".join(_cell(row, column) for column in present) + " |")
    if len(rows) > 5:
        lines.append(f"\nShowing first 5 of {len(rows)} rows.")
    return "\n".join(lines)


def _result_path(repo: Path, name: str) -> Path:
    for directory in (artifact_dir(repo) / "results", repo / "results"):
        path = directory / name
        if path.exists():
            return path
    return artifact_dir(repo) / "results" / name


def _cell(row: dict[str, str], column: str) -> str:
    return str(row.get(column, "")).strip()


def _remaining_issues(repo: Path, check_status: str) -> list[str]:
    issues: list[str] = []
    input_integrity = summarize_official_input_integrity(repo)
    if input_integrity.get("has_blocking_issue"):
        issues.append(
            "Official input files were found or referenced but failed integrity checks. "
            "Empty placeholder files or invalid required inputs block `official_reduced` / L3. "
            "Suggested action: re-download official/paper-linked input or verify artifact source."
        )
        for line in input_integrity.get("summary_lines", [])[:4]:
            issues.append(f"Official input integrity: {line}")
    if check_status == "WARNING":
        issues.append("Manager reported WARNING; inspect CHECK_REPORT.md warnings before treating the run as clean.")
    elif check_status == "FAIL":
        issues.append("Manager reported FAIL; structural cleanup is required.")
    check_text = _read_text(report_path(repo, "check"))
    for section_name in ("Errors", "Warnings"):
        section = _extract_section(check_text, section_name)
        for line in section.splitlines():
            stripped = line.strip()
            if stripped and stripped not in {"- None", "None"}:
                issues.append(stripped[:240])
    status_rows = _rows_from_csv(repo, "reproduction_status.csv")
    for row in status_rows:
        status = str(row.get("status", "")).strip()
        reason = str(row.get("reason", "")).strip()
        next_action = str(row.get("next_action", "")).strip()
        if status and _status_row_is_current_issue(status):
            issues.append(f"{status}: {reason or 'see reproduction_status.csv'}; next action: {next_action or 'inspect status CSV'}.")
    issues.extend(_planner_transaction_issues(repo))
    issues.extend(_backend_transient_issues(repo))
    issues.extend(_stage_boundary_issues(repo))
    return _dedupe(issues)


def _planner_transaction_issues(repo: Path) -> list[str]:
    path = artifact_dir(repo) / "logs" / "planner_transaction.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return [
            "Planner transaction metadata could not be parsed. This is a stage execution/reporting issue, not a paper reproduction failure."
        ]
    if data.get("committed") and data.get("validation_status") == "PASS":
        return []
    failure = data.get("failure_category") or "PLANNER_TRANSACTION_FAILED"
    status = data.get("execution_status") or failure
    staging = data.get("staging_dir", "")
    detail = "; ".join(str(item) for item in data.get("issues", [])[:3])
    return [
        "Planner candidate outputs were rejected by the transaction validator. "
        "No official TASK_SPEC / EXPERIMENT_CONTRACT was committed. "
        "This is a stage execution failure, not a paper reproduction failure. "
        f"failure_category={failure}; execution_status={status}; staging_dir={staging}; details={detail or 'see planner_transaction.json'}."
    ]


def _stage_boundary_issues(repo: Path) -> list[str]:
    issues: list[str] = []
    for path in _backend_log_paths(repo):
        text = _read_text(path)
        if "STAGE_BOUNDARY_VIOLATION" in text or "PLANNER_FORBIDDEN_WRITE" in text:
            issues.append(
                "Planner stage boundary violation: Planner modified files outside its allowed outputs. "
                "This is a backend/stage execution failure, not a paper reproduction failure. "
                f"Evidence: {path}."
            )
            break
    return issues


def _backend_transient_issues(repo: Path) -> list[str]:
    issues: list[str] = []
    issues.extend(_backend_retry_issues(repo))
    if issues:
        return _dedupe(issues)
    for path in _backend_log_paths(repo):
        text = _read_text(path)
        result = classify_backend_error(text, "", backend="claude")
        if result.get("failure_category") == TOOL_CALL_PARSE_FAILURE:
            issues.append(
                "Stage backend failure: Claude Code tool-call parse error. "
                "This is not a paper reproduction failure. "
                "Suggested action: retry the same stage once. "
                f"Evidence: {path}."
            )
        elif result.get("is_backend_failure"):
            issues.append(
                f"Stage backend failure: {result.get('failure_detail') or result.get('failure_category')}. "
                "This is not a paper reproduction failure. "
                f"Suggested action: {result.get('suggested_action') or 'inspect backend configuration'}. "
                f"Evidence: {path}."
            )
    return issues


def _backend_retry_issues(repo: Path) -> list[str]:
    attempts: dict[str, dict[int, list[Path]]] = {}
    pattern = re.compile(r"claude_(?P<stage>.+)_attempt_(?P<attempt>\d+)_(stdout|stderr)\.log$")
    for path in _backend_log_paths(repo):
        match = pattern.match(path.name)
        if not match:
            continue
        stage = match.group("stage")
        attempt = int(match.group("attempt"))
        attempts.setdefault(stage, {}).setdefault(attempt, []).append(path)

    issues: list[str] = []
    for stage, by_attempt in sorted(attempts.items()):
        first_text = "\n".join(_read_text(path) for path in by_attempt.get(1, []))
        first_error = classify_backend_error(first_text, "", backend="claude")
        if first_error.get("failure_category") != TOOL_CALL_PARSE_FAILURE:
            continue
        second_text = "\n".join(_read_text(path) for path in by_attempt.get(2, []))
        evidence = _summarize_attempt_evidence(by_attempt)
        if not second_text:
            issues.append(
                f"Stage backend failure: Claude Code tool-call parse error in `{stage}`. "
                "This is not a paper reproduction failure. Suggested action: retry the same stage once. "
                f"Evidence: {evidence}."
            )
            continue
        second_error = classify_backend_error(second_text, "", backend="claude")
        freshness_failed = "freshness_ok: false" in second_text.lower()
        second_returned_zero = "returncode: 0" in second_text.lower()
        if second_returned_zero and not freshness_failed and not second_error.get("is_backend_failure"):
            issues.append(
                f"Claude Code backend transient failure occurred in `{stage}` and was recovered by retry. "
                "First failure: TOOL_CALL_PARSE_FAILURE. Retry result: success. "
                "This was a backend execution issue, not a paper reproduction failure. "
                f"Evidence: {evidence}."
            )
        elif second_returned_zero and freshness_failed:
            issues.append(
                f"Retry for `{stage}` produced outputs, but output freshness validation failed. "
                "Manual inspection required; partial outputs may exist from the failed attempt. "
                f"Evidence: {evidence}."
            )
        else:
            issues.append(
                f"Claude Code backend transient failure persisted after retry in `{stage}`. "
                "Suggested action: retry the same stage manually or switch backend. "
                "This is not evidence that the paper is unreproducible. "
                f"Evidence: {evidence}."
            )
    return issues


def _summarize_attempt_evidence(by_attempt: dict[int, list[Path]]) -> str:
    selected: list[Path] = []
    for attempt in (1, max(by_attempt) if by_attempt else 1):
        for path in by_attempt.get(attempt, []):
            if len(selected) < 4 and path not in selected:
                selected.append(path)
    all_paths = [path for attempt in sorted(by_attempt) for path in by_attempt[attempt]]
    summary = ", ".join(str(path) for path in selected)
    if len(all_paths) > len(selected):
        summary += f". Additional attempt logs available under `.r2a/runs/` ({len(all_paths) - len(selected)} more file(s))."
    return summary


def _backend_log_paths(repo: Path) -> list[Path]:
    roots = [artifact_dir(repo) / "logs", runs_dir(repo)]
    paths: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        paths.extend(sorted(root.rglob("*_stdout.log")))
        paths.extend(sorted(root.rglob("*_stderr.log")))
    return paths


def _check_status(repo: Path) -> str:
    text = _read_text(report_path(repo, "check"))
    status = _extract_section(text, "Status").strip().splitlines()
    return status[0].strip().upper() if status else ""


def _rows_from_csv(repo: Path, name: str) -> list[dict[str, str]]:
    for directory in (artifact_dir(repo) / "results", repo / "results"):
        path = directory / name
        if not path.exists():
            continue
        result = sanitized_csv_rows(path)
        if result.has_error and not result.rows:
            return []
        return result.rows
    return []


def _csv_exists(repo: Path, name: str) -> bool:
    return any((directory / name).exists() for directory in (artifact_dir(repo) / "results", repo / "results"))


def has_verification_only_reduced_evidence(repo: str | Path) -> bool:
    repo_path = Path(repo)
    if not contract_l2_cap_reason(repo_path):
        return False
    rows = _rows_from_csv(repo_path, "reduced_metrics.csv")
    if not rows or not _csv_exists(repo_path, "command_manifest.csv"):
        return False
    return True


def _status_row_is_current_issue(status: str) -> bool:
    return status.upper() not in {
        "OK",
        "PASS",
        "DONE",
        "FIXED",
        "RESOLVED",
        "SCHEMA_FIXED",
        "VERIFICATION_DOCUMENTED",
        "VERIFICATION_REDUCED_RUN_RECORDED",
        "INPUT_CONTRACT_READY_WITH_REDUCED_METRICS",
        "L2_CEILING_DUE_TO_VERIFICATION_ONLY",
    }


def _extract_section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    start = text.find(marker)
    if start < 0:
        marker = f"### {heading}"
        start = text.find(marker)
    if start < 0:
        return ""
    body_start = start + len(marker)
    next_heading = text.find("\n## ", body_start)
    if next_heading < 0:
        next_heading = text.find("\n### ", body_start)
    return (text[body_start:] if next_heading < 0 else text[body_start:next_heading]).strip()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _level_display(level: str) -> str:
    return LEVEL_DISPLAY.get(level, level or "Unknown")


def _verdict_display(verdict: str) -> str:
    return VERDICT_DISPLAY.get(verdict, verdict.replace("_", " ").title() if verdict else "Unknown")


def _claim_display(claim: str) -> str:
    if "full" in claim.lower():
        return claim
    if claim in {"limited or unresolved", "project health / limited validation"}:
        return "Limited claim: not full reproduction"
    return f"{claim}; not full reproduction"


def _result_type(verdict: str, evidence_level: str) -> str:
    if verdict == "PASS_DEMO_ONLY":
        return "demo-only"
    if evidence_level == "L4_reduced_paper_aligned":
        return "reduced paper-aligned evidence"
    if evidence_level == "L3_official_reduced_run":
        return "official reduced method evidence"
    if evidence_level == "L2_input_contract_ready":
        return "input-contract evidence"
    if evidence_level == "L1_source_artifact_verified":
        return "source/artifact smoke evidence"
    return "project health or unresolved evidence"


def _status_from_evidence_level(level: str) -> str:
    return {
        "L1_source_artifact_verified": "PASS_SMOKE_ONLY",
        "L2_input_contract_ready": "INPUT_CONTRACT_READY",
        "L3_official_reduced_run": "PASS_REDUCED_METHOD_ONLY",
        "L4_reduced_paper_aligned": "PASS_REDUCED_ALIGNED",
        "L5_minimal_baseline_comparison": "PASS_REDUCED_COMPARISON",
        "L6_full_or_near_full_reproduction": "PASS",
    }.get(level, "PASS_WITH_LIMITATIONS")


def _level_at_least(level: str, target: str) -> bool:
    order = list(LEVEL_DISPLAY)
    try:
        return order.index(level) >= order.index(target)
    except ValueError:
        return False


def _next_action(final_verdict: str, detailed_status: str, issues: list[str]) -> str:
    if final_verdict in {"REJECT", "NEEDS_INPUT", "NEEDS_OFFICIAL_INPUT", "NEEDS_INPUT_OR_BUDGET"}:
        return "Resolve the Reviewer blocking condition before claiming success."
    if final_verdict == "NEEDS_FIX":
        return "Fix Manager/Reviewer blocking issues, then rerun Manager and Reviewer."
    if final_verdict == "BORDERLINE":
        return "Clarify or verify the unresolved Reviewer concern before claiming success."
    if detailed_status == "PASS_REDUCED_ALIGNED_WITH_LIMITATIONS":
        return "Perform closure cleanup for Manager warnings/provenance/schema issues; do not rerun expensive experiments unless evidence is stale."
    if detailed_status == "PASS_REDUCED_ALIGNED":
        return "Optionally run a low-cost L5 baseline comparison if explicitly desired."
    if detailed_status == "PASS_REDUCED_METHOD_ONLY":
        return "Add paper alignment mapping before claiming L4."
    if detailed_status == "VERIFICATION_REDUCED_RUN_RECORDED":
        return "Upgrade the next TASK_SPEC/EXPERIMENT_CONTRACT to official_reduced if L3 is desired; generate paper_alignment.csv if L4 is desired."
    if detailed_status == "INPUT_CONTRACT_READY":
        return "Run the smallest official/paper-linked reduced experiment when inputs and budget allow."
    if detailed_status == "PASS_DEMO_ONLY":
        return "Acquire official inputs before any paper reproduction claim."
    if issues:
        return "Resolve the listed remaining issues."
    return "No immediate action required beyond preserving limitations."


def _reviewer_blocks_success(verdict: str) -> bool:
    return verdict in {
        "REJECT",
        "NEEDS_FIX",
        "NEEDS_INPUT",
        "NEEDS_OFFICIAL_INPUT",
        "NEEDS_INPUT_OR_BUDGET",
        "BORDERLINE",
    }


def _reviewer_blocking_issue(verdict: str) -> str:
    return {
        "REJECT": "Reviewer rejected the run; no success claim is allowed.",
        "NEEDS_FIX": "Reviewer requested fixes; no success claim is allowed.",
        "NEEDS_INPUT": "Reviewer requires user input; no success claim is allowed.",
        "NEEDS_OFFICIAL_INPUT": "Reviewer requires official input before a reproduction claim.",
        "NEEDS_INPUT_OR_BUDGET": "Reviewer requires input or budget authorization before a reproduction claim.",
        "BORDERLINE": "Reviewer marked the evidence borderline; no success claim is allowed.",
    }.get(verdict, "Reviewer verdict blocks a success claim.")


def _reviewer_stop_reason(verdict: str) -> str:
    return {
        "REJECT": "reviewer_rejected",
        "NEEDS_FIX": "reviewer_needs_fix",
        "NEEDS_INPUT": "reviewer_needs_input",
        "NEEDS_OFFICIAL_INPUT": "reviewer_needs_official_input",
        "NEEDS_INPUT_OR_BUDGET": "reviewer_needs_input_or_budget",
        "BORDERLINE": "reviewer_borderline",
    }.get(verdict, "reviewer_blocked_success")


def _report_labels(language: str) -> dict[str, str]:
    if language == "zh":
        return {
            "heading_executive_summary": "执行摘要",
            "heading_final_status": "最终状态",
            "heading_total_iterations": "总迭代次数",
            "heading_stop_reason": "停止原因",
            "heading_final_verdict": "最终判定",
            "heading_detailed_status": "详细状态",
            "heading_reproduction_level": "当前复现等级",
            "heading_progress_cards": "阶段进度",
            "heading_what_was_done": "实际完成内容",
            "heading_experiment_summary": "实验摘要",
            "heading_paper_alignment_summary": "论文对齐摘要",
            "heading_remaining_issues": "剩余问题",
            "heading_provenance": "溯源文件",
            "heading_iteration_summary": "迭代摘要",
            "heading_evidence_level_checks": "证据等级检查",
            "heading_limitations": "局限性",
            "heading_raw_engineer_results": "原始工程结果",
            "label_current": "Current",
            "label_target": "Target",
            "label_result_type": "Result Type",
            "label_full_reproduction_claim": "Full Reproduction Claim",
            "label_claim": "Claim",
            "label_download_budget": "Download Budget",
            "label_next_action": "Next Action",
        }
    return {
        "heading_executive_summary": "Executive Summary",
        "heading_final_status": "Final Status",
        "heading_total_iterations": "Total Iterations",
        "heading_stop_reason": "Stop Reason",
        "heading_final_verdict": "Final Verdict",
        "heading_detailed_status": "Detailed Status",
        "heading_reproduction_level": "Reproduction Level",
        "heading_progress_cards": "Progress Cards",
        "heading_what_was_done": "What Was Actually Done",
        "heading_experiment_summary": "Experiment Summary",
        "heading_paper_alignment_summary": "Paper Alignment Summary",
        "heading_remaining_issues": "Remaining Issues",
        "heading_provenance": "Provenance",
        "heading_iteration_summary": "Iteration Summary",
        "heading_evidence_level_checks": "Evidence Level Checks",
        "heading_limitations": "Limitations",
        "heading_raw_engineer_results": "Raw Engineer Results",
        "label_current": "Current",
        "label_target": "Target",
        "label_result_type": "Result Type",
        "label_full_reproduction_claim": "Full Reproduction Claim",
        "label_claim": "Claim",
        "label_download_budget": "Download Budget",
        "label_next_action": "Next Action",
    }


def _t(language: str, zh: str, en: str) -> str:
    return zh if language == "zh" else en


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    output = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _iteration_entry(state: R2AState, iter_dir: Path) -> dict[str, Any]:
    repo = Path(state["repo_path"])
    # 直接从 state 读取正式等级，不进行文件推断
    evidence_level = str(state.get("current_reproduction_level", "") or state.get("reproduction_level", "") or "")
    paths = {
        "task_spec": iter_dir / "TASK_SPEC.md",
        "experiment_contract": iter_dir / "EXPERIMENT_CONTRACT.md",
        "execution_report": iter_dir / "EXECUTION_REPORT.md",
        "check_report": iter_dir / "CHECK_REPORT.md",
        "review_report": iter_dir / "REVIEW_REPORT.md",
        "review_feedback": iter_dir / "REVIEW_FEEDBACK.json",
    }
    return {
        "iteration": int(state.get("iteration", 1)),
        "task_spec": _existing_archive_path(paths["task_spec"]),
        "experiment_contract": _existing_archive_path(paths["experiment_contract"]),
        "execution_report": _existing_archive_path(paths["execution_report"]),
        "check_report": _existing_archive_path(paths["check_report"]),
        "review_report": _existing_archive_path(paths["review_report"]),
        "review_feedback": _existing_archive_path(paths["review_feedback"]),
        "archive_missing_files": [name for name, path in paths.items() if not path.exists()],
        "check_status": state.get("manager_status", ""),
        "reviewer_verdict": state.get("reviewer_verdict", ""),
        "reproduction_level": evidence_level,
        "state_reproduction_level": state.get("reproduction_level", ""),
        "target_reproduction_level": state.get("target_reproduction_level", ""),
        "claim_level": claim_level_for_verdict(state.get("reviewer_verdict", "")),
        "suggested_next_action": state.get("suggested_next_action", ""),
        "stage_backends": _stage_backends(state),
        "codex_stages_used": _codex_stages_used(state),
        "ai_stages_used": _ai_stages_used(state),
        "summary": state.get("final_report", ""),
    }


def _current_iteration_has_reviewer_cycle(state: R2AState) -> bool:
    return bool(
        state.get("reviewer_executed")
        or str(state.get("reviewer_verdict", "") or "").strip()
        or str(state.get("review_report_path", "") or "").strip()
        or str(state.get("review_feedback_path", "") or "").strip()
        or state.get("structured_review_feedback")
    )


def _completed_review_iterations_count(state: R2AState) -> int:
    completed: set[int] = set()
    history = state.get("iteration_history", [])
    if isinstance(history, list):
        for item in history:
            if not isinstance(item, dict):
                continue
            if not _history_entry_has_reviewer_cycle(item):
                continue
            try:
                iteration = int(item.get("iteration", 0) or 0)
            except (TypeError, ValueError):
                iteration = 0
            if iteration > 0:
                completed.add(iteration)
    if _current_iteration_has_reviewer_cycle(state):
        try:
            current_iteration = max(1, int(state.get("iteration", 1) or 1))
        except (TypeError, ValueError):
            current_iteration = 1
        if not completed:
            return current_iteration
        completed.add(current_iteration)
    return len(completed)


def _history_entry_has_reviewer_cycle(item: dict[str, Any]) -> bool:
    if str(item.get("reviewer_verdict", "") or "").strip():
        return True
    for key in ("review_report", "review_feedback", "review_verdict"):
        if str(item.get(key, "") or "").strip():
            return True
    missing = set(str(value) for value in item.get("archive_missing_files", []) or [])
    return "review_report" not in missing and "review_feedback" not in missing and bool(item.get("task_spec"))


def _existing_archive_path(path: Path) -> str:
    return str(path) if path.exists() else ""


def _copy_tree_contents(source: Path, target: Path) -> None:
    if not source.exists():
        return
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)


def _format_iteration_summary(history: list[dict[str, Any]]) -> str:
    if not history:
        return "- No iterations recorded."
    lines = []
    for item in history:
        lines.append(
            f"- Iteration {item.get('iteration')}: manager={item.get('check_status', '')}, reviewer={item.get('reviewer_verdict', '')}, action={item.get('suggested_next_action', '')}"
        )
    return "\n".join(lines)


def _stage_backends(state: R2AState) -> dict[str, str]:
    return {
        "paper": state.get("paper_backend", "preprocess"),
        "planner": state.get("planner_backend", "template"),
        "engineer": state.get("engineer_executor", state.get("executor", "shell")),
        "manager": state.get("manager_backend", "rules"),
        "reviewer": state.get("reviewer_backend", "rules"),
    }


def _codex_stages_used(state: R2AState) -> list[str]:
    backends = _stage_backends(state)
    return [stage for stage, backend in backends.items() if backend in {"codex", "codex_review", "claude", "claude_review", "claude_reader"}]


def _ai_stages_used(state: R2AState) -> list[str]:
    backends = _stage_backends(state)
    ai_backends = {
        "ai_reader",
        "claude_reader",
        "openclaw_reader",
        "codex",
        "claude",
        "codex_review",
        "claude_review",
        "openclaw",
        "openclaw_review",
    }
    return [stage for stage, backend in backends.items() if backend in ai_backends]
