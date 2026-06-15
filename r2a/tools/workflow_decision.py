from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from r2a.core.final_decision import build_final_decision
from r2a.core.evidence_policy import evaluate_l0_l4, level_reached
from r2a.core.paths import artifact_dir, report_path
from r2a.core.verdicts import PASS_LIKE_VERDICTS
from r2a.tools.reproduction_levels import LEVEL_INDEX, normalize_level, official_input_progress_authorized


PAPER_STRUCTURED_KEYS = (
    "paper_context",
    "paper",
    "paper_evidence",
    "paper_reproduction_card",
    "paper_parse_quality",
    "paper_analysis",
    "paper_output",
)

# Core Paper Markdown artifacts that provide usable input for Planner
PAPER_MARKDOWN_ARTIFACT_KEYS = (
    "paper_context",
    "paper",
    "paper_evidence",
    "paper_reproduction_card",
    "paper_text",
    "paper_sections",
    "paper_analysis",
)

PAPER_TEXT_FALLBACK_KEYS = (
    "paper_text",
    "paper_pages",
    "paper_sections",
    "paper_captions",
)

USER_INPUT_BLOCKER_IDS = {
    "missing_official_source",
    "empty_repository_scaffold",
    "missing_official_input_contract",
    "missing_official_dataset_or_ground_truth",
    "algorithm_dependency_network_authorization",
}

RETRYABLE_BACKEND_BLOCKERS = {
    "planner_backend_failure",
    "openclaw_provider_error",
    "openclaw_transport_error",
}

SUCCESS_STATUSES = {
    "OK",
    "PASS",
    "PASSED",
    "FOUND",
    "READY",
    "DONE",
    "RESOLVED",
    "SUPPORTED",
    "GENERATED",
    "BUILT",
    "PRESENT",
    "AVAILABLE",
    "SUCCESS",
    "VERIFIED",
    "VERIFIED_PRESERVED",
    "SOURCE_AVAILABLE",
}
SOURCE_STATUS_COLUMNS = ("status", "access_status", "verdict", "result", "source_verification_status", "source_status")
MISSING_SOURCE_STATUSES = {"NOT_AVAILABLE", "NEEDS_INPUT", "NEEDS_OFFICIAL_INPUT", "NOT_FOUND", "MISSING", "FAILED"}
INPUT_BLOCKING_STATUSES = {"NEEDS_INPUT", "NEEDS_OFFICIAL_INPUT", "NEEDS_INPUT_OR_BUDGET", "NOT_AVAILABLE", "MISSING"}
NETWORK_AUTHORIZATION_MARKERS = (
    "NEEDS_NETWORK_AUTHORIZATION",
    "NETWORK AUTHORIZATION REQUIRED",
    "NETWORK NOT AUTHORIZED",
    "EXTERNAL GIT CLONE REQUIRED",
    "GIT CLONE + CMAKE REQUIRES NETWORK",
    "ALGORITHM BINARIES REQUIRE NETWORK",
    "REQUIRES GIT CLONE + CMAKE",
)
NETWORK_AUTHORIZATION_RESULT_FILES = (
    "input_contract_verification.csv",
    "reproduction_status.csv",
    "algorithm_installation.csv",
    "runtime_smoke.csv",
    "build_smoke.csv",
    "ENGINEER_NOTES.md",
)

DECISION_SOURCE = "decision_aggregator"
BACKEND_RETRY_LIMIT = 2
BLOCKER_CONVERGENCE_LIMIT = 3
NON_TERMINAL_PLAN_QUALITY_BLOCKERS = {"placeholder_task"}

TERMINAL_DECISIONS = {
    "stop_success",
    "stop_evidence_cap",
    "final",
    "request_paper",
    "request_source",
    "request_dataset",
    "request_network_authorization",
    "request_approval",
    "terminal_failed",
}

USER_INPUT_DECISIONS = {
    "request_paper",
    "request_source",
    "request_dataset",
    "request_network_authorization",
    "request_approval",
}

BLOCKER_REASON_CODES = {
    "missing_paper": "MISSING_PAPER",
    "missing_paper_bundle": "MISSING_PAPER_BUNDLE",
    "invalid_paper_output": "INVALID_PAPER_OUTPUT",
    "missing_source": "OFFICIAL_SOURCE_NOT_AVAILABLE",
    "empty_repo": "EMPTY_REPOSITORY_SCAFFOLD",
    "source_inspection_failed": "SOURCE_INSPECTION_FAILED",
    "missing_dataset": "OFFICIAL_DATASET_NOT_AVAILABLE",
    "missing_input_contract": "OFFICIAL_INPUT_CONTRACT_NOT_AVAILABLE",
    "network_authorization": "NETWORK_AUTHORIZATION_REQUIRED",
    "planner_not_ready": "PLANNER_NOT_READY",
    "invalid_planner_output": "INVALID_PLANNER_OUTPUT",
    "placeholder_task": "PLACEHOLDER_TASK",
    "engineer_not_ready": "ENGINEER_NOT_READY",
    "planner_backend_failure": "PLANNER_BACKEND_FAILURE",
    "engineer_backend_failure": "ENGINEER_BACKEND_FAILURE",
    "manager_classification_conflict": "MANAGER_CLASSIFICATION_CONFLICT",
    "evidence_cap": "EVIDENCE_CAP_REACHED",
    "fixable_engineering_failure": "FIXABLE_ENGINEERING_FAILURE",
    "unknown_terminal_failure": "UNKNOWN_TERMINAL_FAILURE",
}


class BlockerLedger:
    """Minimal durable ledger for repeated blockers across iterations."""

    def __init__(self, repo_path: str | Path) -> None:
        self.repo_path = Path(repo_path)
        self.path = artifact_dir(self.repo_path) / "BLOCKER_LEDGER.json"

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "blockers": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": 1, "blockers": {}}
        if not isinstance(data, dict):
            return {"schema_version": 1, "blockers": {}}
        blockers = data.get("blockers")
        if not isinstance(blockers, dict):
            data["blockers"] = {}
        data.setdefault("schema_version", 1)
        return data

    def update(self, blockers: list[dict[str, Any]], iteration: int) -> list[dict[str, Any]]:
        data = self.load()
        ledger = data.setdefault("blockers", {})
        if not isinstance(ledger, dict):
            ledger = {}
            data["blockers"] = ledger
        active: list[dict[str, Any]] = []
        for blocker in blockers:
            normalized = normalize_blocker(blocker)
            blocker_id = str(normalized.get("blocker_id", "") or "")
            if not blocker_id:
                continue
            prior = ledger.get(blocker_id, {}) if isinstance(ledger.get(blocker_id), dict) else {}
            last_seen = int(prior.get("last_seen_iteration", 0) or 0)
            if last_seen == int(iteration):
                count = int(prior.get("count", 1) or 1)
            elif last_seen == int(iteration) - 1:
                count = int(prior.get("count", 0) or 0) + 1
            else:
                count = 1
            entry = {
                **normalized,
                "first_seen_iteration": int(prior.get("first_seen_iteration", iteration) or iteration),
                "last_seen_iteration": int(iteration),
                "count": count,
            }
            ledger[blocker_id] = entry
            active.append(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return active


def aggregate_terminal_decision(
    state: dict[str, Any],
    *,
    manifest: dict[str, Any] | None = None,
    iteration_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the single deterministic workflow routing decision."""
    del manifest, iteration_state
    repo = Path(str(state.get("repo_path", "") or "."))
    iteration = int(state.get("iteration", state.get("current_iteration", 1)) or 1)
    max_iterations = int(state.get("max_iterations", 1) or 1)
    evidence = _evidence_summary(repo, state)
    raw_blockers = [
        *_paper_input_blockers(state),
        *_paper_blockers(repo),
        *_readiness_blockers(state, repo),
        *_source_artifact_blockers(state, repo),
        *_engineer_backend_blockers(state),
    ]
    if _post_review_phase(state) or state.get("engineer_status"):
        raw_blockers.extend(collect_workflow_blockers(state, repo))
        raw_blockers.extend(_manager_decision_blockers(repo))
    active_blockers = BlockerLedger(repo).update(_dedupe_normalized_blockers(raw_blockers), iteration)
    backend_blockers = _blockers_of_type(active_blockers, {"planner_backend_failure", "engineer_backend_failure"})
    paper_blockers = _blockers_of_type(active_blockers, {"missing_paper", "missing_paper_bundle", "invalid_paper_output"})
    source_blockers = _blockers_of_type(active_blockers, {"missing_source", "empty_repo"})
    source_inspection_blockers = _blockers_of_type(active_blockers, {"source_inspection_failed"})
    planner_readiness_blockers = _blockers_of_type(active_blockers, {"planner_not_ready"})
    invalid_plan_blockers = _blockers_of_type(active_blockers, {"invalid_planner_output", "engineer_not_ready"})
    network_blockers = _blockers_of_type(active_blockers, {"network_authorization"})
    dataset_blockers = _terminal_dataset_blockers(
        _blockers_of_type(active_blockers, {"missing_dataset", "missing_input_contract"}),
        state,
        iteration,
        max_iterations,
    )
    conflict_blockers = _blockers_of_type(active_blockers, {"manager_classification_conflict"})
    repeated_blockers = [
        blocker
        for blocker in active_blockers
        if str(blocker.get("type", "")) not in NON_TERMINAL_PLAN_QUALITY_BLOCKERS
        and int(blocker.get("count", 1) or 1) >= BLOCKER_CONVERGENCE_LIMIT
    ]

    if paper_blockers:
        return _decision(
            "request_paper",
            "MISSING_PAPER",
            iteration=iteration,
            active_blockers=paper_blockers,
            evidence_summary=evidence,
            reason=_decision_reason_from_active(paper_blockers),
            required_inputs=["readable_paper_pdf_or_text"],
        )

    planner_failure = _planner_backend_failure(state, repo)
    if planner_failure:
        backend_blockers = _merge_active_blockers(
            backend_blockers,
            BlockerLedger(repo).update([planner_failure], iteration),
        )

    if backend_blockers:
        if any(int(blocker.get("count", 1) or 1) >= BACKEND_RETRY_LIMIT for blocker in backend_blockers):
            return _decision(
                "terminal_failed",
                "BACKEND_RETRY_LIMIT_EXCEEDED",
                iteration=iteration,
                active_blockers=backend_blockers,
                evidence_summary=evidence,
                reason=_decision_reason_from_active(backend_blockers),
            )
        return _decision(
            "retry_backend",
            _first_reason_code(backend_blockers, "BACKEND_RETRY_AVAILABLE"),
            terminal=False,
            retryable=True,
            iteration=iteration,
            active_blockers=backend_blockers,
            evidence_summary=evidence,
            reason=_decision_reason_from_active(backend_blockers),
        )

    planner_terminal_failure = _planner_terminal_failure_reason(state, repo)
    if planner_terminal_failure:
        return _decision(
            "terminal_failed",
            planner_terminal_failure,
            iteration=iteration,
            active_blockers=active_blockers,
            evidence_summary=evidence,
            reason=f"Planner transaction failed before trusted outputs were committed: {planner_terminal_failure}.",
        )

    if _stopped_for_non_backend_failure(state):
        return _decision(
            "terminal_failed",
            str(state.get("stop_reason", "") or "WORKFLOW_STOPPED"),
            iteration=iteration,
            active_blockers=active_blockers,
            evidence_summary=evidence,
            reason=str(state.get("stop_reason", "") or "Workflow stopped before completion."),
        )

    if _stopped_for_approval(state):
        return _decision(
            "request_approval",
            "APPROVAL_REQUIRED_OR_REJECTED",
            iteration=iteration,
            active_blockers=active_blockers,
            evidence_summary=evidence,
            reason=str(state.get("stop_reason", "") or "Human approval is required before Engineer Stage."),
            required_inputs=["human_approval"],
        )

    if source_blockers:
        return _decision(
            "request_source",
            _first_reason_code(source_blockers, "OFFICIAL_SOURCE_NOT_AVAILABLE"),
            iteration=iteration,
            active_blockers=source_blockers,
            evidence_summary=evidence,
            reason=_decision_reason_from_active(source_blockers),
            required_inputs=["official_source_url_or_local_source_path"],
        )

    if source_inspection_blockers:
        return _decision(
            "terminal_failed",
            _first_reason_code(source_inspection_blockers, "SOURCE_INSPECTION_FAILED"),
            iteration=iteration,
            active_blockers=source_inspection_blockers,
            evidence_summary=evidence,
            reason=_decision_reason_from_active(source_inspection_blockers),
        )

    if planner_readiness_blockers:
        return _decision(
            "terminal_failed",
            _first_reason_code(planner_readiness_blockers, "PLANNER_NOT_READY"),
            iteration=iteration,
            active_blockers=planner_readiness_blockers,
            evidence_summary=evidence,
            reason=_decision_reason_from_active(planner_readiness_blockers),
        )

    if invalid_plan_blockers:
        return _decision(
            "terminal_failed",
            _first_reason_code(invalid_plan_blockers, "INVALID_PLANNER_OUTPUT"),
            iteration=iteration,
            active_blockers=invalid_plan_blockers,
            evidence_summary=evidence,
            reason=_decision_reason_from_active(invalid_plan_blockers),
        )

    if network_blockers:
        return _decision(
            "request_network_authorization",
            _first_reason_code(network_blockers, "NETWORK_AUTHORIZATION_REQUIRED"),
            iteration=iteration,
            active_blockers=network_blockers,
            evidence_summary=evidence,
            reason=_decision_reason_from_active(network_blockers),
            required_inputs=["network_authorization"],
        )

    # === 目标检查已移除 ===
    # 不再根据 evidence level 判断是否停止
    # 目标是否达到仅用于展示，不用于自动停止
    # 默认情况下，只要还有迭代次数并且 auto_iterate=True，允许继续完善

    if dataset_blockers:
        typed = "request_dataset"
        reason_code = _first_reason_code(dataset_blockers, "OFFICIAL_DATASET_NOT_AVAILABLE")
        required = _required_inputs(dataset_blockers) or ["official_dataset_or_subset", "query_files", "ground_truth"]
        # 简化：不再根据 evidence level 判断是否 evidence_cap
        return _decision(
            typed,
            reason_code,
            iteration=iteration,
            active_blockers=dataset_blockers,
            evidence_summary=evidence,
            reason=_decision_reason_from_active(dataset_blockers),
            required_inputs=required,
        )

    if conflict_blockers:
        return _decision(
            "terminal_failed",
            "MANAGER_CLASSIFICATION_CONFLICT",
            iteration=iteration,
            active_blockers=conflict_blockers,
            evidence_summary=evidence,
            reason=_decision_reason_from_active(conflict_blockers),
        )

    converged = _converged_blocker_decision(repeated_blockers, iteration, evidence)
    if converged:
        return converged

    if not _post_review_phase(state):
        # Planner/Engineer 阶段，继续下一阶段
        return _decision(
            "continue_iteration",
            "READY_FOR_NEXT_STAGE",
            terminal=False,
            iteration=iteration,
            active_blockers=active_blockers,
            evidence_summary=evidence,
            reason="No terminal workflow decision before the next stage.",
        )

    # === Reviewer 已完成 ===
    # 根据 auto_iterate 和迭代次数决定下一步

    if iteration >= max_iterations:
        # 达到最大迭代次数，正常结束
        # 使用 "final" 而不是 "stop_evidence_cap"
        return _decision(
            "final",
            "MAX_ITERATIONS_REACHED",
            iteration=iteration,
            active_blockers=active_blockers,
            evidence_summary=evidence,
            reason=f"Reached max_iterations ({max_iterations}); finalizing with current evidence level.",
        )

    if not state.get("auto_iterate", False):
        # auto_iterate=False，正常结束
        # 使用 "final" 而不是 "stop_evidence_cap"
        return _decision(
            "final",
            "AUTO_ITERATE_DISABLED",
            iteration=iteration,
            active_blockers=active_blockers,
            evidence_summary=evidence,
            reason="Auto iteration is disabled; finalizing with current evidence level.",
        )

    # 继续下一轮迭代
    return _decision(
        "continue_iteration",
        "READY_FOR_NEXT_ITERATION",
        terminal=False,
        iteration=iteration,
        active_blockers=active_blockers,
        evidence_summary=evidence,
        reason=f"Iteration {iteration} completed; continuing to iteration {iteration + 1}.",
    )


def normalize_blocker(blocker: dict[str, Any]) -> dict[str, Any]:
    raw_id = str(blocker.get("blocker_id") or blocker.get("id") or blocker.get("type") or "").strip()
    blocker_type = _normalize_blocker_type(raw_id, blocker)
    reason_code = str(blocker.get("reason_code") or BLOCKER_REASON_CODES.get(blocker_type, "UNKNOWN_BLOCKER")).strip()
    message = str(blocker.get("last_message") or blocker.get("message") or blocker.get("reason") or reason_code).strip()
    source = str(blocker.get("source") or blocker.get("evidence_source") or "workflow_decision").strip()
    requires_user_input = bool(
        blocker.get("requires_user_input")
        or blocker.get("type") == "user_input_required"
        or blocker_type in {"missing_paper", "missing_paper_bundle", "invalid_paper_output", "missing_source", "empty_repo", "missing_dataset", "missing_input_contract", "network_authorization"}
    )
    retryable = bool(blocker.get("retryable") or blocker.get("type") == "retryable_backend" or blocker_type in {"planner_backend_failure", "engineer_backend_failure", "fixable_engineering_failure", "invalid_planner_output", "planner_not_ready", "engineer_not_ready"})
    normalized_id = _normalized_blocker_id(blocker_type, reason_code, raw_id)
    return {
        "blocker_id": normalized_id,
        "type": blocker_type,
        "reason_code": reason_code,
        "retryable": retryable,
        "requires_user_input": requires_user_input,
        "source": source,
        "last_message": message,
        "required_inputs": list(blocker.get("required_inputs", []) or []),
    }


def is_terminal_decision(decision: dict[str, Any] | None) -> bool:
    if not isinstance(decision, dict):
        return False
    return bool(decision.get("terminal")) or str(decision.get("typed_decision", "")) in TERMINAL_DECISIONS


def _decision(
    typed_decision: str,
    reason_code: str,
    *,
    terminal: bool | None = None,
    requires_user_input: bool | None = None,
    retryable: bool = False,
    iteration: int,
    active_blockers: list[dict[str, Any]],
    evidence_summary: dict[str, Any],
    reason: str = "",
    required_inputs: list[str] | None = None,
) -> dict[str, Any]:
    terminal_value = typed_decision in TERMINAL_DECISIONS if terminal is None else bool(terminal)
    requires_input = typed_decision in USER_INPUT_DECISIONS if requires_user_input is None else bool(requires_user_input)
    blockers = [normalize_blocker(blocker) | _ledger_fields(blocker) for blocker in active_blockers]
    return {
        "schema_version": 1,
        "typed_decision": typed_decision,
        "reason_code": reason_code,
        "terminal": terminal_value,
        "requires_user_input": requires_input,
        "retryable": bool(retryable),
        "source": DECISION_SOURCE,
        "iteration": int(iteration),
        "active_blockers": blockers,
        "active_blocker_ids": [str(blocker.get("blocker_id", "")) for blocker in blockers if str(blocker.get("blocker_id", "")).strip()],
        "required_inputs": list(required_inputs or []),
        "evidence_summary": evidence_summary,
        "reason": reason or reason_code,
    }


def _ledger_fields(blocker: dict[str, Any]) -> dict[str, Any]:
    return {
        key: blocker[key]
        for key in ("first_seen_iteration", "last_seen_iteration", "count")
        if key in blocker
    }


def _evidence_summary(repo: Path, state: dict[str, Any]) -> dict[str, Any]:
    """读取 evidence 状态，不进行计算。

    非权威辅助摘要：只读取 state 中的等级。

    重要：此函数返回的等级只用于展示和调试，
    不用于路由决策。
    路由决策应基于 current_reproduction_level。

    如果 Reviewer 未执行，等级为 UNASSESSED。
    不调用 infer_evidence_level() 或 evaluate_l0_l4()。
    """
    final_decision = build_final_decision(state, write=False, allow_state_compat=True)
    accepted_level = str(final_decision.get("accepted_level", "") or "UNASSESSED")
    observed_level = str(final_decision.get("observed_level", "") or "UNASSESSED")
    target_level = normalize_level(final_decision.get("target_level", state.get("target_reproduction_level", "L4_reduced_paper_aligned")), "L4_reduced_paper_aligned")

    # 检查 Reviewer 是否已执行
    reviewer_executed = bool(
        state.get("reviewer_executed")
        or state.get("reviewer_verdict")
        or state.get("structured_review_feedback")
        or state.get("latest_review_feedback_path")
        or state.get("review_feedback_path")
    )

    # 如果 Reviewer 未执行或正式判定无效，accepted_level 为 UNASSESSED。
    if accepted_level == "UNASSESSED" or not reviewer_executed:
        return {
            "observed_level": observed_level if reviewer_executed else "UNASSESSED",
            "accepted_level": "UNASSESSED",
            "target_level": target_level,
            "status": "UNASSESSED",
            "blocking_reasons": [],
            "cap_reason": "",
            "reviewer_completed": reviewer_executed,
        }

    # Reviewer 已完成，返回其判断的等级
    return {
        "observed_level": observed_level,
        "accepted_level": accepted_level,
        "target_level": target_level,
        "status": "PASS" if state.get("reviewer_verdict", "") in PASS_VERDICTS else "FAIL",
        "blocking_reasons": list(state.get("evidence_blocking_reasons", []) or []),
        "cap_reason": "",
        "reviewer_completed": True,
    }

PASS_VERDICTS = PASS_LIKE_VERDICTS


def _target_reached(evidence: dict[str, Any]) -> bool:
    """检查目标是否达到。

    简化版：只检查 Reviewer 是否完成并判断目标已达到。
    不再用于自动停止决策。
    """
    # 如果 Reviewer 未完成，不能认为目标已达到
    if not evidence.get("reviewer_completed", False):
        return False

    # 检查 Reviewer 判断的等级是否达到目标
    accepted = str(evidence.get("accepted_level", ""))
    target = str(evidence.get("target_level", ""))

    if not accepted or accepted == "UNASSESSED":
        return False

    return level_reached(accepted, target)


def _accepted_level_index(evidence: dict[str, Any]) -> int:
    return LEVEL_INDEX[normalize_level(str(evidence.get("accepted_level", "")))]


def _paper_input_blockers(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Check for paper input blockers.

    Only block if there are NO usable paper inputs at all.
    Missing paper_path alone does NOT block if Markdown artifacts exist.
    """
    paper_path = str(state.get("paper_path", "") or "").strip()
    paper_file_exists = bool(paper_path) and Path(paper_path).exists()

    # Check for Markdown artifacts
    repo_path = state.get("repo_path", "")
    if repo_path:
        markdown_status = paper_markdown_artifacts_available(repo_path)
        has_markdown_artifacts = markdown_status["usable"]
    else:
        has_markdown_artifacts = False

    # Only block if we have NO paper input at all
    if not paper_file_exists and not has_markdown_artifacts:
        if not paper_path:
            return [
                {
                    "id": "missing_paper",
                    "type": "missing_paper",
                    "reason_code": "MISSING_PAPER",
                    "message": "No paper_path provided and no paper artifacts available; Planner cannot create reproduction tasks.",
                    "required_inputs": ["readable_paper_pdf_or_text"],
                    "source": "paper",
                }
            ]
        return [
            {
                "id": "missing_paper",
                "type": "missing_paper",
                "reason_code": "MISSING_PAPER",
                "message": f"paper_path does not exist and no paper artifacts available: {paper_path}",
                "required_inputs": ["readable_paper_pdf_or_text"],
                "source": "paper",
            }
        ]

    # We have usable paper inputs - no blocker
    # Check extraction status for warning only
    status = str(state.get("paper_extraction_status", "") or "").strip().lower()
    if status in {"paper file missing", "extraction failed", "unsupported paper file type"}:
        # This is a warning, not a blocker - Markdown artifacts may still exist
        pass

    return []


def _manager_decision_blockers(repo: Path) -> list[dict[str, Any]]:
    data = _read_json(report_path(repo, "manager_decision"))
    raw_blockers = data.get("blockers", []) if isinstance(data, dict) else []
    if isinstance(raw_blockers, list):
        return [item for item in raw_blockers if isinstance(item, dict)]
    return []


def _readiness_blockers(state: dict[str, Any], repo: Path) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for key in ("paper_readiness", "planner_readiness", "engineer_readiness"):
        readiness = state.get(key)
        if not isinstance(readiness, dict) or readiness.get("ready", True):
            continue
        raw = readiness.get("blockers", [])
        if isinstance(raw, list):
            blockers.extend(item for item in raw if isinstance(item, dict))
        elif isinstance(raw, dict):
            blockers.append(raw)
        if not raw:
            blockers.append(
                {
                    "id": f"{key}:{readiness.get('reason_code', 'NOT_READY')}",
                    "type": _readiness_blocker_type(key, str(readiness.get("reason_code", "") or "")),
                    "reason_code": str(readiness.get("reason_code", "") or "NOT_READY"),
                    "message": str(readiness.get("summary", "") or f"{key} is not ready."),
                    "source": "readiness_gate",
                    "required_inputs": readiness.get("required_inputs", []),
                }
            )
    return blockers


def _source_artifact_blockers(state: dict[str, Any], repo: Path) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    acquisition = state.get("source_acquisition")
    if not isinstance(acquisition, dict) or not acquisition:
        acquisition = _read_json(report_path(repo, "source_acquisition"))
    if isinstance(acquisition, dict) and acquisition:
        raw = acquisition.get("blockers", [])
        if isinstance(raw, list):
            blockers.extend(item for item in raw if isinstance(item, dict))
    elif _paper_ready_enough(state, repo) and not _legacy_source_available(repo):
        blockers.append(
            {
                "id": "missing_source:source_status_unknown",
                "type": "missing_source",
                "reason_code": "OFFICIAL_SOURCE_NOT_FOUND",
                "message": "Source status is unknown before Planner; SourceAcquisition did not produce an available source artifact.",
                "source": "source_acquisition",
                "required_inputs": ["official_source_url_or_local_source_path"],
            }
        )
    inspection = state.get("source_inspection")
    if not isinstance(inspection, dict) or not inspection:
        inspection = _read_json(report_path(repo, "source_inspection"))
    if isinstance(inspection, dict) and inspection:
        raw = inspection.get("blockers", [])
        if isinstance(raw, list):
            blockers.extend(item for item in raw if isinstance(item, dict))
    return blockers


def _readiness_blocker_type(key: str, reason_code: str) -> str:
    upper = reason_code.upper()
    if "PAPER" in upper:
        return "missing_paper_bundle" if "BUNDLE" in upper else "invalid_paper_output"
    if "SOURCE" in upper:
        return "missing_source"
    if "PLACEHOLDER" in upper:
        return "placeholder_task"
    if "PLANNER" in upper:
        return "invalid_planner_output" if "OUTPUT" in upper else "planner_not_ready"
    if "ENGINEER" in upper:
        return "engineer_not_ready"
    return key.replace("_readiness", "_not_ready")


def _paper_ready_enough(state: dict[str, Any], repo: Path) -> bool:
    readiness = state.get("paper_readiness")
    if isinstance(readiness, dict) and readiness:
        return bool(readiness.get("ready"))
    if _paper_input_blockers(state):
        return False
    return paper_bundle_status(repo).get("status") == "valid"


def _legacy_source_available(repo: Path) -> bool:
    if _source_rows_successful(_result_rows(repo, "source_verification.csv")):
        return True
    return not _repo_is_empty_scaffold(repo)


def _engineer_backend_blockers(state: dict[str, Any]) -> list[dict[str, Any]]:
    category = str(state.get("engineer_executor_failure_category", "") or "").strip()
    if not category and not state.get("engineer_executor_unavailable"):
        return []
    reason = category or "ENGINEER_BACKEND_UNAVAILABLE"
    if not _looks_like_backend_reason(reason) and not state.get("engineer_executor_unavailable"):
        return []
    return [
        {
            "id": "engineer_backend_failure",
            "type": "engineer_backend_failure",
            "reason_code": reason,
            "message": f"Engineer backend failed before trustworthy reproduction evidence could be produced: {reason}.",
            "source": "engineer",
            "retryable": True,
        }
    ]


def _planner_backend_failure(state: dict[str, Any], repo: Path) -> dict[str, Any]:
    transaction = dict(state.get("planner_transaction", {}) or {})
    if not transaction:
        transaction = _read_json(artifact_dir(repo) / "logs" / "planner_transaction.json")
    if not transaction:
        return {}
    failed = transaction.get("validation_status") == "FAIL" or transaction.get("committed") is False
    if not failed:
        return {}
    diagnostic = dict(transaction.get("diagnostic", {}) or {})
    reason = str(
        transaction.get("failure_category")
        or transaction.get("execution_status")
        or diagnostic.get("failure_category")
        or ""
    ).strip()
    if not _looks_like_backend_reason(reason):
        return {}
    return {
        "id": "planner_backend_failure",
        "type": "planner_backend_failure",
        "reason_code": reason or "PLANNER_BACKEND_FAILURE",
        "message": f"Planner backend failed before committing trusted outputs: {reason or 'PLANNER_BACKEND_FAILURE'}.",
        "source": "planner",
        "retryable": True,
    }


def _looks_like_backend_reason(reason: str) -> bool:
    upper = str(reason or "").upper()
    return any(marker in upper for marker in ("BACKEND", "PROVIDER", "OPENCLAW", "TOOL_CALL_PARSE", "WSL", "GATEWAY"))


def _planner_terminal_failure_reason(state: dict[str, Any], repo: Path) -> str:
    transaction = dict(state.get("planner_transaction", {}) or {})
    if not transaction:
        transaction = _read_json(artifact_dir(repo) / "logs" / "planner_transaction.json")
    if not transaction:
        return ""
    diagnostic = dict(transaction.get("diagnostic", {}) or {})
    failed = bool(
        transaction.get("validation_status") == "FAIL"
        or transaction.get("committed") is False
        or diagnostic.get("planner_validation_passed") is False
        or diagnostic.get("planner_committed") is False
    )
    if not failed:
        return ""
    for value in (
        transaction.get("failure_category"),
        transaction.get("execution_status"),
        diagnostic.get("failure_category"),
        state.get("stop_reason"),
    ):
        reason = str(value or "").strip()
        if reason and not _looks_like_backend_reason(reason):
            return reason
    return ""


def _stopped_for_approval(state: dict[str, Any]) -> bool:
    if not state.get("stopped"):
        return False
    if str(state.get("loop_status", "") or "") == "planner_failed":
        return False
    reason = str(state.get("stop_reason", "") or "").lower()
    return reason in {"", "human_approval_rejected", "approval_required"} or (state.get("approved") is False and not reason)


def _stopped_for_non_backend_failure(state: dict[str, Any]) -> bool:
    if not state.get("stopped"):
        return False
    reason = str(state.get("stop_reason", "") or "")
    return bool(reason and not _looks_like_backend_reason(reason) and reason != "human_approval_rejected")


def _post_review_phase(state: dict[str, Any]) -> bool:
    return bool(
        state.get("reviewer_executed")
        or state.get("reviewer_verdict")
        or state.get("structured_review_feedback")
        or state.get("latest_review_feedback_path")
        or state.get("review_feedback_path")
        or state.get("manager_executed")
        or state.get("manager_status")
    )


def _normalize_blocker_type(raw_id: str, blocker: dict[str, Any]) -> str:
    explicit = str(blocker.get("type", "") or "").strip()
    if explicit in BLOCKER_REASON_CODES:
        return explicit
    if explicit == "retryable_backend":
        return "planner_backend_failure"
    if explicit == "user_input_required":
        specific = _specific_user_input_blocker_type(raw_id, blocker)
        return specific or explicit
    value = " ".join(str(item or "") for item in (raw_id, explicit, blocker.get("reason_code"), blocker.get("message"))).lower()
    if "missing_paper_structured_bundle" in value or "paper bundle" in value:
        return "missing_paper_bundle"
    if "invalid_paper" in value or "paper output" in value:
        return "invalid_paper_output"
    if "missing_paper" in value or "no paper" in value:
        return "missing_paper"
    if "empty_repository" in value or "empty repo" in value:
        return "empty_repo"
    if "official source" in value or "missing_source" in value or "source unavailable" in value:
        return "missing_source"
    if (
        "needs_network_authorization" in value
        or "network authorization" in value
        or "network not authorized" in value
        or "external git clone required" in value
        or ("git clone" in value and "network" in value)
    ):
        return "network_authorization"
    if "source_inspection" in value or "source inspection" in value:
        return "source_inspection_failed"
    if "planner_not_ready" in value or "planner not ready" in value:
        return "planner_not_ready"
    if "invalid_planner_output" in value or "invalid planner output" in value:
        return "invalid_planner_output"
    if "placeholder_task" in value or "placeholder task" in value or "github.com/x" in value:
        return "placeholder_task"
    if "engineer_not_ready" in value or "engineer not ready" in value:
        return "engineer_not_ready"
    if (
        "missing_input_contract" in value
        or "official_input_contract_not_available" in value
        or (
            ("input_contract" in value or "ground truth" in value or "query" in value)
            and _looks_like_required_input_blocker(value)
        )
    ):
        return "missing_input_contract"
    if "dataset" in value and _looks_like_required_input_blocker(value):
        return "missing_dataset"
    if "planner" in value and _looks_like_backend_reason(value):
        return "planner_backend_failure"
    if "engineer" in value and _looks_like_backend_reason(value):
        return "engineer_backend_failure"
    if "manager_classification_conflict" in value:
        return "manager_classification_conflict"
    if "evidence cap" in value:
        return "evidence_cap"
    return explicit or "unknown_terminal_failure"


def _specific_user_input_blocker_type(raw_id: str, blocker: dict[str, Any]) -> str:
    """Map generic user-input blockers only when evidence is specific enough."""
    raw_key = raw_id.lower()
    evidence_source = str(blocker.get("evidence_source") or blocker.get("source") or "").lower()
    reason_code = str(blocker.get("reason_code") or "").upper()
    message = str(blocker.get("message") or blocker.get("last_message") or blocker.get("reason") or "")
    value = " ".join(item for item in (raw_key, reason_code.lower(), message.lower(), evidence_source) if item)

    if _network_authorization_marker_present(value) or "algorithm_dependency_network_authorization" in raw_key:
        return "network_authorization"
    if raw_key == "empty_repository_scaffold" or "empty_repository" in value or "empty repo" in value:
        return "empty_repo"
    if raw_key == "missing_official_source" or "missing_source" in value or "official source" in value:
        return "missing_source"
    if raw_key == "missing_official_input_contract":
        if (
            reason_code in {"OFFICIAL_INPUT_CONTRACT_NOT_AVAILABLE", "OFFICIAL_DATASET_NOT_AVAILABLE"}
            or "input_contract_verification.csv" in evidence_source
        ):
            return "missing_input_contract"
        return ""
    if raw_key == "missing_official_dataset_or_ground_truth":
        return "missing_input_contract"
    return ""


def _looks_like_required_input_blocker(value: str) -> bool:
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in (
            "missing",
            "not available",
            "unavailable",
            "needs_input",
            "needs input",
            "required",
            "requires",
            "must provide",
            "must supply",
            "user input",
            "local path",
            "not authorized",
            "authorization",
        )
    )


def _normalized_blocker_id(blocker_type: str, reason_code: str, raw_id: str) -> str:
    if blocker_type in {"planner_backend_failure", "engineer_backend_failure"}:
        return f"{blocker_type}:{reason_code}"
    if "algorithm_dependency_network_authorization" in raw_id:
        return f"{blocker_type}:algorithm_dependency_network_authorization"
    if raw_id and raw_id in {
        "missing_official_source",
        "empty_repository_scaffold",
        "missing_official_input_contract",
        "missing_official_dataset_or_ground_truth",
        "algorithm_dependency_network_authorization",
    }:
        return f"{blocker_type}:{raw_id}"
    return f"{blocker_type}:{reason_code}"


def _dedupe_normalized_blockers(blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for blocker in blockers:
        if not isinstance(blocker, dict):
            continue
        normalized = normalize_blocker(blocker)
        blocker_id = str(normalized.get("blocker_id", "") or "")
        if not blocker_id:
            continue
        if blocker_id not in merged:
            merged[blocker_id] = normalized
            continue
        existing = merged[blocker_id]
        existing["last_message"] = _join_unique(existing.get("last_message", ""), normalized.get("last_message", ""))
        existing["required_inputs"] = sorted(set(existing.get("required_inputs", []) or []) | set(normalized.get("required_inputs", []) or []))
        existing["requires_user_input"] = bool(existing.get("requires_user_input") or normalized.get("requires_user_input"))
        existing["retryable"] = bool(existing.get("retryable") or normalized.get("retryable"))
    return list(merged.values())


def _blockers_of_type(blockers: list[dict[str, Any]], types: set[str]) -> list[dict[str, Any]]:
    return [blocker for blocker in blockers if str(blocker.get("type", "")) in types]


def _terminal_dataset_blockers(
    blockers: list[dict[str, Any]],
    state: dict[str, Any],
    iteration: int,
    max_iterations: int,
) -> list[dict[str, Any]]:
    if not blockers:
        return []
    if _official_input_authorized(state) and state.get("auto_iterate", False) and iteration < max_iterations:
        return []
    return blockers


def _merge_active_blockers(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for blocker in [*left, *right]:
        normalized = normalize_blocker(blocker)
        blocker_id = str(normalized.get("blocker_id", "") or "")
        if not blocker_id:
            continue
        existing = merged.get(blocker_id, {})
        merged[blocker_id] = {
            **existing,
            **normalized,
            **_ledger_fields(blocker),
            "count": max(int(existing.get("count", 0) or 0), int(blocker.get("count", 1) or 1)),
        }
    return list(merged.values())


def _first_reason_code(blockers: list[dict[str, Any]], fallback: str) -> str:
    for blocker in blockers:
        reason = str(blocker.get("reason_code", "") or "").strip()
        if reason:
            return reason
    return fallback


def _required_inputs(blockers: list[dict[str, Any]]) -> list[str]:
    required = []
    for blocker in blockers:
        required.extend(str(item) for item in blocker.get("required_inputs", []) or [] if str(item).strip())
    return sorted(dict.fromkeys(required))


def _decision_reason_from_active(blockers: list[dict[str, Any]]) -> str:
    messages = [str(item.get("last_message") or item.get("message") or "").strip() for item in blockers]
    messages = [item for item in messages if item]
    return "; ".join(messages[:3]) if messages else "Workflow blocker requires explicit handling."


def _converged_blocker_decision(blockers: list[dict[str, Any]], iteration: int, evidence: dict[str, Any]) -> dict[str, Any]:
    """Determine decision for converged blockers.

    Schema/format warnings should NOT trigger terminal_failed.
    Only actual evidence failures should trigger terminal_failed.
    """
    if not blockers:
        return {}

    # Filter out schema/format warnings - these are not fatal
    actual_blockers = []
    schema_blockers = []

    for blocker in blockers:
        blocker_type = str(blocker.get("type", ""))
        reason_code = str(blocker.get("reason_code", ""))
        message = str(blocker.get("last_message", "") or blocker.get("message", "")).lower()

        # Check if this is a schema/format issue
        is_schema_issue = any(marker in message for marker in [
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
        ]) or any(marker in reason_code.lower() for marker in [
            "schema",
            "format",
            "csv",
        ])

        if is_schema_issue:
            schema_blockers.append(blocker)
        else:
            actual_blockers.append(blocker)


    # If we have accepted evidence and only schema warnings, use stop_evidence_cap
    accepted_level = str(evidence.get("accepted_level", ""))
    has_accepted_evidence = accepted_level and accepted_level != "L0_project_health"

    if has_accepted_evidence and not actual_blockers:
        # Only schema issues - don't fail, just cap at current evidence level
        return _decision(
            "stop_evidence_cap",
            "EVIDENCE_CAP_WITH_SCHEMA_WARNINGS",
            iteration=iteration,
            active_blockers=schema_blockers,
            evidence_summary=evidence,
            reason="Reached evidence level with schema/format warnings that do not invalidate the evidence.",
        )

    # If we have both accepted evidence and actual blockers, still cap rather than fail
    if has_accepted_evidence:
        source = _blockers_of_type(actual_blockers, {"missing_source", "empty_repo"})
        if source:
            return _decision(
                "request_source",
                _first_reason_code(source, "BLOCKER_CONVERGENCE_LIMIT_REACHED"),
                iteration=iteration,
                active_blockers=source,
                evidence_summary=evidence,
                reason=_decision_reason_from_active(source),
                required_inputs=["official_source_url_or_local_source_path"],
            )
        network = _blockers_of_type(actual_blockers, {"network_authorization"})
        if network:
            return _decision(
                "request_network_authorization",
                _first_reason_code(network, "NETWORK_AUTHORIZATION_REQUIRED"),
                iteration=iteration,
                active_blockers=network,
                evidence_summary=evidence,
                reason=_decision_reason_from_active(network),
                required_inputs=["network_authorization"],
            )
        dataset = _blockers_of_type(actual_blockers, {"missing_dataset", "missing_input_contract"})
        if dataset:
            return _decision(
                "request_dataset",
                _first_reason_code(dataset, "BLOCKER_CONVERGENCE_LIMIT_REACHED"),
                iteration=iteration,
                active_blockers=dataset,
                evidence_summary=evidence,
                reason=_decision_reason_from_active(dataset),
                required_inputs=_required_inputs(dataset) or ["official_dataset_or_subset", "query_files", "ground_truth"],
            )
        # Other blockers with evidence - cap, don't fail
        return _decision(
            "stop_evidence_cap",
            "EVIDENCE_CAP_REACHED",
            iteration=iteration,
            active_blockers=actual_blockers + schema_blockers,
            evidence_summary=evidence,
            reason="Reached evidence level despite repeated issues; stopping at current evidence cap.",
        )

    # No accepted evidence - this is a real failure
    source = _blockers_of_type(actual_blockers, {"missing_source", "empty_repo"})
    if source:
        return _decision(
            "request_source",
            _first_reason_code(source, "BLOCKER_CONVERGENCE_LIMIT_REACHED"),
            iteration=iteration,
            active_blockers=source,
            evidence_summary=evidence,
            reason=_decision_reason_from_active(source),
            required_inputs=["official_source_url_or_local_source_path"],
        )
    network = _blockers_of_type(actual_blockers, {"network_authorization"})
    if network:
        return _decision(
            "request_network_authorization",
            _first_reason_code(network, "NETWORK_AUTHORIZATION_REQUIRED"),
            iteration=iteration,
            active_blockers=network,
            evidence_summary=evidence,
            reason=_decision_reason_from_active(network),
            required_inputs=["network_authorization"],
        )
    dataset = _blockers_of_type(actual_blockers, {"missing_dataset", "missing_input_contract"})
    if dataset:
        return _decision(
            "request_dataset",
            _first_reason_code(dataset, "BLOCKER_CONVERGENCE_LIMIT_REACHED"),
            iteration=iteration,
            active_blockers=dataset,
            evidence_summary=evidence,
            reason=_decision_reason_from_active(dataset),
            required_inputs=_required_inputs(dataset) or ["official_dataset_or_subset", "query_files", "ground_truth"],
        )
    return _decision(
        "terminal_failed",
        "BLOCKER_CONVERGENCE_LIMIT_REACHED",
        iteration=iteration,
        active_blockers=actual_blockers,
        evidence_summary=evidence,
        reason=_decision_reason_from_active(actual_blockers),
    )


def collect_workflow_blockers(state: dict[str, Any], repo_path: str | Path | None = None) -> list[dict[str, Any]]:
    repo = Path(repo_path or state.get("repo_path", "") or ".")
    blockers: list[dict[str, Any]] = []
    blockers.extend(_paper_blockers(repo))
    blockers.extend(_readiness_blockers(state, repo))
    blockers.extend(_source_artifact_blockers(state, repo))
    blockers.extend(_source_blockers(repo))
    blockers.extend(_network_authorization_blockers(state, repo))
    blockers.extend(_input_contract_blockers(repo))
    blockers.extend(_planner_backend_blockers(state, repo))
    blockers.extend(_feedback_blockers(state))
    return _with_ledger(state, _dedupe_blockers(blockers))


def build_workflow_decision(
    state: dict[str, Any],
    *,
    verdict: str = "",
    should_iterate: bool | None = None,
    blockers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    blocker_list = blockers if blockers is not None else collect_workflow_blockers(state)
    blocker_ids = {str(item.get("id", "")) for item in blocker_list}
    terminal_user_blockers = [
        item
        for item in blocker_list
        if item.get("type") == "user_input_required" and not item.get("auto_resolvable", False)
        and _terminal_user_blocker_active(item, state)
    ]
    repeated_terminal = [
        item
        for item in terminal_user_blockers
        if int(item.get("consecutive_count", 1) or 1) >= 2
    ]
    retryable = [item for item in blocker_list if item.get("type") == "retryable_backend"]
    normalized_verdict = str(verdict or state.get("reviewer_verdict", "") or "").upper()
    proposed_iterate = bool(should_iterate) if should_iterate is not None else False

    if repeated_terminal or terminal_user_blockers:
        required = sorted(
            {
                requirement
                for blocker in terminal_user_blockers
                for requirement in blocker.get("required_inputs", []) or []
                if str(requirement).strip()
            }
        )
        return {
            "schema_version": 1,
            "kind": "request_user_input",
            "should_iterate": False,
            "reason": _decision_reason(terminal_user_blockers),
            "required_inputs": required,
            "blockers": blocker_list,
            "blocker_ids": sorted(blocker_ids),
            "active_blocker_ids": sorted(str(item.get("id", "")) for item in terminal_user_blockers if str(item.get("id", "")).strip()),
            "verdict": normalized_verdict,
        }

    if retryable:
        return {
            "schema_version": 1,
            "kind": "retry_backend",
            "should_iterate": proposed_iterate,
            "reason": _decision_reason(retryable),
            "required_inputs": [],
            "blockers": blocker_list,
            "blocker_ids": sorted(blocker_ids),
            "active_blocker_ids": sorted(str(item.get("id", "")) for item in retryable if str(item.get("id", "")).strip()),
            "verdict": normalized_verdict,
        }

    return {
        "schema_version": 1,
        "kind": "continue" if proposed_iterate else "stop",
        "should_iterate": proposed_iterate,
        "reason": "No non-auto-resolvable workflow blocker is active.",
        "required_inputs": [],
        "blockers": blocker_list,
        "blocker_ids": sorted(blocker_ids),
        "active_blocker_ids": [],
        "verdict": normalized_verdict,
    }


def update_state_with_workflow_decision(
    state: dict[str, Any],
    *,
    verdict: str = "",
    should_iterate: bool | None = None,
) -> dict[str, Any]:
    blockers = collect_workflow_blockers(state)
    decision = build_workflow_decision(state, verdict=verdict, should_iterate=should_iterate, blockers=blockers)
    metadata = dict(state.get("metadata", {}) or {})
    metadata["workflow_blocker_ledger"] = {
        str(item.get("id", "")): {
            "first_seen_iteration": item.get("first_seen_iteration", int(state.get("iteration", 1) or 1)),
            "last_seen_iteration": item.get("last_seen_iteration", int(state.get("iteration", 1) or 1)),
            "consecutive_count": item.get("consecutive_count", 1),
            "type": item.get("type", ""),
            "auto_resolvable": bool(item.get("auto_resolvable", False)),
        }
        for item in blockers
        if str(item.get("id", "")).strip()
    }
    return {**state, "workflow_blockers": blockers, "workflow_decision": decision, "metadata": metadata}


def decision_allows_iteration(feedback: dict[str, Any] | None, state: dict[str, Any] | None = None) -> bool:
    feedback = feedback or {}
    decision = feedback.get("workflow_decision")
    if not isinstance(decision, dict) and state:
        decision = state.get("workflow_decision")
    if not isinstance(decision, dict):
        return True
    if decision.get("should_iterate") is False:
        return False
    if str(decision.get("kind", "")) in {"request_user_input", "terminal", "stop_at_evidence_cap"}:
        return False
    return True


def paper_bundle_status(repo: str | Path) -> dict[str, Any]:
    repo_path = Path(repo)
    missing = [key for key in PAPER_STRUCTURED_KEYS if not _artifact_has_content(report_path(repo_path, key))]
    fallback_available = any(_artifact_has_content(report_path(repo_path, key)) for key in PAPER_TEXT_FALLBACK_KEYS)
    if not missing:
        status = "valid"
    elif fallback_available:
        status = "partial_with_text_fallback"
    else:
        status = "missing"
    return {
        "status": status,
        "missing_required": missing,
        "text_fallback_available": fallback_available,
    }


def paper_markdown_artifacts_available(repo: str | Path) -> dict[str, Any]:
    """Check if core Paper Markdown artifacts exist for Planner to use.

    This is a softer check than paper_bundle_status - it only requires
    some usable Markdown artifacts to exist, not the full structured bundle.
    PAPER_OUTPUT.json is NOT required.
    """
    repo_path = Path(repo)
    available = [key for key in PAPER_MARKDOWN_ARTIFACT_KEYS if _artifact_has_content(report_path(repo_path, key))]
    has_paper_output = _artifact_has_content(report_path(repo_path, "paper_output"))

    # Need at least 2 core artifacts to have usable paper context
    usable = len(available) >= 2

    return {
        "usable": usable,
        "available_artifacts": available,
        "artifact_count": len(available),
        "has_paper_output": has_paper_output,
        "missing_paper_output": not has_paper_output,
    }


def _paper_blockers(repo: Path) -> list[dict[str, Any]]:
    """Check for paper blockers.

    Only block if there are NO usable paper artifacts at all.
    Missing PAPER_OUTPUT.json alone does NOT block - Planner can use Markdown artifacts.
    """
    markdown_status = paper_markdown_artifacts_available(repo)

    # Only block if we have NO usable paper artifacts
    if not markdown_status["usable"]:
        # Check if we have any paper input at all
        bundle_status = paper_bundle_status(repo)
        return [
            {
                "id": "missing_paper_structured_bundle",
                "type": "system_action_required",
                "severity": "blocking",
                "auto_resolvable": bool(bundle_status["text_fallback_available"]),
                "message": (
                    "No usable paper artifacts available for Planner."
                ),
                "evidence_source": ".r2a/PAPER_*",
                "required_inputs": [] if bundle_status["text_fallback_available"] else ["readable_paper_pdf_or_text"],
                "missing": bundle_status["missing_required"],
            }
        ]

    # We have usable paper artifacts - no blocker
    # Missing PAPER_OUTPUT.json is just a warning, not a blocker
    return []


def _source_blockers(repo: Path) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    rows = _result_rows(repo, "source_verification.csv")
    source_available = _source_available(repo, rows=rows)
    if _repo_is_empty_scaffold(repo) and not source_available:
        blockers.append(
            {
                "id": "empty_repository_scaffold",
                "type": "user_input_required",
                "severity": "blocking",
                "auto_resolvable": False,
                "message": "Repository contains no apparent source/build artifact outside .r2a/.git scaffold.",
                "evidence_source": str(repo),
                "required_inputs": ["official_source_url_or_local_source_path"],
            }
        )
    if rows and not source_available and _source_rows_explicitly_missing(rows):
        blockers.append(
            {
                "id": "missing_official_source",
                "type": "user_input_required",
                "severity": "blocking",
                "auto_resolvable": False,
                "message": "source_verification.csv does not contain a verified official source/artifact with provenance.",
                "evidence_source": str(artifact_dir(repo) / "results" / "source_verification.csv"),
                "required_inputs": ["official_source_url_or_local_source_path"],
            }
        )
    return blockers


def _input_contract_blockers(repo: Path) -> list[dict[str, Any]]:
    rows = _result_rows(repo, "input_contract_verification.csv")
    if not rows:
        return []
    done_text = _read_text(artifact_dir(repo) / "results" / "ENGINEER_DONE.txt").upper()
    # More lenient ENGINEER_DONE check: accept ENGINEER_DONE marker or success keywords
    if done_text.startswith(("PASS", "DONE", "OK")) or "ENGINEER_DONE" in done_text:
        return []
    text = "\n".join(" ".join(str(value) for value in row.values()) for row in rows).upper()
    statuses = {_first_present(row, ("status", "verdict", "result")).upper() for row in rows}
    statuses.discard("")
    if statuses and statuses <= SUCCESS_STATUSES and not any("NEEDS_INPUT" in item for item in text.splitlines()):
        return []
    if statuses & INPUT_BLOCKING_STATUSES or "NEEDS_INPUT" in text or "GROUND_TRUTH" in text and "MISSING" in text:
        return [
            {
                "id": "missing_official_input_contract",
                "type": "user_input_required",
                "severity": "blocking",
                "auto_resolvable": False,
                "message": "Official reduced input contract is missing dataset/query/ground-truth/metric/command evidence.",
                "evidence_source": str(artifact_dir(repo) / "results" / "input_contract_verification.csv"),
                "required_inputs": ["official_dataset_or_subset", "query_files", "ground_truth", "metric_definition"],
            }
        ]
    return []


def _network_authorization_blockers(state: dict[str, Any], repo: Path) -> list[dict[str, Any]]:
    if _network_authorized(state):
        return []
    evidence_sources: list[str] = []
    for name in NETWORK_AUTHORIZATION_RESULT_FILES:
        path = artifact_dir(repo) / "results" / name
        if _network_authorization_marker_present(_read_text(path)):
            evidence_sources.append(str(path))
    for path in (
        report_path(repo, "review_feedback"),
        report_path(repo, "next_planner_context"),
        report_path(repo, "manager_decision"),
    ):
        if _network_authorization_marker_present(_read_text(path)):
            evidence_sources.append(str(path))
    state_text = json.dumps(
        {
            "structured_review_feedback": state.get("structured_review_feedback"),
            "workflow_decision": state.get("workflow_decision"),
            "decision_status": state.get("decision_status"),
        },
        ensure_ascii=False,
        default=str,
    )
    if _network_authorization_marker_present(state_text):
        evidence_sources.append("state")
    if not evidence_sources:
        return []
    return [_network_authorization_blocker("; ".join(dict.fromkeys(evidence_sources)))]


def _network_authorization_blocker(evidence_source: str, message: str = "") -> dict[str, Any]:
    return {
        "id": "algorithm_dependency_network_authorization",
        "type": "user_input_required",
        "severity": "blocking",
        "auto_resolvable": False,
        "message": message
        or "Algorithm dependency acquisition requires explicit network authorization before external git clone/CMake can proceed.",
        "evidence_source": evidence_source,
        "required_inputs": ["network_authorization"],
    }


def _planner_backend_blockers(state: dict[str, Any], repo: Path) -> list[dict[str, Any]]:
    transaction = dict(state.get("planner_transaction", {}) or {})
    if not transaction:
        transaction = _read_json(artifact_dir(repo) / "logs" / "planner_transaction.json")
    if not transaction:
        return []
    failed = transaction.get("validation_status") == "FAIL" or transaction.get("committed") is False
    if not failed:
        return []
    diagnostic = dict(transaction.get("diagnostic", {}) or {})
    reason = str(
        transaction.get("failure_category")
        or transaction.get("execution_status")
        or diagnostic.get("failure_category")
        or ""
    )
    if reason in {"PLANNER_BACKEND_FAILURE", "BACKEND_TRANSIENT_FAILURE"} or "BACKEND" in reason:
        return [
            {
                "id": "planner_backend_failure",
                "type": "retryable_backend",
                "severity": "blocking",
                "auto_resolvable": True,
                "message": f"Planner backend failed before committing trusted outputs: {reason}.",
                "evidence_source": str(artifact_dir(repo) / "logs" / "planner_transaction.json"),
                "required_inputs": [],
            }
        ]
    return []


def _feedback_blockers(state: dict[str, Any]) -> list[dict[str, Any]]:
    feedback = state.get("structured_review_feedback")
    if not isinstance(feedback, dict):
        return []
    blockers = []
    for item in _feedback_text_items(feedback):
        text = str(item)
        lowered = text.lower()
        if _network_authorization_marker_present(text) and not _network_authorized(state):
            blockers.append(_network_authorization_blocker("REVIEW_FEEDBACK.json", text))
        elif _feedback_requires_source_input(lowered):
            blockers.append(
                {
                    "id": "missing_official_source",
                    "type": "user_input_required",
                    "severity": "blocking",
                    "auto_resolvable": False,
                    "message": text,
                    "evidence_source": "REVIEW_FEEDBACK.json",
                    "required_inputs": ["official_source_url_or_local_source_path"],
                }
            )
        elif _feedback_requires_official_input(lowered, state):
            blockers.append(
                {
                    "id": "missing_official_input_contract",
                    "type": "missing_input_contract",
                    "reason_code": "OFFICIAL_INPUT_CONTRACT_NOT_AVAILABLE",
                    "severity": "blocking",
                    "auto_resolvable": False,
                    "requires_user_input": True,
                    "message": text,
                    "evidence_source": "REVIEW_FEEDBACK.json",
                    "required_inputs": ["official_dataset_or_subset", "query_files", "ground_truth", "metric_definition"],
                }
            )
    return blockers


def _feedback_requires_source_input(lowered: str) -> bool:
    if not any(marker in lowered for marker in ("empty repo", "empty_repository", "no source", "official source")):
        return False
    return any(
        marker in lowered
        for marker in (
            "missing",
            "not available",
            "unavailable",
            "provide",
            "local path",
            "user",
            "manual",
            "authorization",
            "authorize",
            "approval",
        )
    )


def _feedback_requires_official_input(lowered: str, state: dict[str, Any]) -> bool:
    if _official_input_authorized(state):
        return False
    if not any(marker in lowered for marker in ("official input", "input_contract", "input contract", "ground truth", "dataset", "query")):
        return False
    return any(
        marker in lowered
        for marker in (
            "authorization",
            "authorize",
            "approval",
            "permission",
            "not authorized",
            "user input",
            "requires user",
            "needs user",
            "user must",
            "must provide",
            "must supply",
            "local path",
            "manual confirmation",
            "manual confirm",
        )
    )


def _feedback_text_items(feedback: dict[str, Any]) -> list[str]:
    items: list[str] = []
    for key in (
        "active_blockers",
        "required_fixes",
        "engineering_issues",
        "evidence_gaps",
        "remaining_gaps",
        "blocking_issues",
        "forbidden_next_actions",
    ):
        value = feedback.get(key)
        if isinstance(value, list):
            items.extend(str(item) for item in value)
        elif isinstance(value, dict):
            items.append(json.dumps(value, ensure_ascii=False, default=str))
        elif value:
            items.append(str(value))
    return items


def _with_ledger(state: dict[str, Any], blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    iteration = int(state.get("iteration", 1) or 1)
    metadata = dict(state.get("metadata", {}) or {})
    previous = metadata.get("workflow_blocker_ledger", {})
    previous = previous if isinstance(previous, dict) else {}
    output: list[dict[str, Any]] = []
    for blocker in blockers:
        blocker_id = str(blocker.get("id", ""))
        prior = previous.get(blocker_id, {}) if isinstance(previous.get(blocker_id), dict) else {}
        last_seen = int(prior.get("last_seen_iteration", 0) or 0)
        consecutive = int(prior.get("consecutive_count", 0) or 0) + 1 if last_seen == iteration - 1 else 1
        output.append(
            {
                **blocker,
                "first_seen_iteration": int(prior.get("first_seen_iteration", iteration) or iteration),
                "last_seen_iteration": iteration,
                "consecutive_count": consecutive,
            }
        )
    return output


def _terminal_user_blocker_active(blocker: dict[str, Any], state: dict[str, Any]) -> bool:
    blocker_id = str(blocker.get("id", "") or "")
    if blocker_id == "algorithm_dependency_network_authorization":
        return not _network_authorized(state)
    if blocker_id not in USER_INPUT_BLOCKER_IDS:
        return True
    if _official_input_authorized(state) and blocker_id in {
        "missing_official_input_contract",
        "missing_official_dataset_or_ground_truth",
    }:
        return False
    if str(state.get("reviewer_verdict", "") or "").strip():
        return True
    if str(blocker.get("evidence_source", "")) == "REVIEW_FEEDBACK.json":
        return True
    repo = Path(str(state.get("repo_path", "") or "."))
    if blocker_id in {"empty_repository_scaffold", "missing_official_source"}:
        evidence_files = (artifact_dir(repo) / "results" / "source_verification.csv",)
    else:
        evidence_files = (
            artifact_dir(repo) / "results" / "input_contract_verification.csv",
            artifact_dir(repo) / "results" / "ENGINEER_DONE.txt",
        )
    return any(path.exists() for path in evidence_files)


def _network_authorized(state: dict[str, Any]) -> bool:
    authorization = state.get("network_authorization")
    authorized_from_dict = (
        bool(authorization.get("network_authorized"))
        if isinstance(authorization, dict) and authorization.get("network_authorized") is not None
        else False
    )
    return bool(
        authorized_from_dict
        or
        state.get("network_authorized")
        or state.get("allow_network")
        or state.get("user_approved_network")
        or state.get("user_approved_network_authorization")
    )


def _network_authorization_marker_present(text: object) -> bool:
    upper = str(text or "").upper()
    if not upper.strip():
        return False
    if any(marker in upper for marker in NETWORK_AUTHORIZATION_MARKERS):
        return True
    if "NEEDS" in upper and "NETWORK" in upper and "AUTHORIZATION" in upper:
        return True
    return "GIT CLONE" in upper and "NETWORK" in upper and any(
        marker in upper for marker in ("CMAKE", "ALGORITHM", "BINARY", "BINARIES")
    )


def _official_input_authorized(state: dict[str, Any]) -> bool:
    return bool(
        state.get("local_official_input_path")
        or state.get("user_approved_official_download")
        or state.get("user_approved_download")
        or (state.get("allow_official_dataset_download") and int(state.get("download_budget_gb", 0) or 0) > 0)
        or state.get("user_approved_synthetic_demo")
        or state.get("synthetic_demo_approved")
    )


def _repo_is_empty_scaffold(repo: Path) -> bool:
    if not repo.exists():
        return True
    meaningful = []
    ignored_roots = {".git", ".r2a", "results", "__pycache__"}
    source_suffixes = {
        ".py",
        ".cpp",
        ".c",
        ".cc",
        ".h",
        ".hpp",
        ".java",
        ".rs",
        ".go",
        ".js",
        ".ts",
        ".m",
        ".cu",
        ".sh",
        ".bat",
        ".ps1",
    }
    config_names = {
        "CMakeLists.txt",
        "Makefile",
        "pyproject.toml",
        "setup.py",
        "requirements.txt",
        "package.json",
        "Cargo.toml",
        "pom.xml",
    }
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(repo)
        except ValueError:
            continue
        if relative.parts and relative.parts[0] in ignored_roots:
            continue
        if path.name in config_names or path.suffix.lower() in source_suffixes:
            meaningful.append(path)
            if len(meaningful) >= 1:
                return False
    return True


def _source_available(repo: Path, *, rows: list[dict[str, str]] | None = None) -> bool:
    if _source_rows_successful(rows if rows is not None else _result_rows(repo, "source_verification.csv")):
        return True
    if _source_acquisition_available(repo):
        return True
    if _source_inspection_available(repo):
        return True
    if _has_meaningful_source_path(artifact_dir(repo) / "artifacts" / "source"):
        return True
    return not _repo_is_empty_scaffold(repo)


def _source_acquisition_available(repo: Path) -> bool:
    data = _read_json(report_path(repo, "source_acquisition"))
    if str(data.get("source_status", "") or "").strip().upper() not in SUCCESS_STATUSES:
        return False
    local_path = str(data.get("local_path", "") or "").strip()
    return bool(local_path) and _has_meaningful_source_path(Path(local_path))


def _source_inspection_available(repo: Path) -> bool:
    data = _read_json(report_path(repo, "source_inspection"))
    if str(data.get("inspection_status", "") or "").strip().lower() != "complete":
        return False
    repo_root = str(data.get("repo_root", "") or "").strip()
    if repo_root and _has_meaningful_source_path(Path(repo_root)):
        return True
    return bool(data.get("readme_files") or data.get("environment_files") or data.get("entrypoints"))


def _has_meaningful_source_path(path: Path) -> bool:
    return path.exists() and path.is_dir() and not _repo_is_empty_scaffold(path)


def _source_rows_successful(rows: list[dict[str, str]]) -> bool:
    for row in rows:
        status = _first_present(row, SOURCE_STATUS_COLUMNS).upper()
        if status in SUCCESS_STATUSES:
            return True
    return False


def _source_rows_explicitly_missing(rows: list[dict[str, str]]) -> bool:
    if not rows:
        return False
    for row in rows:
        status = _first_present(row, SOURCE_STATUS_COLUMNS).upper()
        text = " ".join(str(value).lower() for value in row.values())
        if status in MISSING_SOURCE_STATUSES:
            return True
        if "no official source" in text or "source unavailable" in text or "official source url was provided" in text:
            return True
    return False


def _result_rows(repo: Path, name: str) -> list[dict[str, str]]:
    for directory in (artifact_dir(repo) / "results", repo / "results"):
        path = directory / name
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                return [{str(k): str(v) for k, v in row.items()} for row in csv.DictReader(handle)]
        except (OSError, csv.Error):
            return []
    return []


def _first_present(row: dict[str, str], columns: tuple[str, ...]) -> str:
    normalized = {_normalize(key): value for key, value in row.items()}
    for column in columns:
        value = row.get(column)
        if value is not None and str(value).strip():
            return str(value).strip()
        value = normalized.get(_normalize(column))
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _artifact_has_content(path: Path) -> bool:
    try:
        return path.exists() and path.stat().st_size > 0 and bool(path.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return False


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    except OSError:
        return ""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _dedupe_blockers(blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for blocker in blockers:
        blocker_id = str(blocker.get("id", "")).strip()
        if not blocker_id:
            continue
        if blocker_id not in merged:
            merged[blocker_id] = dict(blocker)
            continue
        existing = merged[blocker_id]
        existing["message"] = _join_unique(existing.get("message", ""), blocker.get("message", ""))
        existing["required_inputs"] = sorted(
            set(existing.get("required_inputs", []) or []) | set(blocker.get("required_inputs", []) or [])
        )
        existing["auto_resolvable"] = bool(existing.get("auto_resolvable", False) and blocker.get("auto_resolvable", False))
    return list(merged.values())


def _join_unique(left: object, right: object) -> str:
    items = [str(item).strip() for item in (left, right) if str(item).strip()]
    return "; ".join(dict.fromkeys(items))


def _decision_reason(blockers: list[dict[str, Any]]) -> str:
    messages = [str(item.get("message", "")).strip() for item in blockers if str(item.get("message", "")).strip()]
    return "; ".join(messages[:3]) if messages else "Workflow blocker requires explicit handling."


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
