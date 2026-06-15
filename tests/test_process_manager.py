from __future__ import annotations

import json
import subprocess

from r2a.tools.process_manager import (
    create_run_record,
    latest_run_id,
    read_run_record,
    read_run_result,
    register_windows_process,
    register_wsl_pgid,
    request_cancel,
    workflow_run_context,
    write_run_result,
)
from r2a.core.runtime_paths import run_record_path
from r2a.core.run_manifest import latest_run_manifest_path, run_manifest_path


def test_run_registry_records_processes_and_cancel(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime-root"))
    create_run_record(tmp_path, "run-test", status="running", wsl_distro="Ubuntu")

    with workflow_run_context(tmp_path, "run-test", wsl_distro="Ubuntu"):
        register_windows_process(1234, command=["ccr", "code"])
        register_wsl_pgid(5678, distro="Ubuntu")

    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0] == "wsl" and "ps -o pgid=" in command[-1]:
            return subprocess.CompletedProcess(command, 1, "", "")
        if command[0] == "tasklist":
            return subprocess.CompletedProcess(command, 0, "INFO: No tasks are running", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("r2a.tools.process_manager.subprocess.run", fake_run)

    result = request_cancel(tmp_path, "run-test", force=True)

    assert result["status"] == "force_killed"
    assert any(command[:3] == ["taskkill", "/PID", "1234"] for command in commands)
    assert any(command[:4] == ["wsl", "-d", "Ubuntu", "--"] for command in commands)
    record = read_run_record(tmp_path, "run-test")
    assert record["cancel_requested"] is True


def test_runtime_registry_is_outside_repo_and_survives_refresh(tmp_path, monkeypatch) -> None:
    runtime_root = tmp_path / "runtime-root"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(runtime_root))

    record_path = create_run_record(repo, "run-refresh", status="running")
    write_run_result(repo, "run-refresh", {"ok": True})

    assert repo not in record_path.resolve().parents
    assert ".r2a" not in record_path.as_posix()
    assert latest_run_id(repo) == "run-refresh"
    assert read_run_record(repo, "run-refresh")["status"] == "running"
    assert read_run_result(repo, "run-refresh") == {"ok": True}


def test_non_force_cancel_waits_for_safe_boundary(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime-root"))
    create_run_record(tmp_path, "run-test", status="running", wsl_distro="Ubuntu")

    monkeypatch.setattr("r2a.tools.process_manager.subprocess.run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", ""))

    result = request_cancel(tmp_path, "run-test", force=False)

    assert result["status"] == "stopping"
    assert result["cancel_requested"] is True
    assert result["stage_status"] == "stop_requested_waiting_for_safe_boundary"


def test_runtime_record_path_rejects_traversal(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime-root"))

    try:
        run_record_path(tmp_path, "../bad")
    except ValueError as exc:
        assert "Invalid run id" in str(exc)
    else:
        raise AssertionError("path traversal run id should fail")


def test_force_cancel_syncs_run_manifest_terminal_top_level(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime-root"))
    monkeypatch.setattr("r2a.tools.process_manager.time.sleep", lambda *_args, **_kwargs: None)
    create_run_record(tmp_path, "run-runtime", status="running", current_stage="engineer", workspace_dir=str(tmp_path))
    manifest = {
        "schema_version": 1,
        "run_id": "manifest-run",
        "repo_path": str(tmp_path),
        "status": "RUNNING",
        "current_stage": "engineer",
        "finished_at": "",
        "stop_reason": "READY_FOR_NEXT_STAGE",
        "stages": {},
    }
    latest_run_manifest_path(tmp_path).parent.mkdir(parents=True)
    latest_run_manifest_path(tmp_path).write_text(json.dumps(manifest), encoding="utf-8")
    run_manifest_path(tmp_path, "manifest-run").parent.mkdir(parents=True)
    run_manifest_path(tmp_path, "manifest-run").write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(
        "r2a.tools.process_manager.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "INFO: No tasks are running", ""),
    )

    result = request_cancel(tmp_path, "run-runtime", force=True, reason="user_requested_after_status_check")

    assert result["status"] == "force_killed"
    updated = json.loads(latest_run_manifest_path(tmp_path).read_text(encoding="utf-8"))
    assert updated["status"] == "force_killed"
    assert updated["current_stage"] == "engineer"
    assert updated["stop_reason"] == "user_requested_after_status_check"
    assert updated["runtime_status_source"] == "runtime_record"
    assert updated["runtime_run_id"] == "run-runtime"
    primary = json.loads(run_manifest_path(tmp_path, "manifest-run").read_text(encoding="utf-8"))
    assert primary["status"] == "force_killed"


def test_stale_active_run_without_live_process_marks_failed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime-root"))
    monkeypatch.setenv("R2A_STALE_RUN_AFTER_SECONDS", "1")
    create_run_record(tmp_path, "run-stale", status="running", current_stage="reviewer", wsl_distro="Ubuntu")
    record_path = run_record_path(tmp_path, "run-stale")
    record = read_run_record(tmp_path, "run-stale")
    record.update(
        {
            "heartbeat_at": "2000-01-01T00:00:00+00:00",
            "windows_processes": [{"pid": 1234}],
            "wsl_process_groups": [{"pgid": 5678, "distro": "Ubuntu"}],
        }
    )
    record_path.write_text(json.dumps(record), encoding="utf-8")

    def fake_run(command, **kwargs):
        if command[0] == "tasklist":
            return subprocess.CompletedProcess(command, 0, "INFO: No tasks are running", "")
        if command[0] == "wsl":
            return subprocess.CompletedProcess(command, 1, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("r2a.tools.process_manager.subprocess.run", fake_run)

    refreshed = read_run_record(tmp_path, "run-stale")

    assert refreshed["status"] == "failed"
    assert refreshed["stage_status"] == "stale_active_run"
    assert refreshed["termination_reason"] == "STALE_ACTIVE_RUN"
    assert refreshed["failed_stage"] == "reviewer"
    assert refreshed["stale_process_probe"]["alive"] is False


def test_stale_active_run_with_live_registered_process_stays_running(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime-root"))
    monkeypatch.setenv("R2A_STALE_RUN_AFTER_SECONDS", "1")
    create_run_record(tmp_path, "run-live", status="running", current_stage="engineer")
    record_path = run_record_path(tmp_path, "run-live")
    record = read_run_record(tmp_path, "run-live")
    record.update({"heartbeat_at": "2000-01-01T00:00:00+00:00", "windows_processes": [{"pid": 4321}]})
    record_path.write_text(json.dumps(record), encoding="utf-8")

    monkeypatch.setattr(
        "r2a.tools.process_manager.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "python.exe 4321 Console", ""),
    )

    refreshed = read_run_record(tmp_path, "run-live")

    assert refreshed["status"] == "running"
    assert refreshed.get("termination_reason") is None
