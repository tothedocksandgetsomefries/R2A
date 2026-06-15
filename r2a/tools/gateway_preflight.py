from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any

from r2a.core.model_capabilities import check_stage_policy_compatibility, default_gateway_capability_profile


GATEWAY_EXECUTABLE_NOT_FOUND = "GATEWAY_EXECUTABLE_NOT_FOUND"
GATEWAY_NOT_RUNNING = "GATEWAY_NOT_RUNNING"
GATEWAY_START_FAILED = "GATEWAY_START_FAILED"
GATEWAY_CONFIG_INVALID = "GATEWAY_CONFIG_INVALID"
MODEL_CAPABILITY_MISMATCH = "MODEL_CAPABILITY_MISMATCH"

PROVIDER_ENV_CANDIDATES = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_CUSTOM_MODEL_OPTION",
    "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_API_BASE",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "CCR_API_KEY",
)


def check_gateway_preflight(
    executable: str | None = None,
    *,
    stages: list[str] | tuple[str, ...] = (),
    preflight_required: bool = True,
    auto_start: bool = False,
    startup_timeout_seconds: int = 20,
) -> dict[str, Any]:
    requested = (executable or "ccr").strip().strip('"') or "ccr"
    resolved = requested if any(separator in requested for separator in ("/", "\\")) else shutil.which(requested)
    result: dict[str, Any] = {
        "ok": True,
        "gateway_type": "ccr" if _is_ccr(requested) else "claude",
        "gateway_running": None,
        "gateway_executable": requested,
        "resolved_path": resolved or "",
        "gateway_version": "",
        "config_source": "",
        "provider": "",
        "model": "",
        "mode": "",
        "port": "",
        "pid": "",
        "logs_dir": "",
        "env_vars_present": _present_env_vars(),
        "warnings": [],
        "errors": [],
    }
    if not resolved and not any(separator in requested for separator in ("/", "\\")):
        result["ok"] = False
        result["errors"].append(GATEWAY_EXECUTABLE_NOT_FOUND)
        return result

    candidate = resolved or requested
    version = _run_quiet([candidate, "version"] if _is_ccr(candidate) else [candidate, "--version"])
    result["gateway_version"] = _combined_output(version)
    if version.returncode not in {0, None}:
        result["ok"] = False
        result["errors"].append(GATEWAY_EXECUTABLE_NOT_FOUND)
        return result

    if not _is_ccr(candidate):
        _add_capability_checks(result, stages)
        return result

    config = _read_ccr_config()
    result.update(config)
    status = _ccr_status(candidate)
    result.update(status)
    if not result["gateway_running"] and auto_start:
        start = _run_quiet([candidate, "start"], timeout=startup_timeout_seconds)
        if start.returncode == 0:
            deadline = time.monotonic() + startup_timeout_seconds
            while time.monotonic() < deadline:
                status = _ccr_status(candidate)
                result.update(status)
                if result["gateway_running"]:
                    break
                time.sleep(1)
        if not result["gateway_running"]:
            result["errors"].append(GATEWAY_START_FAILED)
    elif preflight_required and not result["gateway_running"]:
        result["errors"].append(GATEWAY_NOT_RUNNING)

    _add_capability_checks(result, stages)
    result["ok"] = not result["errors"]
    return result


def _add_capability_checks(result: dict[str, Any], stages: list[str] | tuple[str, ...]) -> None:
    profile = default_gateway_capability_profile()
    checks = [check_stage_policy_compatibility(stage, profile) for stage in stages]
    result["stage_policy_checks"] = checks
    if any(not item["ok"] for item in checks):
        result["errors"].append(MODEL_CAPABILITY_MISMATCH)


def _is_ccr(executable: str) -> bool:
    return Path(executable or "").stem.lower() == "ccr"


def _run_quiet(command: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(command, 127, "", f"{type(exc).__name__}: {exc}")


def _combined_output(completed: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip())


def _read_ccr_config() -> dict[str, Any]:
    path = Path.home() / ".claude-code-router" / "config.json"
    logs_dir = Path.home() / ".claude-code-router" / "logs"
    data: dict[str, Any] = {"config_source": str(path), "logs_dir": str(logs_dir), "provider": "", "model": "", "mode": ""}
    if not path.exists():
        data["warnings"] = ["CCR config not found."]
        return data
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data["warnings"] = [GATEWAY_CONFIG_INVALID]
        return data
    router = config.get("Router", {}) if isinstance(config.get("Router"), dict) else {}
    route = str(router.get("default", "") or "")
    provider, model = _split_route(route)
    data["provider"] = provider
    data["model"] = model
    data["mode"] = "default"
    return data


def _split_route(route: str) -> tuple[str, str]:
    parts = [part.strip() for part in route.split(",", 1)]
    if len(parts) == 2:
        return parts[0], parts[1]
    return route, ""


def _ccr_status(executable: str) -> dict[str, Any]:
    completed = _run_quiet([executable, "status"])
    text = _combined_output(completed)
    running = "Status: Running" in text or "✅ Status: Running" in text
    pid_match = re.search(r"Process ID:\s*(\d+)", text)
    port_match = re.search(r"Port:\s*(\d+)", text)
    return {
        "gateway_running": running,
        "pid": pid_match.group(1) if pid_match else "",
        "port": port_match.group(1) if port_match else "",
        "status_output": text,
    }


def _present_env_vars() -> dict[str, str]:
    present: dict[str, str] = {}
    for name in PROVIDER_ENV_CANDIDATES:
        value = os.environ.get(name, "")
        if value:
            present[name] = f"SET len={len(value)}"
    return present
