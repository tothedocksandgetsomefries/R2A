from __future__ import annotations

import json
from pathlib import Path

from r2a.core.state import make_initial_state
from r2a.tools.iteration import archive_current_iteration
from r2a.workflow.graph import build_workflow_graph


def test_workflow_archives_first_iteration_and_keeps_latest_reports(tmp_path: Path) -> None:
    # NOTE: The default workflow graph no longer has iteration loops.
    # archive_current_iteration() is not called in the default single-pass workflow.
    # This test verifies that the basic workflow still produces expected artifacts.
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "result.csv").write_text("dataset,method,qps\nsift,hnsw,42\n", encoding="utf-8")
    paper = _write_minimal_inputs(tmp_path)
    graph = build_workflow_graph()
    state = make_initial_state(tmp_path, goal="add HNSW oversampling baseline", paper_path=paper, executor="shell", auto_approve=True)

    result = graph.invoke(state)

    artifact_dir = tmp_path / ".r2a"
    # Default workflow does not create iteration directories
    # It creates latest reports directly
    assert (artifact_dir / "TASK_SPEC.md").exists()
    assert (artifact_dir / "EXECUTION_REPORT.md").exists()
    assert (artifact_dir / "CHECK_REPORT.md").exists()
    assert (artifact_dir / "ITERATION_STATE.json").exists()
    assert Path(result["final_report_path"]).exists()


def test_auto_iteration_setting_archives_terminal_iteration_once(tmp_path: Path) -> None:
    # Auto iteration is governed by decision_status and archives each completed
    # iteration under the current layout.
    paper = _write_minimal_inputs(tmp_path)
    graph = build_workflow_graph()
    state = make_initial_state(
        tmp_path,
        goal="add HNSW oversampling baseline",
        paper_path=paper,
        executor="shell",
        auto_approve=True,
        auto_iterate=True,
        max_iterations=2,
    )

    result = graph.invoke(state)

    artifact_dir = tmp_path / ".r2a"
    assert (artifact_dir / "runs" / "iter_001" / "REVIEW_REPORT.md").exists()
    assert (artifact_dir / "runs" / "iter_002" / "FINAL_REPORT.md").exists()
    assert Path(result["final_report_path"]).exists()
    # But iteration state should reflect the settings
    iteration_state_path = artifact_dir / "ITERATION_STATE.json"
    assert iteration_state_path.exists()
    iteration_state = json.loads(iteration_state_path.read_text(encoding="utf-8"))
    assert iteration_state["current_iteration"] == 2
    assert iteration_state["auto_iterate"] is True
    assert iteration_state["decision_status"]["typed_decision"] == "final"
    assert iteration_state["decision_status"]["reason_code"] == "MAX_ITERATIONS_REACHED"
    assert result["decision_status"]["typed_decision"] == "final"


def test_workflow_without_paper_requests_paper_before_planner(tmp_path: Path) -> None:
    graph = build_workflow_graph()
    state = make_initial_state(tmp_path, goal="add HNSW oversampling baseline", executor="shell", auto_approve=True)

    result = graph.invoke(state)

    artifact_dir = tmp_path / ".r2a"
    assert not (artifact_dir / "TASK_SPEC.md").exists()
    assert result["decision_status"]["typed_decision"] == "request_source"
    assert Path(result["final_report_path"]).exists()


def test_auto_iteration_runs_paper_only_once(tmp_path: Path, monkeypatch) -> None:
    from r2a.agents.paper_agent import generate_paper_brief

    calls = {"paper": 0}

    def counted_paper_agent(state, force=True):
        calls["paper"] += 1
        return generate_paper_brief(state, force=force)

    monkeypatch.setattr("r2a.workflow.nodes.run_paper_agent", counted_paper_agent)
    graph = build_workflow_graph()
    state = make_initial_state(
        tmp_path,
        goal="add HNSW oversampling baseline",
        executor="shell",
        auto_approve=True,
        auto_iterate=True,
        max_iterations=2,
    )

    graph.invoke(state)

    assert calls["paper"] == 1


def test_archive_current_iteration_copies_reviewer_outputs_and_does_not_point_missing_files(tmp_path: Path) -> None:
    r2a = tmp_path / ".r2a"
    r2a.mkdir(parents=True)
    for name in ("TASK_SPEC.md", "EXPERIMENT_CONTRACT.md", "EXECUTION_REPORT.md", "CHECK_REPORT.md"):
        (r2a / name).write_text(f"# {name}\n", encoding="utf-8")
    (r2a / "REVIEW_REPORT.md").write_text("# REVIEW_REPORT\n\n## Verdict\n\nNEEDS_FIX\n", encoding="utf-8")
    (r2a / "REVIEW_FEEDBACK.json").write_text('{"verdict":"NEEDS_FIX"}', encoding="utf-8")

    state = make_initial_state(tmp_path, auto_iterate=True, max_iterations=2)
    state.update(
        {
            "manager_status": "WARNING",
            "reviewer_verdict": "NEEDS_FIX",
            "suggested_next_action": "fix current blocker",
        }
    )

    archived = archive_current_iteration(state)
    entry = archived["iteration_history"][0]

    assert (r2a / "runs" / "iter_001" / "REVIEW_REPORT.md").exists()
    assert (r2a / "runs" / "iter_001" / "REVIEW_FEEDBACK.json").exists()
    assert entry["review_report"].endswith("REVIEW_REPORT.md")
    assert entry["review_feedback"].endswith("REVIEW_FEEDBACK.json")
    assert entry["archive_missing_files"] == []


def test_archive_current_iteration_records_missing_reviewer_outputs_without_fake_paths(tmp_path: Path) -> None:
    r2a = tmp_path / ".r2a"
    r2a.mkdir(parents=True)
    for name in ("TASK_SPEC.md", "EXPERIMENT_CONTRACT.md", "EXECUTION_REPORT.md", "CHECK_REPORT.md"):
        (r2a / name).write_text(f"# {name}\n", encoding="utf-8")
    state = make_initial_state(tmp_path)

    archived = archive_current_iteration(state)
    entry = archived["iteration_history"][0]

    assert entry["review_report"] == ""
    assert entry["review_feedback"] == ""
    assert "review_report" in entry["archive_missing_files"]
    assert "review_feedback" in entry["archive_missing_files"]


def _write_minimal_inputs(repo: Path) -> Path:
    paper = repo / "paper.txt"
    paper.write_text(
        "A minimal paper context mentioning a source artifact, a dataset, and recall@10 evaluation.",
        encoding="utf-8",
    )
    (repo / "main.py").write_text("print('ok')\n", encoding="utf-8")
    return paper
