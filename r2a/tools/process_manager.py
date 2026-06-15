from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
import uuid
from typing import Any, Iterator

from r2a.core.runtime_paths import active_run_pointer_path, latest_run_pointer_path, run_record_path, run_result_path, runtime_runs_dir, web_runtime_dir


RUN_ID_ENV = "R2A_RUN_ID"
RUNTIME_DIR_ENV = "R2A_RUNTIME_DIR"
REPO_PATH_ENV = "R2A_REPO_PATH"
WSL_DISTRO_ENV = "R2A_WSL_DISTRO"
ACTIVE_RUN_STATUSES = {"running", "stopping", "force_killing", "failed_to_kill"}
DEFAULT_STALE_RUN_AFTER_SECONDS = 30 * 60


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:8]


def create_run_record(repo_path: str | Path, run_id: str, **fields: Any) -> Path:
    runs = runtime_runs_dir(repo_path)
    runs.mkdir(parents=True, exist_ok=True)
    record = {
        "run_id": run_id,
        "status": fields.pop("status", "running"),
        "started_at": _now(),
        "updated_at": _now(),
        "cancel_requested": False,
        "force_requested": False,
        "termination_reason": None,
        "windows_processes": [],
        "wsl_process_groups": [],
        **fields,
    }
    path = run_record_path(repo_path, run_id)
    _write_json(path, record)
    _write_latest_run_id(repo_path, run_id)
    # P1: Write active_run pointer for UI recovery
    _write_active_run_pointer(
        repo_path,
        run_id,
        workspace_dir=fields.get("workspace_dir"),
        status=record.get("status"),
        current_stage=fields.get("current_stage"),
    )
    return path


def read_run_record(repo_path: str | Path, run_id: str) -> dict[str, Any]:
    path = run_record_path(repo_path, run_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return refresh_stale_run_record(repo_path, run_id, data=data)


def refresh_stale_run_record(
    repo_path: str | Path,
    run_id: str,
    *,
    data: dict[str, Any] | None = None,
    stale_after_seconds: int | None = None,
) -> dict[str, Any]:
    record = dict(data or _read_run_record_raw(repo_path, run_id))
    if not record:
        return {}
    if str(record.get("status", "")).lower() not in ACTIVE_RUN_STATUSES:
        return record
    cutoff = _stale_after_seconds(stale_after_seconds)
    last_seen = _last_activity_at(record)
    if last_seen is None:
        return record
    age_seconds = (datetime.now(timezone.utc) - last_seen).total_seconds()
    if age_seconds < cutoff:
        return record
    probe = _registered_process_probe(record)
    if probe["alive"]:
        return record
    updated = {
        **record,
        "status": "failed",
        "stage_status": "stale_active_run",
        "termination_reason": "STALE_ACTIVE_RUN",
        "stale_active_run": True,
        "stale_checked_at": _now(),
        "stale_after_seconds": cutoff,
        "stale_last_activity_at": last_seen.isoformat(),
        "stale_process_probe": probe,
    }
    if not updated.get("failed_stage") and updated.get("current_stage"):
        updated["failed_stage"] = updated.get("current_stage")
    updated["updated_at"] = _now()
    _write_json(run_record_path(repo_path, run_id), updated)
    _write_latest_run_id(repo_path, run_id)
    return updated


def update_run_record(repo_path: str | Path, run_id: str, **fields: Any) -> dict[str, Any]:
    path = run_record_path(repo_path, run_id)
    data = read_run_record(repo_path, run_id)
    if not data:
        data = {"run_id": run_id, "started_at": _now()}
    data.update(fields)
    data["updated_at"] = _now()
    _write_json(path, data)
    _write_latest_run_id(repo_path, run_id)
    # P1: Update active_run pointer if status/stage changed
    if "status" in fields or "current_stage" in fields:
        _write_active_run_pointer(
            repo_path,
            run_id,
            workspace_dir=data.get("workspace_dir"),
            status=data.get("status"),
            current_stage=data.get("current_stage"),
        )
    _sync_terminal_manifest_if_needed(repo_path, run_id, data)
    return data


def _read_run_record_raw(repo_path: str | Path, run_id: str) -> dict[str, Any]:
    path = run_record_path(repo_path, run_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _sync_terminal_manifest_if_needed(repo_path: str | Path, run_id: str, record: dict[str, Any]) -> None:
    try:
        from r2a.core.run_manifest import RUNTIME_TERMINAL_STATUSES, sync_manifest_terminal_status_from_runtime

        if str(record.get("status", "") or "").lower() in RUNTIME_TERMINAL_STATUSES:
            sync_manifest_terminal_status_from_runtime(repo_path, run_id, record)
    except Exception:
        return


def update_run_heartbeat(
    repo_path: str | Path,
    run_id: str,
    *,
    current_stage: str | None = None,
    stage_status: str | None = None,
    iteration: int | None = None,
    backend: str | None = None,
    fallback_used: bool | None = None,
    warning: str | None = None,
    blocker: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {"heartbeat_at": _now()}
    if current_stage is not None:
        fields["current_stage"] = current_stage
    if stage_status is not None:
        fields["stage_status"] = stage_status
    if iteration is not None:
        fields["iteration"] = iteration
    if backend is not None:
        fields["backend"] = backend
    if fallback_used is not None:
        fields["fallback_used"] = fallback_used
    if warning is not None:
        fields["warning"] = warning
    if blocker is not None:
        fields["blocker"] = blocker
    return update_run_record(repo_path, run_id, **fields)


def latest_run_id(repo_path: str | Path) -> str:
    path = latest_run_pointer_path(repo_path)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def latest_run_record(repo_path: str | Path) -> dict[str, Any]:
    run_id = latest_run_id(repo_path)
    if not run_id:
        return {}
    return read_run_record(repo_path, run_id)


def write_run_result(repo_path: str | Path, run_id: str, result: dict[str, Any]) -> Path:
    path = run_result_path(repo_path, run_id)
    _write_json(path, result)
    return path


def read_run_result(repo_path: str | Path, run_id: str) -> dict[str, Any]:
    path = run_result_path(repo_path, run_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def register_windows_process(pid: int, *, command: list[str] | None = None, env: dict[str, str] | None = None) -> None:
    context = _context_from_env(env)
    if not context:
        return
    repo_path, run_id = context
    data = read_run_record(repo_path, run_id)
    processes = list(data.get("windows_processes", []))
    entry = {"pid": int(pid), "command": _command_label(command), "registered_at": _now()}
    if not any(item.get("pid") == entry["pid"] for item in processes if isinstance(item, dict)):
        processes.append(entry)
    update_run_record(repo_path, run_id, windows_processes=processes, heartbeat_at=_now())


def register_wsl_pgid(pgid: int, *, distro: str = "", env: dict[str, str] | None = None) -> None:
    context = _context_from_env(env)
    if not context:
        return
    repo_path, run_id = context
    data = read_run_record(repo_path, run_id)
    groups = list(data.get("wsl_process_groups", []))
    entry = {"pgid": int(pgid), "distro": distro or os.environ.get(WSL_DISTRO_ENV, "Ubuntu"), "registered_at": _now()}
    if not any(item.get("pgid") == entry["pgid"] for item in groups if isinstance(item, dict)):
        groups.append(entry)
    update_run_record(repo_path, run_id, wsl_process_groups=groups, heartbeat_at=_now())


def request_cancel(repo_path: str | Path, run_id: str, *, force: bool = False, reason: str = "user_requested") -> dict[str, Any]:
    status = "force_killing" if force else "stopping"
    update_run_record(
        repo_path,
        run_id,
        status=status,
        cancel_requested=True,
        force_requested=force,
        cancel_requested_at=_now(),
        termination_reason=reason,
    )
    return terminate_run(repo_path, run_id, force=force, mark_stopped=force)


def terminate_run(repo_path: str | Path, run_id: str, *, force: bool = False, wait_seconds: int = 5, mark_stopped: bool = True) -> dict[str, Any]:
    data = read_run_record(repo_path, run_id)
    windows_results = []
    wsl_results = []
    for group in data.get("wsl_process_groups", []) or []:
        if not isinstance(group, dict) or not group.get("pgid"):
            continue
        wsl_results.append(_terminate_wsl_group(int(group["pgid"]), str(group.get("distro") or "Ubuntu"), force=force))
    for process in data.get("windows_processes", []) or []:
        if not isinstance(process, dict) or not process.get("pid"):
            continue
        windows_results.append(_terminate_windows_process(int(process["pid"]), force=force))
    time.sleep(max(0, min(wait_seconds, 10)))
    residuals = {
        "windows": [_windows_alive(int(item["pid"])) for item in data.get("windows_processes", []) or [] if isinstance(item, dict) and item.get("pid")],
        "wsl": [_wsl_group_alive(int(item["pgid"]), str(item.get("distro") or "Ubuntu")) for item in data.get("wsl_process_groups", []) or [] if isinstance(item, dict) and item.get("pgid")],
    }
    alive = any(residuals["windows"]) or any(residuals["wsl"])
    fields: dict[str, Any] = {
        "termination_result": {
            "windows": windows_results,
            "wsl": wsl_results,
            "residuals": residuals,
        },
    }
    if alive:
        fields["status"] = "failed_to_kill"
    elif mark_stopped:
        fields["status"] = "force_killed" if force else "stopped"
    else:
        fields["status"] = "stopping"
        fields["stage_status"] = "stop_requested_waiting_for_safe_boundary"
    return update_run_record(repo_path, run_id, **fields)


def cancel_requested(env: dict[str, str] | None = None) -> bool:
    context = _context_from_env(env)
    if not context:
        return False
    repo_path, run_id = context
    data = read_run_record(repo_path, run_id)
    return bool(data.get("cancel_requested"))


def _stale_after_seconds(value: int | None = None) -> int:
    if value is not None:
        return max(1, int(value))
    raw = os.environ.get("R2A_STALE_RUN_AFTER_SECONDS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            return DEFAULT_STALE_RUN_AFTER_SECONDS
    return DEFAULT_STALE_RUN_AFTER_SECONDS


def _last_activity_at(record: dict[str, Any]) -> datetime | None:
    for key in ("heartbeat_at", "updated_at", "started_at"):
        parsed = _parse_datetime(record.get(key))
        if parsed is not None:
            return parsed
    return None


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _registered_process_probe(record: dict[str, Any]) -> dict[str, Any]:
    windows_results = [
        _windows_alive(int(item["pid"]))
        for item in record.get("windows_processes", []) or []
        if isinstance(item, dict) and item.get("pid")
    ]
    wsl_results = [
        _wsl_group_alive(int(item["pgid"]), str(item.get("distro") or record.get("wsl_distro") or "Ubuntu"))
        for item in record.get("wsl_process_groups", []) or []
        if isinstance(item, dict) and item.get("pgid")
    ]
    return {
        "alive": any(windows_results) or any(wsl_results),
        "windows": windows_results,
        "wsl": wsl_results,
        "registered_windows_count": len(windows_results),
        "registered_wsl_count": len(wsl_results),
    }


@contextmanager
def workflow_run_context(repo_path: str | Path, run_id: str, *, wsl_distro: str = "Ubuntu") -> Iterator[None]:
    previous = {name: os.environ.get(name) for name in (RUN_ID_ENV, RUNTIME_DIR_ENV, REPO_PATH_ENV, WSL_DISTRO_ENV, "R2A_WSL_PGID_FILE")}
    os.environ[RUN_ID_ENV] = run_id
    os.environ[RUNTIME_DIR_ENV] = str(runtime_runs_dir(repo_path))
    os.environ[REPO_PATH_ENV] = str(Path(repo_path).resolve())
    os.environ[WSL_DISTRO_ENV] = wsl_distro
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _context_from_env(env: dict[str, str] | None = None) -> tuple[Path, str] | None:
    source = env or os.environ
    run_id = source.get(RUN_ID_ENV, "")
    repo_path = source.get(REPO_PATH_ENV, "")
    if not run_id or not repo_path:
        return None
    return Path(repo_path), run_id


def _terminate_windows_process(pid: int, *, force: bool) -> dict[str, Any]:
    command = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        command.append("/F")
    completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return {"pid": pid, "returncode": int(completed.returncode), "force": force}


def _terminate_wsl_group(pgid: int, distro: str, *, force: bool) -> dict[str, Any]:
    signal = "KILL" if force else "TERM"
    completed = subprocess.run(
        ["wsl", "-d", distro, "--", "bash", "-lc", f"kill -{signal} -- -{pgid} 2>/dev/null"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return {"pgid": pgid, "distro": distro, "signal": signal, "returncode": int(completed.returncode)}


def _windows_alive(pid: int) -> bool:
    completed = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, check=False)
    return str(pid) in (completed.stdout or "")


def _wsl_group_alive(pgid: int, distro: str) -> bool:
    completed = subprocess.run(
        ["wsl", "-d", distro, "--", "bash", "-lc", f"ps -o pgid= -g {pgid} 2>/dev/null | grep -q {pgid}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def _command_label(command: list[str] | None) -> str:
    if not command:
        return ""
    return " ".join(Path(str(part)).name if index == 0 else str(part) for index, part in enumerate(command[:4]))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _write_latest_run_id(repo_path: str | Path, run_id: str) -> None:
    path = latest_run_pointer_path(repo_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(run_id), encoding="utf-8")


def _write_active_run_pointer(
    repo_path: str | Path,
    run_id: str,
    workspace_dir: str | None = None,
    status: str | None = None,
    current_stage: str | None = None,
) -> None:
    """Write the active_run.json pointer for UI recovery.

    This enables fast UI recovery without scanning all runtime records.
    The pointer is updated when a run is created or its status changes.
    """
    path = active_run_pointer_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "run_id": str(run_id),
        "repo_path": str(repo_path),
        "workspace_dir": str(workspace_dir or ""),
        "status": str(status or "running"),
        "current_stage": str(current_stage or ""),
        "updated_at": _now(),
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
