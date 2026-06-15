from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from r2a.core.paths import artifact_dir
from r2a.tools.process_tree import _kill_registered_wsl_group, run_command_with_timeout
from r2a.tools.stage_guard import check_stage_allowed_modifications, snapshot_stage_changes
from r2a.tools.wsl import DEFAULT_WSL_DISTRO, _wsl_pgid_file, windows_to_wsl_path


DEFAULT_OPENCLAW_EXECUTABLE = "openclaw"
DEFAULT_OPENCLAW_PROVIDER = "ai-coding-plan"
DEFAULT_OPENCLAW_MODEL = "glm-5"
DEFAULT_OPENCLAW_RUNNER = "embedded"
DEFAULT_OPENCLAW_AGENT = ""
DEFAULT_OPENCLAW_CONFIG_PATH = ""

OPENCLAW_EXECUTABLE_ENV = "R2A_OPENCLAW_EXECUTABLE_PATH"
OPENCLAW_PROVIDER_ENV = "R2A_OPENCLAW_PROVIDER"
OPENCLAW_MODEL_ENV = "R2A_OPENCLAW_MODEL"
OPENCLAW_RUNNER_ENV = "R2A_OPENCLAW_RUNNER"
OPENCLAW_AGENT_ENV = "R2A_OPENCLAW_AGENT"
OPENCLAW_CONFIG_PATH_ENV = "R2A_OPENCLAW_CONFIG_PATH"

OPENCLAW_STAGE_PROFILES: dict[str, dict[str, str]] = {
    "paper": {
        "backend": "openclaw",
        "agent": "",
        "provider": "ai-coding-plan",
        "model": "glm-5",
        "runner": "embedded",
    },
    "planner": {
        "backend": "openclaw",
        "agent": "",
        "provider": "ai-coding-plan",
        "model": "glm-5",
        "runner": "embedded",
    },
    "engineer": {
        "backend": "openclaw",
        "agent": "",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "runner": "embedded",
    },
    "manager": {
        "backend": "openclaw_review",
        "agent": "",
        "provider": "ai-coding-plan",
        "model": "glm-5",
        "runner": "embedded",
    },
    "reviewer": {
        "backend": "openclaw",
        "agent": "",
        "provider": "ai-coding-plan",
        "model": "glm-5",
        "runner": "embedded",
    },
    "final_writer": {
        "backend": "openclaw",
        "agent": "",
        "provider": "ai-coding-plan",
        "model": "glm-5",
        "runner": "embedded",
        "mode": "narrative_only",
    },
}


def openclaw_stage_profile(stage: str | None) -> dict[str, str]:
    if not stage:
        return {
            "backend": "openclaw",
            "agent": DEFAULT_OPENCLAW_AGENT,
            "provider": DEFAULT_OPENCLAW_PROVIDER,
            "model": DEFAULT_OPENCLAW_MODEL,
            "runner": DEFAULT_OPENCLAW_RUNNER,
        }
    normalized = str(stage).strip().lower()
    return dict(
        OPENCLAW_STAGE_PROFILES.get(
            normalized,
            {
                "backend": "openclaw",
                "agent": DEFAULT_OPENCLAW_AGENT,
                "provider": DEFAULT_OPENCLAW_PROVIDER,
                "model": DEFAULT_OPENCLAW_MODEL,
                "runner": DEFAULT_OPENCLAW_RUNNER,
            },
        )
    )


def openclaw_stage_profiles() -> dict[str, dict[str, str]]:
    return {stage: dict(profile) for stage, profile in OPENCLAW_STAGE_PROFILES.items()}


def openclaw_stage_model_config_from_state(state: dict[str, Any], stage: str) -> dict[str, str]:
    """Return configured provider/model for a single OpenClaw-capable stage.

    User stage_model_selection takes precedence, then legacy per-stage fields,
    then the static OpenClaw stage profile. This is configuration resolution;
    the stage runner still records actual provider/model from OpenClaw stdout.
    """
    normalized = str(stage or "").strip().lower()
    profile = openclaw_stage_profile(normalized)
    selected = _stage_model_selection_entry(state, normalized)
    provider_key = f"{normalized}_provider"
    model_key = f"{normalized}_model"
    profile_key = f"{normalized}_profile"
    return {
        "backend": str(selected.get("backend") or profile.get("backend") or "openclaw"),
        "provider": str(selected.get("provider") or state.get(provider_key) or profile.get("provider") or DEFAULT_OPENCLAW_PROVIDER),
        "model": str(selected.get("model") or state.get(model_key) or profile.get("model") or DEFAULT_OPENCLAW_MODEL),
        "runner": str(selected.get("runner") or selected.get("profile") or state.get("openclaw_runner") or profile.get("runner") or DEFAULT_OPENCLAW_RUNNER),
        "agent": str(selected.get("agent") or state.get("openclaw_agent") or profile.get("agent") or DEFAULT_OPENCLAW_AGENT),
        "profile": str(selected.get("profile") or state.get(profile_key) or normalized),
        "mode": str(selected.get("mode") or profile.get("mode") or ""),
    }


def _stage_model_selection_entry(state: dict[str, Any], stage: str) -> dict[str, str]:
    raw = state.get("stage_model_selection") or state.get("stage_models") or {}
    if not isinstance(raw, dict):
        return {}
    entry = raw.get(stage) or raw.get(stage.replace("_", "-")) or raw.get(stage.title())
    if not isinstance(entry, dict):
        return {}
    return {str(key): str(value) for key, value in entry.items() if value is not None}


def detect_openclaw_model_profiles(
    *,
    openclaw_config_path: str | None = None,
    stage_profiles: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Detect provider/model/profile choices from OpenClaw config when readable.

    This intentionally avoids inventing a CLI list command. If the configured
    OpenClaw JSON is not readable from the current host, model choices are
    reported as not detected instead of inventing names from R2A defaults.
    """
    config = resolve_openclaw_config(openclaw_config_path=openclaw_config_path)
    config_path = str(openclaw_config_path or config.get("openclaw_config_path", "") or "").strip()
    warnings: list[str] = []
    errors: list[str] = []
    config_data: dict[str, Any] = {}
    config_read_path = ""
    checked_paths = _openclaw_config_read_candidates(config_path)
    source = "not_detected"
    if config_path:
        for candidate in checked_paths:
            candidate_path = Path(candidate)
            if not candidate_path.exists():
                continue
            config_read_path = str(candidate_path)
            source = "openclaw_wsl_config" if _is_wsl_unc_path(str(candidate_path)) else "openclaw_config"
            try:
                parsed = json.loads(candidate_path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(parsed, dict):
                    config_data = parsed
                else:
                    errors.append("Detected config format unsupported: top-level JSON value must be an object")
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"Config file unreadable or invalid: {type(exc).__name__}: {exc}")
            break
        if not config_data and not errors:
            if _looks_like_wsl_posix_path(config_path):
                tried = ", ".join(_openclaw_config_read_candidates(config_path))
                warnings.append(f"WSL config not accessible from Windows process: {config_path} (tried: {tried})")
            else:
                warnings.append(f"OpenClaw config path not found: {config_path}")
    else:
        errors.append("OpenClaw config path not configured")
    rows = _model_rows_from_openclaw_config(config_data)
    if config_data and not rows and not errors:
        warnings.append("OpenClaw config readable but no model entries found")
    return {
        "ok": bool(rows) and not errors,
        "source": source,
        "config_path": config_path,
        "config_read_path": config_read_path,
        "checked_paths": checked_paths,
        "warnings": warnings,
        "errors": errors,
        "models": rows,
    }


def test_openclaw_configuration(
    *,
    openclaw_executable_path: str | None = None,
    openclaw_config_path: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Lightweight OpenClaw configuration test.

    This intentionally validates only executable/config/profile discovery. R2A
    does not invent an OpenClaw live prompt command here.
    """
    config = resolve_openclaw_config(
        openclaw_executable_path=openclaw_executable_path,
        openclaw_config_path=openclaw_config_path,
    )
    executable = str(config.get("openclaw_executable_path", "") or "").strip()
    config_path = str(config.get("openclaw_config_path", "") or "").strip()
    provider_text = str(provider or "").strip()
    model_text = str(model or "").strip()
    profile_text = str(profile or "").strip()
    errors: list[str] = []
    warnings: list[str] = []

    executable_status = _openclaw_executable_status(executable)
    if executable_status["error"]:
        errors.append(str(executable_status["error"]))
    if executable_status["warning"]:
        warnings.append(str(executable_status["warning"]))

    detection = detect_openclaw_model_profiles(openclaw_config_path=config_path)
    warnings.extend(str(item) for item in detection.get("warnings", []) or [])
    errors.extend(str(item) for item in detection.get("errors", []) or [])
    models = [item for item in detection.get("models", []) or [] if isinstance(item, dict)]
    if config_path and not detection.get("config_read_path"):
        if any("WSL config not accessible" in str(item) for item in detection.get("warnings", []) or []):
            errors.append("WSL config not accessible from Windows process")
        else:
            errors.append("OpenClaw config path not found")
    if config_path and detection.get("source") != "not_detected" and not models:
        errors.append("No model/profile entries found")
    if not config_path:
        errors.append("OpenClaw config path not configured")
    if provider_text or model_text or profile_text:
        if not _detected_model_entry(models, provider=provider_text, model=model_text, profile=profile_text):
            warnings.append("Saved default profile not detected")

    return {
        "success": not errors,
        "tested_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "executable_path": executable,
        "executable_check": executable_status,
        "config_path": config_path,
        "config_read_path": str(detection.get("config_read_path", "") or ""),
        "checked_config_paths": list(detection.get("checked_paths", []) or []),
        "detection_source": str(detection.get("source", "") or ""),
        "provider": provider_text,
        "model": model_text,
        "profile": profile_text,
        "models": models,
        "warnings": _unique_nonempty(warnings),
        "errors": _unique_nonempty(errors),
        "error_message": "; ".join(_unique_nonempty(errors)),
    }


def _openclaw_executable_status(executable: str) -> dict[str, str]:
    if not executable:
        return {"kind": "missing", "resolved_path": "", "error": "OpenClaw executable not found", "warning": ""}
    if _looks_like_wsl_posix_path(executable):
        return {
            "kind": "wsl_posix",
            "resolved_path": executable,
            "error": "",
            "warning": "OpenClaw executable is a WSL/POSIX path; existence is verified by WSL runtime preflight.",
        }
    candidate = Path(executable)
    if candidate.exists():
        if candidate.is_dir():
            return {"kind": "local_path", "resolved_path": str(candidate), "error": "OpenClaw executable path is a directory", "warning": ""}
        return {"kind": "local_path", "resolved_path": str(candidate), "error": "", "warning": ""}
    resolved = shutil.which(executable)
    if resolved:
        return {"kind": "path_lookup", "resolved_path": resolved, "error": "", "warning": ""}
    return {"kind": "missing", "resolved_path": "", "error": "OpenClaw executable not found", "warning": ""}


def _detected_model_entry(
    models: list[dict[str, Any]],
    *,
    provider: str,
    model: str,
    profile: str = "",
) -> bool:
    if not provider and not model:
        return True
    for item in models:
        item_profile = str(item.get("profile", "") or "")
        if item.get("provider") == provider and item.get("model") == model and (not profile or item_profile == profile):
            return True
    return False


def _openclaw_config_read_candidates(config_path: str) -> list[str]:
    text = str(config_path or "").strip()
    if not text:
        return []
    candidates = [text]
    if _looks_like_wsl_posix_path(text):
        rel = text.lstrip("/").replace("/", "\\")
        distro = os.environ.get("R2A_WSL_DISTRO") or DEFAULT_WSL_DISTRO
        candidates.extend(
            [
                f"\\\\wsl.localhost\\{distro}\\{rel}",
                f"\\\\wsl$\\{distro}\\{rel}",
            ]
        )
    return _unique_nonempty(candidates)


def _looks_like_wsl_posix_path(path: str) -> bool:
    text = str(path or "").strip().replace("\\", "/")
    return os.name == "nt" and text.startswith("/") and not text.startswith("//")


def _is_wsl_unc_path(path: str) -> bool:
    text = str(path or "").strip().replace("/", "\\").lower()
    return text.startswith("\\\\wsl.localhost\\") or text.startswith("\\\\wsl$\\")


def _wsl_unc_to_posix_path(path: str) -> str:
    """Convert WSL UNC path to POSIX path.

    Examples:
        \\\\wsl.localhost\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json -> /home/r2auser/.openclaw/openclaw.json
        \\\\wsl$\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json -> /home/r2auser/.openclaw/openclaw.json
    """
    text = str(path or "").strip()
    if not _is_wsl_unc_path(text):
        return text

    # Normalize slashes
    text = text.replace("/", "\\")

    # Remove WSL UNC prefix
    # \\\\wsl.localhost\\Ubuntu\\... or \\\\wsl$\\Ubuntu\\...
    for prefix in ["\\\\wsl.localhost\\", "\\\\wsl$\\"]:
        if text.lower().startswith(prefix):
            # Skip the distro name and continue with the rest
            rest = text[len(prefix):]
            # Find the next backslash after distro name
            if "\\" in rest:
                distro, path_part = rest.split("\\", 1)
                return "/" + path_part.replace("\\", "/")
            break

    return text


def _unique_nonempty(items: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            unique.append(text)
    return unique


def _model_rows_from_openclaw_config(config: dict[str, Any]) -> list[dict[str, str]]:
    if not config:
        return []
    rows: list[dict[str, str]] = []
    agents = config.get("agents", {}) if isinstance(config.get("agents"), dict) else {}
    agent_list = agents.get("list", []) if isinstance(agents.get("list"), list) else []
    defaults = agents.get("defaults", {}) if isinstance(agents.get("defaults"), dict) else {}
    sources = [("default", defaults), *[(str(item.get("id") or "agent"), item) for item in agent_list if isinstance(item, dict)]]
    for profile_name, source in sources:
        models = source.get("models", {}) if isinstance(source, dict) else {}
        if not isinstance(models, dict):
            continue
        for model_id in models:
            provider, model = _split_openclaw_model_id(str(model_id))
            _append_model_row(rows, provider, model, profile_name, agent="" if profile_name == "default" else profile_name)
    providers = ((config.get("models") or {}).get("providers") or {}) if isinstance(config.get("models"), dict) else {}
    if isinstance(providers, dict):
        for provider, provider_config in providers.items():
            for model in _provider_models(provider_config):
                item_provider, item_model = _split_openclaw_model_id(model)
                _append_model_row(rows, item_provider or str(provider), item_model or model, "config")
    return rows


def _provider_models(provider_config: object) -> list[str]:
    if isinstance(provider_config, dict):
        for key in ("models", "model", "available_models"):
            value = provider_config.get(key)
            if isinstance(value, dict):
                return [str(item) for item in value if str(item).strip()]
            if isinstance(value, list):
                return [_model_id_from_provider_item(item) for item in value if _model_id_from_provider_item(item)]
            if isinstance(value, str) and value.strip():
                return [value.strip()]
    return []


def _model_id_from_provider_item(item: object) -> str:
    if isinstance(item, dict):
        for key in ("id", "model", "name"):
            value = str(item.get(key, "") or "").strip()
            if value:
                return value
        return ""
    return str(item or "").strip()


def _append_model_row(
    rows: list[dict[str, str]],
    provider: str,
    model: str,
    profile: str,
    *,
    agent: str = "",
) -> None:
    provider = str(provider or "").strip()
    model = str(model or "").strip()
    profile = str(profile or "").strip() or "config"
    if not provider or not model or _has_model_row(rows, provider, model, profile):
        return
    rows.append(
        {
            "provider": provider,
            "model": model,
            "profile": profile,
            "runner": "embedded",
            "agent": agent,
            "display_name": f"{provider}/{model} ({profile})",
            "stage_default": "",
        }
    )


def _split_openclaw_model_id(model_id: str) -> tuple[str, str]:
    text = model_id.strip()
    if "/" not in text:
        return "", text
    provider, model = text.split("/", 1)
    return provider.strip(), model.strip()


def _has_model_row(rows: list[dict[str, str]], provider: str, model: str, profile: str) -> bool:
    return any(
        row.get("provider") == provider
        and row.get("model") == model
        and (not profile or row.get("profile") == profile or row.get("stage_default") == profile)
        for row in rows
    )


def resolve_openclaw_config(
    *,
    stage: str | None = None,
    openclaw_executable_path: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    runner: str | None = None,
    agent: str | None = None,
    openclaw_config_path: str | None = None,
) -> dict[str, str]:
    profile = openclaw_stage_profile(stage)
    return {
        "openclaw_executable_path": str(openclaw_executable_path or os.environ.get(OPENCLAW_EXECUTABLE_ENV) or DEFAULT_OPENCLAW_EXECUTABLE),
        "provider": str(provider or os.environ.get(OPENCLAW_PROVIDER_ENV) or profile.get("provider") or DEFAULT_OPENCLAW_PROVIDER),
        "model": str(model or os.environ.get(OPENCLAW_MODEL_ENV) or profile.get("model") or DEFAULT_OPENCLAW_MODEL),
        "runner": str(runner or os.environ.get(OPENCLAW_RUNNER_ENV) or profile.get("runner") or DEFAULT_OPENCLAW_RUNNER),
        "agent": str(agent or os.environ.get(OPENCLAW_AGENT_ENV) or profile.get("agent") or DEFAULT_OPENCLAW_AGENT),
        "openclaw_config_path": str(openclaw_config_path or os.environ.get(OPENCLAW_CONFIG_PATH_ENV) or DEFAULT_OPENCLAW_CONFIG_PATH),
        "stage_profile": str(stage or ""),
    }


def openclaw_config_from_state(state: dict[str, Any], *, stage: str | None = None) -> dict[str, str]:
    stage_config = openclaw_stage_model_config_from_state(state, stage or "") if stage else {}
    config_path_from_state = str(state.get("openclaw_config_path", "") or "")
    # Defensive: convert UNC to POSIX for consistency
    # state may contain UNC path from Windows UI; normalize to POSIX
    config_path_normalized = _wsl_unc_to_posix_path(config_path_from_state) if config_path_from_state else ""
    return resolve_openclaw_config(
        stage=stage,
        openclaw_executable_path=str(state.get("openclaw_executable_path", "") or "") or None,
        provider=str(stage_config.get("provider") or state.get("openclaw_provider", "") or "") or None,
        model=str(stage_config.get("model") or state.get("openclaw_model", "") or "") or None,
        runner=str(stage_config.get("runner") or state.get("openclaw_runner", "") or "") or None,
        agent=str(stage_config.get("agent") or state.get("openclaw_agent", "") or "") or None,
        openclaw_config_path=config_path_normalized or None,
    )


def run_openclaw_stage(
    repo_path: str | Path,
    stage: str,
    input_path: str | Path,
    allowed_outputs: list[str],
    *,
    session_key: str,
    iteration: int | None = None,
    timeout: int = 180,
    openclaw_executable_path: str | None = None,
    wsl_distro: str = "Ubuntu",
    env: dict[str, str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    runner: str | None = None,
    agent: str | None = None,
    openclaw_config_path: str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_path)
    stage_name = str(stage)
    input_file = Path(input_path)
    effective_allowed_outputs = _allowed_outputs_with_logs(stage_name, allowed_outputs)
    baseline_changes = snapshot_stage_changes(repo)
    logs_dir = artifact_dir(repo) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    raw_stdout_log = logs_dir / f"openclaw_{stage_name}_raw_stdout.json"
    raw_stderr_log = logs_dir / f"openclaw_{stage_name}_raw_stderr.log"
    wrapper_script = logs_dir / f"openclaw_{stage_name}_wrapper.sh"
    invocation_id = _openclaw_invocation_id(stage_name, session_key, iteration)
    invocation_dir = _openclaw_invocation_dir(repo, stage_name, iteration, invocation_id)
    config = resolve_openclaw_config(
        stage=stage_name,
        openclaw_executable_path=openclaw_executable_path,
        provider=provider,
        model=model,
        runner=runner,
        agent=agent,
        openclaw_config_path=openclaw_config_path,
    )
    provider = config["provider"]
    model = config["model"]
    runner = config["runner"]
    agent = config["agent"]
    executable = windows_to_wsl_path(config["openclaw_executable_path"])
    preflight = preflight_openclaw_stage(
        stage_name,
        executable=executable,
        provider=provider,
        model=model,
        runner=runner,
        agent=agent,
        wsl_distro=wsl_distro,
        openclaw_config_path=config["openclaw_config_path"],
        repo_path=repo,
    )
    if not preflight.get("ok"):
        stderr = _preflight_error_text(preflight)
        stdout_log, stderr_log = _write_stage_logs(
            repo,
            stage_name,
            "",
            stderr,
            executable,
            [],
            session_key,
            input_file,
            {},
        )
        invocation_meta = _write_invocation_archive(
            repo,
            stage_name,
            invocation_id=invocation_id,
            invocation_dir=invocation_dir,
            iteration=iteration,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            raw_stdout_log=raw_stdout_log,
            raw_stderr_log=raw_stderr_log,
            wrapper_script=wrapper_script,
            stdout="",
            stderr=stderr,
            command=[],
            session_key=session_key,
            input_path=input_file,
            metadata={},
            config=config,
            preflight=preflight,
            returncode=int(preflight.get("returncode", 1) or 1),
            timed_out=False,
            success=False,
        )
        return {
            "stage": stage_name,
            "backend": "openclaw",
            "returncode": int(preflight.get("returncode", 1) or 1),
            "stdout_log_path": str(stdout_log),
            "stderr_log_path": str(stderr_log),
            **invocation_meta,
            "stdout_tail": "",
            "stderr_tail": _tail(stderr),
            "allowed_outputs": effective_allowed_outputs,
            "attempted_executable": executable,
            "resolved_executable": executable,
            "command": [],
            "success": False,
            "error": stderr,
            "hint": str(preflight.get("hint", "")),
            "baseline_changed_files": [],
            "stage_changed_files": [],
            "signature_changed_files": [],
            "unexpected_modifications": [],
            "stage_guard_ok": False,
            "guard_available": True,
            "guard_backend": "",
            "stage_guard_error": "",
            "stage_guard_warning": "",
            "cleaned_unexpected_modifications": [],
            "uncleaned_unexpected_modifications": [],
            "failure_category": str(preflight.get("failure_category") or "OPENCLAW_PREFLIGHT_FAILED"),
            "execution_status": str(preflight.get("failure_category") or "OPENCLAW_PREFLIGHT_FAILED"),
            "timed_out": False,
            "session_key": session_key,
            "iteration": iteration,
            "actual_session_key": "",
            "session_id": "",
            "stdout_json": False,
            "payload": "",
            "provider": "",
            "model": "",
            "runner": "",
            "configured_provider": provider,
            "configured_model": model,
            "configured_runner": runner,
            "configured_agent": agent,
            "configured_openclaw_executable_path": config["openclaw_executable_path"],
            "openclaw_config": config,
            "openclaw_preflight": preflight,
            "transport": "",
            "fallbackUsed": None,
            "fallbackFrom": "",
            "fallbackReason": "",
        }
    message = f"Read and follow the instructions in {windows_to_wsl_path(input_file)} exactly. Return only raw JSON status."
    openclaw_args = [
        executable,
        "agent",
        "--local",
    ]
    if agent and agent not in {"default", "defaults"}:
        openclaw_args.extend(["--agent", agent])
    openclaw_args.extend(
        [
            "--session-key",
            session_key,
            "--model",
            f"{provider}/{model}",
            "--message",
            message,
            "--timeout",
            str(timeout),
            "--json",
        ]
    )
    command = _openclaw_wsl_command(
        openclaw_args,
        cwd=repo,
        distro=wsl_distro,
        stdout_path=raw_stdout_log,
        stderr_path=raw_stderr_log,
        wrapper_path=wrapper_script,
    )
    stdout = ""
    stderr = ""
    returncode = 0
    timed_out = False
    runtime_env = _openclaw_runtime_env(env)
    try:
        completed = run_command_with_timeout(
            command,
            cwd=str(repo),
            input_text="",
            timeout=timeout + 15,
            env=runtime_env,
        )
        stdout = _read(raw_stdout_log)
        stderr = "\n".join(part for part in (completed.stderr or "", _read(raw_stderr_log)) if part)
        returncode = int(completed.returncode)
        timed_out = bool(completed.timed_out)
        if timed_out:
            stderr += f"\nTimeoutExpired: OpenClaw {stage_name} exceeded {timeout + 15} seconds and the process tree was terminated."
    except FileNotFoundError as exc:
        stderr = f"FileNotFoundError while invoking OpenClaw through WSL: {exc}"
        returncode = 127
    except PermissionError as exc:
        stderr = f"PermissionError while invoking OpenClaw through WSL: {exc}"
        returncode = 126
    finally:
        _kill_registered_wsl_group(runtime_env)

    metadata = _parse_openclaw_stdout(stdout)
    guard = check_stage_allowed_modifications(repo, stage_name, effective_allowed_outputs, baseline_changes)
    cleanup = _cleanup_new_unexpected_modifications(repo, guard)
    stderr = _append_guard_message(stderr, guard)
    stderr = _append_cleanup_message(stderr, cleanup)
    stdout_log, stderr_log = _write_stage_logs(
        repo,
        stage_name,
        stdout,
        stderr,
        executable,
        command,
        session_key,
        input_file,
        metadata,
    )
    metadata_ok = _metadata_ok(metadata, provider=provider, model=model, runner=runner)
    provider_error = _openclaw_payload_error(metadata, stdout, stderr)
    success = returncode == 0 and guard["ok"] and metadata_ok and not provider_error
    failure_category = ""
    execution_status = ""
    error = ""
    if returncode != 0:
        error = stderr
        failure_category = f"{stage_name.upper()}_BACKEND_FAILURE"
        execution_status = f"{stage_name.upper()}_BACKEND_FAILURE"
    elif provider_error:
        error = provider_error
        failure_category = f"{stage_name.upper()}_BACKEND_FAILURE"
        execution_status = f"{stage_name.upper()}_BACKEND_FAILURE"
    elif not metadata_ok:
        error = _metadata_error(metadata, provider=provider, model=model, runner=runner)
        failure_category = f"{stage_name.upper()}_BACKEND_FAILURE"
        execution_status = f"{stage_name.upper()}_BACKEND_FAILURE"
    elif not guard["ok"]:
        error = f"Stage guard rejected unexpected modifications: {guard['unexpected_modifications']}"
        failure_category = str(guard.get("failure_category") or "STAGE_BOUNDARY_VIOLATION")
        execution_status = str(guard.get("execution_status") or f"{stage_name.upper()}_FORBIDDEN_WRITE")
    invocation_meta = _write_invocation_archive(
        repo,
        stage_name,
        invocation_id=invocation_id,
        invocation_dir=invocation_dir,
        iteration=iteration,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        raw_stdout_log=raw_stdout_log,
        raw_stderr_log=raw_stderr_log,
        wrapper_script=wrapper_script,
        stdout=stdout,
        stderr=stderr,
        command=_redacted_command(command),
        session_key=session_key,
        input_path=input_file,
        metadata=metadata,
        config=config,
        preflight=preflight,
        returncode=returncode,
        timed_out=timed_out,
        success=success,
    )
    return {
        "stage": stage_name,
        "backend": "openclaw",
        "returncode": returncode,
        "stdout_log_path": str(stdout_log),
        "stderr_log_path": str(stderr_log),
        **invocation_meta,
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
        "allowed_outputs": effective_allowed_outputs,
        "attempted_executable": executable,
        "resolved_executable": executable,
        "command": _redacted_command(command),
        "success": success,
        "error": error,
        "hint": "",
        "baseline_changed_files": guard.get("baseline_changed_files", []),
        "stage_changed_files": guard.get("stage_changed_files", []),
        "signature_changed_files": guard.get("signature_changed_files", []),
        "unexpected_modifications": guard.get("unexpected_modifications", []),
        "stage_guard_ok": guard.get("ok", False),
        "guard_available": guard.get("guard_available", True),
        "guard_backend": guard.get("guard_backend", ""),
        "stage_guard_error": guard.get("error", ""),
        "stage_guard_warning": guard.get("warning", ""),
        "cleaned_unexpected_modifications": cleanup["cleaned"],
        "uncleaned_unexpected_modifications": cleanup["uncleaned"],
        "failure_category": failure_category or guard.get("failure_category", ""),
        "execution_status": execution_status or guard.get("execution_status", ""),
        "timed_out": timed_out,
        "session_key": session_key,
        "iteration": iteration,
        "actual_session_key": metadata.get("actual_session_key", ""),
        "session_id": metadata.get("session_id", ""),
        "stdout_json": metadata.get("stdout_json", False),
        "payload": metadata.get("payload", ""),
        "provider_error": provider_error,
        "provider": metadata.get("provider", ""),
        "model": metadata.get("model", ""),
        "runner": metadata.get("runner", ""),
        "configured_provider": provider,
        "configured_model": model,
        "configured_runner": runner,
        "configured_agent": agent,
        "configured_openclaw_executable_path": config["openclaw_executable_path"],
        "openclaw_config": config,
        "openclaw_preflight": preflight,
        "transport": metadata.get("transport", ""),
        "fallbackUsed": metadata.get("fallbackUsed"),
        "fallbackFrom": metadata.get("fallbackFrom", ""),
        "fallbackReason": metadata.get("fallbackReason", ""),
        "token_usage": metadata.get("token_usage", {}),
    }


def _allowed_outputs_with_logs(stage: str, allowed_outputs: list[str]) -> list[str]:
    outputs = list(allowed_outputs)
    for path in (
        f".r2a/logs/{stage}_stdout.log",
        f".r2a/logs/{stage}_stderr.log",
        f".r2a/logs/openclaw_{stage}_raw_stdout.json",
        f".r2a/logs/openclaw_{stage}_raw_stderr.log",
        f".r2a/logs/openclaw_{stage}_wrapper.sh",
        f".r2a/logs/invocations/{stage}/**",
    ):
        if path not in outputs:
            outputs.append(path)
    return outputs


def _openclaw_invocation_id(stage: str, session_key: str, iteration: int | None) -> str:
    iteration_label = f"iter_{int(iteration):03d}" if iteration is not None else "iter_unknown"
    safe_session = _safe_component(session_key)[:48] or "session"
    timestamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
    millis = int((time.time() % 1) * 1000)
    return f"{_safe_component(stage)}-{iteration_label}-{safe_session}-{timestamp}-{millis:03d}"


def _openclaw_invocation_dir(repo: Path, stage: str, iteration: int | None, invocation_id: str) -> Path:
    iteration_label = f"iter_{int(iteration):03d}" if iteration is not None else "iter_unknown"
    return artifact_dir(repo) / "logs" / "invocations" / _safe_component(stage) / iteration_label / "attempt_001" / invocation_id


def _safe_component(value: object) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return safe.strip("._-") or "unknown"


def _write_invocation_archive(
    repo: Path,
    stage: str,
    *,
    invocation_id: str,
    invocation_dir: Path,
    iteration: int | None,
    stdout_log: Path,
    stderr_log: Path,
    raw_stdout_log: Path,
    raw_stderr_log: Path,
    wrapper_script: Path,
    stdout: str,
    stderr: str,
    command: list[str],
    session_key: str,
    input_path: Path,
    metadata: dict[str, object],
    config: dict[str, str],
    preflight: dict[str, Any],
    returncode: int,
    timed_out: bool,
    success: bool,
) -> dict[str, Any]:
    invocation_dir.mkdir(parents=True, exist_ok=True)
    copied = {
        "stdout_log": _copy_if_exists(stdout_log, invocation_dir / stdout_log.name),
        "stderr_log": _copy_if_exists(stderr_log, invocation_dir / stderr_log.name),
        "raw_stdout": _copy_if_exists(raw_stdout_log, invocation_dir / raw_stdout_log.name),
        "raw_stderr": _copy_if_exists(raw_stderr_log, invocation_dir / raw_stderr_log.name),
        "wrapper_script": _copy_if_exists(wrapper_script, invocation_dir / wrapper_script.name),
    }
    manifest = {
        "schema_version": 1,
        "invocation_id": invocation_id,
        "stage": stage,
        "iteration": iteration,
        "attempt": 1,
        "repo_path": str(repo),
        "session_key": session_key,
        "actual_session_key": metadata.get("actual_session_key", ""),
        "session_id": metadata.get("session_id", ""),
        "input_path": str(input_path),
        "returncode": returncode,
        "timed_out": timed_out,
        "success": success,
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
        "command": command,
        "configured_provider": config.get("provider", ""),
        "configured_model": config.get("model", ""),
        "configured_runner": config.get("runner", ""),
        "configured_agent": config.get("agent", ""),
        "provider": metadata.get("provider", ""),
        "model": metadata.get("model", ""),
        "runner": metadata.get("runner", ""),
        "agent": metadata.get("agent", ""),
        "transport": metadata.get("transport", ""),
        "fallbackUsed": metadata.get("fallbackUsed"),
        "fallbackFrom": metadata.get("fallbackFrom", ""),
        "fallbackReason": metadata.get("fallbackReason", ""),
        "stdout_json": metadata.get("stdout_json", False),
        "token_usage": metadata.get("token_usage", {}),
        "copied_logs": copied,
        "latest_logs": {
            "stdout_log_path": str(stdout_log),
            "stderr_log_path": str(stderr_log),
            "raw_stdout_log_path": str(raw_stdout_log),
            "raw_stderr_log_path": str(raw_stderr_log),
            "wrapper_script_path": str(wrapper_script),
        },
        "preflight_config_path": preflight.get("config_path", ""),
        "runtime_config_path": preflight.get("config_path_runtime") or preflight.get("config_path", ""),
        "wrapper_passes_config_path": False,
        "uses_openclaw_default_config_discovery": True,
        "openclaw_preflight": preflight,
    }
    manifest_path = invocation_dir / "invocation.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "invocation_id": invocation_id,
        "invocation_log_dir": str(invocation_dir),
        "invocation_manifest_path": str(manifest_path),
        "token_usage_source_path": str(manifest_path),
    }


def _copy_if_exists(source: Path, destination: Path) -> str:
    if not source.exists():
        return ""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())
    return str(destination)


def _cleanup_new_unexpected_modifications(repo: Path, guard: dict[str, Any]) -> dict[str, list[str]]:
    """Remove only new unauthorized files created by the just-finished stage.

    The stage still fails. Cleanup only prevents forbidden fresh artifacts from
    polluting later evidence discovery after a failed transaction.
    """
    unexpected = {str(path).replace("\\", "/") for path in guard.get("unexpected_modifications", []) or []}
    new_dirty = {str(path).replace("\\", "/") for path in guard.get("new_dirty_files", []) or []}
    cleaned: list[str] = []
    uncleaned: list[str] = []
    if not unexpected:
        return {"cleaned": cleaned, "uncleaned": uncleaned}

    repo_root = repo.resolve()
    for relative in sorted(unexpected):
        if relative not in new_dirty:
            uncleaned.append(relative)
            continue
        target = (repo / relative).resolve()
        try:
            if not target.is_relative_to(repo_root):
                uncleaned.append(relative)
                continue
            if target.is_file() or target.is_symlink():
                target.unlink()
                cleaned.append(relative)
                _prune_empty_parents(target.parent, repo_root)
            elif target.exists():
                uncleaned.append(relative)
        except OSError:
            uncleaned.append(relative)
    return {"cleaned": cleaned, "uncleaned": uncleaned}


def _prune_empty_parents(path: Path, stop_at: Path) -> None:
    current = path.resolve()
    while current != stop_at and current.is_relative_to(stop_at):
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _openclaw_wsl_command(
    openclaw_args: list[str],
    *,
    cwd: str | Path,
    distro: str,
    stdout_path: str | Path,
    stderr_path: str | Path,
    wrapper_path: str | Path,
) -> list[str]:
    wsl_cwd = windows_to_wsl_path(cwd)
    command_text = " ".join(shlex.quote(windows_to_wsl_path(arg)) for arg in openclaw_args)
    executable_dir = str(Path(openclaw_args[0]).parent)
    path_prefix = windows_to_wsl_path(executable_dir) if executable_dir not in {"", "."} else ""
    path_export = f"export PATH={shlex.quote(path_prefix)}:\"$PATH\"\n" if path_prefix else ""
    script = (
        f"{path_export}"
        f"cd {shlex.quote(wsl_cwd)} || exit $?\n"
        f"exec {command_text} > {shlex.quote(windows_to_wsl_path(stdout_path))} "
        f"2> {shlex.quote(windows_to_wsl_path(stderr_path))}\n"
    )
    pgid_file = _wsl_pgid_file()
    _write_wsl_wrapper(Path(wrapper_path), script, pgid_file)
    wrapper_wsl_path = windows_to_wsl_path(wrapper_path)
    return ["wsl", "-d", distro, "--", "bash", "-lc", f"setsid --wait bash {shlex.quote(wrapper_wsl_path)}"]


def _write_wsl_wrapper(wrapper_path: Path, script: str, pgid_file: str) -> None:
    pgid_block = ""
    if pgid_file:
        pgid_block = (
            "pgid=$(ps -o pgid= -p \"$$\" 2>/dev/null | tr -d ' ')\n"
            "if [ -n \"$pgid\" ]; then\n"
            f"  echo \"$pgid\" > {shlex.quote(pgid_file)}\n"
            "fi\n"
        )
    wrapper_path.parent.mkdir(parents=True, exist_ok=True)
    text = "#!/usr/bin/env bash\n" "set +e\n" f"{pgid_block}" f"{script}"
    with wrapper_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _openclaw_runtime_env(env: dict[str, str] | None) -> dict[str, str] | None:
    if env is None:
        return None
    merged = dict(env)
    for name in ("R2A_RUN_ID", "R2A_RUNTIME_DIR", "R2A_REPO_PATH", "R2A_WSL_DISTRO", "R2A_WSL_PGID_FILE"):
        value = os.environ.get(name)
        if value and not merged.get(name):
            merged[name] = value
    return merged


def preflight_openclaw_stage(
    stage: str,
    *,
    executable: str,
    provider: str,
    model: str,
    runner: str,
    agent: str,
    wsl_distro: str,
    openclaw_config_path: str,
    repo_path: str | Path,
    timeout: int = 10,
) -> dict[str, Any]:
    # Defensive conversion: UNC path -> POSIX path for WSL runtime
    # Windows UI may pass UNC read path; WSL runtime needs POSIX path
    runtime_config_path = _wsl_unc_to_posix_path(openclaw_config_path)
    code = r"""
import json
import os
import sys
from pathlib import Path

executable, config_path, stage, agent, provider, model, runner = sys.argv[1:8]
result = {
    "ok": True,
    "stage": stage,
    "agent": agent or "default",
    "provider": provider,
    "model": model,
    "runner": runner,
    "executable": executable,
    "config_path": config_path,
    "failure_category": "",
    "errors": [],
    "warnings": [],
    "available_agents": [],
    "available_providers": [],
    "available_models": [],
}

exe = Path(executable)
if not exe.exists():
    result["errors"].append("OPENCLAW_EXECUTABLE_NOT_FOUND")
elif not os.access(exe, os.X_OK):
    result["errors"].append("OPENCLAW_EXECUTABLE_NOT_EXECUTABLE")

config = {}
path = Path(config_path)
if not path.exists():
    result["errors"].append("OPENCLAW_CONFIG_NOT_FOUND")
else:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        result["errors"].append(f"OPENCLAW_CONFIG_INVALID: {type(exc).__name__}: {exc}")

agents = config.get("agents", {}) if isinstance(config, dict) else {}
agent_list = agents.get("list", []) if isinstance(agents, dict) else []
agent_ids = [
    str(item.get("id", "")).strip()
    for item in agent_list
    if isinstance(item, dict) and str(item.get("id", "")).strip()
]
result["available_agents"] = agent_ids
if agent and agent not in {"default", "defaults"}:
    if not agent_ids or agent not in agent_ids:
        result["errors"].append("OPENCLAW_AGENT_NOT_FOUND")

models = config.get("models", {}) if isinstance(config, dict) else {}
providers = models.get("providers", {}) if isinstance(models, dict) else {}
provider_ids = sorted(str(key) for key in providers) if isinstance(providers, dict) else []
result["available_providers"] = provider_ids
if provider and provider_ids and provider not in provider_ids:
    result["errors"].append("OPENCLAW_PROVIDER_NOT_FOUND")

available_models = set()
defaults = agents.get("defaults", {}) if isinstance(agents, dict) else {}
for source in [defaults, *[item for item in agent_list if isinstance(item, dict)]]:
    source_models = source.get("models", {}) if isinstance(source, dict) else {}
    if isinstance(source_models, dict):
        available_models.update(str(key) for key in source_models)
model_id = f"{provider}/{model}" if provider and model else ""
result["available_models"] = sorted(available_models)
if model_id and available_models and model_id not in available_models:
    result["errors"].append("OPENCLAW_MODEL_NOT_AVAILABLE")

if result["errors"]:
    result["ok"] = False
    result["failure_category"] = result["errors"][0].split(":", 1)[0]

print(json.dumps(result, ensure_ascii=False))
sys.exit(0 if result["ok"] else 2)
"""
    try:
        completed = run_command_with_timeout(
            [
                "wsl",
                "-d",
                wsl_distro,
                "--",
                "python3",
                "-c",
                code,
                executable,
                runtime_config_path,  # Use POSIX path for WSL runtime
                stage,
                agent,
                provider,
                model,
                runner,
            ],
            cwd=str(repo_path),
            input_text="",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "stage": stage,
            "agent": agent or "default",
            "provider": provider,
            "model": model,
            "runner": runner,
            "executable": executable,
            "config_path": openclaw_config_path,
            "config_path_runtime": runtime_config_path,
            "returncode": 127,
            "failure_category": "OPENCLAW_WSL_NOT_FOUND",
            "errors": [f"OPENCLAW_WSL_NOT_FOUND: {exc}"],
            "warnings": [],
        }
    except Exception as exc:
        return {
            "ok": False,
            "stage": stage,
            "agent": agent or "default",
            "provider": provider,
            "model": model,
            "runner": runner,
            "executable": executable,
            "config_path": openclaw_config_path,
            "config_path_runtime": runtime_config_path,
            "returncode": 1,
            "failure_category": "OPENCLAW_PREFLIGHT_FAILED",
            "errors": [f"OPENCLAW_PREFLIGHT_FAILED: {type(exc).__name__}: {exc}"],
            "warnings": [],
        }
    parsed = _parse_preflight_stdout(completed.stdout or "")
    if not parsed:
        parsed = {
            "ok": False,
            "stage": stage,
            "agent": agent or "default",
            "provider": provider,
            "model": model,
            "runner": runner,
            "executable": executable,
            "config_path": openclaw_config_path,
            "config_path_runtime": runtime_config_path,
            "failure_category": "OPENCLAW_PREFLIGHT_NO_JSON",
            "errors": ["OPENCLAW_PREFLIGHT_NO_JSON"],
            "warnings": [],
            "stdout_tail": _tail(completed.stdout or ""),
            "stderr_tail": _tail(completed.stderr or ""),
        }
    else:
        # Ensure original and runtime paths are both recorded
        parsed["config_path"] = openclaw_config_path  # Original input
        parsed["config_path_runtime"] = runtime_config_path  # What WSL sees
    parsed["returncode"] = int(completed.returncode)
    if completed.timed_out:
        parsed["ok"] = False
        parsed["failure_category"] = "OPENCLAW_PREFLIGHT_TIMEOUT"
        parsed.setdefault("errors", []).append(f"OPENCLAW_PREFLIGHT_TIMEOUT: exceeded {timeout} seconds")
    if completed.returncode != 0 and parsed.get("ok"):
        parsed["ok"] = False
        parsed["failure_category"] = "OPENCLAW_PREFLIGHT_FAILED"
        parsed.setdefault("errors", []).append(f"OPENCLAW_PREFLIGHT_FAILED: returncode={completed.returncode}")
    return parsed


def _parse_preflight_stdout(stdout: str) -> dict[str, Any]:
    text = stdout or ""
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _preflight_error_text(preflight: dict[str, Any]) -> str:
    errors = [str(item) for item in preflight.get("errors", []) or []]
    warnings = [str(item) for item in preflight.get("warnings", []) or []]
    config_path_display = preflight.get("config_path_runtime") or preflight.get("config_path", "")
    lines = [
        "OpenClaw preflight failed.",
        f"stage: {preflight.get('stage', '')}",
        f"agent: {preflight.get('agent', '')}",
        f"provider: {preflight.get('provider', '')}",
        f"model: {preflight.get('model', '')}",
        f"runner: {preflight.get('runner', '')}",
        f"executable: {preflight.get('executable', '')}",
        f"config_path: {config_path_display}",
        f"failure_category: {preflight.get('failure_category', 'OPENCLAW_PREFLIGHT_FAILED')}",
    ]
    if errors:
        lines.append("errors:")
        lines.extend(f"- {item}" for item in errors)
    if warnings:
        lines.append("warnings:")
        lines.extend(f"- {item}" for item in warnings)
    if preflight.get("stderr_tail"):
        lines.append("stderr_tail:")
        lines.append(str(preflight.get("stderr_tail", "")))
    return "\n".join(lines)


def _parse_openclaw_stdout(stdout: str) -> dict[str, object]:
    try:
        parsed = json.loads(stdout or "")
        stdout_json = True
    except Exception:
        parsed = {}
        stdout_json = False
    token_usage = _normalize_token_usage(
        _json_pick(
            parsed,
            (
                "meta.usage",
                "meta.tokenUsage",
                "meta.token_usage",
                "meta.agentMeta.usage",
                "meta.executionTrace.usage",
                "meta.executionTrace.attempts[0].usage",
                "usage",
                "tokenUsage",
                "token_usage",
            ),
        )
    )
    return {
        "stdout_json": stdout_json,
        "payload": _json_pick(parsed, ("payloads[0].text", "meta.finalAssistantVisibleText", "meta.finalAssistantRawText", "payload", "response", "message")),
        "is_error": _json_pick(parsed, ("isError", "error", "meta.isError", "meta.aborted", "meta.agentMeta.isError", "meta.executionTrace.isError")),
        "error_message": _json_pick(parsed, ("error", "message", "meta.error", "meta.errorMessage", "meta.agentMeta.error", "meta.executionTrace.error")),
        "provider": _json_pick(parsed, ("meta.agentMeta.provider", "meta.executionTrace.winnerProvider", "meta.executionTrace.attempts[0].provider", "meta.systemPromptReport.provider")),
        "model": _json_pick(parsed, ("meta.agentMeta.model", "meta.executionTrace.winnerModel", "meta.executionTrace.attempts[0].model", "meta.systemPromptReport.model")),
        "runner": _json_pick(parsed, ("meta.executionTrace.runner", "meta.agentMeta.runner", "meta.systemPromptReport.runner")),
        "agent": _json_pick(parsed, ("meta.agentMeta.agentId", "meta.agentMeta.agent", "meta.systemPromptReport.agentId", "meta.systemPromptReport.agent")),
        "transport": _json_pick(parsed, ("meta.executionTrace.transport", "meta.executionTrace.attempts[0].transport", "meta.agentMeta.transport", "meta.systemPromptReport.transport")),
        "fallbackUsed": _json_pick(parsed, ("meta.executionTrace.fallbackUsed", "fallbackUsed", "meta.fallbackUsed")),
        "fallbackFrom": _json_pick(parsed, ("meta.executionTrace.fallbackFrom", "fallbackFrom", "meta.fallbackFrom")),
        "fallbackReason": _json_pick(parsed, ("meta.executionTrace.fallbackReason", "fallbackReason", "meta.fallbackReason")),
        "actual_session_key": _json_pick(parsed, ("meta.systemPromptReport.sessionKey", "sessionKey", "session_key", "session.key")),
        "session_id": _json_pick(parsed, ("meta.agentMeta.sessionId", "meta.systemPromptReport.sessionId", "sessionId", "session_id", "session.id")),
        "token_usage": token_usage,
    }


def _normalize_token_usage(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    usage: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(item, (str, int, float, bool)) or item is None:
            usage[str(key)] = item
    return usage


def _json_pick(data: object, paths: tuple[str, ...]) -> object:
    for path in paths:
        current = data
        matched = True
        for part in path.split("."):
            array_match = re.match(r"^(.+)\[(\d+)\]$", part)
            if array_match:
                key = array_match.group(1)
                index = int(array_match.group(2))
                if isinstance(current, dict) and isinstance(current.get(key), list) and len(current[key]) > index:
                    current = current[key][index]
                else:
                    matched = False
                    break
            elif isinstance(current, dict) and part in current:
                current = current[part]
            else:
                matched = False
                break
        if matched and current is not None:
            return current
    return ""


def _metadata_ok(metadata: dict[str, object], *, provider: str, model: str, runner: str) -> bool:
    return (
        metadata.get("stdout_json") is True
        and metadata.get("provider") == provider
        and metadata.get("model") == model
        and metadata.get("runner") == runner
        and metadata.get("fallbackUsed") is False
    )


def _openclaw_payload_error(metadata: dict[str, object], stdout: str, stderr: str) -> str:
    explicit = metadata.get("is_error")
    if explicit is True or str(explicit).strip().lower() in {"true", "1", "yes"}:
        detail = str(metadata.get("error_message") or metadata.get("payload") or "").strip()
        return f"OpenClaw reported an error payload: {detail or 'isError=true'}"
    combined = "\n".join(
        str(part or "")
        for part in (
            metadata.get("error_message"),
            metadata.get("payload"),
            stdout,
            stderr,
        )
    )
    lowered = combined.lower()
    markers = (
        "request failed",
        "xunfei request failed",
        "unknown description",
        "provider error",
        "gateway error",
        "rate limit",
        "quota",
        "code: 10050",
    )
    if any(marker in lowered for marker in markers):
        excerpt = _tail(combined, max_lines=20).strip()
        return f"OpenClaw provider/model error detected: {excerpt}"
    return ""


def _metadata_error(metadata: dict[str, object], *, provider: str, model: str, runner: str) -> str:
    return (
        "OpenClaw metadata validation failed: "
        f"stdout_json={metadata.get('stdout_json')}; "
        f"provider={metadata.get('provider')} expected={provider}; "
        f"model={metadata.get('model')} expected={model}; "
        f"runner={metadata.get('runner')} expected={runner}; "
        f"fallbackUsed={metadata.get('fallbackUsed')}."
    )


def _write_stage_logs(
    repo: Path,
    stage: str,
    stdout: str,
    stderr: str,
    executable: str,
    command: list[str],
    session_key: str,
    input_path: Path,
    metadata: dict[str, object],
) -> tuple[Path, Path]:
    logs_dir = artifact_dir(repo) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = logs_dir / f"{stage}_stdout.log"
    stderr_log = logs_dir / f"{stage}_stderr.log"
    header = (
        f"openclaw_executable_path: {executable}\n"
        f"session_key: {session_key}\n"
        f"input_path: {input_path}\n"
        f"stdout_json: {metadata.get('stdout_json')}\n"
        f"provider: {metadata.get('provider', '')}\n"
        f"model: {metadata.get('model', '')}\n"
        f"runner: {metadata.get('runner', '')}\n"
        f"agent: {metadata.get('agent', '')}\n"
        f"fallbackUsed: {metadata.get('fallbackUsed')}\n"
        f"command: {_redacted_command(command)} [...message omitted...]\n\n"
    )
    stdout_log.write_text(header + (stdout or ""), encoding="utf-8")
    stderr_log.write_text(header + (stderr or ""), encoding="utf-8")
    return stdout_log, stderr_log


def _append_guard_message(stderr: str, guard: dict[str, object]) -> str:
    messages: list[str] = []
    if guard.get("warning"):
        messages.append(str(guard["warning"]))
    if guard.get("error"):
        messages.append(str(guard["error"]))
    if guard.get("unexpected_modifications"):
        messages.append(f"Unexpected modifications: {guard['unexpected_modifications']}")
    if not messages:
        return stderr
    guard_text = "\n".join(messages)
    return f"{stderr.rstrip()}\n\nStage Guard:\n{guard_text}\n" if stderr else f"Stage Guard:\n{guard_text}\n"


def _append_cleanup_message(stderr: str, cleanup: dict[str, list[str]]) -> str:
    messages: list[str] = []
    if cleanup["cleaned"]:
        messages.append(f"Removed new unexpected modifications: {cleanup['cleaned']}")
    if cleanup["uncleaned"]:
        messages.append(f"Left pre-existing or unsafe unexpected modifications untouched: {cleanup['uncleaned']}")
    if not messages:
        return stderr
    cleanup_text = "\n".join(messages)
    return f"{stderr.rstrip()}\n\nStage Guard Cleanup:\n{cleanup_text}\n" if stderr else f"Stage Guard Cleanup:\n{cleanup_text}\n"


def _tail(text: str, max_lines: int = 80) -> str:
    lines = (text or "").splitlines()
    return "\n".join(lines[-max_lines:])


def _redacted_command(command: list[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    for part in command:
        if skip_next:
            redacted.append("<message omitted>")
            skip_next = False
            continue
        text = str(part)
        redacted.append(text)
        if text == "--message":
            skip_next = True
    return redacted


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")
