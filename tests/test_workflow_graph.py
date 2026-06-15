from pathlib import Path

from r2a.core.paths import report_path
from r2a.core.state import make_initial_state
from r2a.tools.workflow_decision import PAPER_STRUCTURED_KEYS
from r2a.workflow.graph import build_workflow_graph


def test_workflow_graph_runs_all_stages_with_mock_executor(tmp_path: Path) -> None:
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "result.csv").write_text("dataset,method,qps\nsift,hnsw,42\n", encoding="utf-8")
    graph = build_workflow_graph()
    state = _state_with_paper(tmp_path, goal="add HNSW oversampling baseline", executor="mock", auto_approve=True)

    result = graph.invoke(state)

    assert "R2A workflow finalized with decision" in result["final_report"]
    assert Path(result["paper_brief_path"]).exists()
    assert Path(result["task_spec_path"]).exists()
    assert Path(result["execution_report_path"]).exists()
    assert Path(result["check_report_path"]).exists()
    assert Path(result["final_report_path"]).exists()
    assert (tmp_path / ".r2a" / "results" / "project_tests.csv").exists()
    assert (tmp_path / ".r2a" / "results" / "input_contract_verification.csv").exists()
    assert result["auto_iterate"] is False
    assert result["max_iterations"] == 12
    assert result["planner_transaction"]["validation_status"] == "PASS"
    assert result["planner_transaction"]["diagnostic"]["approval_passed"] is True
    assert result["manager_executed"] is True
    assert result["reviewer_executed"] is True


def test_auto_iteration_default_is_off(tmp_path: Path) -> None:
    state = make_initial_state(tmp_path)

    assert state["auto_iterate"] is False


def test_workflow_graph_stops_without_approval(tmp_path: Path) -> None:
    graph = build_workflow_graph()
    state = _state_with_paper(tmp_path, goal="needs approval", executor="mock", auto_approve=False)

    result = graph.invoke(state)

    assert result["stopped"] is True
    assert "Human approval is required" in "\n".join(result["errors"])
    assert result["decision_status"]["typed_decision"] == "request_approval"


def test_planner_failure_stops_before_approval_and_manager(tmp_path: Path, monkeypatch) -> None:
    def fake_planner(state, *, force=True):
        return {
            **state,
            "stopped": True,
            "approved": False,
            "approval_ready": False,
            "loop_status": "planner_failed",
            "stop_reason": "PLANNER_BACKEND_FAILURE",
            "manager_status": "FAIL",
            "manager_executed": False,
            "planner_transaction": {
                "validation_status": "FAIL",
                "execution_status": "PLANNER_BACKEND_FAILURE",
                "backend_failure_category": "TOOL_CALL_PARSE_FAILURE",
            },
        }

    monkeypatch.setattr("r2a.workflow.nodes.run_planner_agent", fake_planner)
    monkeypatch.setattr("r2a.workflow.nodes.run_engineer_agent", lambda state: (_ for _ in ()).throw(AssertionError("engineer should not run")))
    monkeypatch.setattr("r2a.workflow.nodes.run_manager_agent", lambda state: (_ for _ in ()).throw(AssertionError("manager should not run")))
    graph = build_workflow_graph()
    state = _state_with_paper(tmp_path, goal="planner fails", executor="mock", auto_approve=True)

    result = graph.invoke(state)

    assert result["loop_status"] == "completed_with_failure"
    assert result["stop_reason"] == "PLANNER_BACKEND_FAILURE"
    assert result["manager_executed"] is False
    assert result["decision_status"]["typed_decision"] in {"retry_backend", "terminal_failed"}


def _state_with_paper(tmp_path: Path, **kwargs) -> dict:
    paper = tmp_path / "paper.txt"
    paper.write_text("paper text", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    results = tmp_path / ".r2a" / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / "source_verification.csv").write_text(
        "status,artifact_url,source_path,branch,commit,notes\n"
        "PASS,https://example.test/repo,.,main,abc123,official source verified\n",
        encoding="utf-8",
    )
    (results / "build_smoke.csv").write_text(
        "status,command,exit_code,duration_sec,component,notes\n"
        "PASS,python main.py,0,0.1,main,smoke passed\n",
        encoding="utf-8",
    )
    for key in PAPER_STRUCTURED_KEYS:
        path = report_path(tmp_path, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = '{"ok": true}' if key == "paper_output" else f"# {key}\n\nok\n"
        path.write_text(body, encoding="utf-8")
    return make_initial_state(tmp_path, paper_path=paper, **kwargs)
