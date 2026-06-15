from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from r2a.core.paths import artifact_dir, report_path
from r2a.core.planner_schema import PlannerOutput
from r2a.tools.csv_schemas import allowed_values_for_csv, csv_header


DEFAULT_NETWORK_SCOPE = "external_git_clone_for_algorithm_dependencies"
RAW_TASK_HINT_FIELDS = {
    "task_kind",
    "task_type",
    "kind",
    "requires_network",
    "requested_network_scope",
    "requested_network_scopes",
    "network_scope",
    "network_reason",
}


def compile_canonical_planner_output(
    raw_output: PlannerOutput | dict[str, Any],
    *,
    network_authorization: dict[str, Any] | None = None,
    network_authorized: bool | None = None,
    allowed_network_scope: list[str] | str | None = None,
) -> PlannerOutput:
    """Compile a raw Planner candidate into the canonical executable PlannerOutput."""
    data = raw_output.model_dump(mode="json") if isinstance(raw_output, PlannerOutput) else _json_clone(raw_output)
    if not isinstance(data, dict):
        raise ValueError("Planner raw output must be a JSON object.")
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("Planner raw output must include a tasks array.")

    authorization = network_authorization if isinstance(network_authorization, dict) else {}
    authorized = _compile_network_authorized(authorization, network_authorized)
    allowed_scope = _compile_allowed_network_scope(authorization, allowed_network_scope)

    compiled_tasks: list[Any] = []
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            compiled_tasks.append(task)
            continue
        compiled_tasks.append(
            _compile_canonical_task(
                task,
                index=index,
                network_authorized=authorized,
                allowed_network_scope=allowed_scope,
            )
        )
    data["tasks"] = compiled_tasks
    return PlannerOutput.model_validate(data)


def planner_staging_dir(repo_path: str | Path, iteration: int, attempt: int = 1) -> Path:
    return artifact_dir(repo_path) / "staging" / "planner" / f"iter_{int(iteration):03d}" / f"attempt_{int(attempt):03d}"


def planner_allowed_outputs(repo_path: str | Path, staging_dir: str | Path) -> list[str]:
    repo = Path(repo_path)
    staging = Path(staging_dir)
    return [
        _repo_rel(repo, staging / "PLANNER_OUTPUT.json"),
        _repo_rel(repo, staging / "TASK_SPEC.md"),
        _repo_rel(repo, staging / "EXPERIMENT_CONTRACT.md"),
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
    issues: list[str] = []
    paths = {
        "planner_output": staging / "PLANNER_OUTPUT.json",
        "task": staging / "TASK_SPEC.md",
        "experiment_contract": staging / "EXPERIMENT_CONTRACT.md",
    }
    if not result.get("success", True):
        issues.append(str(result.get("failure_reason") or "Planner backend did not complete successfully."))
    parsed_output: PlannerOutput | None = None
    for label, path in paths.items():
        if not path.exists():
            issues.append(f"Missing required planner staging output: {path.name}.")
            continue
        if path.stat().st_size == 0:
            issues.append(f"Planner staging output is empty: {path.name}.")
        if attempt_started_at is not None and path.stat().st_mtime + 2.0 < float(attempt_started_at):
            issues.append(f"Planner staging output is stale: {path.name}.")
    if paths["planner_output"].exists() and paths["planner_output"].stat().st_size > 0:
        try:
            parsed_output = PlannerOutput.model_validate_json(paths["planner_output"].read_text(encoding="utf-8"))
        except Exception as exc:
            issues.append(f"PLANNER_OUTPUT.json schema validation failed: {exc}.")
    if parsed_output is not None:
        issues.extend(_planner_task_invariant_issues(parsed_output))
        issues.extend(_network_authorization_issues(parsed_output, result))
    for label in ("task", "experiment_contract"):
        text = _read_text(paths[label])
        if "PLANNER_OUTPUT.json" not in text:
            issues.append(f"{paths[label].name} must identify PLANNER_OUTPUT.json as source of truth.")
    rejected = _unexpected_staging_files(repo, staging, set(paths.values()))
    if rejected:
        issues.append("Planner staging contains files outside the three allowed outputs.")
    validation_status = "PASS" if not issues else "FAIL"
    failure_category = "" if validation_status == "PASS" else _failure_category(issues)
    backend_failure_category = str(result.get("backend_failure_category") or result.get("failure_category") or "")
    backend_execution_status = str(result.get("execution_status") or "")
    backend_returncode = result.get("returncode", "")
    backend_stderr_tail = str(result.get("stderr_tail") or "")
    backend_stdout_tail = str(result.get("stdout_tail") or "")
    backend_invocation_manifest_path = str(result.get("invocation_manifest_path") or "")
    backend_invocation_log_dir = str(result.get("invocation_log_dir") or "")
    diagnostic = {
        "planner_backend": result.get("planner_backend", ""),
        "planner_status": "success" if validation_status == "PASS" else "failed",
        "planner_schema_version": parsed_output.schema_version if parsed_output else "",
        "planning_mode": parsed_output.planning_mode if parsed_output else "",
        "iteration_strategy": parsed_output.iteration_strategy if parsed_output else "",
        "contract_mode": parsed_output.contract_mode if parsed_output else "",
        "staging_planner_output_written": paths["planner_output"].exists(),
        "staging_task_spec_written": paths["task"].exists(),
        "staging_experiment_contract_written": paths["experiment_contract"].exists(),
        "planner_validation_passed": validation_status == "PASS",
        "planner_committed": False,
        "approval_passed": False,
        "failure_category": failure_category,
        "failure_reason": "; ".join(issues),
        "diagnostics_path": result.get("diagnostics_path", ""),
        "provider": result.get("provider", ""),
        "model": result.get("model", ""),
        "runner": result.get("runner", ""),
        "configured_provider": result.get("configured_provider", ""),
        "configured_model": result.get("configured_model", ""),
        "configured_runner": result.get("configured_runner", ""),
        "configured_agent": result.get("configured_agent", ""),
        "backend_failure_category": backend_failure_category,
        "backend_execution_status": backend_execution_status,
        "backend_returncode": backend_returncode,
        "backend_stderr_tail": backend_stderr_tail,
        "backend_stdout_tail": backend_stdout_tail,
        "backend_invocation_manifest_path": backend_invocation_manifest_path,
        "backend_invocation_log_dir": backend_invocation_log_dir,
        "backend_error": str(result.get("error") or ""),
        "backend_provider_error": str(result.get("provider_error") or ""),
        "network_authorized": _network_authorized_from_result(result),
        "allowed_network_scope": _network_scope_from_result(result),
    }
    return {
        "stage": "planner",
        "schema_version": parsed_output.schema_version if parsed_output else "",
        "iteration": int(iteration),
        "attempt": int(attempt),
        "staging_dir": str(staging),
        "committed": False,
        "validation_status": validation_status,
        "failure_category": failure_category,
        "execution_status": "" if validation_status == "PASS" else failure_category,
        "committed_files": [],
        "rejected_files": rejected,
        "backend_failure_category": backend_failure_category,
        "backend_execution_status": backend_execution_status,
        "backend_returncode": backend_returncode,
        "backend_stderr_tail": backend_stderr_tail,
        "backend_stdout_tail": backend_stdout_tail,
        "backend_invocation_manifest_path": backend_invocation_manifest_path,
        "backend_invocation_log_dir": backend_invocation_log_dir,
        "boundary_violation": bool(rejected),
        "contract_mode_after_validation": parsed_output.contract_mode if parsed_output and validation_status == "PASS" else "",
        "issues": issues,
        "stdout_log_path": result.get("stdout_log_path", ""),
        "stderr_log_path": result.get("stderr_log_path", ""),
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
        tmp = target.with_suffix(target.suffix + ".tmp")
        shutil.copy2(source, tmp)
        os.replace(tmp, target)
        committed_files.append(_repo_rel(repo, target))
    return {
        **metadata,
        "committed": True,
        "validation_status": "PASS",
        "failure_category": "",
        "execution_status": "",
        "committed_files": committed_files,
        "diagnostic": {
            **dict(metadata.get("diagnostic", {}) or {}),
            "planner_validation_passed": True,
            "planner_committed": True,
            "approval_passed": False,
            "failure_category": "",
            "failure_reason": "",
        },
    }


def write_planner_transaction_metadata(repo_path: str | Path, metadata: dict[str, Any]) -> Path:
    path = artifact_dir(repo_path) / "logs" / "planner_transaction.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _compile_canonical_task(
    raw_task: dict[str, Any],
    *,
    index: int,
    network_authorized: bool,
    allowed_network_scope: list[str],
) -> dict[str, Any]:
    task = dict(raw_task)
    hints = {field: task.pop(field) for field in RAW_TASK_HINT_FIELDS if field in task}
    if task.get("depends_on") is None:
        task["depends_on"] = []
    if task.get("allowed_write_paths") is None:
        task["allowed_write_paths"] = []
    if task.get("expected_outputs") is None:
        task["expected_outputs"] = []
    if task.get("acceptance_criteria") is None:
        task["acceptance_criteria"] = []
    task["stop_conditions"] = _compile_stop_conditions(task, hints, index=index)
    task["allow_network"] = _compile_task_allow_network(
        task,
        hints,
        network_authorized=network_authorized,
        allowed_network_scope=allowed_network_scope,
    )
    return task


def _compile_stop_conditions(task: dict[str, Any], hints: dict[str, Any], *, index: int) -> list[str]:
    existing = _string_list(task.get("stop_conditions"))
    if existing:
        return existing

    output_categories = _categories_from_expected_outputs(task.get("expected_outputs"))
    intent_categories = _categories_from_intent(task, hints)
    selected: list[str] = []
    if intent_categories and output_categories:
        selected = [category for category in output_categories if category in intent_categories]
    elif len(output_categories) == 1:
        selected = output_categories

    if not selected:
        task_id = str(task.get("task_id") or f"task_{index}")
        raise ValueError(
            "Cannot derive canonical stop_conditions for task "
            f"{task_id}: task intent and expected_outputs do not identify a known R2A evidence artifact."
        )

    conditions: list[str] = []
    for category in selected:
        conditions.extend(_stop_conditions_for_category(category))
    return _dedupe(conditions)


def _categories_from_expected_outputs(value: Any) -> list[str]:
    categories: list[str] = []
    for item in _string_list(value):
        text = item.lower()
        for marker, category in _OUTPUT_CATEGORY_MARKERS:
            if marker in text and category not in categories:
                categories.append(category)
    return categories


def _categories_from_intent(task: dict[str, Any], hints: dict[str, Any]) -> list[str]:
    parts = [
        hints.get("task_kind"),
        hints.get("task_type"),
        hints.get("kind"),
        task.get("task_id"),
        task.get("title"),
        task.get("objective"),
        task.get("rationale"),
        task.get("actions"),
        task.get("acceptance_criteria"),
    ]
    text = " ".join(_flatten_text(parts)).lower()
    categories: list[str] = []
    for category, markers in _INTENT_CATEGORY_MARKERS.items():
        if any(marker in text for marker in markers):
            categories.append(category)
    return categories


_OUTPUT_CATEGORY_MARKERS = (
    ("source_verification.csv", "source_verification"),
    ("build_smoke.csv", "build_smoke"),
    ("runtime_smoke.csv", "runtime_smoke"),
    ("project_tests.csv", "project_tests"),
    ("input_contract_verification.csv", "input_contract"),
    ("reduced_metrics.csv", "reduced_metrics"),
    ("paper_alignment.csv", "paper_alignment"),
    ("baseline_comparison.csv", "baseline_comparison"),
    ("command_manifest.csv", "command_manifest"),
    ("reproduction_status.csv", "reproduction_status"),
)


_INTENT_CATEGORY_MARKERS = {
    "source_verification": (
        "source_verification",
        "source verification",
        "source integrity",
        "artifact verification",
        "repository provenance",
        "source provenance",
    ),
    "build_smoke": ("build_smoke", "build smoke", "compile", "compilation", "make ", "cmake", "toolchain"),
    "runtime_smoke": ("runtime_smoke", "runtime smoke", "entrypoint", "--help", "loader", "dll"),
    "project_tests": ("project_tests", "project tests", "test availability", "test entrypoint"),
    "input_contract": (
        "input_contract",
        "input contract",
        "official input",
        "dataset integrity",
        "dataset",
        "ground_truth",
        "ground truth",
        "query",
    ),
    "reduced_metrics": (
        "reduced_metrics",
        "reduced metrics",
        "reduced benchmark",
        "official reduced",
        "reduced method",
        "measured metrics",
    ),
    "paper_alignment": ("paper_alignment", "paper alignment", "map reduced metrics", "paper settings"),
    "baseline_comparison": ("baseline_comparison", "baseline comparison", "same-input baseline", "baseline"),
    "command_manifest": ("command_manifest", "command manifest", "command provenance"),
    "reproduction_status": ("reproduction_status", "reproduction status", "status evidence"),
}


def _stop_conditions_for_category(category: str) -> list[str]:
    conditions = {
        "source_verification": [
            "Stop after `.r2a/results/source_verification.csv` records actual artifact_url/source_path, branch, commit, readme/build evidence, and a truthful source status.",
        ],
        "build_smoke": [
            "Stop after `.r2a/results/build_smoke.csv` records command, exit_code, duration_sec, component, notes, and PASS/FAIL/BLOCKED build status.",
        ],
        "runtime_smoke": [
            "Stop after `.r2a/results/runtime_smoke.csv` records command, exit_code, duration_sec, component, evidence_source, notes, and PASS/FAIL/BLOCKED runtime status.",
        ],
        "project_tests": [
            "Stop after `.r2a/results/project_tests.csv` records the discovered project test command or SKIPPED_WITH_REASON with command/test_scope/log evidence.",
        ],
        "input_contract": [
            "Stop after `.r2a/results/input_contract_verification.csv` covers dataset, query, ground_truth, metric, command/current status, evidence_source, and NEEDS_INPUT for unavailable official inputs.",
        ],
        "reduced_metrics": [
            f"Stop after `.r2a/results/reduced_metrics.csv` contains measured official or paper-linked reduced metrics using required columns `{csv_header('reduced_metrics.csv')}` and metric/provenance fields.",
        ],
        "paper_alignment": [
            f"Stop after `.r2a/results/paper_alignment.csv` uses header `{csv_header('paper_alignment.csv')}` and match_status values only from {', '.join(allowed_values_for_csv('paper_alignment.csv', 'match_status'))}.",
        ],
        "baseline_comparison": [
            f"Stop after `.r2a/results/baseline_comparison.csv` records same-input baseline evidence using required columns `{csv_header('baseline_comparison.csv')}` or explains NOT_RUN/NEEDS_INPUT in reproduction_status.csv.",
        ],
        "command_manifest": [
            f"Stop after `.r2a/results/command_manifest.csv` maps each command_id to command, exit_code, duration_sec, log/artifact path or hash, and input provenance using `{csv_header('command_manifest.csv')}`.",
        ],
        "reproduction_status": [
            f"Stop after `.r2a/results/reproduction_status.csv` records status, reason, evidence_source, and next_action using `{csv_header('reproduction_status.csv')}`.",
        ],
    }
    return conditions.get(category, [])


def _compile_task_allow_network(
    task: dict[str, Any],
    hints: dict[str, Any],
    *,
    network_authorized: bool,
    allowed_network_scope: list[str],
) -> bool:
    raw_request = bool(task.get("allow_network") or hints.get("requires_network"))
    requested_scope = _scope_list(
        hints.get("requested_network_scope")
        or hints.get("requested_network_scopes")
        or hints.get("network_scope")
    )
    if raw_request and not requested_scope:
        requested_scope = [DEFAULT_NETWORK_SCOPE]
    if not raw_request:
        return False
    return bool(network_authorized and requested_scope and all(scope in allowed_network_scope for scope in requested_scope))


def _compile_network_authorized(authorization: dict[str, Any], value: bool | None) -> bool:
    if value is not None:
        return bool(value)
    return bool(authorization.get("network_authorized"))


def _compile_allowed_network_scope(authorization: dict[str, Any], value: list[str] | str | None) -> list[str]:
    scope = _scope_list(value if value is not None else authorization.get("allowed_network_scope"))
    return scope if _compile_network_authorized(authorization, None) or scope else []


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _scope_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.replace(";", ",").replace("\n", ",").split(",") if item.strip()]
    return []


def _flatten_text(values: list[Any]) -> list[str]:
    flattened: list[str] = []
    for value in values:
        if isinstance(value, list):
            flattened.extend(_flatten_text(value))
        elif value is not None and str(value).strip():
            flattened.append(str(value))
    return flattened


def _dedupe(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return seen


def _json_clone(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _network_authorization_issues(output: PlannerOutput, result: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    network_tasks = [
        str(task.task_id or task.title or f"task_{index}")
        for index, task in enumerate(output.tasks, start=1)
        if bool(task.allow_network)
    ]
    if network_tasks and not _network_authorized_from_result(result):
        issues.append(
            "Planner output requested allow_network=true without network authorization: "
            + ", ".join(network_tasks)
            + "."
        )
    elif network_tasks and not _network_scope_from_result(result):
        issues.append(
            "Planner output requested allow_network=true without an allowed network scope: "
            + ", ".join(network_tasks)
            + "."
        )
    if network_tasks:
        return issues
    if not _network_authorized_from_result(result) and _claims_network_authorization_resolved(output.model_dump(mode="json")):
        issues.append("Planner output claimed network_authorization_resolved without network authorization.")
    return issues


def _planner_task_invariant_issues(output: PlannerOutput) -> list[str]:
    issues: list[str] = []
    for index, task in enumerate(output.tasks, start=1):
        task_id = str(task.task_id or f"task_{index}")
        if not task.stop_conditions:
            issues.append(f"PLANNER_OUTPUT.json schema validation failed: task {task_id} stop_conditions must not be empty.")
    return issues


def _network_authorized_from_result(result: dict[str, Any]) -> bool:
    authorization = result.get("network_authorization")
    authorized_from_dict = (
        bool(authorization.get("network_authorized"))
        if isinstance(authorization, dict) and authorization.get("network_authorized") is not None
        else False
    )
    return bool(
        authorized_from_dict
        or result.get("network_authorized")
        or result.get("allow_network")
        or result.get("user_approved_network")
        or result.get("user_approved_network_authorization")
    )


def _network_scope_from_result(result: dict[str, Any]) -> list[str]:
    authorization = result.get("network_authorization")
    value = authorization.get("allowed_network_scope") if isinstance(authorization, dict) else result.get("allowed_network_scope")
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.replace(";", ",").replace("\n", ",").split(",") if item.strip()]
    return []


def _claims_network_authorization_resolved(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_claims_network_authorization_resolved(item) for item in [*value.keys(), *value.values()])
    if isinstance(value, list):
        return any(_claims_network_authorization_resolved(item) for item in value)
    text = str(value or "").lower()
    return "network_authorization_resolved" in text or ("network authorization" in text and "resolved" in text)


def _unexpected_staging_files(repo: Path, staging: Path, allowed: set[Path]) -> list[str]:
    if not staging.exists():
        return []
    allowed_resolved = {path.resolve() for path in allowed}
    rejected = []
    for path in sorted(item for item in staging.rglob("*") if item.is_file()):
        if path.resolve() not in allowed_resolved:
            rejected.append(_repo_rel(repo, path))
    return rejected


def _failure_category(issues: list[str]) -> str:
    joined = " ".join(issues)
    if "schema validation" in joined:
        return "PLANNER_SCHEMA_VALIDATION_FAILED"
    if "Missing required" in joined:
        return "PLANNER_MISSING_REQUIRED_OUTPUT"
    if "outside the three allowed outputs" in joined:
        return "PLANNER_FORBIDDEN_WRITE"
    if "stale" in joined:
        return "PLANNER_STALE_OUTPUT"
    return "PLANNER_TRANSACTION_FAILED"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _repo_rel(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
