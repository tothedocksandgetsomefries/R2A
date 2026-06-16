from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from r2a.core.paths import artifact_dir, report_path
from r2a.core.review_verdict import normalize_verdict_token
from r2a.tools.evidence_levels import contract_l2_cap_reason
from r2a.tools.input_integrity import summarize_official_input_integrity

PLANNER_REQUIRED_TASK_SECTIONS = (
    "Reproducibility Gate Summary",
    "Max Evidence Level Allowed",
    "L3 Entry Criteria",
    "L4 Alignment Criteria",
)

PLANNER_REQUIRED_CONTRACT_SECTIONS = (
    "Contract Mode",
    "Max Evidence Level Allowed",
    "Reproducibility Gate",
    "Claim Restrictions",
)

PLANNER_FORBIDDEN_CANDIDATE_NAMES = {
    "reduced_metrics.csv",
    "command_manifest.csv",
    "input_contract_verification.csv",
}

PLANNER_FORBIDDEN_CANDIDATE_PARTS = {
    "artifacts",
    "results",
    "experiments",
    "datasets",
    "build",
    "cmake-build",
    "target",
    "dist",
    "__pycache__",
}

PLANNER_FORBIDDEN_DATA_SUFFIXES = {".fvecs", ".ivecs", ".bvecs"}
REVIEWER_ALLOWED_VERDICTS = {
    "PASS",
    "PASS_WITH_LIMITATIONS",
    "PASS_SMOKE_ONLY",
    "INPUT_CONTRACT_READY",
    "PASS_DEMO_ONLY",
    "PASS_REDUCED_METHOD_ONLY",
    "PASS_REDUCED_ALIGNED",
    "PASS_REDUCED_COMPARISON",
    "NEEDS_FIX",
    "NEEDS_OFFICIAL_INPUT",
    "NEEDS_INPUT_OR_BUDGET",
    "MANAGER_CLASSIFICATION_CONFLICT",
    "NEEDS_DETERMINISTIC_RECHECK",
    "HUMAN_REVIEW_REQUIRED",
    "PASS_WITH_REVIEW_CONFLICT",
    "BORDERLINE",
    "REJECT",
}
REVIEWER_L3_L4_VERDICTS = {
    "PASS_REDUCED_METHOD_ONLY",
    "PASS_REDUCED_ALIGNED",
    "PASS_REDUCED_COMPARISON",
}
REVIEWER_PASS_LIKE_VERDICTS = {
    "PASS",
    "PASS_WITH_LIMITATIONS",
    "PASS_SMOKE_ONLY",
    "INPUT_CONTRACT_READY",
    "PASS_DEMO_ONLY",
    *REVIEWER_L3_L4_VERDICTS,
}


def planner_staging_dir(repo_path: str | Path, iteration: int, attempt: int = 1) -> Path:
    return artifact_dir(repo_path) / "staging" / "planner" / f"iter_{int(iteration):03d}" / f"attempt_{int(attempt):03d}"


def planner_allowed_outputs(repo_path: str | Path, staging_dir: str | Path) -> list[str]:
    repo = Path(repo_path)
    staging = Path(staging_dir)
    return [
        _repo_rel(repo, staging / "PLANNER_OUTPUT.json"),
        _repo_rel(repo, staging / "TASK_SPEC.md"),
        _repo_rel(repo, staging / "EXPERIMENT_CONTRACT.md"),
        _repo_rel(repo, staging / "logs") + "/",
    ]


def reviewer_staging_dir(repo_path: str | Path, iteration: int, attempt: int = 1) -> Path:
    return artifact_dir(repo_path) / "staging" / "reviewer" / f"iter_{int(iteration):03d}" / f"attempt_{int(attempt):03d}"


def reviewer_allowed_outputs(repo_path: str | Path, staging_dir: str | Path) -> list[str]:
    repo = Path(repo_path)
    staging = Path(staging_dir)
    return [
        _repo_rel(repo, staging / "REVIEW_REPORT.md"),
        _repo_rel(repo, staging / "REVIEW_FEEDBACK.json"),
        _repo_rel(repo, staging / "logs") + "/",
    ]


def validate_planner_transaction(
    repo_path: str | Path,
    staging_dir: str | Path,
    result: dict[str, Any],
    *,
    iteration: int,
    attempt: int = 1,
    attempt_started_at: float | None = None,
) -> dict[str, Any]:
    repo = Path(repo_path)
    staging = Path(staging_dir)
    planner_output_path = staging / "PLANNER_OUTPUT.json"
    task_path = staging / "TASK_SPEC.md"
    contract_path = staging / "EXPERIMENT_CONTRACT.md"
    issues: list[str] = []
    rejected_files: list[str] = []
    backend_failure_category = str(result.get("backend_failure_category") or "")
    failure_category = ""
    execution_status = ""

    if result.get("unexpected_modifications"):
        failure_category = "STAGE_BOUNDARY_VIOLATION"
        execution_status = "PLANNER_FORBIDDEN_WRITE"
        issues.append("Planner backend modified files outside the allowed staging outputs.")

    if result.get("transient_backend_failure") or backend_failure_category == "TOOL_CALL_PARSE_FAILURE":
        failure_category = failure_category or "PLANNER_BACKEND_FAILURE"
        execution_status = execution_status or "PLANNER_BACKEND_FAILURE"
        issues.append("Planner backend reported a tool-call parse failure.")

    if int(result.get("returncode", 0) or 0) != 0:
        failure_category = failure_category or "PLANNER_BACKEND_FAILURE"
        execution_status = execution_status or "PLANNER_BACKEND_FAILURE"
        issues.append(f"Planner backend returned non-zero exit code: {result.get('returncode')}.")

    if not result.get("success"):
        failure_category = failure_category or str(result.get("failure_category") or "PLANNER_TRANSACTION_FAILED")
        execution_status = execution_status or str(result.get("execution_status") or "PLANNER_TRANSACTION_FAILED")
        issues.append("Planner backend did not report a clean successful stage result.")

    rejected_files.extend(_forbidden_candidate_files(repo, staging))
    if rejected_files:
        failure_category = "STAGE_BOUNDARY_VIOLATION"
        execution_status = "PLANNER_FORBIDDEN_WRITE"
        issues.append("Planner candidate included forbidden artifact/result/dataset outputs.")

    for required_path, label in (
        (planner_output_path, "PLANNER_OUTPUT.json"),
        (task_path, "TASK_SPEC.md"),
        (contract_path, "EXPERIMENT_CONTRACT.md"),
    ):
        if not required_path.exists():
            failure_category = failure_category or "PLANNER_MISSING_REQUIRED_OUTPUT"
            execution_status = execution_status or "PLANNER_MISSING_REQUIRED_OUTPUT"
            issues.append(f"Missing required planner candidate output: {label}.")
            continue
        if required_path.stat().st_size == 0:
            failure_category = failure_category or "PLANNER_MISSING_REQUIRED_OUTPUT"
            execution_status = execution_status or "PLANNER_MISSING_REQUIRED_OUTPUT"
            issues.append(f"Planner candidate output is empty: {label}.")
        if attempt_started_at is not None and required_path.stat().st_mtime + 2.0 < float(attempt_started_at):
            failure_category = failure_category or "PLANNER_STALE_OUTPUT"
            execution_status = execution_status or "PLANNER_STALE_OUTPUT"
            issues.append(f"Planner candidate output is stale: {label}.")

    task_text = _read_text(task_path)
    contract_text = _read_text(contract_path)
    for section in PLANNER_REQUIRED_TASK_SECTIONS:
        if section.lower() not in task_text.lower():
            failure_category = failure_category or "PLANNER_CONTRACT_VALIDATION_FAILED"
            execution_status = execution_status or "PLANNER_CONTRACT_VALIDATION_FAILED"
            issues.append(f"TASK_SPEC.md is missing required section: {section}.")
    for section in PLANNER_REQUIRED_CONTRACT_SECTIONS:
        if section.lower() not in contract_text.lower():
            failure_category = failure_category or "PLANNER_CONTRACT_VALIDATION_FAILED"
            execution_status = execution_status or "PLANNER_CONTRACT_VALIDATION_FAILED"
            issues.append(f"EXPERIMENT_CONTRACT.md is missing required section: {section}.")

    contract_mode = _extract_contract_mode(contract_text)
    input_integrity = summarize_official_input_integrity(repo)
    if contract_mode == "official_reduced":
        if not input_integrity.get("all_required_inputs_ok"):
            failure_category = failure_category or "PLANNER_CONTRACT_VALIDATION_FAILED"
            execution_status = execution_status or "PLANNER_CONTRACT_VALIDATION_FAILED"
            issues.append(
                "Planner candidate requested official_reduced, but official input integrity is not clean."
            )

    validation_status = "PASS" if not issues else "FAIL"
    planner_output_written = planner_output_path.exists() and planner_output_path.stat().st_size > 0
    task_written = task_path.exists() and task_path.stat().st_size > 0
    contract_written = contract_path.exists() and contract_path.stat().st_size > 0
    backend_execution_status = str(result.get("execution_status") or "")
    backend_returncode = result.get("returncode", "")
    backend_stderr_tail = str(result.get("stderr_tail") or "")
    backend_stdout_tail = str(result.get("stdout_tail") or "")
    backend_invocation_manifest_path = str(result.get("invocation_manifest_path") or "")
    backend_invocation_log_dir = str(result.get("invocation_log_dir") or "")
    diagnostic = {
        "planner_backend": result.get("planner_backend", ""),
        "prompt_file": result.get("prompt_file_path", ""),
        "prompt_size": result.get("prompt_size_bytes", ""),
        "allowed_tools": result.get("allowed_tools", ""),
        "staging_planner_output_written": planner_output_written,
        "staging_task_spec_written": task_written,
        "staging_experiment_contract_written": contract_written,
        "planner_validation_passed": validation_status == "PASS",
        "planner_committed": False,
        "approval_passed": False,
        "failure_category": "" if validation_status == "PASS" else (failure_category or "PLANNER_TRANSACTION_FAILED"),
        "failure_reason": "; ".join(issues),
        "is_claude_ccr_call_problem": bool(result.get("transient_backend_failure") or backend_failure_category),
        "backend_failure_category": backend_failure_category or str(result.get("failure_category") or ""),
        "backend_execution_status": backend_execution_status,
        "backend_returncode": backend_returncode,
        "backend_stderr_tail": backend_stderr_tail,
        "backend_stdout_tail": backend_stdout_tail,
        "backend_invocation_manifest_path": backend_invocation_manifest_path,
        "backend_invocation_log_dir": backend_invocation_log_dir,
        "backend_error": str(result.get("error") or ""),
    }
    return {
        "stage": "planner",
        "iteration": int(iteration),
        "attempt": int(attempt),
        "staging_dir": str(staging),
        "committed": False,
        "validation_status": validation_status,
        "failure_category": "" if validation_status == "PASS" else (failure_category or "PLANNER_TRANSACTION_FAILED"),
        "execution_status": "" if validation_status == "PASS" else (execution_status or "PLANNER_TRANSACTION_FAILED"),
        "committed_files": [],
        "rejected_files": rejected_files,
        "backend_failure_category": backend_failure_category or str(result.get("failure_category") or ""),
        "backend_execution_status": backend_execution_status,
        "backend_returncode": backend_returncode,
        "backend_stderr_tail": backend_stderr_tail,
        "backend_stdout_tail": backend_stdout_tail,
        "backend_invocation_manifest_path": backend_invocation_manifest_path,
        "backend_invocation_log_dir": backend_invocation_log_dir,
        "boundary_violation": bool(result.get("unexpected_modifications") or rejected_files),
        "input_integrity_status": input_integrity.get("input_contract_integrity_status", ""),
        "contract_mode_before_validation": contract_mode,
        "contract_mode_after_validation": contract_mode if validation_status == "PASS" else "",
        "issues": issues,
        "stdout_log_path": result.get("stdout_log_path", ""),
        "stderr_log_path": result.get("stderr_log_path", ""),
        "unexpected_modifications": result.get("unexpected_modifications", []),
        "diagnostic": diagnostic,
    }


def commit_planner_transaction(repo_path: str | Path, staging_dir: str | Path, metadata: dict[str, Any]) -> dict[str, Any]:
    repo = Path(repo_path)
    staging = Path(staging_dir)
    commits = [
        (staging / "PLANNER_OUTPUT.json", report_path(repo, "planner_output")),
        (staging / "TASK_SPEC.md", report_path(repo, "task")),
        (staging / "EXPERIMENT_CONTRACT.md", report_path(repo, "experiment_contract")),
    ]
    committed_files: list[str] = []
    for source, target in commits:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        committed_files.append(_repo_rel(repo, target))
    updated = {
        **metadata,
        "committed": True,
        "validation_status": "PASS",
        "failure_category": "",
        "execution_status": "",
        "committed_files": committed_files,
    }
    updated["diagnostic"] = {
        **metadata.get("diagnostic", {}),
        "planner_validation_passed": True,
        "planner_committed": True,
        "approval_passed": False,
        "failure_category": "",
        "failure_reason": "",
    }
    return updated


def validate_reviewer_transaction(
    repo_path: str | Path,
    staging_dir: str | Path,
    result: dict[str, Any],
    *,
    iteration: int,
    attempt: int = 1,
    attempt_started_at: float | None = None,
    manager_status: str = "",
    check_status: str = "",
) -> dict[str, Any]:
    repo = Path(repo_path)
    staging = Path(staging_dir)
    report = staging / "REVIEW_REPORT.md"
    feedback = staging / "REVIEW_FEEDBACK.json"
    issues: list[str] = []
    failure_category = ""
    execution_status = ""
    backend_failure_category = str(result.get("backend_failure_category") or "")
    parsed_feedback: dict[str, Any] = {}
    manager_classification_conflict = False

    if result.get("unexpected_modifications"):
        failure_category = "STAGE_BOUNDARY_VIOLATION"
        execution_status = "REVIEWER_FORBIDDEN_WRITE"
        issues.append("Reviewer backend modified files outside the allowed staging outputs.")

    if result.get("transient_backend_failure") or backend_failure_category == "TOOL_CALL_PARSE_FAILURE":
        failure_category = failure_category or "REVIEWER_BACKEND_FAILURE"
        execution_status = execution_status or "REVIEWER_BACKEND_FAILURE"
        issues.append("Reviewer backend reported a tool-call parse failure.")

    if int(result.get("returncode", 0) or 0) != 0:
        failure_category = failure_category or "REVIEWER_BACKEND_FAILURE"
        execution_status = execution_status or "REVIEWER_BACKEND_FAILURE"
        issues.append(f"Reviewer backend returned non-zero exit code: {result.get('returncode')}.")

    if not result.get("success"):
        failure_category = failure_category or str(result.get("failure_category") or "REVIEWER_TRANSACTION_FAILED")
        execution_status = execution_status or str(result.get("execution_status") or "REVIEWER_TRANSACTION_FAILED")
        issues.append("Reviewer backend did not report a clean successful stage result.")

    for required_path, label in ((report, "REVIEW_REPORT.md"), (feedback, "REVIEW_FEEDBACK.json")):
        if not required_path.exists():
            failure_category = failure_category or "REVIEWER_MISSING_REQUIRED_OUTPUT"
            execution_status = execution_status or "REVIEWER_MISSING_REQUIRED_OUTPUT"
            issues.append(f"Missing required reviewer candidate output: {label}.")
            continue
        try:
            stat = required_path.stat()
        except OSError:
            failure_category = failure_category or "REVIEWER_MISSING_REQUIRED_OUTPUT"
            execution_status = execution_status or "REVIEWER_MISSING_REQUIRED_OUTPUT"
            issues.append(f"Reviewer candidate output is unreadable: {label}.")
            continue
        if stat.st_size == 0:
            failure_category = failure_category or "REVIEWER_MISSING_REQUIRED_OUTPUT"
            execution_status = execution_status or "REVIEWER_MISSING_REQUIRED_OUTPUT"
            issues.append(f"Reviewer candidate output is empty: {label}.")
        if attempt_started_at is not None and stat.st_mtime + 2.0 < float(attempt_started_at):
            failure_category = failure_category or "REVIEWER_STALE_OUTPUT"
            execution_status = execution_status or "REVIEWER_STALE_OUTPUT"
            issues.append(f"Reviewer candidate output is stale: {label}.")

    if feedback.exists() and feedback.stat().st_size > 0:
        try:
            parsed = json.loads(_read_text(feedback))
            if isinstance(parsed, dict):
                parsed_feedback = parsed
            else:
                raise ValueError("REVIEW_FEEDBACK.json must be a JSON object.")
        except Exception as exc:
            failure_category = failure_category or "REVIEWER_FEEDBACK_VALIDATION_FAILED"
            execution_status = execution_status or "REVIEWER_MALFORMED_FEEDBACK"
            issues.append(f"REVIEW_FEEDBACK.json is not valid JSON: {exc}.")

    raw_verdict = parsed_feedback.get("verdict") or parsed_feedback.get("status")
    verdict = _normalize_verdict(raw_verdict)
    normalization_reason = ""
    if parsed_feedback and verdict and str(raw_verdict or "").strip().upper() != verdict:
        raw_upper = str(raw_verdict or "").strip().upper()
        if "NEEDS_INPUT" in raw_upper and verdict == "NEEDS_INPUT_OR_BUDGET":
            normalization_reason = (
                "NEEDS_INPUT is an internal blocking status alias, not an allowed formal reviewer verdict."
            )
        else:
            normalization_reason = "Reviewer feedback verdict token was normalized before formal validation."
        parsed_feedback.setdefault("raw_verdict", str(raw_verdict or "").strip())
        parsed_feedback["verdict"] = verdict
        parsed_feedback["normalization_reason"] = normalization_reason
        feedback.write_text(json.dumps(parsed_feedback, indent=2, ensure_ascii=False), encoding="utf-8")
    if parsed_feedback and not verdict:
        failure_category = failure_category or "REVIEWER_FEEDBACK_VALIDATION_FAILED"
        execution_status = execution_status or "REVIEWER_INVALID_VERDICT"
        issues.append("REVIEW_FEEDBACK.json must include verdict or status.")
    if verdict and verdict not in REVIEWER_ALLOWED_VERDICTS:
        failure_category = failure_category or "REVIEWER_FEEDBACK_VALIDATION_FAILED"
        execution_status = execution_status or "REVIEWER_INVALID_VERDICT"
        issues.append(f"REVIEW_FEEDBACK.json verdict is not allowed: {verdict}.")

    effective_manager_status = _normalize_verdict(manager_status or check_status)
    if effective_manager_status == "FAIL" and verdict in REVIEWER_PASS_LIKE_VERDICTS:
        if _declares_manager_classification_conflict(parsed_feedback):
            manager_classification_conflict = True
            execution_status = execution_status or "MANAGER_CLASSIFICATION_CONFLICT"
        else:
            failure_category = failure_category or "REVIEWER_SAFETY_VALIDATION_FAILED"
            execution_status = execution_status or "REVIEWER_MANAGER_FAIL_PASS"
            issues.append("Manager/CHECK status is FAIL, so AI Reviewer cannot commit a pass-like verdict.")
    elif verdict in {
        "MANAGER_CLASSIFICATION_CONFLICT",
        "NEEDS_DETERMINISTIC_RECHECK",
        "HUMAN_REVIEW_REQUIRED",
        "PASS_WITH_REVIEW_CONFLICT",
    }:
        manager_classification_conflict = True
        execution_status = execution_status or verdict

    cap_reason = contract_l2_cap_reason(repo)
    if cap_reason and verdict in REVIEWER_L3_L4_VERDICTS:
        failure_category = failure_category or "REVIEWER_SAFETY_VALIDATION_FAILED"
        execution_status = execution_status or "REVIEWER_CONTRACT_L2_CAP_BLOCKED_L3"
        issues.append(f"Contract L2 cap prevents AI Reviewer from committing L3/L4 verdicts: {cap_reason}.")

    input_integrity = summarize_official_input_integrity(repo)
    if not cap_reason and input_integrity.get("has_blocking_issue") and verdict in REVIEWER_L3_L4_VERDICTS:
        failure_category = failure_category or "REVIEWER_SAFETY_VALIDATION_FAILED"
        execution_status = execution_status or "REVIEWER_INPUT_INTEGRITY_BLOCKED_L3"
        issues.append("Official input integrity blocker prevents AI Reviewer from committing L3/L4 verdicts.")

    validation_status = "PASS" if not issues else "FAIL"
    return {
        "stage": "reviewer",
        "iteration": int(iteration),
        "attempt": int(attempt),
        "staging_dir": str(staging),
        "committed": False,
        "validation_status": validation_status,
        "failure_category": "" if validation_status == "PASS" else (failure_category or "REVIEWER_TRANSACTION_FAILED"),
        "execution_status": execution_status if validation_status == "PASS" else (execution_status or "REVIEWER_TRANSACTION_FAILED"),
        "committed_files": [],
        "backend_failure_category": backend_failure_category,
        "boundary_violation": bool(result.get("unexpected_modifications")),
        "input_integrity_status": input_integrity.get("input_contract_integrity_status", ""),
        "contract_l2_cap_reason": cap_reason,
        "raw_candidate_verdict": str(raw_verdict or "").strip(),
        "candidate_verdict": verdict,
        "verdict_normalization_reason": normalization_reason,
        "manager_classification_conflict": manager_classification_conflict,
        "classification_conflicts": _classification_conflicts(parsed_feedback),
        "issues": issues,
        "stdout_log_path": result.get("stdout_log_path", ""),
        "stderr_log_path": result.get("stderr_log_path", ""),
        "unexpected_modifications": result.get("unexpected_modifications", []),
    }


def commit_reviewer_transaction(repo_path: str | Path, staging_dir: str | Path, metadata: dict[str, Any]) -> dict[str, Any]:
    repo = Path(repo_path)
    staging = Path(staging_dir)
    commits = [
        (staging / "REVIEW_REPORT.md", report_path(repo, "review")),
        (staging / "REVIEW_FEEDBACK.json", report_path(repo, "review_feedback")),
    ]
    committed_files: list[str] = []
    for source, target in commits:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        committed_files.append(_repo_rel(repo, target))
    return {
        **metadata,
        "committed": True,
        "validation_status": "PASS",
        "failure_category": "",
        "execution_status": metadata.get("execution_status", "") if metadata.get("manager_classification_conflict") else "",
        "committed_files": committed_files,
    }


def write_planner_transaction_metadata(repo_path: str | Path, metadata: dict[str, Any]) -> Path:
    path = artifact_dir(repo_path) / "logs" / "planner_transaction.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_reviewer_transaction_metadata(repo_path: str | Path, metadata: dict[str, Any]) -> Path:
    path = artifact_dir(repo_path) / "logs" / "reviewer_transaction.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _forbidden_candidate_files(repo: Path, staging: Path) -> list[str]:
    if not staging.exists():
        return []
    forbidden: list[str] = []
    for path in sorted(item for item in staging.rglob("*") if item.is_file()):
        rel = path.relative_to(staging).as_posix()
        lower = rel.lower()
        name = path.name.lower()
        parts = set(part.lower() for part in Path(rel).parts)
        if name in PLANNER_FORBIDDEN_CANDIDATE_NAMES:
            forbidden.append(_repo_rel(repo, path))
            continue
        if parts & PLANNER_FORBIDDEN_CANDIDATE_PARTS:
            forbidden.append(_repo_rel(repo, path))
            continue
        if path.suffix.lower() in PLANNER_FORBIDDEN_DATA_SUFFIXES:
            forbidden.append(_repo_rel(repo, path))
            continue
        if lower.startswith("results/") or lower.startswith(".r2a/results/"):
            forbidden.append(_repo_rel(repo, path))
    return forbidden


def _requests_official_reduced(contract_text: str) -> bool:
    lower = contract_text.lower()
    return "official_reduced" in lower or "l3_official_reduced_run" in lower


def _extract_contract_mode(contract_text: str) -> str:
    modes = {"verification_only", "smoke", "official_reduced", "full_benchmark"}
    heading_match = re.search(
        r"^\s*##\s*Contract Mode\s*$\s*`?([A-Za-z0-9_\-]+)`?",
        contract_text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if heading_match and heading_match.group(1).lower() in modes:
        return heading_match.group(1).lower()
    match = re.search(r"contract mode\s*[:|-]\s*`?([A-Za-z0-9_\-]+)`?", contract_text, flags=re.IGNORECASE)
    if match and match.group(1).lower() in modes:
        return match.group(1).lower()
    return ""


def _normalize_verdict(value: object) -> str:
    return normalize_verdict_token(value)


def _declares_manager_classification_conflict(feedback: dict[str, Any]) -> bool:
    if not feedback:
        return False
    if bool(feedback.get("manager_classification_conflict")):
        return True
    if _normalize_verdict(feedback.get("conflict_type")) == "MANAGER_CLASSIFICATION_CONFLICT":
        return True
    if _normalize_verdict(feedback.get("status")) == "MANAGER_CLASSIFICATION_CONFLICT":
        return True
    if _normalize_verdict(feedback.get("verdict")) in {
        "MANAGER_CLASSIFICATION_CONFLICT",
        "NEEDS_DETERMINISTIC_RECHECK",
        "HUMAN_REVIEW_REQUIRED",
        "PASS_WITH_REVIEW_CONFLICT",
    }:
        return True
    return bool(_classification_conflicts(feedback))


def _classification_conflicts(feedback: dict[str, Any]) -> list[str]:
    raw = feedback.get("classification_conflicts", [])
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _repo_rel(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
