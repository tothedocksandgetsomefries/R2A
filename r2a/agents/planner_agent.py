from __future__ import annotations

import json
from pathlib import Path
import time

from r2a.core.paths import report_path, require_repo_dir
from r2a.core.planner_schema import PlannerOutput, enforce_system_contract_mode, planner_output_schema_json
from r2a.core.runtime_paths import repo_runtime_dir
from r2a.core.state import R2AState
from r2a.tools import openclaw_stage_runner
from r2a.tools.planner_input_builder import build_planner_input
from r2a.tools.planner_model_client import PlannerBackendNotConfigured, PlannerModelError, call_planner_model
from r2a.tools.planner_renderer import render_experiment_contract, render_planner_json, render_task_spec
from r2a.tools.planner_transaction import (
    compile_canonical_planner_output,
    commit_planner_transaction,
    planner_allowed_outputs,
    planner_staging_dir,
    validate_planner_transaction,
    write_planner_transaction_metadata,
)
from r2a.tools.prompt_loader import load_prompt
from r2a.tools.wsl import windows_to_wsl_path

REQUIRED_ENGINEER_EVIDENCE_OUTPUTS = [
    ".r2a/results/project_tests.csv",
    ".r2a/results/source_verification.csv",
    ".r2a/results/build_smoke.csv",
    ".r2a/results/runtime_smoke.csv",
    ".r2a/results/input_contract_verification.csv with dataset, query, ground_truth, metric, command, current status, and evidence source; use NEEDS_INPUT when official inputs are not yet available",
]


def run_planner_agent(state: R2AState, *, force: bool = True) -> R2AState:
    return generate_task_spec(state, force=force)


def generate_task_spec(state: R2AState, *, force: bool = True) -> R2AState:
    repo = require_repo_dir(state["repo_path"])
    iteration = int(state.get("iteration", 1))
    if iteration <= 1 and state.get("need_replan"):
        iteration = 2
    attempt_started_at = time.time()
    staging = planner_staging_dir(repo, iteration, attempt=1)
    staging.mkdir(parents=True, exist_ok=True)
    backend = str(state.get("planner_backend", "template") or "template")
    try:
        bundle = build_planner_input(state)
        validation_context = _planner_validation_context(bundle)
        allowed_scope = bundle.get("allowed_scope") if isinstance(bundle.get("allowed_scope"), dict) else None
        if backend == "openclaw":
            backend_result = _run_openclaw_planner(
                repo,
                state,
                bundle,
                staging,
                iteration=iteration,
            )
            if not backend_result.get("success"):
                transaction = validate_planner_transaction(
                    repo,
                    staging,
                    {**backend_result, **validation_context},
                    iteration=iteration,
                    attempt=1,
                    attempt_started_at=attempt_started_at,
                )
                write_planner_transaction_metadata(repo, transaction)
                return _planner_stage_failure_state(state, transaction, list(state.get("warnings", [])))
            if missing_outputs := _missing_openclaw_file_write_outputs(staging):
                transaction = validate_planner_transaction(
                    repo,
                    staging,
                    {
                        **backend_result,
                        **validation_context,
                        "success": False,
                        "failure_reason": "OpenClaw Planner file-write mode did not create required staging outputs: "
                        + ", ".join(missing_outputs),
                    },
                    iteration=iteration,
                    attempt=1,
                    attempt_started_at=attempt_started_at,
                )
                write_planner_transaction_metadata(repo, transaction)
                return _planner_stage_failure_state(state, transaction, list(state.get("warnings", [])))
            # Compile raw model output, then ENFORCE system contract_mode.
            planner_output = _ensure_required_engineer_outputs(
                enforce_system_contract_mode(
                    _compile_planner_output(
                        _load_openclaw_planner_output(staging / "PLANNER_OUTPUT.json"),
                        validation_context,
                    ),
                    allowed_scope,
                )
            )
        else:
            model_payload = call_planner_model(
                bundle,
                backend=backend,
                timeout=int(state.get("codex_stage_timeout", state.get("timeout", 300))),  # Default 300s for complex planner prompts
            )
            backend_result = {"success": True, "planner_backend": backend}
            # Compile raw model output, then ENFORCE system contract_mode.
            planner_output = _ensure_required_engineer_outputs(
                enforce_system_contract_mode(
                    _compile_planner_output(model_payload, validation_context),
                    allowed_scope,
                )
            )
        _write_staging(staging, planner_output)
        transaction = validate_planner_transaction(
            repo,
            staging,
            {**backend_result, **validation_context, "success": True, "planner_backend": backend},
            iteration=iteration,
            attempt=1,
            attempt_started_at=attempt_started_at,
        )
        if transaction["validation_status"] != "PASS":
            write_planner_transaction_metadata(repo, transaction)
            return _planner_stage_failure_state(state, transaction, list(state.get("warnings", [])))
        transaction = commit_planner_transaction(repo, staging, transaction)
        write_planner_transaction_metadata(repo, transaction)
        return {
            **state,
            "task_spec_path": str(report_path(repo, "task")),
            "latest_task_spec_path": str(report_path(repo, "task")),
            "experiment_contract_path": str(report_path(repo, "experiment_contract")),
            "latest_experiment_contract_path": str(report_path(repo, "experiment_contract")),
            "planner_output_path": str(report_path(repo, "planner_output")),
            "latest_planner_output_path": str(report_path(repo, "planner_output")),
            # max_evidence_level_allowed: Planner 允许尝试的最高等级，不是当前已达到的等级
            # reproduction_level 只能由实际 evidence 推断，Planner 不得覆盖
            "max_evidence_level_allowed": planner_output.max_evidence_level_allowed,
            "need_replan": False,
            "approval_ready": True,
            "planner_status": "success",
            "planner_schema_version": planner_output.schema_version,
            "planning_mode": planner_output.planning_mode,
            "iteration_strategy": planner_output.iteration_strategy,
            "contract_mode": planner_output.contract_mode,
            "planner_transaction": transaction,
            "metadata": {
                **dict(state.get("metadata", {}) or {}),
                "planner_v2": {
                    "schema_version": planner_output.schema_version,
                    "planning_mode": planner_output.planning_mode,
                    "iteration_strategy": planner_output.iteration_strategy,
                    "contract_mode": planner_output.contract_mode,
                },
            },
        }
    except Exception as exc:
        diagnostic_path = _write_failure_diagnostic(repo, iteration, backend, exc)
        transaction = {
            "stage": "planner",
            "iteration": iteration,
            "attempt": 1,
            "staging_dir": str(staging),
            "committed": False,
            "validation_status": "FAIL",
            "failure_category": _failure_category(exc),
            "execution_status": _failure_category(exc),
            "committed_files": [],
            "issues": [f"{type(exc).__name__}: {exc}"],
            "diagnostic": {
                "planner_backend": backend,
                "planner_status": "failed",
                "planner_schema_version": "",
                "planner_validation_passed": False,
                "planner_committed": False,
                "approval_passed": False,
                "failure_category": _failure_category(exc),
                "failure_reason": f"{type(exc).__name__}: {exc}",
                "diagnostics_path": str(diagnostic_path),
            },
        }
        write_planner_transaction_metadata(repo, transaction)
        return _planner_stage_failure_state(state, transaction, list(state.get("warnings", [])))


def _write_staging(staging: Path, planner_output: PlannerOutput) -> None:
    (staging / "PLANNER_OUTPUT.json").write_text(render_planner_json(planner_output), encoding="utf-8")
    (staging / "TASK_SPEC.md").write_text(render_task_spec(planner_output), encoding="utf-8")
    (staging / "EXPERIMENT_CONTRACT.md").write_text(render_experiment_contract(planner_output), encoding="utf-8")


def _load_openclaw_planner_output(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("PLANNER_OUTPUT.json must be a JSON object")
    return _normalize_openclaw_planner_output(data)


def _compile_planner_output(raw_output: dict | PlannerOutput, validation_context: dict) -> PlannerOutput:
    return compile_canonical_planner_output(
        raw_output,
        network_authorization=validation_context.get("network_authorization"),
        network_authorized=bool(validation_context.get("network_authorized")),
        allowed_network_scope=validation_context.get("allowed_network_scope"),
    )


def _normalize_openclaw_planner_output(data: dict) -> dict:
    normalized = dict(data)
    tasks = normalized.get("tasks")
    if not isinstance(tasks, list):
        return normalized
    updated_tasks = []
    for task in tasks:
        if not isinstance(task, dict):
            updated_tasks.append(task)
            continue
        item = dict(task)
        if item.get("depends_on") is None:
            item["depends_on"] = []
        if item.get("allowed_write_paths") is None:
            item["allowed_write_paths"] = []
        updated_tasks.append(item)
    normalized["tasks"] = updated_tasks
    return normalized


def _run_openclaw_planner(
    repo: Path,
    state: R2AState,
    bundle: dict,
    staging: Path,
    *,
    iteration: int,
) -> dict:
    input_path = _write_openclaw_planner_input(repo, bundle, staging, iteration=iteration)
    planner_provider, planner_model = _resolve_planner_openclaw_config(state)
    planner_config = openclaw_stage_runner.openclaw_stage_model_config_from_state(state, "planner")
    result = openclaw_stage_runner.run_openclaw_stage(
        repo,
        "planner",
        input_path,
        planner_allowed_outputs(repo, staging),
        session_key=_openclaw_planner_session_key(state, iteration),
        iteration=iteration,
        timeout=int(state.get("codex_stage_timeout", state.get("timeout", 300))),  # Default 300s for complex planner prompts
        openclaw_executable_path=state.get("openclaw_executable_path"),
        openclaw_config_path=state.get("openclaw_config_path"),
        wsl_distro=str(state.get("wsl_distro", "Ubuntu") or "Ubuntu"),
        provider=planner_provider,
        model=planner_model,
        runner=planner_config.get("runner") or state.get("openclaw_runner"),
        agent=planner_config.get("agent") or state.get("openclaw_agent"),
    )
    return {
        **result,
        "planner_backend": "openclaw",
        "prompt_file_path": str(input_path),
        "prompt_size_bytes": input_path.stat().st_size if input_path.exists() else 0,
    }


def _resolve_planner_openclaw_config(state: dict) -> tuple[str, str]:
    config = openclaw_stage_runner.openclaw_stage_model_config_from_state(state, "planner")
    provider = str(state.get("planner_provider", "") or config.get("provider", "") or "")
    model = str(state.get("planner_model", "") or config.get("model", "") or "")
    return provider, model


def _missing_openclaw_file_write_outputs(staging: Path) -> list[str]:
    missing: list[str] = []
    for filename in ("PLANNER_OUTPUT.json", "TASK_SPEC.md", "EXPERIMENT_CONTRACT.md"):
        path = staging / filename
        if not path.exists() or path.stat().st_size == 0:
            missing.append(filename)
    return missing


def _planner_validation_context(bundle: dict) -> dict:
    authorization = bundle.get("network_authorization")
    authorization = authorization if isinstance(authorization, dict) else {}
    allowed_scope = bundle.get("allowed_network_scope") or authorization.get("allowed_network_scope") or []
    if not isinstance(allowed_scope, list):
        allowed_scope = [str(allowed_scope)] if str(allowed_scope or "").strip() else []
    return {
        "network_authorization": authorization,
        "network_authorized": bool(bundle.get("network_authorized") or authorization.get("network_authorized")),
        "allowed_network_scope": allowed_scope,
    }

def _write_openclaw_planner_input(repo: Path, bundle: dict, staging: Path, *, iteration: int) -> Path:
    prompt_dir = repo_runtime_dir(repo) / "planner" / f"iter_{int(iteration):03d}"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    input_path = prompt_dir / "OPENCLAW_INPUT.md"
    try:
        planner_prompt = _openclaw_file_write_planner_prompt(load_prompt("planner_v2"))
    except FileNotFoundError:
        planner_prompt = "# R2A Planner V2\nWrite a valid PlannerOutput JSON object to PLANNER_OUTPUT.json."
    output_paths = {
        "planner_output": staging / "PLANNER_OUTPUT.json",
        "task_spec": staging / "TASK_SPEC.md",
        "experiment_contract": staging / "EXPERIMENT_CONTRACT.md",
    }
    input_path.write_text(
        "# R2A OpenClaw Planner Input\n\n"
        "Read this file and create one structured Planner file-write candidate exactly within the allowed staging directory.\n\n"
        "This is OpenClaw Planner file-write mode. The generic Planner V2 JSON-only instructions have been adapted below: "
        "`PLANNER_OUTPUT.json` is the JSON output, while stdout is only a short status channel.\n\n"
        "## Allowed Writes\n\n"
        f"- `{windows_to_wsl_path(output_paths['planner_output'])}`\n"
        f"- `{windows_to_wsl_path(output_paths['task_spec'])}`\n"
        f"- `{windows_to_wsl_path(output_paths['experiment_contract'])}`\n"
        "\n"
        "Do not write any other file. Do not modify the repository outside these three Planner staging outputs.\n"
        "Do not write to `.r2a/results`, source files, tests, config, Gateway, or OpenClaw settings.\n\n"
        "## Required Output Rules\n\n"
        "1. Write `PLANNER_OUTPUT.json` as one valid JSON object conforming to the existing PlannerOutput schema.\n"
        "2. Write `TASK_SPEC.md` as a concise Markdown rendering derived from `PLANNER_OUTPUT.json`; include `PLANNER_OUTPUT.json` as the source of truth.\n"
        "3. Write `EXPERIMENT_CONTRACT.md` as a concise Markdown contract derived from `PLANNER_OUTPUT.json`; include `PLANNER_OUTPUT.json` as the source of truth.\n"
        "4. **CRITICAL: `contract_mode` is determined by system, NOT by model.** Use the value from `bundle.allowed_scope.contract_mode`. Do NOT override it.\n"
        "5. **CRITICAL: `max_evidence_level_allowed` is determined by system, NOT by model.** Use the value from `bundle.allowed_scope.max_target_level`. Do NOT override it.\n"
        "6. `bundle.allowed_scope` is READ-ONLY system configuration. Planner MUST NOT modify, upgrade, or downgrade contract_mode.\n"
        "7. Do not authorize full benchmark, Docker escalation, system installs, destructive actions, or large downloads without explicit approval in the input bundle.\n"
        "8. Every task must have non-empty `actions` array. Other fields (objective, title, expected_outputs, etc.) are optional.\n"
        "9. `planning_mode` must be exactly `initial` or `iterative_progress`; `iteration_strategy` must be exactly `FIX_AND_PROGRESS`, `PROGRESS_ONLY`, or `BLOCKED_OR_NEEDS_APPROVAL`.\n\n"
        "## Canonical PlannerOutput Contract\n\n"
        "`PLANNER_OUTPUT.json` is the canonical machine-readable PlannerOutput contract, not a free-form plan.\n"
        "`TASK_SPEC.md` and `EXPERIMENT_CONTRACT.md` may contain natural-language explanations, but they must be derived from `PLANNER_OUTPUT.json`.\n"
        "Never add root fields outside the supplied PlannerOutput schema. Use `iteration`, not `iteration_number`.\n"
        "For the current schema, forbidden root fields include `iteration_number`, `target_reproduction_level`, `paper_info`, "
        "`source_info`, `source_inspection_summary`, `evidence`, `expected_outputs`, and `next_steps`.\n"
        "If you want to include paper/source summaries, expected output explanations, or next-step prose, write them in the Markdown files, not in `PLANNER_OUTPUT.json`.\n"
        "`evidence_gaps` items must use only `claim`, `source`, `status`, and optional `notes`. "
        "`tasks[].actions` must be `string[]`, not object arrays.\n"
        "If `PLANNER_OUTPUT.json` does not conform to the schema, the transaction will not commit and Engineer will not run.\n\n"
        "## Missing Previous Result Artifacts\n\n"
        "Previous iteration result artifacts can be absent. If `.r2a/results/reduced_metrics.csv`, "
        "`.r2a/results/command_manifest.csv`, or `.r2a/results/paper_alignment.csv` is absent or the bundle marks it missing, "
        "do not call tools to read that path. Treat the absent file as missing evidence / missing artifact, record the gap in "
        "`PLANNER_OUTPUT.json`, and plan Engineer work to create the missing file when it is in scope. Missing previous results "
        "must not make Planner fail by itself.\n\n"
        "After writing all three staging files, stdout must contain only a short raw JSON status such as `{\"status\":\"ok\"}`. "
        "If you cannot complete the three required writes, return a short failure JSON such as "
        "`{\"status\":\"failed\",\"reason\":\"...\"}` and include a concrete reason in stdout or stderr; do not exit silently.\n\n"
        "## Planner Core Rules Adapted For File-Write Mode\n\n"
        f"{planner_prompt}\n\n"
        "## Existing PlannerOutput JSON Schema\n\n"
        "```json\n"
        f"{json.dumps(planner_output_schema_json(), indent=2, ensure_ascii=False)}\n"
        "```\n\n"
        "## Planner Input Bundle\n\n"
        "```json\n"
        f"{json.dumps(bundle, indent=2, ensure_ascii=False)}\n"
        "```\n",
        encoding="utf-8",
    )
    return input_path


def _openclaw_file_write_planner_prompt(prompt: str) -> str:
    """Adapt the generic Planner V2 prompt so OpenClaw writes staging files."""
    adapted: list[str] = []
    skip_json_stdout_tail = False
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped == "Return JSON only.":
            skip_json_stdout_tail = True
            continue
        if skip_json_stdout_tail:
            continue
        if stripped in {"Do not call tools.", "Do not write files."}:
            continue
        if stripped == "Return exactly one JSON object matching the supplied schema.":
            adapted.append("Write `PLANNER_OUTPUT.json` as exactly one JSON object matching the supplied schema.")
            continue
        adapted.append(line)
    return "\n".join(adapted).strip()


def _openclaw_planner_session_key(state: R2AState, iteration: int) -> str:
    run_id = str(state.get("run_id", "run")).replace(":", "-").replace("/", "-").replace("\\", "-")
    return f"r2a-planner-{run_id}-{int(iteration)}-{int(time.time())}"


def _ensure_required_engineer_outputs(planner_output: PlannerOutput) -> PlannerOutput:
    data = planner_output.model_dump()
    if not data["tasks"]:
        return planner_output
    outputs = list(data["tasks"][0].get("expected_outputs", []))
    for required in REQUIRED_ENGINEER_EVIDENCE_OUTPUTS:
        if required not in outputs:
            outputs.append(required)
    data["tasks"][0]["expected_outputs"] = outputs
    allowed = list(data["tasks"][0].get("allowed_write_paths", []))
    if ".r2a/results/**" not in allowed:
        allowed.append(".r2a/results/**")
    data["tasks"][0]["allowed_write_paths"] = allowed
    note = "Planner contract requires project_tests, source_verification, build_smoke, runtime_smoke, and input_contract_verification evidence outputs."
    if note not in data.get("planner_notes", []):
        data.setdefault("planner_notes", []).append(note)
    return PlannerOutput.model_validate(data)


def _write_failure_diagnostic(repo: Path, iteration: int, backend: str, exc: Exception) -> Path:
    diagnostics_dir = repo_runtime_dir(repo) / "planner" / f"iter_{iteration:03d}"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    path = diagnostics_dir / "failure.json"
    path.write_text(
        json.dumps(
            {
                "planner_backend": backend,
                "failure_type": type(exc).__name__,
                "failure_message": str(exc),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _failure_category(exc: Exception) -> str:
    if isinstance(exc, PlannerBackendNotConfigured):
        return "PLANNER_BACKEND_NOT_CONFIGURED"
    if isinstance(exc, PlannerModelError):
        return "PLANNER_MODEL_FAILURE"
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return "PLANNER_SCHEMA_VALIDATION_FAILED"
    return "PLANNER_TRANSACTION_FAILED"


def _planner_stage_failure_state(state: R2AState, transaction: dict, warnings: list[str]) -> R2AState:
    failure_category = str(transaction.get("failure_category") or "PLANNER_TRANSACTION_FAILED")
    message = (
        "Planner V2 failed before a trusted work package could be committed. "
        f"failure_category={failure_category}; diagnostics={transaction.get('diagnostic', {}).get('diagnostics_path', '')}."
    )
    return {
        **state,
        "stopped": True,
        "approved": False,
        "auto_approve": False,
        "need_replan": False,
        "reviewer_verdict": "NEEDS_FIX",
        "manager_status": state.get("manager_status", ""),
        "manager_executed": False,
        "reviewer_executed": False,
        "approval_ready": False,
        "planner_status": "failed",
        "loop_status": "planner_failed",
        "failed_stage": "planner",
        "stop_reason": failure_category,
        "errors": [*state.get("errors", []), message],
        "warnings": warnings,
        "planner_transaction": transaction,
        "metadata": {
            **dict(state.get("metadata", {}) or {}),
            "planner_stage_failure": {
                "failure_category": failure_category,
                "execution_status": transaction.get("execution_status", failure_category),
                "planner_transaction": transaction,
            },
        },
    }
