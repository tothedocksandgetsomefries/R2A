from __future__ import annotations

from pathlib import Path

from r2a.core.feature_flags import minimal_workflow_defaults
from r2a.core.paths import report_path
from r2a.core.state import make_initial_state
from r2a.tools.process_manager import create_run_record, read_run_record, update_run_record, write_run_result
from r2a.tools.workflow_decision import PAPER_STRUCTURED_KEYS
from r2a.workflow.graph import build_workflow_graph
from r2a.workflow.router import approval_router


def test_minimal_workflow_local_paper_ccr_planner_mock_engineer_rules_manager_rules_reviewer(tmp_path: Path) -> None:
    paper = _write_paper(tmp_path)
    graph = build_workflow_graph()
    state = make_initial_state(
        tmp_path,
        goal="minimal chain",
        paper_path=paper,
        executor="mock",
        paper_backend="preprocess",
        planner_backend="template",
        engineer_executor="mock",
        manager_backend="rules",
        reviewer_backend="rules",
        auto_approve=True,
        auto_iterate=False,
        max_iterations=1,
    )

    result = graph.invoke(state)

    assert Path(result["paper_brief_path"]).exists()
    assert Path(result["task_spec_path"]).exists()
    assert Path(result["review_report_path"]).exists()
    assert Path(result["review_feedback_path"]).exists()
    assert Path(result["latest_review_feedback_path"]).exists()
    assert Path(result["final_report_path"]).exists()
    assert result.get("reviewer_verdict")
    assert result["auto_iterate"] is False


def test_auto_iterate_loops_back_to_planner_until_max_iterations(tmp_path: Path) -> None:
    paper = _write_paper(tmp_path)
    graph = build_workflow_graph()
    state = make_initial_state(
        tmp_path,
        goal="minimal reviewer auto iteration",
        paper_path=paper,
        executor="mock",
        planner_backend="template",
        engineer_executor="mock",
        manager_backend="rules",
        reviewer_backend="rules",
        auto_approve=True,
        auto_iterate=True,
        max_iterations=3,
    )

    result = graph.invoke(state)

    assert result["iteration"] == 3
    assert result["stop_reason"] == "MAX_ITERATIONS_REACHED"
    assert result["decision_status"]["typed_decision"] == "final"
    assert Path(result["final_report_path"]).exists()
    assert (tmp_path / ".r2a" / "runs" / "iter_001" / "REVIEW_REPORT.md").exists()
    assert (tmp_path / ".r2a" / "runs" / "iter_002").exists()
    assert (tmp_path / ".r2a" / "runs" / "iter_003" / "FINAL_REPORT.md").exists()


def test_minimal_workflow_stops_at_approval(tmp_path: Path) -> None:
    paper = _write_paper(tmp_path)
    graph = build_workflow_graph()
    state = make_initial_state(tmp_path, goal="approval gate", paper_path=paper, executor="mock", auto_approve=False)

    result = graph.invoke(state)

    assert result["stopped"] is True
    assert "Human approval is required" in "\n".join(result.get("errors", []))


def test_approval_reject_routes_to_final() -> None:
    assert approval_router({"stopped": True, "approved": False}) == "final"


def test_approval_accept_routes_to_mock_engineer(tmp_path: Path) -> None:
    paper = _write_paper(tmp_path)
    _write_paper_bundle(tmp_path)
    assert (
        approval_router(
            {
                "repo_path": str(tmp_path),
                "paper_path": str(paper),
                "stopped": False,
                "approved": True,
                "auto_approve": True,
            }
        )
        == "engineer"
    )


def test_auto_iterate_disabled_by_default() -> None:
    defaults = minimal_workflow_defaults()
    assert defaults["auto_iterate"] is False


def test_failed_run_is_failed(tmp_path: Path) -> None:
    run_id = "run_failed_test"
    create_run_record(tmp_path, run_id, status="failed")
    record = read_run_record(tmp_path, run_id)
    assert record is not None
    assert record["status"] == "failed"


def test_completed_success_only_on_success(tmp_path: Path) -> None:
    run_id = "run_ok_test"
    create_run_record(tmp_path, run_id, status="running")
    update_run_record(tmp_path, run_id, status="completed_success")
    write_run_result(tmp_path, run_id, {"workflow_error": ""})
    record = read_run_record(tmp_path, run_id)
    assert record is not None
    assert record["status"] == "completed_success"


def test_polling_is_read_only(tmp_path: Path, monkeypatch) -> None:
    from r2a_web import workspace_state as ws

    session = {"workspace": {"repo_path": str(tmp_path), "workspace_dir": str(tmp_path)}, "workspace_created": True, "active_run_id": "r1"}
    monkeypatch.setattr(ws, "read_run_record", lambda repo, run_id: {"status": "running"})
    monkeypatch.setattr(ws, "latest_run_id", lambda repo: "r1")

    before = dict(session["workspace"])
    ws.sync_background_run_readonly(session)
    assert session["workspace"] == before


def test_heartbeat_is_read_only_for_workspace(tmp_path: Path) -> None:
    from r2a_web.workspace_state import sync_background_run_readonly

    session = {
        "workspace": {"repo_path": str(tmp_path), "workspace_dir": str(tmp_path / "ws")},
        "workspace_created": True,
        "workspace_path": str(tmp_path / "ws"),
        "active_run_id": "",
    }
    sync_background_run_readonly(session)
    assert session["workspace_path"] == str(tmp_path / "ws")


def test_registry_is_runtime_only(tmp_path: Path) -> None:
    from r2a.core.runtime_paths import runtime_runs_dir

    create_run_record(tmp_path, "run_registry_only", status="running")
    assert runtime_runs_dir(tmp_path).exists()
    assert not (tmp_path / "WORKSPACE_MANIFEST.json").exists()


def _write_paper(repo: Path) -> Path:
    paper = repo / "paper.txt"
    paper.write_text("Minimal paper context for workflow smoke tests.", encoding="utf-8")
    (repo / "main.py").write_text("print('ok')\n", encoding="utf-8")
    return paper


def _write_paper_bundle(repo: Path) -> None:
    for key in PAPER_STRUCTURED_KEYS:
        path = report_path(repo, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = '{"ok": true}' if key == "paper_output" else f"# {key}\n\nok\n"
        path.write_text(body, encoding="utf-8")
