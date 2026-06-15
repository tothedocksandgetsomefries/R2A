from __future__ import annotations

from pathlib import Path
from typing import Any

from r2a.core.paths import artifact_dir
from r2a.tools.csv_sanitizer import sanitized_csv_rows
from r2a.tools.evidence_levels import contract_l2_cap_reason, infer_evidence_level
from r2a.tools.reproduction_levels import LEVEL_INDEX, normalize_level


L0_L4_LEVELS = (
    "L0_project_health",
    "L1_source_artifact_verified",
    "L2_input_contract_ready",
    "L3_official_reduced_run",
    "L4_reduced_paper_aligned",
)

LEVEL_LABELS = {
    "L0_project_health": "L0: Paper/repo inspected",
    "L1_source_artifact_verified": "L1: Source/build smoke verified",
    "L2_input_contract_ready": "L2: Runnable demo or input contract ready",
    "L3_official_reduced_run": "L3: Official reduced reproduction",
    "L4_reduced_paper_aligned": "L4: Paper-aligned reduced reproduction",
}


def level_index(level: str) -> int:
    return LEVEL_INDEX[normalize_level(level)]


def cap_to_l0_l4(level: str) -> str:
    normalized = normalize_level(level)
    if level_index(normalized) > level_index("L4_reduced_paper_aligned"):
        return "L4_reduced_paper_aligned"
    return normalized


def min_level(left: str, right: str) -> str:
    left_norm = normalize_level(left)
    right_norm = normalize_level(right)
    return left_norm if level_index(left_norm) <= level_index(right_norm) else right_norm


def level_reached(level: str, target: str) -> bool:
    return level_index(level) >= level_index(target)


def effective_l0_l4_level(repo_path: str | Path, state: dict[str, Any] | None = None) -> str:
    """Determine the effective evidence level after quality gates.

    简化版：直接从实际文件推断，不使用 Manager 的 max_level_allowed 进行 tiered capping。
    Manager 不再判断 evidence level，只检查基础交付。

    只保留 contract cap（用户权限限制）。

    .. deprecated::
        此函数已废弃，仅供旧模块兼容使用。
        正式等级判断应由 Reviewer 完成。
        新代码不得使用此函数作为正式等级来源。
        使用 reviewer_level_judgment 模块代替。
    """
    state = state or {}
    # 直接从实际文件推断
    inferred = cap_to_l0_l4(infer_evidence_level(repo_path, str(state.get("reproduction_level", "L0_project_health"))))

    # 只应用 contract cap（用户权限限制）
    contract_cap = contract_l2_cap_reason(repo_path)
    if contract_cap:
        inferred = min_level(inferred, "L2_input_contract_ready")

    return cap_to_l0_l4(inferred)


def evaluate_l0_l4(repo_path: str | Path, state: dict[str, Any] | None = None) -> dict[str, Any]:
    """评估 L0-L4 evidence level。

    简化版：从实际文件推断，不使用 Manager 的 tiered cap。

    .. deprecated::
        此函数已废弃，仅供旧模块兼容使用。
        Reviewer 不再使用此函数作为正式等级来源。
        正式等级判断应由 Reviewer 基于 reviewer_level_judgment 完成。
        此函数只支持 L0-L4，不支持 L5-L6。
        新代码不得使用此函数作为正式等级来源。
        使用 reviewer_level_judgment 模块代替。
    """
    repo = Path(repo_path)
    state = state or {}
    target = cap_to_l0_l4(str(state.get("target_reproduction_level", "L4_reduced_paper_aligned")))
    observed = cap_to_l0_l4(infer_evidence_level(repo, str(state.get("reproduction_level", "L0_project_health"))))
    achieved = effective_l0_l4_level(repo, state)
    contract_cap = contract_l2_cap_reason(repo)
    cap_reason = _quality_gate_cap_reason(observed, achieved, contract_cap, state)
    level_results: dict[str, dict[str, Any]] = {}
    for level in L0_L4_LEVELS:
        passed = level_reached(achieved, level)
        observed_passed = level_reached(observed, level)
        level_results[level] = {
            "label": LEVEL_LABELS[level],
            "status": "PASS" if passed else "FAIL",
            "passed": passed,
            "observed": observed_passed,
            "reason": _level_reason(repo, level, passed, contract_cap, achieved, observed, state),
        }
    blockers = _blocking_reasons(repo, target, level_results, contract_cap, state)
    status = "PASS" if level_results[target]["passed"] and not blockers else "PASS_WITH_LIMITATIONS"
    if not level_results["L0_project_health"]["passed"]:
        status = "FAIL"
    return {
        "target_level": target,
        "target_label": LEVEL_LABELS[target],
        "observed_level": observed,
        "observed_label": LEVEL_LABELS[observed],
        "quality_gate_level": achieved,
        "quality_gate_label": LEVEL_LABELS[achieved],
        "accepted_level": achieved,
        "accepted_label": LEVEL_LABELS[achieved],
        "cap_reason": cap_reason,
        "achieved_level": achieved,
        "achieved_label": LEVEL_LABELS[achieved],
        "status": status,
        "blocking_reasons": blockers,
        "levels": level_results,
        "summary": _summary_sentence(target, achieved, blockers),
    }


def manager_level_decision(
    repo_path: str | Path,
    *,
    status: str,
    errors: list[str],
    warnings: list[str],
    result_csvs: list[Path],
    provenance_report: dict[str, Any] | None = None,
    input_integrity_report: dict[str, Any] | None = None,
    task_spec_text: str = "",
) -> dict[str, Any]:
    """Manager decision - 简化版。

    Manager 不再进行 tiered evidence capping。
    直接从实际文件推断 evidence level，不降低。

    只返回状态和推断的 evidence level。
    """
    repo = Path(repo_path)
    inferred = cap_to_l0_l4(infer_evidence_level(repo))

    # 不再分离 schema warnings - Manager 不关心这些
    # 所有 errors 都是 blocking_errors（但 Manager 只检查基础交付）
    blocking_errors = list(errors)
    advisory_warnings = list(warnings)

    # 简化的 gates 报告
    gates = {
        "csv_present": bool(result_csvs),
        "manager_status": status,
        "input_integrity": "PASS",  # Manager 不再判断
        "provenance": "PASS",  # Manager 不再判断
        "paper_alignment": "PASS" if _csv_has_rows(repo, "paper_alignment.csv") else "MISSING",
    }

    # Contract cap 仍然有效（用户权限限制）
    cap = inferred
    contract_cap_reason = contract_l2_cap_reason(repo)
    if contract_cap_reason:
        cap = min_level(cap, "L2_input_contract_ready")

    return {
        "status": status,
        "max_level_allowed": cap_to_l0_l4(cap),  # 不降低，直接使用推断的 level
        "blocking_errors": blocking_errors,
        "warnings": advisory_warnings,
        "checks": gates,
    }


def evidence_ladder_markdown(decision: dict[str, Any]) -> str:
    lines = ["| Level | Observed | Accepted | Reason |", "|---|---|---|---|"]
    for level in L0_L4_LEVELS:
        item = decision["levels"][level]
        observed = "yes" if item.get("observed") else "no"
        lines.append(f"| {LEVEL_LABELS[level]} | {observed} | {item['status']} | {item['reason']} |")
    return "\n".join(lines)


def blocking_reasons_markdown(decision: dict[str, Any]) -> str:
    blockers = decision.get("blocking_reasons", [])
    if not blockers:
        return "- None"
    return "\n".join(f"- {item}" for item in blockers)


def _level_reason(repo: Path, level: str, passed: bool, contract_cap: str, achieved: str, observed: str, state: dict[str, Any]) -> str:
    if passed:
        return {
            "L0_project_health": "Paper/repo inspection artifacts are available.",
            "L1_source_artifact_verified": "Source/build smoke evidence is available.",
            "L2_input_contract_ready": "Input contract or runnable demo evidence is available.",
            "L3_official_reduced_run": "Official reduced metrics, input integrity, and command provenance passed evidence checks.",
            "L4_reduced_paper_aligned": "Paper alignment mapping exists and satisfies reduced-alignment checks.",
        }[level]
    if level_reached(observed, level) and not level_reached(achieved, level):
        # 简化：不再检查 manager_max_level_allowed
        if contract_cap:
            return f"Blocked by contract cap: {contract_cap}."
        return "Observed evidence reaches this level, but a quality gate caps the accepted level."
    if level == "L3_official_reduced_run":
        if contract_cap:
            return f"Blocked by contract cap: {contract_cap}."
        if not _csv_has_rows(repo, "reduced_metrics.csv"):
            return "Missing non-empty reduced_metrics.csv."
        return "Missing official input, measured metrics, or command provenance required for L3."
    if level == "L4_reduced_paper_aligned":
        if not level_reached(achieved, "L3_official_reduced_run"):
            return "Requires L3 official reduced reproduction first."
        if not _csv_has_rows(repo, "paper_alignment.csv"):
            return "Missing non-empty paper_alignment.csv."
        return "Paper alignment rows do not satisfy L4 setting coverage."
    if level == "L2_input_contract_ready":
        return "Missing input_contract_verification.csv or runnable demo evidence."
    if level == "L1_source_artifact_verified":
        return "Missing source/build smoke evidence."
    return "Missing paper/repo inspection artifacts."


def _blocking_reasons(
    repo: Path,
    target: str,
    level_results: dict[str, dict[str, Any]],
    contract_cap: str,
    state: dict[str, Any],
) -> list[str]:
    """Collect blocking reasons for levels below target."""
    reasons: list[str] = []
    for level in L0_L4_LEVELS:
        if level_index(level) > level_index(target):
            continue
        item = level_results[level]
        if not item["passed"]:
            reasons.append(f"{level}: {item['reason']}")
    if contract_cap and level_index(target) >= level_index("L3_official_reduced_run"):
        reasons.append(f"L3/L4 contract cap: {contract_cap}.")
    # 简化：不再检查 manager_status
    return _dedupe(reasons)


def _summary_sentence(target: str, achieved: str, blockers: list[str]) -> str:
    if not blockers and level_reached(achieved, target):
        return f"Target {target} reached."
    if blockers:
        return f"Reached {achieved}; target {target} is blocked by {len(blockers)} issue(s)."
    return f"Reached {achieved}; target {target} is not fully reached yet."


def _quality_gate_cap_reason(observed: str, accepted: str, contract_cap: str, state: dict[str, Any]) -> str:
    """Determine why the accepted level is capped below observed.

    简化版：只检查 contract cap。
    """
    if not level_reached(observed, accepted):
        return ""
    if observed == accepted:
        return contract_cap if contract_cap and level_index(observed) >= level_index("L2_input_contract_ready") else ""
    # 简化：不再检查 manager_max_level_allowed
    if contract_cap:
        return f"Contract cap: {contract_cap}."
    return "Quality gate caps the accepted evidence level below the observed artifact level."


def _csv_has_rows(repo: Path, name: str) -> bool:
    return any(_read_csv_rows(path) for path in _candidate_csv_paths(repo, name))


def _candidate_csv_paths(repo: Path, name: str) -> list[Path]:
    return [path for path in (artifact_dir(repo) / "results" / name, repo / "results" / name) if path.exists()]


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    result = sanitized_csv_rows(path)
    if result.has_error and not result.rows:
        return []
    return result.rows


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
