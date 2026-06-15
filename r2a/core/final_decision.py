from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from r2a.core.paths import artifact_dir, report_path
from r2a.core.reviewer_level_judgment import is_valid_level
from r2a.core.verdicts import is_pass_like_verdict, normalize_verdict
from r2a.tools.csv_sanitizer import sanitized_csv_rows
from r2a.tools.reproduction_levels import level_reached, normalize_level


UNASSESSED = "UNASSESSED"
PAPER_ALIGNMENT_STATUSES = {
    "MATCH",
    "PARTIAL_MATCH",
    "MISMATCH",
    "NOT_AVAILABLE",
    "NEEDS_HUMAN_VERIFICATION",
}


def final_decision_path(repo_path: str | Path) -> Path:
    return report_path(repo_path, "final_decision")


def read_final_decision(repo_path: str | Path) -> dict[str, Any]:
    return _read_json(final_decision_path(repo_path))


def build_final_decision(
    state: dict[str, Any],
    *,
    write: bool = True,
    allow_state_compat: bool = False,
) -> dict[str, Any]:
    repo = Path(str(state.get("repo_path", "") or "."))
    evidence_decision = _read_evidence_decision(repo, state, allow_state_compat=allow_state_compat)
    formal_verdict = _formal_verdict(evidence_decision, state)
    accepted_level, accepted_valid, accepted_source = _accepted_level_from_evidence(evidence_decision)
    observed_level, observed_source = _observed_level(repo, accepted_level if accepted_valid else "")
    target_level = normalize_level(
        str(state.get("target_reproduction_level", "") or evidence_decision.get("target_level", "") or ""),
        "L4_reduced_paper_aligned",
    )
    target_reached = bool(accepted_valid and accepted_level != UNASSESSED and level_reached(accepted_level, target_level))
    decision_status = state.get("decision_status") if isinstance(state.get("decision_status"), dict) else {}
    stop_reason = str(decision_status.get("reason_code") or state.get("stop_reason") or "").strip()
    final_status = _final_status(target_reached, accepted_valid, decision_status, stop_reason, state)
    warnings = _warnings(
        repo,
        evidence_decision=evidence_decision,
        formal_verdict=formal_verdict,
        accepted_level=accepted_level,
        accepted_valid=accepted_valid,
        observed_level=observed_level,
        state=state,
    )
    payload = {
        "schema_version": 1,
        "formal_verdict": formal_verdict,
        "accepted_level": accepted_level,
        "accepted_level_valid": accepted_valid,
        "accepted_level_source": accepted_source,
        "observed_level": observed_level,
        "observed_level_source": observed_source,
        "target_level": target_level,
        "target_reached": target_reached,
        "final_status": final_status,
        "stop_reason": stop_reason,
        "warnings": warnings,
        "evidence_decision_valid": _evidence_decision_is_valid(evidence_decision),
    }
    if write:
        path = final_decision_path(repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _read_evidence_decision(repo: Path, state: dict[str, Any], *, allow_state_compat: bool) -> dict[str, Any]:
    candidates = []
    explicit = str(state.get("evidence_decision_path", "") or "").strip()
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(report_path(repo, "evidence_decision"))
    for path in candidates:
        data = _read_json(path)
        if data:
            return data
    if allow_state_compat:
        level = str(state.get("current_reproduction_level", "") or "").strip()
        verdict = normalize_verdict(state.get("reviewer_verdict"))
        if level and is_valid_level(level) and is_pass_like_verdict(verdict):
            return {
                "current_reproduction_level": level,
                "verdict": verdict,
                "level_valid": True,
                "level_source": "state_compatibility",
            }
    return {}


def _formal_verdict(evidence_decision: dict[str, Any], state: dict[str, Any]) -> str:
    verdict = normalize_verdict(evidence_decision.get("verdict") or state.get("reviewer_verdict"))
    return verdict or "UNASSESSED"


def _accepted_level_from_evidence(evidence_decision: dict[str, Any]) -> tuple[str, bool, str]:
    if not _evidence_decision_is_valid(evidence_decision):
        return UNASSESSED, False, "UNASSESSED"
    level = normalize_level(str(evidence_decision.get("current_reproduction_level", "") or ""), "")
    return level, True, "EVIDENCE_DECISION.json"


def _evidence_decision_is_valid(evidence_decision: dict[str, Any]) -> bool:
    if not evidence_decision:
        return False
    verdict = normalize_verdict(evidence_decision.get("verdict"))
    if verdict == "NEEDS_FIX" or not is_pass_like_verdict(verdict):
        return False
    if evidence_decision.get("level_valid") is not True:
        return False
    level = str(evidence_decision.get("current_reproduction_level", "") or "").strip()
    return bool(level and is_valid_level(level))


def _observed_level(repo: Path, accepted_level: str) -> tuple[str, str]:
    if accepted_level and accepted_level != UNASSESSED:
        return accepted_level, "EVIDENCE_DECISION.json"
    results = artifact_dir(repo) / "results"
    names = _result_names(repo)
    if "full_reproduction.csv" in names:
        return "L6_full_or_near_full_reproduction", "artifact_scan"
    if "baseline_comparison.csv" in names:
        return "L5_minimal_baseline_comparison", "artifact_scan"
    if (
        ("reduced_metrics.csv" in names and "paper_alignment.csv" in names)
        or "l4_alignment_summary.md" in names
        or "l4_evidence_summary.md" in names
        or (results / "L4_ALIGNMENT_SUMMARY.md").exists()
        or (results / "L4_EVIDENCE_SUMMARY.md").exists()
    ):
        return "L4_reduced_paper_aligned", "artifact_scan"
    if "reduced_metrics.csv" in names:
        return "L3_official_reduced_run", "artifact_scan"
    if "input_contract_verification.csv" in names:
        return "L2_input_contract_ready", "artifact_scan"
    if "source_verification.csv" in names or "build_smoke.csv" in names or "runtime_smoke.csv" in names:
        return "L1_source_artifact_verified", "artifact_scan"
    if artifact_dir(repo).exists():
        return "L0_project_health", "artifact_scan"
    return UNASSESSED, "none"


def _result_names(repo: Path) -> set[str]:
    names: set[str] = set()
    for root in (repo / "results", artifact_dir(repo) / "results"):
        if not root.exists():
            continue
        for path in root.iterdir():
            if path.is_file():
                names.add(path.name.lower())
    return names


def _final_status(
    target_reached: bool,
    accepted_valid: bool,
    decision_status: dict[str, Any],
    stop_reason: str,
    state: dict[str, Any],
) -> str:
    typed = str(decision_status.get("typed_decision", "") or "").strip()
    if target_reached:
        return "completed_success"
    if typed in {"terminal_failed", "request_paper", "request_source", "request_dataset", "request_approval"}:
        return "completed_with_failure"
    if stop_reason == "MAX_ITERATIONS_REACHED":
        return "completed_with_failure"
    if accepted_valid:
        return "completed_with_limitations"
    loop_status = str(state.get("loop_status", "") or "")
    if loop_status in {"failed", "completed_with_failure", "planner_failed"}:
        return "completed_with_failure"
    return "completed_with_failure"


def _warnings(
    repo: Path,
    *,
    evidence_decision: dict[str, Any],
    formal_verdict: str,
    accepted_level: str,
    accepted_valid: bool,
    observed_level: str,
    state: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    if not evidence_decision:
        warnings.append("EVIDENCE_DECISION.json missing; accepted_level is UNASSESSED.")
    elif not accepted_valid:
        reason = str(evidence_decision.get("level_reasoning", "") or "EVIDENCE_DECISION is invalid.").strip()
        warnings.append(f"EVIDENCE_DECISION invalid; accepted_level is UNASSESSED. Reason: {reason}")
    if formal_verdict == "NEEDS_FIX":
        warnings.append("Reviewer verdict is NEEDS_FIX; no accepted reproduction level is recorded.")
    if state.get("safety_override_triggered") or "safety override" in str(evidence_decision.get("level_reasoning", "")).lower():
        warnings.append("Safety Override was triggered in the reviewer chain.")
    decision_warnings = evidence_decision.get("warnings", [])
    if isinstance(decision_warnings, list):
        warnings.extend(str(item) for item in decision_warnings if str(item).strip())
    if accepted_level == UNASSESSED and observed_level not in {"", UNASSESSED}:
        warnings.append(f"Observed candidate evidence reaches {observed_level}, but it is not formally accepted.")
    names = _result_names(repo)
    if ("reduced_metrics.csv" in names or "paper_alignment.csv" in names) and "command_manifest.csv" not in names:
        warnings.append("command_manifest.csv missing; do not list it as existing provenance.")
    schema_warning = _paper_alignment_schema_warning(repo)
    if schema_warning:
        warnings.append(schema_warning)
    return _dedupe(warnings)


def _paper_alignment_schema_warning(repo: Path) -> str:
    rows = _rows_from_csv(repo, "paper_alignment.csv")
    if not rows:
        return ""
    bad_statuses = sorted(
        {
            str(row.get("match_status", "") or "").strip()
            for row in rows
            if str(row.get("match_status", "") or "").strip()
            and str(row.get("match_status", "") or "").strip() not in PAPER_ALIGNMENT_STATUSES
        }
    )
    if bad_statuses:
        return "paper_alignment.csv contains non-canonical match_status values: " + ", ".join(bad_statuses)
    return ""


def _rows_from_csv(repo: Path, name: str) -> list[dict[str, str]]:
    for root in (artifact_dir(repo) / "results", repo / "results"):
        path = root / name
        if not path.exists():
            continue
        result = sanitized_csv_rows(path)
        if result.has_error and not result.rows:
            return []
        return result.rows
    return []


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output
