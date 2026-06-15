from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, MutableMapping

from r2a.core.runtime_paths import active_run_pointer_path, runtime_root
from r2a.tools.process_manager import latest_run_id, read_run_record
from r2a.workspace.manifest import read_workspace_manifest, workspace_from_manifest, workspace_manifest_exists

ACTIVE_POLLING_STATUSES = {"running", "stopping", "force_killing", "failed_to_kill"}
TERMINAL_RUN_STATUSES = {
    "cancelled",
    "completed",
    "completed_success",
    "completed_with_failure",
    "completed_with_limitations",
    "failed",
    "force_killed",
    "stopped",
    "terminal_failed",
}
AUTO_REFRESH_DISABLED_REASON = "auto-refresh disabled; manual refresh only"
RUNTIME_RECOVERY_SCAN_LIMIT = 200


def apply_workspace_session(session: MutableMapping[str, Any], workspace: dict[str, Any]) -> None:
    session["workspace"] = dict(workspace)
    session["workspace_path"] = str(workspace.get("workspace_dir", "") or "")
    session["workspace_id"] = str(workspace.get("run_id", "") or "")
    session["workspace_created"] = True


def clear_workspace_session(session: MutableMapping[str, Any]) -> None:
    session["workspace"] = None
    session["workspace_path"] = ""
    session["workspace_id"] = ""
    session["workspace_created"] = False


def restore_workspace_session(session: MutableMapping[str, Any]) -> bool:
    if session.get("workspace_created") and session.get("workspace"):
        apply_workspace_session(session, dict(session["workspace"]))
        return True
    workspace_dir = str(session.get("workspace_path", "") or "").strip()
    if not workspace_dir:
        workspace = session.get("workspace")
        if isinstance(workspace, dict):
            workspace_dir = str(workspace.get("workspace_dir", "") or "").strip()
    if not workspace_dir:
        return False
    manifest = read_workspace_manifest(workspace_dir)
    if not manifest:
        return False
    workspace = workspace_from_manifest(manifest)
    apply_workspace_session(session, workspace)
    return True


def restore_runtime_run_session(session: MutableMapping[str, Any]) -> bool:
    """Recover an empty Streamlit session from the active_run.json pointer.

    This is a lightweight recovery that reads only the pointer file,
    not a full runtime records scan. For scan-based recovery, use
    restore_runtime_run_session_by_scan() via manual UI action.

    The runtime record remains the live source of truth. This helper only
    restores UI session state; it never creates a workspace or writes workflow
    artifacts.
    """
    if isinstance(session.get("workspace"), dict) or str(session.get("workspace_path", "") or "").strip():
        return False

    # P0/P1: Only read from active_run.json pointer, never scan all records
    pointer_data = _read_active_run_pointer()
    if not pointer_data:
        return False

    run_id = str(pointer_data.get("run_id", "") or "").strip()
    repo_path = str(pointer_data.get("repo_path", "") or "").strip()
    workspace_dir = str(pointer_data.get("workspace_dir", "") or "").strip()

    if not run_id or not repo_path:
        return False

    # Verify runtime record exists and get current status
    record = read_run_record(repo_path, run_id)
    if not record:
        # Pointer is stale, clear recovery state but don't crash
        session["runtime_recovery"] = {
            "recovered": False,
            "reason": "pointer_stale",
            "message": "Active run pointer references a non-existent run.",
            "pointer_run_id": run_id,
        }
        return False

    status = str(record.get("status", "")).lower()

    # Build workspace from workspace_dir or pointer
    workspace: dict[str, Any] = {}
    if workspace_dir:
        manifest = read_workspace_manifest(workspace_dir)
        if manifest:
            workspace = workspace_from_manifest(manifest)
    if not workspace:
        # Fallback: construct minimal workspace from pointer
        workspace = {
            "workspace_dir": workspace_dir,
            "repo_path": repo_path,
            "run_id": run_id,
        }

    apply_workspace_session(session, workspace)
    session["active_run_id"] = run_id
    session["workflow_running"] = status in ACTIVE_POLLING_STATUSES

    message = (
        "Recovered active run from active_run.json pointer."
        if status in ACTIVE_POLLING_STATUSES
        else f"Recovered run from active_run.json pointer (status: {status})."
    )
    session["runtime_recovery"] = {
        "recovered": True,
        "mode": "pointer",
        "message": message,
        "selected_run_id": run_id,
        "selected_status": status,
        "selected_workspace_dir": workspace_dir,
    }
    return True


def restore_runtime_run_session_by_scan(session: MutableMapping[str, Any]) -> bool:
    """Recover an empty Streamlit session by scanning runtime records.

    This is a slower recovery method that scans runtime records to find
    active runs. It should only be triggered manually by the user.

    The runtime record remains the live source of truth. This helper only
    restores UI session state; it never creates a workspace or writes workflow
    artifacts.
    """
    if isinstance(session.get("workspace"), dict) or str(session.get("workspace_path", "") or "").strip():
        return False

    candidates = _runtime_recovery_candidates()
    active = [item for item in candidates if item["status"] in ACTIVE_POLLING_STATUSES]
    selected = active[0] if active else _latest_non_terminal_candidate(candidates)
    if not selected:
        terminal_count = sum(1 for item in candidates if item["status"] in TERMINAL_RUN_STATUSES)
        if terminal_count:
            session["runtime_recovery"] = {
                "recovered": False,
                "reason": "terminal runs only",
                "terminal_count": terminal_count,
                "message": "No active runtime run recovered; latest records are terminal.",
            }
        return False

    apply_workspace_session(session, dict(selected["workspace"]))
    session["active_run_id"] = selected["run_id"]
    session["workflow_running"] = selected["status"] in ACTIVE_POLLING_STATUSES
    mode = "active" if selected in active else "history"
    message = (
        "Recovered active run from runtime record scan."
        if mode == "active"
        else "Recovered recent non-terminal run from runtime record scan as history."
    )
    session["runtime_recovery"] = {
        "recovered": True,
        "mode": mode,
        "message": message,
        "selected_run_id": selected["run_id"],
        "selected_status": selected["status"],
        "selected_workspace_dir": selected["workspace"].get("workspace_dir", ""),
        "active_candidate_count": len(active),
        "candidates": _candidate_diagnostics(active or candidates),
    }
    return True


def _read_active_run_pointer() -> dict[str, Any]:
    """Read the active_run.json pointer file.

    Returns empty dict if file doesn't exist or is invalid.
    """
    path = active_run_pointer_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def workspace_manifest_ready(session: Mapping[str, Any]) -> bool:
    workspace = session.get("workspace")
    if not isinstance(workspace, dict):
        return False
    workspace_dir = str(workspace.get("workspace_dir", "") or session.get("workspace_path", "") or "").strip()
    if not workspace_dir:
        return False
    return workspace_manifest_exists(workspace_dir)


def planner_backend_ready(backend: str) -> tuple[bool, str]:
    normalized = (backend or "template").strip().lower()
    if normalized in {"template", "mock"}:
        return True, "template/mock selected explicitly"
    if normalized in {"ccr", "ccr_text"}:
        return True, "CCR text endpoint uses R2A_PLANNER_CCR_URL or http://127.0.0.1:3456/v1/messages"
    if normalized == "openclaw":
        return True, "OpenClaw local embedded backend is selected; workflow preflight will check WSL availability"
    if normalized == "command":
        import os

        return bool(os.environ.get("R2A_PLANNER_COMMAND", "").strip()), "requires R2A_PLANNER_COMMAND"
    if normalized in {"claude", "codex", "openai_compatible", "anthropic"}:
        import os

        ready = bool(os.environ.get("R2A_PLANNER_COMMAND", "").strip())
        return ready, "requires R2A_PLANNER_COMMAND or choose ccr_text/command/template explicitly"
    return False, f"unsupported backend {backend}"


def has_active_run(session: Mapping[str, Any]) -> bool:
    workspace = session.get("workspace")
    if not isinstance(workspace, dict):
        return False
    repo_path = str(workspace.get("repo_path", "") or "").strip()
    if not repo_path:
        return False
    run_id = str(session.get("active_run_id", "") or "").strip()
    if not run_id:
        run_id = latest_run_id(repo_path)
    if not run_id:
        return False
    record = read_run_record(repo_path, run_id)
    if not record:
        return False
    return str(record.get("status", "")) in ACTIVE_POLLING_STATUSES


def active_run_autorefresh_off_message(session: Mapping[str, Any]) -> str:
    if _auto_refresh_interval_seconds(session) > 0:
        return ""
    if has_active_run(session) or bool(session.get("workflow_running")):
        return "Status refresh: Manual. Use Refresh Status to update workflow status."
    return ""


def run_workflow_button_disabled(session: Mapping[str, Any], planner_backend: str) -> tuple[bool, str]:
    if not session.get("workspace_created"):
        return True, "workspace not created"
    if not session.get("workspace"):
        return True, "workspace missing from session"
    if not workspace_manifest_ready(session):
        return True, "WORKSPACE_MANIFEST.json missing"
    ready, message = planner_backend_ready(planner_backend)
    if not ready:
        return True, f"planner backend not ready: {message}"
    if has_active_run(session):
        return True, "workflow run already active"
    return False, ""


def sync_background_run_readonly(session: MutableMapping[str, Any]) -> None:
    """Update workflow_running/result from registry without touching workspace fields."""
    workspace = session.get("workspace")
    if not isinstance(workspace, dict):
        return
    run_id = str(session.get("active_run_id", "") or "").strip()
    repo_path = str(workspace.get("repo_path", "") or "").strip()
    if not repo_path:
        return
    if not run_id:
        run_id = latest_run_id(repo_path)
        if run_id:
            session["active_run_id"] = run_id
    if not run_id:
        return
    record = read_run_record(repo_path, run_id)
    if not record:
        return
    status = str(record.get("status", ""))
    session["workflow_running"] = status in ACTIVE_POLLING_STATUSES


def polling_should_autorefresh(session: Mapping[str, Any], *, ui_polling_enabled: bool) -> bool:
    snapshot = dict(session)
    return bool(autorefresh_decision(snapshot, ui_polling_enabled=ui_polling_enabled)["should_refresh"])


def autorefresh_decision(
    session: MutableMapping[str, Any],
    *,
    ui_polling_enabled: bool,
    terminal_grace_refreshes: int = 0,
) -> dict[str, Any]:
    return {
        "should_refresh": False,
        "reason": AUTO_REFRESH_DISABLED_REASON,
        "interval_seconds": 0,
        "status": "",
        "repo_path": "",
        "run_id": "",
        "terminal_grace_remaining": 0,
    }


def _auto_refresh_interval_seconds(session: Mapping[str, Any]) -> int:
    return 0


def _runtime_recovery_candidates() -> list[dict[str, Any]]:
    root = runtime_root()
    if not root.exists():
        return []
    paths = []
    for path in (root / "repos").glob("*/runs/*.json"):
        if path.name.endswith(".result.json"):
            continue
        try:
            paths.append(path)
        except OSError:
            continue
    paths = sorted(paths, key=_record_path_mtime, reverse=True)[:RUNTIME_RECOVERY_SCAN_LIMIT]
    candidates: list[dict[str, Any]] = []
    for path in paths:
        record = _read_runtime_record_path(path)
        if not record:
            continue
        run_id = str(record.get("run_id", "") or path.stem).strip()
        if not run_id:
            continue
        workspace = _workspace_from_runtime_record(record)
        if not workspace:
            continue
        repo_path = str(workspace.get("repo_path", "") or "").strip()
        if repo_path:
            refreshed = read_run_record(repo_path, run_id)
            if refreshed:
                record = refreshed
        status = str(record.get("status", "") or "").lower()
        candidates.append(
            {
                "path": str(path),
                "run_id": run_id,
                "status": status,
                "workspace": workspace,
                "sort_key": _record_sort_key(record, path),
            }
        )
    return sorted(candidates, key=lambda item: item["sort_key"], reverse=True)


def _latest_non_terminal_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in candidates:
        status = str(item.get("status", "") or "")
        if status and status not in TERMINAL_RUN_STATUSES:
            return item
    return None


def _workspace_from_runtime_record(record: Mapping[str, Any]) -> dict[str, Any]:
    workspace_dir = str(record.get("workspace_dir", "") or record.get("workspace_path", "") or "").strip()
    repo_path = str(record.get("repo_path", "") or "").strip()
    if not workspace_dir and repo_path:
        candidate = Path(repo_path).expanduser()
        if candidate.name.lower() == "repo":
            workspace_dir = str(candidate.parent)
    if workspace_dir:
        manifest = read_workspace_manifest(workspace_dir)
        if manifest:
            return workspace_from_manifest(manifest)
    if not workspace_dir:
        return {}
    repo_candidate = Path(workspace_dir) / "repo"
    if not repo_path and repo_candidate.is_dir():
        repo_path = str(repo_candidate)
    if not repo_path:
        return {}
    return {
        "run_id": Path(workspace_dir).name,
        "workspace_dir": workspace_dir,
        "paper_path": "",
        "repo_path": repo_path,
        "data_dir": str(Path(workspace_dir) / "data"),
        "metadata_path": str(Path(workspace_dir) / "metadata.json"),
        "goal": "",
        "repo_download": {},
        "dataset_downloads": [],
    }


def _read_runtime_record_path(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _record_sort_key(record: Mapping[str, Any], path: Path) -> float:
    for key in ("heartbeat_at", "updated_at", "started_at"):
        parsed = _parse_timestamp(record.get(key))
        if parsed is not None:
            return parsed
    return _record_path_mtime(path)


def _parse_timestamp(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _record_path_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _candidate_diagnostics(candidates: list[dict[str, Any]], limit: int = 5) -> list[dict[str, str]]:
    rows = []
    for item in candidates[:limit]:
        workspace = item.get("workspace", {}) if isinstance(item.get("workspace"), dict) else {}
        rows.append(
            {
                "run_id": str(item.get("run_id", "")),
                "status": str(item.get("status", "")),
                "workspace_dir": str(workspace.get("workspace_dir", "")),
                "record_path": str(item.get("path", "")),
            }
        )
    return rows
