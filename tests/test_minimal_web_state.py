from __future__ import annotations

import json
from pathlib import Path

import pytest

from r2a.workspace.manager import create_workspace
from r2a.workspace.manifest import workspace_manifest_exists, workspace_manifest_path
from r2a_web import workspace_state as ws


def _session() -> dict:
    return {
        "workspace": None,
        "workspace_path": "",
        "workspace_id": "",
        "workspace_created": False,
        "active_run_id": "",
        "workflow_running": False,
    }


def test_create_workspace_writes_manifest(tmp_path: Path) -> None:
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4")
    workspace = create_workspace(tmp_path / "runs", goal="demo", paper_file_path=paper)

    assert workspace_manifest_exists(workspace["workspace_dir"])
    manifest = json.loads(workspace_manifest_path(workspace["workspace_dir"]).read_text(encoding="utf-8"))
    assert manifest["workspace_id"] == workspace["run_id"]
    assert manifest["status"] == "created"
    assert manifest["planner_backend"] == "openclaw"
    assert manifest["engineer_executor"] == "openclaw"


def test_create_workspace_persists_after_rerun(tmp_path: Path) -> None:
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4")
    workspace = create_workspace(tmp_path / "runs", goal="demo", paper_file_path=paper)
    session = _session()
    ws.apply_workspace_session(session, workspace)

    session_after = _session()
    session_after["workspace_path"] = workspace["workspace_dir"]
    ws.restore_workspace_session(session_after)

    assert session_after["workspace_created"] is True
    assert session_after["workspace"]["workspace_dir"] == workspace["workspace_dir"]


def test_workspace_restores_from_manifest(tmp_path: Path) -> None:
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4")
    workspace = create_workspace(tmp_path / "runs", goal="demo", paper_file_path=paper)
    session = _session()
    session["workspace_path"] = workspace["workspace_dir"]

    assert ws.restore_workspace_session(session) is True
    assert session["workspace"]["repo_path"] == workspace["repo_path"]


def test_polling_does_not_clear_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4")
    workspace = create_workspace(tmp_path / "runs", goal="demo", paper_file_path=paper)
    session = _session()
    ws.apply_workspace_session(session, workspace)
    session["active_run_id"] = "run_test"

    monkeypatch.setattr(
        "r2a_web.workspace_state.read_run_record",
        lambda repo, run_id: {"status": "running"},
    )
    ws.sync_background_run_readonly(session)

    assert session["workspace_created"] is True
    assert session["workspace"]["workspace_dir"] == workspace["workspace_dir"]


def test_run_button_enabled_after_workspace_created(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path / "runs", goal="demo")
    session = _session()
    ws.apply_workspace_session(session, workspace)

    disabled, reason = ws.run_workflow_button_disabled(session, "ccr_text")

    assert disabled is False
    assert reason == ""


def test_run_button_disabled_before_workspace_created() -> None:
    session = _session()
    disabled, reason = ws.run_workflow_button_disabled(session, "ccr_text")
    assert disabled is True
    assert "workspace not created" in reason


def test_run_button_disabled_when_planner_backend_not_ready(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path / "runs", goal="demo")
    session = _session()
    ws.apply_workspace_session(session, workspace)

    disabled, reason = ws.run_workflow_button_disabled(session, "claude")

    assert disabled is True
    assert "planner backend not ready" in reason


def test_upload_pdf_survives_create_workspace_flow(tmp_path: Path) -> None:
    paper = tmp_path / "upload.pdf"
    paper.write_bytes(b"%PDF-1.4 demo")
    workspace = create_workspace(tmp_path / "runs", goal="demo", paper_file_path=paper)

    copied = Path(workspace["paper_path"])
    assert copied.exists()
    assert copied.read_bytes() == b"%PDF-1.4 demo"


def test_single_web_registry_does_not_affect_workspace_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = create_workspace(tmp_path / "runs", goal="demo")
    session = _session()
    ws.apply_workspace_session(session, workspace)

    monkeypatch.setattr("r2a_web.workspace_state.latest_run_id", lambda repo: "foreign_run")
    monkeypatch.setattr(
        "r2a_web.workspace_state.read_run_record",
        lambda repo, run_id: {"status": "running"},
    )

    ws.sync_background_run_readonly(session)

    assert session["workspace"]["workspace_dir"] == workspace["workspace_dir"]
    assert session["workspace_created"] is True
