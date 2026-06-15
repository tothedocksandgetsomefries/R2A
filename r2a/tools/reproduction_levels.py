from __future__ import annotations

from pathlib import Path
from typing import Any


REPRODUCTION_LEVELS = (
    "L0_project_health",
    "L1_source_artifact_verified",
    "L2_input_contract_ready",
    "L3_official_reduced_run",
    "L4_reduced_paper_aligned",
    "L5_minimal_baseline_comparison",
    "L6_full_or_near_full_reproduction",
)

DEFAULT_TARGET_REPRODUCTION_LEVEL = "L4_reduced_paper_aligned"
DEFAULT_DOWNLOAD_BUDGET_GB = 20

LEVEL_INDEX = {level: index for index, level in enumerate(REPRODUCTION_LEVELS)}
LEGACY_LEVEL_ALIASES = {
    "L0_source_build_smoke": "L1_source_artifact_verified",
    "L1_input_data_contract": "L2_input_contract_ready",
    "L2_official_reduced_run": "L3_official_reduced_run",
    "L3_reduced_paper_alignment": "L4_reduced_paper_aligned",
    "L4_minimal_baseline_comparison": "L5_minimal_baseline_comparison",
}

PROGRESS_VERDICTS = {
    "PASS_SMOKE_ONLY",
    "INPUT_CONTRACT_READY",
    "PASS_DEMO_ONLY",
    "PASS_REDUCED_METHOD_ONLY",
    "PASS_REDUCED_ALIGNED",
    "PASS_REDUCED_COMPARISON",
}

TERMINAL_SUCCESS_VERDICTS = {
    "PASS",
}

ITERATION_VERDICTS = {
    "PASS_WITH_LIMITATIONS",
    "NEEDS_FIX",
    "BORDERLINE",
    "NEEDS_OFFICIAL_INPUT",
    "NEEDS_INPUT_OR_BUDGET",
    *PROGRESS_VERDICTS,
}


def normalize_level(level: str | None, default: str = "L0_project_health") -> str:
    normalized = LEGACY_LEVEL_ALIASES.get(str(level or ""), str(level or ""))
    if normalized in LEVEL_INDEX:
        return normalized
    return default


def target_level(state: dict[str, Any]) -> str:
    return normalize_level(str(state.get("target_reproduction_level", "") or ""), DEFAULT_TARGET_REPRODUCTION_LEVEL)


def current_level(state: dict[str, Any]) -> str:
    return normalize_level(str(state.get("reproduction_level", "") or ""), "L0_project_health")


def level_reached(level: str, target: str) -> bool:
    return LEVEL_INDEX[normalize_level(level)] >= LEVEL_INDEX[normalize_level(target, DEFAULT_TARGET_REPRODUCTION_LEVEL)]


def download_budget_gb(state: dict[str, Any]) -> int:
    try:
        budget = int(state.get("download_budget_gb", DEFAULT_DOWNLOAD_BUDGET_GB))
    except (TypeError, ValueError):
        budget = DEFAULT_DOWNLOAD_BUDGET_GB
    return max(0, budget)


def infer_level_from_verdict(verdict: str) -> str:
    return {
        "PASS_WITH_LIMITATIONS": "L0_project_health",
        "PASS_SMOKE_ONLY": "L1_source_artifact_verified",
        "INPUT_CONTRACT_READY": "L2_input_contract_ready",
        "PASS_DEMO_ONLY": "L2_input_contract_ready",
        "NEEDS_OFFICIAL_INPUT": "L2_input_contract_ready",
        "NEEDS_INPUT_OR_BUDGET": "L2_input_contract_ready",
        "PASS_REDUCED_METHOD_ONLY": "L3_official_reduced_run",
        "PASS_REDUCED_ALIGNED": "L4_reduced_paper_aligned",
        "PASS_REDUCED_COMPARISON": "L5_minimal_baseline_comparison",
        "PASS": "L6_full_or_near_full_reproduction",
    }.get(verdict, "L0_project_health")


def next_level_after_verdict(verdict: str, state: dict[str, Any]) -> str:
    target = target_level(state)
    current = infer_level_from_verdict(verdict)
    if verdict in {"NEEDS_FIX", "BORDERLINE"}:
        return current_level(state)
    if verdict in {"NEEDS_OFFICIAL_INPUT", "NEEDS_INPUT_OR_BUDGET"}:
        return "L2_input_contract_ready"
    if verdict == "PASS_DEMO_ONLY":
        return "L2_input_contract_ready"
    next_index = min(LEVEL_INDEX[current] + 1, LEVEL_INDEX[target])
    return REPRODUCTION_LEVELS[next_index]


def should_continue_after_verdict(verdict: str, state: dict[str, Any]) -> bool:
    decision = state.get("workflow_decision")
    if isinstance(decision, dict) and decision.get("should_iterate") is False:
        return False
    blockers = state.get("workflow_blockers")
    if _has_non_auto_user_blocker(blockers):
        return False
    if verdict not in ITERATION_VERDICTS:
        return False
    if not state.get("auto_iterate", False):
        return False
    if verdict == "NEEDS_OFFICIAL_INPUT":
        if not official_input_progress_authorized(state):
            return False
        if official_input_search_exhausted(state):
            return False
    if _same_blocker_without_progress(state):
        return False
    if int(state.get("iteration", 1)) >= int(state.get("max_iterations", 1)):
        return False
    if verdict in {"NEEDS_FIX", "BORDERLINE", "NEEDS_OFFICIAL_INPUT", "NEEDS_INPUT_OR_BUDGET"}:
        return True
    if verdict == "INPUT_CONTRACT_READY" and LEVEL_INDEX[target_level(state)] >= LEVEL_INDEX["L3_official_reduced_run"]:
        if not official_input_progress_authorized(state):
            return False
    reached = infer_level_from_verdict(verdict)
    return not level_reached(reached, target_level(state))


def official_input_search_exhausted(state: dict[str, Any]) -> bool:
    if state.get("official_input_search_exhausted"):
        return True
    repo_path = state.get("repo_path")
    if not repo_path:
        return False
    status_path = Path(str(repo_path)) / ".r2a" / "results" / "reproduction_status.csv"
    if not status_path.exists():
        return False
    try:
        text = status_path.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False
    terminal_markers = (
        "missing_official_input_after_network_search",
        "official_input_search_exhausted",
        "no_official_input_after_network_search",
        "official_input_contract_acquisition_with_network",
        "bounded network acquisition",
        "no official or paper-linked",
        "no official input",
    )
    return any(marker in text for marker in terminal_markers)


def official_input_progress_authorized(state: dict[str, Any]) -> bool:
    if state.get("local_official_input_path"):
        return True
    if state.get("user_approved_official_download") or state.get("user_approved_download"):
        return True
    if state.get("allow_official_dataset_download") and download_budget_gb(state) > 0:
        return True
    if state.get("user_approved_synthetic_demo") or state.get("synthetic_demo_approved"):
        return True
    return False


def _same_blocker_without_progress(state: dict[str, Any]) -> bool:
    history = state.get("iteration_history", [])
    if not isinstance(history, list) or len(history) < 2:
        return False
    latest = history[-1] if isinstance(history[-1], dict) else {}
    previous = history[-2] if isinstance(history[-2], dict) else {}
    latest_action = str(latest.get("suggested_next_action", "") or "")
    previous_action = str(previous.get("suggested_next_action", "") or "")
    latest_verdict = str(latest.get("reviewer_verdict", "") or "")
    previous_verdict = str(previous.get("reviewer_verdict", "") or "")
    if latest_verdict and latest_verdict == previous_verdict and latest_action and latest_action == previous_action:
        return True
    return False


def _has_non_auto_user_blocker(blockers: Any) -> bool:
    if not isinstance(blockers, list):
        return False
    for blocker in blockers:
        if not isinstance(blocker, dict):
            continue
        if blocker.get("type") == "user_input_required" and not blocker.get("auto_resolvable", False):
            return True
    return False


def claim_level_for_verdict(verdict: str) -> str:
    return {
        "PASS_WITH_LIMITATIONS": "project health / limited validation",
        "PASS_SMOKE_ONLY": "source/artifact smoke only",
        "INPUT_CONTRACT_READY": "input contract ready",
        "PASS_DEMO_ONLY": "demo-only validation",
        "PASS_REDUCED_METHOD_ONLY": "official reduced method only",
        "PASS_REDUCED_ALIGNED": "official reduced paper-aligned",
        "PASS_REDUCED_COMPARISON": "official reduced comparison",
        "PASS": "full or near-full reproduction",
        "NEEDS_OFFICIAL_INPUT": "blocked: official input required",
        "NEEDS_INPUT_OR_BUDGET": "blocked: input or budget required",
    }.get(verdict, "limited or unresolved")
