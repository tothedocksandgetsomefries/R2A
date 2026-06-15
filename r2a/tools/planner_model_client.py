from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError

from r2a.core.planner_schema import PlannerOutput, planner_output_schema_json
from r2a.core.runtime_paths import repo_runtime_dir
from r2a.tools.csv_schemas import allowed_values_for_csv, csv_header
from r2a.tools.planner_transaction import compile_canonical_planner_output
from r2a.tools.process_tree import run_command_with_timeout
from r2a.tools.prompt_loader import load_prompt


# Retry configuration for backend calls
MAX_RETRIES = 2  # Maximum number of retries for transient failures
RETRY_DELAY_SECONDS = 2.0  # Initial delay before retry
RETRY_BACKOFF_FACTOR = 1.5  # Exponential backoff factor


class PlannerModelError(RuntimeError):
    pass


class PlannerBackendNotConfigured(PlannerModelError):
    pass


class PlannerBackendTransientError(PlannerModelError):
    """Transient backend error that may be retried."""
    pass


class PlannerModelConfig:
    def __init__(
        self,
        *,
        backend: str,
        provider: str = "",
        model: str = "",
        command: str = "",
        endpoint: str = "",
        repo_path: str = "",
    ) -> None:
        self.backend = (backend or "template").strip().lower()
        self.provider = provider.strip()
        self.model = model.strip()
        self.command = command.strip()
        self.endpoint = endpoint.strip()
        self.repo_path = repo_path


def call_planner_model(bundle: dict[str, Any], *, backend: str = "template", timeout: int = 300) -> dict[str, Any]:
    data, _meta = call_planner_model_with_diagnostics(bundle, backend=backend, timeout=timeout)
    return data


def generate_planner_json(
    *,
    prompt: str,
    schema: dict[str, Any],
    config: PlannerModelConfig,
    timeout_seconds: int,
) -> str:
    backend = config.backend
    if backend in {"template", "mock"}:
        raise PlannerModelError("template backend returns structured JSON directly; use call_planner_model for template tests")
    if backend in {"ccr", "ccr_text"}:
        return _generate_ccr_text(prompt, config=config, timeout=timeout_seconds)
    if backend == "command":
        if not config.command:
            raise PlannerBackendNotConfigured("PLANNER_BACKEND_NOT_CONFIGURED: command backend requires R2A_PLANNER_COMMAND")
        return _generate_command_text(config.command, prompt, timeout=timeout_seconds, repo_path=config.repo_path)
    if backend in {"openai_compatible", "anthropic"}:
        if not config.endpoint and not config.command:
            raise PlannerBackendNotConfigured(
                f"PLANNER_BACKEND_NOT_CONFIGURED: {backend} requires an endpoint or R2A_PLANNER_COMMAND"
            )
        if config.command:
            return _generate_command_text(config.command, prompt, timeout=timeout_seconds, repo_path=config.repo_path)
        raise PlannerBackendNotConfigured(f"PLANNER_BACKEND_NOT_CONFIGURED: {backend} HTTP adapter is not configured")
    if backend in {"claude", "codex"}:
        command = config.command or os.environ.get("R2A_PLANNER_COMMAND", "").strip()
        if command:
            return _generate_command_text(command, prompt, timeout=timeout_seconds, repo_path=config.repo_path)
        raise PlannerBackendNotConfigured(
            f"PLANNER_BACKEND_NOT_CONFIGURED: backend `{backend}` requires R2A_PLANNER_COMMAND, "
            "ccr_text, command, or explicit template/mock selection"
        )
    raise PlannerBackendNotConfigured(f"PLANNER_BACKEND_NOT_CONFIGURED: unsupported planner backend `{backend}`")


def call_planner_model_with_diagnostics(
    bundle: dict[str, Any], *, backend: str = "template", timeout: int = 300
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Call planner model with retry/backoff for transient failures.

    Retries up to MAX_RETRIES times for transient backend errors (timeout, connection issues).
    Does not retry for validation errors or configuration errors.
    """
    started_backend = (backend or "template").strip().lower()
    if started_backend in {"template", "mock"}:
        data = _template_planner_output(bundle)
        return data, {
            "provider": "template",
            "model": "template",
            "backend": started_backend,
            "gateway": "",
            "duration": 0.0,
            "json_parse_passed": True,
            "json_repair_used": False,
            "schema_passed": True,
            "diagnostic_path": "",
        }

    prompt = _build_prompt(bundle)
    config = PlannerModelConfig(
        backend=started_backend,
        provider=os.environ.get("R2A_PLANNER_PROVIDER", ""),
        model=os.environ.get("R2A_PLANNER_MODEL", ""),
        command=os.environ.get("R2A_PLANNER_COMMAND", ""),
        endpoint=os.environ.get("R2A_PLANNER_CCR_URL", ""),
        repo_path=str(bundle.get("repo_path", "") or ""),
    )

    # Retry loop with exponential backoff
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            started_at = time.time()
            text = generate_planner_json(
                prompt=prompt,
                schema=planner_output_schema_json(),
                config=config,
                timeout_seconds=timeout,
            )
            data, parse_meta = parse_planner_json_with_metadata(text)
            data = _compile_model_planner_payload(data, bundle).model_dump(mode="json")
            duration = time.time() - started_at
            diagnostic_path = _latest_diagnostic_path(bundle, started_backend)
            provider = config.provider or ("ccr" if started_backend in {"ccr", "ccr_text"} else started_backend)
            model = config.model or (_default_ccr_model() if started_backend in {"ccr", "ccr_text"} else "")
            gateway = config.endpoint or (_default_ccr_gateway() if started_backend in {"ccr", "ccr_text"} else "")

            # Success - return with diagnostic info
            meta = {
                "provider": provider,
                "model": model,
                "backend": started_backend,
                "gateway": gateway,
                "duration": duration,
                **parse_meta,
                "schema_passed": True,
                "diagnostic_path": diagnostic_path,
            }
            if attempt > 0:
                meta["retries"] = attempt
            return data, meta

        except (TimeoutError, subprocess.TimeoutExpired, ConnectionError, URLError, HTTPError) as e:
            # Transient error - retry with backoff
            last_error = e
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAY_SECONDS * (RETRY_BACKOFF_FACTOR ** attempt)
                time.sleep(delay)
                continue
            # Max retries exceeded - raise as backend failure
            raise PlannerBackendTransientError(
                f"Backend call failed after {MAX_RETRIES + 1} attempts: {type(e).__name__}: {e}"
            ) from e

        except (PlannerModelError, PlannerBackendNotConfigured):
            # Configuration/validation errors - don't retry
            raise

        except Exception as e:
            # Unexpected error - check if it's transient
            error_str = str(e).lower()
            if any(token in error_str for token in ["timeout", "connection", "network", "gateway", "unavailable"]):
                last_error = e
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAY_SECONDS * (RETRY_BACKOFF_FACTOR ** attempt)
                    time.sleep(delay)
                    continue
                raise PlannerBackendTransientError(
                    f"Backend call failed after {MAX_RETRIES + 1} attempts: {type(e).__name__}: {e}"
                ) from e
            # Non-transient error - don't retry
            raise

    # Should not reach here, but just in case
    raise PlannerBackendTransientError(
        f"Backend call failed after {MAX_RETRIES + 1} attempts: {last_error}"
    )


def parse_planner_json(text: str) -> dict[str, Any]:
    data, _meta = parse_planner_json_with_metadata(text)
    return data


def parse_planner_json_with_metadata(text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    candidates = [text.strip(), _extract_fenced_json(text), _extract_braced_json(text)]
    last_error: Exception | None = None
    for candidate in candidates:
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data, {"json_parse_passed": True, "json_repair_used": False}
        except json.JSONDecodeError as exc:
            last_error = exc
    repaired = _repair_json(text)
    if repaired != text:
        try:
            data = json.loads(repaired)
            if isinstance(data, dict):
                return data, {"json_parse_passed": True, "json_repair_used": True}
        except json.JSONDecodeError as exc:
            last_error = exc
    raise PlannerModelError(f"Planner backend did not return a JSON object: {last_error}")


def _compile_model_planner_payload(data: dict[str, Any], bundle: dict[str, Any]) -> PlannerOutput:
    authorization = bundle.get("network_authorization")
    return compile_canonical_planner_output(
        data,
        network_authorization=authorization if isinstance(authorization, dict) else None,
        network_authorized=bool(bundle.get("network_authorized")),
        allowed_network_scope=bundle.get("allowed_network_scope"),
    )


def _build_prompt(bundle: dict[str, Any]) -> str:
    try:
        prompt = load_prompt("planner_v2")
    except FileNotFoundError:
        prompt = "# R2A Planner V2\nReturn JSON only."
    return (
        prompt
        + "\n\n## JSON Schema\n\n```json\n"
        + json.dumps(planner_output_schema_json(), indent=2, ensure_ascii=False)
        + "\n```\n\n## Input Bundle\n\n```json\n"
        + json.dumps(bundle, indent=2, ensure_ascii=False)
        + "\n```\n"
    )


def _generate_command_text(command: str, prompt: str, *, timeout: int, repo_path: str) -> str:
    diagnostics_dir = repo_runtime_dir(repo_path or ".") / "planner"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    diagnostic_path = diagnostics_dir / "planner_backend_stdout.json"

    # Check if command uses {prompt} placeholder (argument mode)
    use_argument_mode = "{prompt}" in command

    if use_argument_mode:
        # Argument mode: create a temporary batch file to avoid Windows quoting issues
        quoted_prompt = _quote_prompt_for_shell(prompt)
        final_command = command.replace("{prompt}", quoted_prompt)
        input_text = ""  # No stdin input in argument mode
    else:
        # Stdin mode: original behavior
        final_command = command
        input_text = prompt

    # Build command execution approach
    if os.name == "nt" and use_argument_mode:
        # Windows argument mode: use temporary batch file to avoid cmd quoting issues
        batch_file = diagnostics_dir / "planner_command.bat"
        batch_file.write_text(f"@echo off\n{final_command}", encoding="utf-8")
        command_list = ["cmd", "/c", str(batch_file)]
    elif os.name == "nt":
        # Windows stdin mode: original behavior
        command_list = ["cmd", "/c", final_command]
    else:
        command_list = ["sh", "-c", final_command]

    try:
        result = run_command_with_timeout(
            command_list,
            cwd=repo_path or ".",
            input_text=input_text,
            timeout=timeout,
        )
    except Exception as exc:
        raise PlannerModelError(f"Planner backend command failed: {type(exc).__name__}: {exc}") from exc

    stdout = result.stdout or ""
    stderr = result.stderr or ""
    returncode = result.returncode

    # Handle timeout/cancel cases
    if result.timed_out:
        stderr += f"\nTimeoutExpired: planner backend command exceeded {timeout} seconds."

    diagnostic_path.write_text(
        json.dumps(
            {
                "returncode": returncode,
                "stdout_excerpt": stdout[-8000:],
                "stderr_excerpt": stderr[-4000:],
                "timed_out": result.timed_out,
                "argument_mode": use_argument_mode,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if returncode != 0:
        raise PlannerModelError(f"Planner backend command returned {returncode}; diagnostics={diagnostic_path}")
    return stdout


def _quote_prompt_for_shell(prompt: str) -> str:
    """Quote prompt safely for shell argument.

    Uses double quotes with minimal escaping.
    For Windows batch files, double quotes are preserved literally.
    """
    # Use double quotes, escape internal double quotes by doubling them
    escaped = prompt.replace('"', '""')
    return f'"{escaped}"'


def _generate_ccr_text(prompt: str, *, config: PlannerModelConfig, timeout: int) -> str:
    gateway = config.endpoint or _default_ccr_gateway()
    model = config.model or _default_ccr_model()
    provider = config.provider or "ccr"
    payload = {
        "model": model,
        "max_tokens": int(os.environ.get("R2A_PLANNER_MAX_TOKENS", "4096")),
        "temperature": float(os.environ.get("R2A_PLANNER_TEMPERATURE", "0.1")),
        "messages": [{"role": "user", "content": prompt}],
    }
    diagnostics_dir = repo_runtime_dir(config.repo_path or ".") / "planner"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    diagnostic_path = diagnostics_dir / "ccr_gateway_response.json"
    http_request = request.Request(
        gateway,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError) as exc:
        diagnostic_path.write_text(
            json.dumps({"provider": provider, "model": model, "gateway": gateway, "error": str(exc)}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        raise PlannerModelError(f"CCR gateway request failed: {exc}") from exc
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        diagnostic_path.write_text(raw[-12000:], encoding="utf-8")
        raise PlannerModelError(f"CCR gateway returned non-JSON envelope: {exc}") from exc
    text = _extract_gateway_text(envelope)
    diagnostic_path.write_text(
        json.dumps(
            {
                "provider": provider,
                "model": envelope.get("model", model),
                "gateway": gateway,
                "response_id": envelope.get("id", ""),
                "usage": envelope.get("usage", {}),
                "text_excerpt": text[:12000],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return text


def _default_ccr_gateway() -> str:
    return "http://127.0.0.1:3456/v1/messages"


def _default_ccr_model() -> str:
    return "deepseek-v4-flash"


def _latest_diagnostic_path(bundle: dict[str, Any], backend: str) -> str:
    diagnostics_dir = repo_runtime_dir(bundle.get("repo_path", ".") or ".") / "planner"
    if backend in {"ccr", "ccr_text"}:
        path = diagnostics_dir / "ccr_gateway_response.json"
    else:
        path = diagnostics_dir / "planner_backend_stdout.json"
    return str(path) if path.exists() else ""


def _extract_gateway_text(envelope: dict[str, Any]) -> str:
    content = envelope.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(part for part in parts if part)
    choices = envelope.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        return str(message.get("content", ""))
    return ""


def _template_planner_output(bundle: dict[str, Any]) -> dict[str, Any]:
    iteration = int(bundle.get("iteration", 1) or 1)
    mode = "initial" if iteration == 1 else "iterative_progress"
    feedback = bundle.get("structured_review_feedback") if isinstance(bundle.get("structured_review_feedback"), dict) else {}
    failure_categories = [str(item) for item in feedback.get("failure_categories", [])] if feedback else []
    active_blockers = [str(item) for item in feedback.get("active_blockers", [])] if feedback else []
    required_fixes = active_blockers or ([str(item) for item in feedback.get("required_fixes", [])] if feedback else [])
    recommended_scope = [str(item) for item in feedback.get("recommended_task_scope", [])] if feedback else []
    forbidden_next = [str(item) for item in feedback.get("forbidden_next_actions", [])] if feedback else []
    preserve = [str(item) for item in feedback.get("preserve_successful_steps", [])] if feedback else []
    verdict = str(feedback.get("verdict", "") or "").upper()
    manual_needed = _requires_manual_approval(bundle, feedback)
    network_authorized = _network_authorized(bundle)
    low_quality_paper = str(bundle.get("paper_quality", "")).upper() == "LOW_CONFIDENCE"
    blocker_category = _first_failure_category(failure_categories)
    if manual_needed:
        strategy = "BLOCKED_OR_NEEDS_APPROVAL"
    elif mode == "iterative_progress" and (required_fixes or failure_categories):
        strategy = "FIX_AND_PROGRESS"
    else:
        strategy = "PROGRESS_ONLY"
    contract_mode = "verification_only"
    if manual_needed:
        contract_mode = "verification_only"
    elif mode == "iterative_progress" and verdict == "INPUT_CONTRACT_READY":
        contract_mode = "official_reduced" if bundle.get("allow_official_dataset_download") else "verification_only"
    elif mode == "iterative_progress" and verdict in {"PASS_REDUCED_METHOD_ONLY", "PASS_REDUCED_ALIGNED", "PASS_REDUCED_COMPARISON"}:
        contract_mode = "official_reduced"
    allowed_scope = bundle.get("allowed_scope") if isinstance(bundle.get("allowed_scope"), dict) else {}
    forced_contract = str(allowed_scope.get("contract_mode", "") or "").strip()
    if forced_contract in {"verification_only", "smoke", "official_reduced", "full_benchmark"}:
        contract_mode = forced_contract
    max_level = str(allowed_scope.get("max_target_level", "") or "") or _template_max_level(contract_mode, verdict)
    blocking_issues = []
    placeholder_issue = _placeholder_input_issue(bundle)
    if manual_needed:
        blocking_issues.append(
            {
                "issue_id": "B001",
                "category": "NEEDS_MANUAL_APPROVAL",
                "description": "Requested next step requires budget, full benchmark, large download, Docker escalation, or user choice.",
                "evidence_source": "Planner input bundle policy flags and review feedback",
                "severity": "BLOCKING",
                "suggested_resolution": "Ask the user to approve the high-cost or unsafe action before planning execution.",
            }
        )
    elif mode == "iterative_progress" and (required_fixes or failure_categories):
        blocking_issues.append(
            {
                "issue_id": "B001",
                "category": blocker_category,
                "description": "; ".join(required_fixes[:3]) or "Reviewer reported a fixable blocker.",
                "evidence_source": "REVIEW_FEEDBACK.json",
                "severity": "BLOCKING",
                "suggested_resolution": "Engineer should resolve the blocker with the smallest safe artifact-only change or status evidence.",
            }
        )
    if placeholder_issue:
        contract_mode = "verification_only"
        max_level = "L2_input_contract_ready"
        blocking_issues.append(placeholder_issue)
    tasks = _template_tasks(
        mode,
        strategy,
        contract_mode,
        blocker_category,
        [*required_fixes, *recommended_scope, *forbidden_next],
        manual_needed,
        int(bundle.get("download_budget_gb", 0) or 0),
        low_quality_paper,
        verdict,
        bool(bundle.get("allow_external_baselines", False)),
        network_authorized,
    )
    user_hints = bundle.get("user_hints") if isinstance(bundle.get("user_hints"), dict) else {}
    user_hint_note = _user_hint_note(user_hints)
    data = {
        "schema_version": "2.0",
        "iteration": iteration,
        "planning_mode": mode,
        "iteration_strategy": strategy,
        "objective": str(bundle.get("goal") or "Create a bounded reproduction work package."),
        "contract_mode": contract_mode,
        "max_evidence_level_allowed": max_level,
        "current_status_summary": _status_summary(mode, strategy, contract_mode),
        "completed_capabilities": preserve,
        "blocking_issues": blocking_issues,
        "evidence_used": [
            {
                "claim": "Planner used bounded Paper/Review bundle excerpts only.",
                "source": "planner_input_builder",
                "status": "SUPPORTED",
                "notes": _paper_card_note(bundle) or "Full paper text/pages are not attached unless needed by evidence gaps.",
            }
        ]
        + (
            [
                {
                    "claim": "User Guidance was carried as optional user_provided_hint context.",
                    "source": "USER_HINTS.json",
                    "status": "INFERRED",
                    "notes": user_hint_note,
                }
            ]
            if user_hint_note
            else []
        ),
        "evidence_gaps": [
            *_paper_gaps(bundle),
            *_paper_quality_gaps(bundle),
            *_metric_gaps(bundle),
            *_placeholder_gaps(bundle),
        ],
        "tasks": tasks,
        "claim_restrictions": [
            "Do not claim full reproduction.",
            "Do not treat synthetic or verification-only outputs as paper reproduction.",
            "Final evidence level is decided by Manager/Reviewer, not Planner.",
        ],
        "manual_approval_points": [
            "Large downloads, full benchmark, long training, Docker escalation, system installs, or destructive actions."
        ]
        if manual_needed
        else [],
        "preserve_outputs": preserve,
        "planner_notes": [
            "Generated by provider-agnostic Planner V2 template backend.",
            *([user_hint_note] if user_hint_note else []),
        ],
    }
    if allowed_scope:
        data["planner_notes"].append(f"Allowed scope enforced by system: {allowed_scope}")
    PlannerOutput.model_validate(data)
    return data


def _user_hint_note(user_hints: dict[str, Any]) -> str:
    if not user_hints:
        return ""
    parts = [
        "User Guidance is optional user-provided context; do not treat it as verified paper evidence unless independently confirmed.",
    ]
    for label, key in (
        ("source_urls", "source_urls"),
        ("dataset_urls", "dataset_urls"),
        ("model_weight_urls", "model_weight_urls"),
        ("preferred_metrics", "preferred_metrics"),
        ("preferred_experiments", "preferred_experiments"),
    ):
        values = [str(value) for value in user_hints.get(key, []) or [] if str(value).strip()]
        if values:
            parts.append(f"{label}: {', '.join(values)}")
    return " ".join(parts) if len(parts) > 1 or user_hints.get("text") else ""


def _template_tasks(
    mode: str,
    strategy: str,
    contract_mode: str,
    blocker_category: str,
    required_fixes: list[str],
    manual_needed: bool,
    download_budget: int,
    low_quality_paper: bool = False,
    verdict: str = "",
    allow_external_baselines: bool = False,
    network_authorized: bool = False,
) -> list[dict[str, Any]]:
    if manual_needed:
        return [
            {
                "task_id": "T001",
                "title": "Request manual approval for blocked progress",
                "objective": "Stop before unsafe or high-cost execution and surface the required decision.",
                "rationale": "Planner must not fabricate progress when approval, data, or budget is missing.",
                "actions": ["Write a status-only plan for the missing approval/input decision."],
                "depends_on": [],
                "run_if": None,
                "expected_outputs": [".r2a/results/reproduction_status.csv with NEEDS_INPUT or FAIL"],
                "acceptance_criteria": ["No source, data, Docker, or benchmark execution is performed before approval."],
                "stop_conditions": ["Manual approval or required official input remains unavailable."],
                "allowed_write_paths": [".r2a/results/reproduction_status.csv", ".r2a/logs/**"],
                "allow_network": False,
                "allow_docker": False,
                "requires_manual_approval": True,
            }
        ]
    if mode == "initial" or low_quality_paper:
        return [
            {
                "task_id": "T001",
                "title": "Verify source, artifacts, datasets, and input contract",
                "objective": "Restrict work to source verification, artifact verification, dataset verification, and input discovery.",
                "rationale": "Paper artifacts are low-confidence or initial; do not turn uncertain paper fields into hard acceptance criteria.",
                "actions": [
                    "Inspect local repository and paper artifacts for official source, entry points, datasets, and metrics.",
                    "Run import/build/test smoke only when commands are clearly local and low cost.",
                    f"Record source verification, project health, and input contract gaps within a {download_budget}GB budget.",
                ],
                "depends_on": [],
                "run_if": None,
                "expected_outputs": [
                    ".r2a/results/project_tests.csv",
                    ".r2a/results/source_verification.csv",
                    ".r2a/results/build_smoke.csv",
                    ".r2a/results/runtime_smoke.csv",
                    ".r2a/results/input_contract_verification.csv with dataset, query, ground_truth, metric, command, current status, and evidence source; use NEEDS_INPUT for missing official inputs",
                ],
                "acceptance_criteria": [
                    "Official/paper-linked claims are supported by paper artifacts or marked as gaps.",
                    "Low-confidence paper metadata is not used as a hard reproduction acceptance criterion.",
                    "No full benchmark, large download, or unapproved Docker escalation is attempted.",
                ],
                "stop_conditions": [
                    "Official source cannot be confirmed.",
                    "Required data or weights need manual approval or exceed budget.",
                ],
                "allowed_write_paths": [".r2a/results/**", ".r2a/logs/**", ".r2a/experiments/**"],
                "allow_network": network_authorized,
                "allow_docker": False,
                "requires_manual_approval": False,
            }
        ]
    if strategy == "PROGRESS_ONLY" and verdict == "INPUT_CONTRACT_READY" and contract_mode == "official_reduced":
        return [_official_reduced_task(download_budget, network_authorized)]
    if strategy == "PROGRESS_ONLY" and verdict == "PASS_REDUCED_METHOD_ONLY":
        return [_paper_alignment_task()]
    if strategy == "PROGRESS_ONLY" and verdict == "PASS_REDUCED_ALIGNED":
        return [_baseline_comparison_task(allow_external_baselines, network_authorized)]
    tasks = [
        {
            "task_id": "T001",
            "title": "Fix current blocker",
            "objective": "Resolve the Reviewer-reported blocker without repeating still-valid successful work.",
            "rationale": f"Reviewer feedback identifies {blocker_category} as the immediate blocker.",
            "actions": required_fixes[:5] or ["Apply the smallest safe fix or record why the blocker cannot be fixed automatically."],
            "depends_on": [],
            "run_if": None,
            "expected_outputs": [".r2a/results/reproduction_status.csv", ".r2a/results/command_manifest.csv when commands run"],
            "acceptance_criteria": ["The blocker is resolved or truthfully reported as FAIL, NEEDS_INPUT, or NOT_RUN with evidence."],
            "stop_conditions": ["Fix requires algorithm rewrite, missing input, large download, or manual decision."],
            "allowed_write_paths": [".r2a/results/**", ".r2a/logs/**", ".r2a/experiments/**"],
            "allow_network": network_authorized
            and any("network" in item.lower() or "official_input" in item.lower() for item in required_fixes),
            "allow_docker": False,
            "requires_manual_approval": False,
        }
    ]
    if strategy == "FIX_AND_PROGRESS":
        tasks.append(
            {
                "task_id": "T002",
                "title": "Continue with bounded downstream progress",
                "objective": "After T001 succeeds, run the next low-cost smoke or input-contract step.",
                "rationale": "A fixable blocker should not consume the whole iteration when safe progress remains.",
                "actions": [
                    "Rerun the smallest relevant smoke/check command.",
                    f"If successful, locate the next experiment entry point or official reduced input contract within a {download_budget}GB budget.",
                ],
                "depends_on": ["T001"],
                "run_if": "Run only if T001 resolves the blocker without manual approval.",
                "expected_outputs": [
                    ".r2a/results/build_smoke.csv or runtime_smoke.csv when applicable",
                    ".r2a/results/input_contract_verification.csv for discovered reduced inputs",
                ],
                "acceptance_criteria": ["Downstream progress is low-cost, bounded, and does not overwrite successful prior evidence."],
                "stop_conditions": ["New step requires full benchmark, large download, Docker escalation, or unclear official inputs."],
                "allowed_write_paths": [".r2a/results/**", ".r2a/logs/**", ".r2a/experiments/**"],
                "allow_network": network_authorized and contract_mode == "official_reduced",
                "allow_docker": False,
                "requires_manual_approval": False,
            }
        )
    return tasks


def _template_max_level(contract_mode: str, verdict: str) -> str:
    if contract_mode != "official_reduced":
        return "L2_input_contract_ready"
    if verdict == "PASS_REDUCED_METHOD_ONLY":
        return "L4_reduced_paper_aligned"
    if verdict == "PASS_REDUCED_ALIGNED":
        return "L5_minimal_baseline_comparison"
    return "L3_official_reduced_run"


def _official_reduced_task(download_budget: int, network_authorized: bool = False) -> dict[str, Any]:
    return {
        "task_id": "T001",
        "title": "Run smallest official reduced method experiment",
        "objective": "Use the verified input contract to run the smallest official or paper-linked reduced method measurement.",
        "rationale": "Reviewer marked the input contract ready, so the next L0-L6 step is L3 official_reduced evidence.",
        "actions": [
            f"Use only verified official or paper-linked inputs within the {download_budget}GB budget.",
            "Run the smallest method command that measures at least one paper-supported metric.",
            "Record command provenance, input provenance, parameters, and measured metrics.",
        ],
        "depends_on": [],
        "run_if": None,
        "expected_outputs": [
            f".r2a/results/reduced_metrics.csv with header `{csv_header('reduced_metrics.csv')}` plus measured metric columns",
            f".r2a/results/command_manifest.csv with header `{csv_header('command_manifest.csv')}`",
            ".r2a/results/input_contract_verification.csv updated with PASS/NEEDS_INPUT rows for all required inputs",
        ],
        "acceptance_criteria": [
            "Metrics are measured from an official or paper-linked reduced input, not synthetic/demo data.",
            "Every reduced_metrics.csv command_id is present in command_manifest.csv.",
            "Missing official inputs are recorded as NEEDS_INPUT instead of fabricated.",
        ],
        "stop_conditions": [
            "Required official inputs are missing, empty, invalid, or exceed budget.",
            "The reduced command requires full benchmark scale or manual approval.",
        ],
        "allowed_write_paths": [".r2a/results/**", ".r2a/logs/**", ".r2a/experiments/**"],
        "allow_network": network_authorized,
        "allow_docker": False,
        "requires_manual_approval": False,
    }


def _paper_alignment_task() -> dict[str, Any]:
    header = csv_header("paper_alignment.csv")
    statuses = ", ".join(allowed_values_for_csv("paper_alignment.csv", "match_status"))
    return {
        "task_id": "T001",
        "title": "Map reduced metrics to paper settings",
        "objective": "Produce L4 paper-alignment evidence for the existing official reduced method run.",
        "rationale": "Reviewer marked reduced method metrics complete; the next L0-L6 step is paper alignment.",
        "actions": [
            "Compare reduced dataset, method, k, metric, parameters, runtime budget, hardware, and input source against the paper.",
            f"Write paper_alignment.csv using exactly this standard header: `{header}`.",
            f"Use only these schema values for match_status: {statuses}.",
            "Record evidence_source for every row and describe any item that cannot be aligned.",
        ],
        "depends_on": [],
        "run_if": None,
        "expected_outputs": [
            f".r2a/results/paper_alignment.csv with header `{header}`",
            ".r2a/results/reproduction_status.csv documenting any L4 gaps",
        ],
        "acceptance_criteria": [
            "paper_alignment.csv uses reduced_setting, not verified_setting, as the standard field.",
            "At least one row is MATCH or PARTIAL_MATCH when evidence supports L4.",
            "Known alignment gaps are explicit and do not inflate the final claim.",
        ],
        "stop_conditions": [
            "Reduced metrics are missing or stale.",
            "Paper setting evidence is unavailable for the claimed alignment.",
        ],
        "allowed_write_paths": [".r2a/results/**", ".r2a/logs/**"],
        "allow_network": False,
        "allow_docker": False,
        "requires_manual_approval": False,
    }


def _baseline_comparison_task(allow_external_baselines: bool, network_authorized: bool = False) -> dict[str, Any]:
    return {
        "task_id": "T001",
        "title": "Run one minimal same-input baseline comparison",
        "objective": "Attempt the smallest fair baseline comparison on the same reduced input, metric, and environment.",
        "rationale": "Reviewer marked reduced paper alignment complete; L5 requires a minimal baseline comparison when available.",
        "actions": [
            "Identify a local baseline already present in the official artifact or approved by configuration.",
            "Run at most one low-cost baseline command on the same reduced input and metric.",
            "Write baseline_comparison.csv with method, baseline_method, reduced_input_id, metric, environment, and budget_notes.",
            "If no fair baseline exists, write reproduction_status.csv with NOT_RUN or NEEDS_INPUT and a concrete reason.",
        ],
        "depends_on": [],
        "run_if": None,
        "expected_outputs": [
            f".r2a/results/baseline_comparison.csv with header `{csv_header('baseline_comparison.csv')}` when a fair baseline is available",
            f".r2a/results/command_manifest.csv with header `{csv_header('command_manifest.csv')}` when commands run",
            ".r2a/results/reproduction_status.csv documenting baseline gaps when no fair run is available",
        ],
        "acceptance_criteria": [
            "The baseline uses the same reduced input, metric, and environment as the reduced method run.",
            "External baselines are used only when explicitly allowed.",
            "No L6/full benchmark claim is made from this task.",
        ],
        "stop_conditions": [
            "No local or approved baseline can run fairly on the same reduced input.",
            "The baseline requires full-scale data, long training, or unapproved external artifacts.",
        ],
        "allowed_write_paths": [".r2a/results/**", ".r2a/logs/**", ".r2a/experiments/**"],
        "allow_network": bool(allow_external_baselines and network_authorized),
        "allow_docker": False,
        "requires_manual_approval": False,
    }


def _requires_manual_approval(bundle: dict[str, Any], feedback: dict[str, Any]) -> bool:
    decision = bundle.get("workflow_decision")
    if isinstance(decision, dict) and decision.get("kind") == "request_user_input":
        return True
    if _feedback_mentions_network_authorization(feedback) and not _network_authorized(bundle):
        return True
    text = json.dumps(feedback, ensure_ascii=False).lower()
    verdict = str(feedback.get("verdict", "") or "").upper()
    if verdict == "INPUT_CONTRACT_READY" and _target_needs_official_input(bundle) and not _official_input_authorized(bundle):
        return True
    if verdict in {"NEEDS_INPUT", "NEEDS_OFFICIAL_INPUT"} or "MISSING_ARTIFACT_OR_DATA" in feedback.get("failure_categories", []):
        if not _official_input_authorized(bundle):
            return True
    if any(token in text for token in ("full benchmark", "long training", "large download", "docker", "manual approval")):
        return True
    if verdict in {"NEEDS_INPUT", "NEEDS_INPUT_OR_BUDGET"}:
        return True
    return bool(bundle.get("allow_full_benchmark") is True and bundle.get("download_budget_gb", 0) <= 0)


def _network_authorized(bundle: dict[str, Any]) -> bool:
    authorization = bundle.get("network_authorization")
    authorized_from_dict = (
        bool(authorization.get("network_authorized"))
        if isinstance(authorization, dict) and authorization.get("network_authorized") is not None
        else False
    )
    return bool(
        authorized_from_dict
        or bundle.get("network_authorized")
        or bundle.get("allow_network")
        or bundle.get("user_approved_network")
        or bundle.get("user_approved_network_authorization")
    )


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


def _official_input_authorized(bundle: dict[str, Any]) -> bool:
    return bool(
        bundle.get("local_official_input_path")
        or bundle.get("user_approved_official_download")
        or bundle.get("user_approved_download")
        or (bundle.get("allow_official_dataset_download") and int(bundle.get("download_budget_gb", 0) or 0) > 0)
        or str(bundle.get("contract_mode", "")) in {"official_reduced"}
    )


def _target_needs_official_input(bundle: dict[str, Any]) -> bool:
    return str(bundle.get("target_reproduction_level", "")) not in {
        "",
        "L0_project_health",
        "L1_source_artifact_verified",
        "L2_input_contract_ready",
    }


def _first_failure_category(categories: list[str]) -> str:
    allowed = {
        "SAFE_BUILD_COMPATIBILITY",
        "TOOLCHAIN_OR_ENVIRONMENT",
        "MISSING_ARTIFACT_OR_DATA",
        "API_OR_ALGORITHM_SEMANTICS",
        "RESULT_MISMATCH",
        "SCHEMA_OR_REPORTING",
        "NEEDS_MANUAL_APPROVAL",
        "OTHER",
    }
    for category in categories:
        if category in allowed:
            return category
    return "OTHER"


def _status_summary(mode: str, strategy: str, contract_mode: str) -> str:
    if strategy == "BLOCKED_OR_NEEDS_APPROVAL":
        return "Progress is blocked until required input, budget, or manual approval is provided."
    if mode == "initial":
        return "Initial bounded package: verify source/project health and discover input contract before experiments."
    return f"Iterative bounded package: resolve current blocker, then continue safe progress under `{contract_mode}`."


def _paper_gaps(bundle: dict[str, Any]) -> list[dict[str, str]]:
    gaps = []
    paper_bundle = bundle.get("paper_bundle", {}) if isinstance(bundle.get("paper_bundle"), dict) else {}
    for key in ("paper_analysis", "paper_reproduction_card", "paper_evidence"):
        item = paper_bundle.get(key, {}) if isinstance(paper_bundle.get(key), dict) else {}
        if item.get("available") != "yes":
            gaps.append(
                {
                    "claim": f"{key} is unavailable or empty.",
                    "source": item.get("path", key),
                    "status": "GAP",
                    "notes": "Planner must avoid inventing paper facts from this missing artifact.",
                }
            )
    return gaps


def _paper_quality_gaps(bundle: dict[str, Any]) -> list[dict[str, str]]:
    if str(bundle.get("paper_quality", "")).upper() != "LOW_CONFIDENCE":
        return []
    return [
        {
            "claim": "Paper parse quality is LOW_CONFIDENCE.",
            "source": "PAPER_OUTPUT.json / Paper Quality Gate",
            "status": "GAP",
            "notes": "Planner scope is restricted to source/artifact/dataset/input discovery until paper fields are verified.",
        }
    ]


def _metric_gaps(bundle: dict[str, Any]) -> list[dict[str, str]]:
    paper_bundle = bundle.get("paper_bundle", {}) if isinstance(bundle.get("paper_bundle"), dict) else {}
    combined = "\n".join(
        str((paper_bundle.get(key, {}) if isinstance(paper_bundle.get(key), dict) else {}).get("excerpt", ""))
        for key in ("paper", "paper_evidence")
    )
    if "Not available in MVP" not in combined:
        return []
    return [
        {
            "claim": "Evidence Gap for `metrics`",
            "source": "PAPER_BRIEF.md / PAPER_EVIDENCE.md",
            "status": "GAP",
            "notes": "Metrics are not available in the current Paper artifacts.",
        }
    ]


def _paper_card_note(bundle: dict[str, Any]) -> str:
    paper_bundle = bundle.get("paper_bundle", {}) if isinstance(bundle.get("paper_bundle"), dict) else {}
    card = paper_bundle.get("paper_reproduction_card", {}) if isinstance(paper_bundle.get("paper_reproduction_card"), dict) else {}
    excerpt = str(card.get("excerpt", "")).strip()
    if not excerpt:
        return ""
    return "Paper Reproduction Card Summary: " + excerpt[:1000]


def _placeholder_input_issue(bundle: dict[str, Any]) -> dict[str, str]:
    repo_path = str(bundle.get("repo_path", "") or "")
    if not repo_path:
        return {}
    path = Path(repo_path) / ".r2a" / "results" / "input_contract_verification.csv"
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    if "EMPTY_PLACEHOLDER_INPUT" not in text:
        return {}
    return {
        "issue_id": "B_EMPTY_INPUT",
        "category": "MISSING_ARTIFACT_OR_DATA",
        "description": "Official input integrity found EMPTY_PLACEHOLDER_INPUT; cannot enter L3.",
        "evidence_source": str(path),
        "severity": "BLOCKING",
        "suggested_resolution": "Re-download or verify official non-empty query/database/ground truth files before official_reduced.",
    }


def _placeholder_gaps(bundle: dict[str, Any]) -> list[dict[str, str]]:
    issue = _placeholder_input_issue(bundle)
    if not issue:
        return []
    return [
        {
            "claim": "EMPTY_PLACEHOLDER_INPUT blocks official_reduced and cannot enter L3.",
            "source": issue["evidence_source"],
            "status": "GAP",
            "notes": "Contract remains verification_only until official inputs are non-empty and parseable.",
        }
    ]


def _extract_fenced_json(text: str) -> str:
    start = text.find("```")
    if start < 0:
        return ""
    body = text[start + 3 :]
    if body.lstrip().lower().startswith("json"):
        body = body.lstrip()[4:]
    end = body.find("```")
    return body[:end].strip() if end >= 0 else ""


def _extract_braced_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    return text[start : end + 1] if start >= 0 and end > start else ""


def _repair_json(text: str) -> str:
    return _extract_braced_json(text).replace(",\n}", "\n}").replace(",\n]", "\n]")
