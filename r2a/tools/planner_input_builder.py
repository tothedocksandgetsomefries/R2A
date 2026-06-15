from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from r2a.core.paths import report_path, require_repo_dir
from r2a.core.state import R2AState
from r2a.core.user_hints import user_hints_from_state, write_user_hints_artifact
from r2a.tools.readiness_gate import check_planner_readiness
from r2a.tools.reproduction_levels import current_level, download_budget_gb, target_level
from r2a.tools.source_acquisition import read_source_acquisition
from r2a.tools.source_inspection import read_source_inspection
from r2a.tools.workflow_decision import (
    PAPER_TEXT_FALLBACK_KEYS,
    build_workflow_decision,
    collect_workflow_blockers,
    paper_bundle_status,
)

PAPER_KEYS = (
    "paper_analysis",
    "paper_reproduction_card",
    "paper",
    "paper_evidence",
    "paper_parse_quality",
    "paper_output",
)

REVIEW_KEYS = (
    "planner_output",
    "task",
    "experiment_contract",
    "check",
    "execution",
)

# Reviewer feedback is optional - not required for iteration
REVIEW_FEEDBACK_KEY = "review_feedback"


def build_planner_input(state: R2AState) -> dict[str, Any]:
    repo = require_repo_dir(state["repo_path"])
    user_hints = user_hints_from_state(state)
    write_user_hints_artifact(repo, user_hints)
    iteration = int(state.get("iteration", 1))
    if iteration <= 1 and state.get("need_replan"):
        iteration = 2
    mode = "initial" if iteration == 1 else "iterative_progress"
    paper_bundle = _read_artifacts(repo, PAPER_KEYS, limit=6000)
    paper_status = paper_bundle_status(repo)
    if (
        paper_status["status"] != "valid"
        and paper_status["text_fallback_available"]
        and not _has_any_available_artifact(
            paper_bundle,
            ("paper_analysis", "paper_reproduction_card", "paper", "paper_evidence", "paper_output"),
        )
    ):
        paper_bundle.update(_read_artifacts(repo, PAPER_TEXT_FALLBACK_KEYS, limit=8000))
    workflow_blockers = collect_workflow_blockers({**state, "iteration": iteration}, repo)
    workflow_decision = build_workflow_decision(
        {**state, "iteration": iteration},
        verdict=str(state.get("reviewer_verdict", "") or ""),
        should_iterate=bool(state.get("need_replan", False)),
        blockers=workflow_blockers,
    )
    source_acquisition = read_source_acquisition(repo)
    source_inspection = read_source_inspection(repo)
    next_planner_context = _read_next_planner_context(state, repo)
    authorization_state = _state_with_official_download_aliases(state)
    planner_readiness = state.get("planner_readiness") if isinstance(state.get("planner_readiness"), dict) else check_planner_readiness(authorization_state)
    allowed_scope = _allowed_scope_from_readiness(planner_readiness, source_inspection, authorization_state)
    contract_mode = str(allowed_scope.get("contract_mode") or state.get("contract_mode", "") or "")
    official_input_authorization = _official_input_authorization(state, contract_mode=contract_mode)
    network_authorization = _network_authorization(state)
    decision_status = state.get("decision_status", {}) if isinstance(state.get("decision_status"), dict) else {}
    reviewer_guidance = _string_list(next_planner_context.get("reviewer_guidance")) or _string_list((state.get("structured_review_feedback") or {}).get("next_iteration_guidance") if isinstance(state.get("structured_review_feedback"), dict) else [])
    do_not_repeat = _string_list(next_planner_context.get("do_not_repeat")) or _string_list((state.get("structured_review_feedback") or {}).get("do_not_repeat") if isinstance(state.get("structured_review_feedback"), dict) else [])
    active_blockers = list(decision_status.get("active_blockers", []) or next_planner_context.get("active_blockers", []) or workflow_blockers)
    bundle: dict[str, Any] = {
        "schema_version": "2.0",
        "repo_path": str(repo),
        "goal": state.get("goal", ""),
        "user_hints": user_hints,
        "optional_guidance": user_hints,
        "language": state.get("language", "en"),
        "iteration": iteration,
        "planning_mode": mode,
        "target_reproduction_level": target_level(state),
        "download_budget_gb": official_input_authorization["download_budget_gb"],
        "max_dataset_download_gb": official_input_authorization["max_dataset_download_gb"],
        "current_evidence_level": current_level(state),
        "paper_bundle": paper_bundle,
        "paper_context": paper_bundle,
        "paper_bundle_status": paper_status,
        "paper_quality": state.get("paper_quality", ""),
        "paper_backend_effective": state.get("paper_backend", ""),
        "paper_fallback_used": bool(state.get("fallback_used") or state.get("paper_ai_reader_failed")),
        "workflow_blockers": workflow_blockers,
        "workflow_decision": workflow_decision,
        "decision_status": decision_status,
        "source_acquisition": source_acquisition,
        "source_inspection": source_inspection,
        "previous_iteration_context": next_planner_context,
        "reviewer_guidance": reviewer_guidance,
        "do_not_repeat": do_not_repeat,
        "active_blockers": active_blockers,
        "evidence_status": decision_status.get("evidence_summary", {}),
        "allowed_scope": allowed_scope,
        "planner_readiness": planner_readiness,
        "review_bundle": {},
        "structured_review_feedback": {},
        "completed_tasks": [],
        "failed_tasks": [],
        "required_authorizations": [],
        "manager_status": state.get("manager_status", ""),
        "reviewer_verdict": state.get("reviewer_verdict", ""),
        "allow_official_dataset_download": official_input_authorization["allow_official_dataset_download"],
        "allow_full_benchmark": bool(state.get("allow_full_benchmark", False)),
        "allow_external_baselines": bool(state.get("allow_external_baselines", False)),
        "allow_network": network_authorization["raw_allow_network"],
        "network_authorized": network_authorization["network_authorized"],
        "allowed_network_scope": network_authorization["allowed_network_scope"],
        "network_authorization_reason": network_authorization["network_authorization_reason"],
        "network_authorization": network_authorization,
        "local_official_input_path": official_input_authorization["local_official_input_path"],
        "official_input_authorized": official_input_authorization["official_input_authorized"],
        "official_input_authorization": official_input_authorization,
        "official_input_authorization_reason": official_input_authorization["authorization_reason"],
        "authorization_reason": official_input_authorization["authorization_reason"],
        "user_approved_official_download": official_input_authorization["official_input_authorized"],
        "user_approved_download": bool(state.get("user_approved_download", False)),
        "user_approved_synthetic_demo": bool(state.get("user_approved_synthetic_demo", False)),
        "contract_mode": contract_mode,
    }
    if mode == "iterative_progress":
        bundle["review_bundle"] = _read_artifacts(repo, REVIEW_KEYS, limit=4000)
        # Reviewer feedback is optional - read if available but don't require it
        feedback_path = Path(state.get("latest_review_feedback_path", report_path(repo, "review_feedback")))
        feedback_data = _read_json(feedback_path)
        bundle["structured_review_feedback"] = feedback_data
        bundle.update(_structured_iteration_summary(state, feedback_data, contract_mode=contract_mode))
        # Also include manager decision for next iteration planning
        manager_decision_path = report_path(repo, "manager_decision")
        if manager_decision_path.exists():
            manager_decision = _read_json(manager_decision_path)
            bundle["manager_decision"] = manager_decision
            bundle["manager_errors"] = _string_list(manager_decision.get("blocking_errors"))
            bundle["manager_warnings"] = _string_list(manager_decision.get("warnings"))
    bundle["reviewer_guidance"] = reviewer_guidance or _string_list(bundle.get("structured_review_feedback", {}).get("next_iteration_guidance") if isinstance(bundle.get("structured_review_feedback"), dict) else [])
    bundle["do_not_repeat"] = do_not_repeat or _string_list(bundle.get("structured_review_feedback", {}).get("do_not_repeat") if isinstance(bundle.get("structured_review_feedback"), dict) else [])
    bundle["active_blockers"] = active_blockers or _string_list(bundle.get("active_blockers"))
    bundle["allowed_scope"] = allowed_scope
    bundle["contract_mode"] = contract_mode
    return bundle


def _structured_iteration_summary(state: R2AState, feedback: dict[str, Any], *, contract_mode: str = "") -> dict[str, Any]:
    preserve = _string_list(feedback.get("preserve_successful_steps"))
    blockers = _string_list(feedback.get("active_blockers"))
    required_fixes = _string_list(feedback.get("required_fixes"))
    failed = blockers or required_fixes
    current = str(feedback.get("current_level") or current_level(state))
    required_authorizations = _required_authorizations(state, feedback, contract_mode=contract_mode)
    return {
        "current_evidence_level": current,
        "completed_tasks": preserve,
        "failed_tasks": failed,
        "active_blockers": blockers,
        "reviewer_blockers": blockers,
        "classification_conflicts": _string_list(feedback.get("classification_conflicts")),
        "required_authorizations": required_authorizations,
    }


def _required_authorizations(state: R2AState, feedback: dict[str, Any], *, contract_mode: str = "") -> list[str]:
    verdict = str(feedback.get("verdict", "") or "").upper()
    missing = _string_list(feedback.get("missing_l3_requirements"))
    if _feedback_mentions_network_authorization(feedback) and not _network_authorization(state)["network_authorized"]:
        return ["network_authorization: external git clone/CMake for algorithm dependencies requires explicit user approval"]
    if verdict not in {"INPUT_CONTRACT_READY", "NEEDS_OFFICIAL_INPUT", "NEEDS_INPUT", "NEEDS_INPUT_OR_BUDGET"}:
        return []
    if _official_input_authorized(state, contract_mode=contract_mode):
        return []
    if verdict == "INPUT_CONTRACT_READY" and not _target_needs_official_input(state):
        return []
    reason = "; ".join(missing) if missing else "official reduced input path or approved download"
    return [f"official_input_download_or_local_path: {reason}"]


def _official_input_authorized(state: R2AState, *, contract_mode: str = "") -> bool:
    return bool(_official_input_authorization(state, contract_mode=contract_mode)["official_input_authorized"])


def _network_authorization(state: R2AState) -> dict[str, Any]:
    raw = state.get("network_authorization")
    raw_auth = raw if isinstance(raw, dict) else {}
    explicit_authorized = raw_auth.get("network_authorized")
    raw_allow_network = bool(state.get("allow_network", False))
    authorized = bool(
        (bool(explicit_authorized) if explicit_authorized is not None else False)
        or state.get("network_authorized")
        or raw_allow_network
        or state.get("user_approved_network")
        or state.get("user_approved_network_authorization")
    )
    scope = _scope_list(
        raw_auth.get("allowed_network_scope")
        or state.get("allowed_network_scope")
        or state.get("network_scope")
    )
    if authorized and not scope:
        scope = ["external_git_clone_for_algorithm_dependencies"]
    if not authorized:
        scope = []
    reason = str(
        raw_auth.get("network_authorization_reason")
        or state.get("network_authorization_reason")
        or ("explicit_user_allowed_network" if authorized else "network_not_authorized")
    )
    return {
        "schema_version": 1,
        "network_authorized": authorized,
        "allowed_network_scope": scope,
        "network_authorization_reason": reason,
        "raw_allow_network": raw_allow_network,
        "raw_user_approved_network": bool(state.get("user_approved_network", False)),
        "raw_user_approved_network_authorization": bool(state.get("user_approved_network_authorization", False)),
    }


def _official_input_authorization(state: R2AState, *, contract_mode: str = "") -> dict[str, Any]:
    budget = download_budget_gb(state)
    effective_contract_mode = str(contract_mode or state.get("contract_mode", "") or "")
    local_path = str(state.get("local_official_input_path", "") or "")
    allow_download = bool(state.get("allow_official_dataset_download", False))
    raw_user_approved_official_download = bool(state.get("user_approved_official_download", False))
    raw_user_approved_download = bool(state.get("user_approved_download", False))
    target = target_level(state)
    target_requires_official_input = _target_needs_official_input(state)
    contract_requires_official_input = effective_contract_mode in {"official_reduced", "full_benchmark"}
    user_allows_download = bool(
        allow_download
        or raw_user_approved_official_download
        or raw_user_approved_download
    )
    budget_sufficient = budget > 0

    if local_path:
        authorized = True
        reason = "local_official_input_path_provided"
    elif not target_requires_official_input:
        authorized = False
        reason = "target_level_does_not_require_official_input"
    elif user_allows_download and not budget_sufficient:
        authorized = False
        reason = "insufficient_download_budget"
    elif not contract_requires_official_input:
        authorized = False
        reason = "contract_mode_does_not_require_official_input"
    elif not user_allows_download:
        authorized = False
        reason = "official_dataset_download_not_allowed"
    else:
        authorized = True
        if allow_download:
            reason = "user_allowed_official_dataset_download_with_sufficient_budget"
        elif raw_user_approved_official_download:
            reason = "user_approved_official_download_with_sufficient_budget"
        else:
            reason = "user_approved_download_with_sufficient_budget"

    return {
        "schema_version": 1,
        "official_input_authorized": authorized,
        "authorization_reason": reason,
        "target_reproduction_level": target,
        "target_requires_official_input": target_requires_official_input,
        "contract_mode": effective_contract_mode,
        "contract_mode_requires_official_input": contract_requires_official_input,
        "allow_official_dataset_download": allow_download,
        "download_budget_gb": budget,
        "max_dataset_download_gb": budget,
        "download_budget_sufficient": budget_sufficient,
        "raw_user_approved_official_download": raw_user_approved_official_download,
        "raw_user_approved_download": raw_user_approved_download,
        "user_allows_official_dataset_download": user_allows_download,
        "local_official_input_path": local_path,
    }


def _target_needs_official_input(state: R2AState) -> bool:
    return target_level(state) not in {
        "L0_project_health",
        "L1_source_artifact_verified",
        "L2_input_contract_ready",
    }


def _read_next_planner_context(state: R2AState, repo: Path) -> dict[str, Any]:
    candidates: list[Path] = []
    explicit = str(state.get("next_planner_context_path", "") or "").strip()
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(report_path(repo, "next_planner_context"))
    for candidate in candidates:
        data = _read_json(candidate)
        if data:
            return data
    metadata = state.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("next_iteration_context"), dict):
        return dict(metadata["next_iteration_context"])
    return {}


def _allowed_scope_from_readiness(readiness: Any, inspection: dict[str, Any], state: R2AState) -> dict[str, Any]:
    """
    Compute allowed scope based on user permissions and safety boundaries.

    NOTE: This function NO longer caps max_target_level based on SourceInspection.supports.
    Static inspection uncertainty should become Planner notes, not hard caps.
    """
    if isinstance(readiness, dict):
        constraints = readiness.get("constraints")
        if isinstance(constraints, dict) and constraints:
            return dict(constraints)

    target = target_level(state)
    allow_download = bool(
        state.get("allow_official_dataset_download", False)
        or state.get("user_approved_official_download", False)
        or state.get("user_approved_download", False)
    )
    allow_full_benchmark = bool(state.get("allow_full_benchmark", False))
    download_budget = int(state.get("download_budget_gb", 0) or 0)

    # Safety check: download budget must be sufficient
    if allow_download and download_budget <= 0:
        return {
            "target_level": target,
            "contract_mode": "verification_only",
            "max_target_level": target,
            "reason": "Download budget insufficient for official dataset download.",
        }

    # Determine contract_mode based on user permissions
    if allow_full_benchmark:
        contract_mode = "full_benchmark"
    elif allow_download:
        contract_mode = "official_reduced"
    else:
        contract_mode = "verification_only"

    return {
        "target_level": target,
        "contract_mode": contract_mode,
        "max_target_level": target,  # User's target, not capped by static inspection
        "reason": "User permissions satisfied; actual feasibility determined by execution.",
    }


def _state_with_official_download_aliases(state: R2AState) -> R2AState:
    if state.get("allow_official_dataset_download", False):
        return state
    if not (state.get("user_approved_official_download", False) or state.get("user_approved_download", False)):
        return state
    # Legacy approval fields mean network input acquisition is allowed, but do not
    # by themselves prove the official input contract needed for L3+ is ready.
    return {
        **state,
        "allow_official_dataset_download": True,
        "target_reproduction_level": "L2_input_contract_ready",
    }


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _scope_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.replace(";", ",").replace("\n", ",").split(",") if item.strip()]
    return []


def _feedback_mentions_network_authorization(feedback: dict[str, Any]) -> bool:
    text = json.dumps(feedback, ensure_ascii=False, default=str).upper()
    return any(
        marker in text
        for marker in (
            "NEEDS_NETWORK_AUTHORIZATION",
            "NETWORK AUTHORIZATION REQUIRED",
            "NETWORK NOT AUTHORIZED",
            "EXTERNAL GIT CLONE REQUIRED",
            "GIT CLONE + CMAKE REQUIRES NETWORK",
            "ALGORITHM BINARIES REQUIRE NETWORK",
        )
    ) or ("GIT CLONE" in text and "NETWORK" in text)


def _read_artifacts(repo: Path, keys: tuple[str, ...], *, limit: int) -> dict[str, dict[str, str]]:
    artifacts: dict[str, dict[str, str]] = {}
    for key in keys:
        path = report_path(repo, key)
        text = _read_text(path)
        artifacts[key] = {
            "path": str(path),
            "available": "yes" if text.strip() else "no",
            "excerpt": _excerpt(text, limit),
        }
    return artifacts


def _read_json(path: Path) -> dict[str, Any]:
    text = _read_text(path)
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _has_any_available_artifact(artifacts: dict[str, dict[str, str]], keys: tuple[str, ...]) -> bool:
    return any((artifacts.get(key, {}) or {}).get("available") == "yes" for key in keys)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _excerpt(text: str, limit: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "\n...(truncated)"
