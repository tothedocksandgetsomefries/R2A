from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from r2a.core.paths import report_path
from r2a.core.planner_schema import PlannerOutput
from r2a.tools.source_acquisition import read_source_acquisition
from r2a.tools.source_inspection import read_source_inspection
from r2a.tools.workflow_decision import PAPER_STRUCTURED_KEYS, paper_bundle_status, paper_markdown_artifacts_available


PLACEHOLDER_PATTERNS = (
    re.compile(r"github\.com[/\\]x(?:\b|/)", re.IGNORECASE),
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\bTBD\b", re.IGNORECASE),
    re.compile(r"\bFIXME\b", re.IGNORECASE),
    re.compile(r"\brun experiment\b", re.IGNORECASE),
    re.compile(r"download dataset from url", re.IGNORECASE),
)
def check_paper_readiness(state: dict[str, Any], workspace: str | Path | None = None) -> dict[str, Any]:
    """Check if paper inputs are ready for Planner.

    This check is now soft - it only blocks if there are NO usable paper inputs at all.
    Missing PAPER_OUTPUT.json alone does NOT block - Planner can use Markdown artifacts.
    """
    repo = _repo(state, workspace)
    paper_path = str(state.get("paper_path", "") or "").strip()

    # Collect warnings for diagnostic purposes
    warnings: list[str] = []

    # Check if original paper file exists
    paper_file_exists = False
    if paper_path:
        if Path(paper_path).exists():
            paper_file_exists = True
        else:
            warnings.append(f"paper_path does not exist: {paper_path}")

    # Check extraction status
    extraction = str(state.get("paper_extraction_status", "") or "").lower()
    if extraction in {"paper file missing", "extraction failed", "unsupported paper file type"}:
        warnings.append(f"Paper extraction status is not ideal: {extraction}")


    # Check for usable Markdown artifacts
    markdown_status = paper_markdown_artifacts_available(repo)
    available_artifacts = markdown_status["available_artifacts"]
    artifact_count = markdown_status["artifact_count"]
    has_paper_output = markdown_status["has_paper_output"]

    # If PAPER_OUTPUT.json is missing, add warning but don't block
    if not has_paper_output:
        warnings.append("PAPER_OUTPUT.json is missing; Planner will use Markdown paper artifacts.")

    # Determine if we have ANY usable paper input
    has_any_paper_input = paper_file_exists or markdown_status["usable"]

    # Only block if we have NO paper input at all
    if not has_any_paper_input:
        return _not_ready(
            "paper",
            "MISSING_PAPER",
            "No paper_path provided and no paper artifacts available.",
            "missing_paper",
            ["readable_paper_pdf_or_text"],
        )

    # We have usable paper inputs - allow Planner to proceed
    summary = f"Paper inputs available: {artifact_count} Markdown artifacts"
    if paper_file_exists:
        summary = f"Paper file exists and {artifact_count} Markdown artifacts available"


    return {
        "ready": True,
        "stage": "paper",
        "reason_code": "PAPER_INPUTS_AVAILABLE",
        "blockers": [],
        "required_inputs": [],
        "summary": summary,
        "available_artifacts": available_artifacts,
        "artifact_count": artifact_count,
        "has_paper_output": has_paper_output,
        "warnings": warnings,
        "paper_file_exists": paper_file_exists,
    }


def check_planner_readiness(state: dict[str, Any], workspace: str | Path | None = None) -> dict[str, Any]:
    repo = _repo(state, workspace)
    paper = state.get("paper_readiness") if isinstance(state.get("paper_readiness"), dict) else check_paper_readiness(state, workspace=workspace)
    if not paper.get("ready"):
        return _not_ready(
            "planner",
            "PLANNER_NOT_READY",
            "Planner is blocked because Paper is not ready.",
            "planner_not_ready",
            paper.get("required_inputs", []),
            blockers=paper.get("blockers", []),
        )
    acquisition = _source_acquisition(state, repo)
    status = str(acquisition.get("source_status", "") or "").strip().lower()
    if status != "available":
        if bool(state.get("allow_source_missing_fallback", False)):
            return {
                "ready": True,
                "stage": "planner",
                "reason_code": "SOURCE_MISSING_BUT_VERIFICATION_ONLY_ALLOWED",
                "blockers": list(acquisition.get("blockers", []) or []),
                "required_inputs": [],
                "summary": "Source is missing, but explicit verification-only fallback is allowed.",
                "constraints": _allowed_scope(state, read_source_inspection(repo), fallback=True),
            }
        return _not_ready(
            "planner",
            "MISSING_SOURCE",
            "Source acquisition did not produce an available source artifact.",
            "missing_source",
            ["official_source_url_or_local_source_path"],
            blockers=acquisition.get("blockers", []),
        )
    inspection = _source_inspection(state, repo)
    inspection_status = str(inspection.get("inspection_status", "") or "").lower()
    if inspection_status != "complete":
        return _not_ready(
            "planner",
            "SOURCE_INSPECTION_FAILED",
            "Source inspection is missing or blocked before Planner.",
            "source_inspection_failed",
            ["official_source_url_or_local_source_path"],
            blockers=inspection.get("blockers", []),
        )
    return {
        "ready": True,
        "stage": "planner",
        "reason_code": "PLANNER_READY",
        "blockers": [],
        "required_inputs": [],
        "summary": "Paper, source acquisition, and source inspection are ready for Planner.",
        "constraints": _allowed_scope(state, inspection),
        "has_next_planner_context": report_path(repo, "next_planner_context").exists() or bool((state.get("metadata", {}) or {}).get("next_iteration_context") if isinstance(state.get("metadata"), dict) else False),
    }


def check_engineer_readiness(state: dict[str, Any], workspace: str | Path | None = None) -> dict[str, Any]:
    repo = _repo(state, workspace)
    decision = state.get("decision_status")
    if isinstance(decision, dict) and decision and str(decision.get("typed_decision", "")) not in {"continue_iteration"}:
        return _not_ready(
            "engineer",
            "ENGINEER_NOT_READY",
            f"Current decision_status does not allow Engineer: {decision.get('typed_decision')}",
            "engineer_not_ready",
            [],
        )
    planner_path = Path(str(state.get("planner_output_path") or report_path(repo, "planner_output")))
    task_path = Path(str(state.get("task_spec_path") or report_path(repo, "task")))
    if not planner_path.exists() or not task_path.exists():
        return _not_ready(
            "engineer",
            "INVALID_PLANNER_OUTPUT",
            "Planner output or TASK_SPEC.md is missing.",
            "invalid_planner_output",
            [],
        )
    planner_data = _read_json(planner_path)
    if not planner_data:
        return _not_ready(
            "engineer",
            "INVALID_PLANNER_OUTPUT",
            "PLANNER_OUTPUT.json is invalid or empty.",
            "invalid_planner_output",
            [],
        )
    try:
        planner = PlannerOutput.model_validate(planner_data)
    except Exception as exc:
        return _not_ready(
            "engineer",
            "INVALID_PLANNER_OUTPUT",
            f"PLANNER_OUTPUT.json failed schema validation: {type(exc).__name__}: {exc}",
            "invalid_planner_output",
            [],
        )
    if not planner.tasks:
        return _not_ready(
            "engineer",
            "INVALID_PLANNER_OUTPUT",
            "Planner output contains no tasks.",
            "invalid_planner_output",
            [],
        )
    plan_quality_warnings = _plan_quality_warnings(planner, task_path, repo, state=state)
    return {
        "ready": True,
        "stage": "engineer",
        "reason_code": "ENGINEER_READY",
        "blockers": [],
        "required_inputs": [],
        "summary": "Planner output and TASK_SPEC.md are executable enough for Engineer.",
        "task_count": len(planner.tasks),
        "warnings": plan_quality_warnings,
        "readiness_warnings": plan_quality_warnings,
        "non_blocking_warnings": plan_quality_warnings,
        "diagnostics": {
            "plan_quality_warnings": plan_quality_warnings,
        },
    }


def _not_ready(
    stage: str,
    reason_code: str,
    summary: str,
    blocker_type: str,
    required_inputs: list[str],
    *,
    blockers: Any = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    blocker_list = [item for item in (blockers or []) if isinstance(item, dict)]
    if not blocker_list:
        blocker_list = [
            {
                "blocker_id": f"{blocker_type}:{reason_code}",
                "type": blocker_type,
                "reason_code": reason_code,
                "requires_user_input": blocker_type in {"missing_paper", "missing_paper_bundle", "invalid_paper_output", "missing_source", "empty_repo"},
                "retryable": blocker_type in {"invalid_planner_output", "planner_not_ready", "engineer_not_ready"},
                "source": "readiness_gate",
                "last_message": summary,
                "required_inputs": required_inputs,
            }
        ]
    return {
        "ready": False,
        "stage": stage,
        "reason_code": reason_code,
        "blockers": blocker_list,
        "required_inputs": required_inputs,
        "summary": summary,
        **dict(extra or {}),
    }


def _allowed_scope(state: dict[str, Any], inspection: dict[str, Any], *, fallback: bool = False) -> dict[str, Any]:
    """
    Compute allowed scope based on user permissions and safety boundaries.

    NOTE: This function NO longer caps max_target_level based on SourceInspection.supports.
    Static inspection uncertainty should become Planner notes, not hard caps.
    """
    target = str(state.get("target_reproduction_level", "") or "L4_reduced_paper_aligned")
    allow_download = bool(state.get("allow_official_dataset_download", False))
    allow_full_benchmark = bool(state.get("allow_full_benchmark", False))
    download_budget = int(state.get("download_budget_gb", 0) or 0)

    # Fallback mode: source is unavailable
    if fallback:
        return {
            "target_level": target,
            "contract_mode": "verification_only",
            "max_target_level": target,  # Don't cap
            "reason": "Source is unavailable; Planner should verify before execution.",
        }

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
    # Dataset available and source supports L3
    return {
        "target_level": target,
        "contract_mode": "official_reduced" if allow_download else "verification_only",
        "max_target_level": target,
        "reason": "Source inspection supports bounded planning under current approvals.",
    }


def _plan_quality_warnings(
    planner: PlannerOutput,
    task_path: Path,
    repo: Path,
    state: dict[str, Any] | None = None,
) -> list[str]:
    del task_path
    warnings: list[str] = []
    seen: set[str] = set()
    source_root = _get_source_root(state or {}, repo)
    for task in planner.tasks:
        task_id = str(getattr(task, "task_id", "") or "unknown_task")
        fields: list[str] = []
        for value in (
            task.title,
            task.objective,
            task.rationale,
            task.actions,
            task.acceptance_criteria,
            task.expected_outputs,
            task.stop_conditions,
        ):
            if isinstance(value, list):
                fields.extend(str(item) for item in value)
            elif value is not None:
                fields.append(str(value))

        for action in task.actions:
            missing_script = _missing_python_script(action, repo, source_root=source_root)
            if missing_script:
                _append_warning(
                    warnings,
                    seen,
                    f"Plan-quality warning in {task_id}: referenced Python script does not exist: {missing_script}",
                )

        for field in fields:
            for pattern in PLACEHOLDER_PATTERNS:
                if pattern.search(field):
                    _append_warning(
                        warnings,
                        seen,
                        f"Plan-quality warning in {task_id}: placeholder-like text matched `{pattern.pattern}`: "
                        f"{_warning_snippet(field)}",
                    )
    return warnings


def _append_warning(warnings: list[str], seen: set[str], warning: str) -> None:
    if warning in seen:
        return
    seen.add(warning)
    warnings.append(warning)


def _warning_snippet(text: str, limit: int = 160) -> str:
    normalized = " ".join(str(text).split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _get_source_root(state: dict[str, Any], repo: Path) -> Path:
    """Get the actual source root from SOURCE_ACQUISITION.json or fallback paths."""
    # First, try to get local_path from SOURCE_ACQUISITION.json
    acquisition = _source_acquisition(state, repo)
    local_path = str(acquisition.get("local_path", "") or "").strip()
    if local_path:
        source_root = Path(local_path)
        if source_root.exists() and source_root.is_dir():
            return source_root.resolve()

    # Fallback: repo/.r2a/artifacts/source
    artifacts_source = repo / ".r2a" / "artifacts" / "source"
    if artifacts_source.exists() and artifacts_source.is_dir():
        return artifacts_source.resolve()

    # Last resort: repo root
    return repo.resolve()


def _normalize_source_script_path(script: str, source_root: Path) -> str:
    """Normalize a script path that may contain the source_root prefix.

    Planner may generate repo-relative paths like:
    - .r2a/artifacts/source/benchmark.py
    - .r2a\\artifacts\\source\\benchmark.py

    When source_root is already .../repo/.r2a/artifacts/source, we should:
    1. Strip the .r2a/artifacts/source/ prefix from script
    2. Then it becomes a proper source_root-relative path

    Args:
        script: The script path extracted from action (may be repo-relative or source_root-relative)
        source_root: The actual source root path

    Returns:
        Normalized script path (source_root-relative)
    """
    # Normalize separators
    script = script.replace("\\", "/")

    # Common source artifact prefixes to strip
    # These are repo-relative paths that point into source_root
    prefixes_to_strip = [
        ".r2a/artifacts/source/",
        "./.r2a/artifacts/source/",
    ]

    for prefix in prefixes_to_strip:
        if script.startswith(prefix):
            return script[len(prefix):]

    return script


def _missing_python_script(action: str, repo: Path, source_root: Path | None = None) -> str:
    """Check if a Python script referenced in action exists.

    Args:
        action: The action string containing potential python command
        repo: The repo root path (used for relative path resolution)
        source_root: The actual source root where scripts are located.
                    If None, defaults to repo root for backward compatibility.

    Returns:
        Empty string if script exists, otherwise the script name.
    """
    match = re.search(r"(?:python|python3)\s+((?:[A-Za-z]:)?[A-Za-z0-9_./\\:-]+\.py)\b", action)
    if not match:
        return ""
    script = match.group(1).replace("\\", "/")

    # Use source_root if provided, otherwise use repo
    effective_root = (source_root or repo).resolve()

    # Handle absolute paths - check directly without joining to effective_root
    if Path(script).is_absolute():
        path = Path(script).resolve()
        return script if not path.exists() else ""

    # Normalize the script path to handle repo-relative source artifact paths
    normalized_script = _normalize_source_script_path(script, effective_root)

    path = (effective_root / normalized_script).resolve()
    try:
        path.relative_to(effective_root)
    except ValueError:
        # Script path escapes the root - treat as missing
        return script

    return script if not path.exists() else ""


def _source_acquisition(state: dict[str, Any], repo: Path) -> dict[str, Any]:
    direct = state.get("source_acquisition")
    if isinstance(direct, dict):
        return direct
    return read_source_acquisition(repo)


def _source_inspection(state: dict[str, Any], repo: Path) -> dict[str, Any]:
    direct = state.get("source_inspection")
    if isinstance(direct, dict):
        return direct
    return read_source_inspection(repo)


def _repo(state: dict[str, Any], workspace: str | Path | None) -> Path:
    return Path(str(state.get("repo_path", "") or workspace or ".")).resolve()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
