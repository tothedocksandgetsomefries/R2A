from __future__ import annotations

import json
from pathlib import Path

from r2a.core.runtime_paths import active_run_pointer_path, runtime_root
from r2a.tools.process_manager import create_run_record, update_run_record
from r2a.workspace.manifest import build_workspace_manifest, write_workspace_manifest
from r2a_web.workspace_state import (
    restore_runtime_run_session,
    restore_runtime_run_session_by_scan,
    run_workflow_button_disabled,
)


def _workspace(tmp_path: Path, name: str) -> tuple[Path, Path]:
    workspace = tmp_path / name
    repo = workspace / "repo"
    repo.mkdir(parents=True)
    manifest = build_workspace_manifest(
        workspace_id=name,
        workspace_path=workspace,
        paper_path="",
        extra={"repo_path": str(repo), "data_dir": str(workspace / "data"), "goal": "demo"},
    )
    write_workspace_manifest(workspace, manifest)
    return workspace, repo


def _write_active_run_pointer(tmp_path: Path, run_id: str, repo_path: str, workspace_dir: str, status: str = "running") -> None:
    """Helper to write active_run.json pointer."""
    pointer_path = active_run_pointer_path()
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "run_id": run_id,
        "repo_path": repo_path,
        "workspace_dir": workspace_dir,
        "status": status,
        "updated_at": "2026-06-15T00:00:00Z",
    }
    pointer_path.write_text(json.dumps(data), encoding="utf-8")


# P0 tests: restore_runtime_run_session now only reads from pointer


def test_empty_session_recovers_from_pointer(tmp_path: Path, monkeypatch) -> None:
    """P1: Recovery should use active_run.json pointer, not scan."""
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime"))
    workspace, repo = _workspace(tmp_path, "run-active")
    create_run_record(repo, "run-1", status="running", current_stage="engineer", workspace_dir=str(workspace))
    _write_active_run_pointer(tmp_path, "run-1", str(repo), str(workspace))
    session: dict = {}

    recovered = restore_runtime_run_session(session)

    assert recovered is True
    assert session["workspace"]["workspace_dir"] == str(workspace)
    assert session["workspace"]["repo_path"] == str(repo)
    assert session["active_run_id"] == "run-1"
    assert session["workflow_running"] is True
    assert session["recovered_active_run"] is True
    assert "pointer" in session["runtime_recovery"]["mode"]


def test_no_pointer_no_recovery(tmp_path: Path, monkeypatch) -> None:
    """P0: Without pointer, restore_runtime_run_session should not scan.

    Note: create_run_record now writes active_run_pointer, so this test
    checks that recovery fails when pointer is explicitly deleted.
    """
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime"))
    workspace, repo = _workspace(tmp_path, "run-active")
    create_run_record(repo, "run-1", status="running", current_stage="engineer", workspace_dir=str(workspace))
    # Delete pointer to simulate no-pointer state
    pointer_path = active_run_pointer_path()
    if pointer_path.exists():
        pointer_path.unlink()
    session: dict = {}

    recovered = restore_runtime_run_session(session)

    assert recovered is False
    assert "workspace" not in session


def test_pointer_stale_no_crash(tmp_path: Path, monkeypatch) -> None:
    """P1: Stale pointer (run doesn't exist) should not crash."""
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime"))
    workspace, repo = _workspace(tmp_path, "run-active")
    # Don't create run record
    _write_active_run_pointer(tmp_path, "run-nonexistent", str(repo), str(workspace))
    session: dict = {}

    recovered = restore_runtime_run_session(session)

    assert recovered is False
    assert session["runtime_recovery"]["reason"] == "pointer_stale"


def test_pointer_corrupted_json_no_crash(tmp_path: Path, monkeypatch) -> None:
    """P1: Corrupted pointer file should not crash."""
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime"))
    pointer_path = active_run_pointer_path()
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text("{invalid json", encoding="utf-8")
    session: dict = {}

    recovered = restore_runtime_run_session(session)

    assert recovered is False


def test_pointer_missing_fields_no_crash(tmp_path: Path, monkeypatch) -> None:
    """P1: Pointer with missing required fields should not crash."""
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime"))
    pointer_path = active_run_pointer_path()
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(json.dumps({"run_id": ""}), encoding="utf-8")
    session: dict = {}

    recovered = restore_runtime_run_session(session)

    assert recovered is False


def test_pointer_terminal_run_not_running(tmp_path: Path, monkeypatch) -> None:
    """P1: Pointer to terminal run should NOT be recovered as active run.

    Terminal runs (force_killed, completed, failed, etc) should not be
    auto-recovered on fresh startup to avoid confusing historical runs
    with current active runs.
    """
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime"))
    workspace, repo = _workspace(tmp_path, "run-terminal")
    create_run_record(repo, "run-dead", status="force_killed", current_stage="engineer", workspace_dir=str(workspace))
    _write_active_run_pointer(tmp_path, "run-dead", str(repo), str(workspace), status="force_killed")
    session: dict = {}

    recovered = restore_runtime_run_session(session)

    assert recovered is False  # Terminal run should NOT be recovered
    assert session.get("workflow_running") is not True  # Not running
    assert session["runtime_recovery"]["reason"] == "terminal_run"  # Marked as terminal


def test_pointer_stopping_stop_requested_not_recovered_as_active(tmp_path: Path, monkeypatch) -> None:
    """A stopped/cancelling pointer must not lock the UI on fresh startup."""
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime"))
    workspace, repo = _workspace(tmp_path, "run-stopping")
    create_run_record(
        repo,
        "run-stop",
        status="stopping",
        current_stage="engineer",
        stage_status="stop_requested",
        stop_reason="user_requested",
        workspace_dir=str(workspace),
    )
    _write_active_run_pointer(tmp_path, "run-stop", str(repo), str(workspace), status="stopping")
    session: dict = {}

    recovered = restore_runtime_run_session(session)

    assert recovered is False
    assert session.get("active_run_id") in {None, ""}
    assert session.get("workflow_running") is not True
    assert session["runtime_recovery"]["reason"] == "stale_active_pointer"


def test_pointer_final_status_cancelled_not_recovered_as_active(tmp_path: Path, monkeypatch) -> None:
    """final_status=cancelled wins even if the runtime status is still running."""
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime"))
    workspace, repo = _workspace(tmp_path, "run-cancelled")
    record_path = create_run_record(repo, "run-cancelled", status="running", current_stage="engineer", workspace_dir=str(workspace))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["final_status"] = "cancelled"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    _write_active_run_pointer(tmp_path, "run-cancelled", str(repo), str(workspace), status="running")
    session: dict = {}

    recovered = restore_runtime_run_session(session)

    assert recovered is False
    assert session.get("workflow_running") is not True
    assert session["runtime_recovery"]["reason"] == "terminal_run"


def test_terminal_pointer_statuses_are_not_recovered_as_active(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime"))
    for status in ("completed_with_failure", "terminal_failed", "failed", "stopped"):
        workspace, repo = _workspace(tmp_path, f"run-{status}")
        run_id = f"run-{status}"
        create_run_record(repo, run_id, status=status, current_stage="final", workspace_dir=str(workspace))
        _write_active_run_pointer(tmp_path, run_id, str(repo), str(workspace), status=status)
        session: dict = {}

        assert restore_runtime_run_session(session) is False
        assert session.get("workflow_running") is not True
        assert session["runtime_recovery"]["reason"] == "terminal_run"


def test_stale_active_pointer_does_not_disable_run_workflow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime"))
    workspace, repo = _workspace(tmp_path, "run-button")
    create_run_record(
        repo,
        "run-stop",
        status="stopping",
        current_stage="engineer",
        stage_status="stop_requested",
        stop_reason="user_requested",
        workspace_dir=str(workspace),
    )
    session: dict = {
        "workspace_created": True,
        "workspace": {
            "workspace_dir": str(workspace),
            "repo_path": str(repo),
        },
        "active_run_id": "run-stop",
    }

    disabled, reason = run_workflow_button_disabled(session, "ccr_text")

    assert disabled is False
    assert reason == ""


# P0 tests: restore_runtime_run_session_by_scan for manual recovery


def test_scan_recovers_active_runtime_run(tmp_path: Path, monkeypatch) -> None:
    """Manual scan should find active runs."""
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime"))
    workspace, repo = _workspace(tmp_path, "run-active")
    create_run_record(repo, "run-1", status="running", current_stage="engineer", workspace_dir=str(workspace))
    session: dict = {}

    recovered = restore_runtime_run_session_by_scan(session)

    assert recovered is True
    assert session["workspace"]["workspace_dir"] == str(workspace)
    assert session["workspace"]["repo_path"] == str(repo)
    assert session["active_run_id"] == "run-1"
    assert session["workflow_running"] is True
    assert session["recovered_active_run"] is True


def test_scan_selects_most_recent_active_run(tmp_path: Path, monkeypatch) -> None:
    """Scan should select the most recently updated active run."""
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime"))
    older_workspace, older_repo = _workspace(tmp_path, "run-older")
    newer_workspace, newer_repo = _workspace(tmp_path, "run-newer")
    older_record = create_run_record(older_repo, "run-older", status="running", workspace_dir=str(older_workspace))
    newer_record = create_run_record(newer_repo, "run-newer", status="running", workspace_dir=str(newer_workspace))
    older = json.loads(older_record.read_text(encoding="utf-8"))
    older["updated_at"] = "2029-01-01T00:00:00+00:00"
    older_record.write_text(json.dumps(older), encoding="utf-8")
    newer = json.loads(newer_record.read_text(encoding="utf-8"))
    newer["updated_at"] = "2030-01-01T00:00:00+00:00"
    newer_record.write_text(json.dumps(newer), encoding="utf-8")
    session: dict = {}

    assert restore_runtime_run_session_by_scan(session) is True

    assert session["active_run_id"] == "run-newer"
    assert session["workspace"]["workspace_dir"] == str(newer_workspace)
    assert session["runtime_recovery"]["active_candidate_count"] == 2


def test_scan_terminal_run_not_recovered_as_active(tmp_path: Path, monkeypatch) -> None:
    """Terminal runs should not be recovered as active."""
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime"))
    workspace, repo = _workspace(tmp_path, "run-terminal")
    create_run_record(repo, "run-dead", status="force_killed", current_stage="engineer", workspace_dir=str(workspace))
    session: dict = {}

    recovered = restore_runtime_run_session_by_scan(session)

    assert recovered is False
    assert "workspace" not in session
    assert session["runtime_recovery"]["reason"] == "terminal runs only"


def test_scan_does_not_require_web_server_registry(tmp_path: Path, monkeypatch) -> None:
    """Scan should work without web_server.json."""
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime"))
    workspace, repo = _workspace(tmp_path, "run-no-web-registry")
    create_run_record(repo, "run-active", status="running", workspace_dir=str(workspace))
    assert not (runtime_root() / "web" / "web_server.json").exists()

    session: dict = {}

    assert restore_runtime_run_session_by_scan(session) is True
    assert session["active_run_id"] == "run-active"


# P1 tests: active_run pointer is written on run creation/update


def test_create_run_record_writes_active_run_pointer(tmp_path: Path, monkeypatch) -> None:
    """P1: create_run_record should write active_run.json pointer."""
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime"))
    workspace, repo = _workspace(tmp_path, "run-pointer")

    create_run_record(repo, "run-1", status="running", workspace_dir=str(workspace))

    pointer_path = active_run_pointer_path()
    assert pointer_path.exists()
    data = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert data["run_id"] == "run-1"
    assert data["repo_path"] == str(repo)
    assert data["workspace_dir"] == str(workspace)


def test_update_run_record_updates_pointer_status(tmp_path: Path, monkeypatch) -> None:
    """P1: update_run_record should update active_run.json status."""
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime"))
    workspace, repo = _workspace(tmp_path, "run-update")
    create_run_record(repo, "run-1", status="running", workspace_dir=str(workspace))

    update_run_record(repo, "run-1", status="force_killed", current_stage="final")

    pointer_path = active_run_pointer_path()
    data = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert data["status"] == "force_killed"
    assert data["current_stage"] == "final"


# Auto-refresh Off message tests (should still work)


def test_active_run_autorefresh_off_message_when_active(tmp_path: Path, monkeypatch) -> None:
    """Auto-refresh off message should still work with pointer recovery."""
    from r2a_web.workspace_state import active_run_autorefresh_off_message

    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime"))
    workspace, repo = _workspace(tmp_path, "run-active")
    create_run_record(repo, "run-1", status="running", workspace_dir=str(workspace))
    _write_active_run_pointer(tmp_path, "run-1", str(repo), str(workspace))

    session: dict = {"auto_refresh_interval_seconds": 0}
    restore_runtime_run_session(session)

    message = active_run_autorefresh_off_message(session)
    assert "Status refresh: Manual" in message
    assert "Refresh Status" in message
