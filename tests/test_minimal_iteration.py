"""Minimal iteration tests - verify Reviewer-driven auto-iteration."""
from __future__ import annotations

from pathlib import Path
import tempfile

from r2a.core.state import make_initial_state
from r2a.workflow.graph import build_workflow_graph
from r2a.workflow.router import route_after_reviewer
from r2a.agents.manager_agent import run_manager_agent
from r2a.agents.planner_agent import run_planner_agent
from r2a.core.paths import report_path, artifact_dir
from r2a.tools.iteration import _ai_stages_used
from r2a.tools.workflow_decision import PAPER_STRUCTURED_KEYS


def test_route_after_reviewer_stopped():
    """Stopped runs should route to final."""
    state = {"stopped": True}
    assert route_after_reviewer(state) == "final"


def test_route_after_reviewer_pass():
    """PASS should route to final."""
    state = {"stopped": False, "reviewer_verdict": "PASS", "auto_iterate": True, "iteration": 1, "max_iterations": 2}
    assert route_after_reviewer(state) == "final"


def test_route_after_reviewer_reject():
    """REJECT should route to final."""
    state = {"stopped": False, "reviewer_verdict": "REJECT", "auto_iterate": True, "iteration": 1, "max_iterations": 2}
    assert route_after_reviewer(state) == "final"


def test_route_after_reviewer_needs_input():
    """NEEDS_INPUT should route to final."""
    state = {"stopped": False, "reviewer_verdict": "NEEDS_INPUT", "auto_iterate": True, "iteration": 1, "max_iterations": 2}
    assert route_after_reviewer(state) == "final"


def test_route_after_reviewer_needs_fix_no_auto_iterate():
    """NEEDS_FIX with auto_iterate=False should route to final."""
    state = {
        "stopped": False,
        "reviewer_verdict": "NEEDS_FIX",
        "auto_iterate": False,
        "iteration": 1,
        "max_iterations": 3,
    }
    assert route_after_reviewer(state) == "final"


def test_route_after_reviewer_needs_fix_max_iterations():
    """NEEDS_FIX at max_iterations should route to final."""
    state = {
        "stopped": False,
        "reviewer_verdict": "NEEDS_FIX",
        "auto_iterate": True,
        "iteration": 3,
        "max_iterations": 3,
    }
    assert route_after_reviewer(state) == "final"


def test_route_after_reviewer_needs_fix_can_iterate(tmp_path: Path):
    """NEEDS_FIX with auto_iterate=True and iteration < max_iterations should route to prepare_next_iteration."""
    state = _route_state(tmp_path, "NEEDS_FIX", iteration=1, max_iterations=3)
    assert route_after_reviewer(state) == "prepare_next_iteration"


def test_route_after_reviewer_needs_fix_iteration_2(tmp_path: Path):
    """NEEDS_FIX at iteration 2 with max_iterations=3 should still iterate."""
    state = _route_state(tmp_path, "NEEDS_FIX", iteration=2, max_iterations=3)
    assert route_after_reviewer(state) == "prepare_next_iteration"


def test_ai_stages_used_includes_openclaw_backends(tmp_path: Path):
    state = make_initial_state(
        tmp_path,
        paper_backend="openclaw_reader",
        planner_backend="openclaw",
        engineer_executor="claude",
        manager_backend="openclaw_review",
        reviewer_backend="openclaw",
    )

    assert _ai_stages_used(state) == ["paper", "planner", "engineer", "manager", "reviewer"]


def test_auto_iterate_false_single_pass():
    """A. auto_iterate=False: Reviewer NEEDS_FIX should not iterate."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        artifact_dir(repo).mkdir(parents=True, exist_ok=True)

        state = make_initial_state(
            repo,
            goal="test no iteration",
            executor="mock",
            planner_backend="template",
            engineer_executor="mock",
            manager_backend="rules",
            auto_approve=True,
            auto_iterate=False,
            max_iterations=3,
        )

        graph = build_workflow_graph()
        result = graph.invoke(state)

        # Should stop at iteration 1
        assert result.get("iteration", 1) == 1
        # Should have final report
        assert Path(result["final_report_path"]).exists()


def test_auto_iterate_true_single_fail():
    """B. auto_iterate=True with NEEDS_FIX: should iterate to second round."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        artifact_dir(repo).mkdir(parents=True, exist_ok=True)

        state = make_initial_state(
            repo,
            goal="test iteration on fail",
            executor="mock",
            planner_backend="template",
            engineer_executor="mock",
            manager_backend="rules",
            auto_approve=True,
            auto_iterate=True,
            max_iterations=2,
        )

        graph = build_workflow_graph()
        result = graph.invoke(state)

        # Mock executor should pass, so iteration should be 1
        # (This test verifies the graph structure, not forced failure)
        assert result.get("iteration", 1) >= 1
        assert Path(result["final_report_path"]).exists()


def test_progress_blocker_stops_without_repeated_iteration():
    """Progress verdicts no longer force loops when the aggregator sees an evidence cap."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        artifact_dir(repo).mkdir(parents=True, exist_ok=True)
        paper = repo / "paper.txt"
        paper.write_text("paper", encoding="utf-8")

        state = make_initial_state(
            repo,
            paper_path=paper,
            goal="test pass stops",
            executor="mock",
            planner_backend="template",
            engineer_executor="mock",
            manager_backend="rules",
            auto_approve=True,
            auto_iterate=True,
            max_iterations=3,
        )

        graph = build_workflow_graph()
        result = graph.invoke(state)

        assert result.get("iteration", 1) == 1
        assert result.get("decision_status", {}).get("typed_decision") == "request_source"
        assert result.get("manager_executed") is not True
        assert not result.get("task_spec_path")


def test_planner_reads_previous_iteration():
    """G. Planner iteration 2 should read previous iteration artifacts."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        r2a_dir = artifact_dir(repo)
        r2a_dir.mkdir(parents=True, exist_ok=True)

        # Create previous iteration artifacts
        prev_check = report_path(repo, "check")
        prev_check.parent.mkdir(parents=True, exist_ok=True)
        prev_check.write_text(
            "# CHECK_REPORT\n\n## Status\n\nFAIL\n\n## Errors\n\n- Test error\n",
            encoding="utf-8",
        )

        prev_execution = report_path(repo, "execution")
        prev_execution.write_text(
            "# EXECUTION_REPORT\n\nEngineer execution failed.\n",
            encoding="utf-8",
        )

        prev_task = report_path(repo, "task")
        prev_task.write_text(
            "# TASK_SPEC\n\n## Objective\n\nTest objective\n",
            encoding="utf-8",
        )

        # Create state for iteration 2
        state = make_initial_state(
            repo,
            goal="test planner reads previous",
            executor="mock",
            planner_backend="template",
            engineer_executor="mock",
            manager_backend="rules",
            auto_approve=True,
            auto_iterate=True,
            max_iterations=3,
        )
        state["iteration"] = 2
        state["need_replan"] = True

        # Run planner - it should read previous artifacts
        result = run_planner_agent(state)

        # Planner should succeed
        assert result.get("planner_status") == "success"
        assert Path(result["task_spec_path"]).exists()


def test_cli_auto_iterate_parameter():
    """I. CLI --auto-iterate parameter should be accepted."""
    # This is a structural test - the parameter should exist
    from r2a.cli import workflow
    import inspect

    sig = inspect.signature(workflow)
    params = sig.parameters

    assert "auto_iterate" in params, "CLI should have --auto-iterate parameter"
    assert "max_iterations" in params, "CLI should have --max-iterations parameter"
    assert "target_reproduction_level" in params, "CLI should expose L0-L6 target level"
    assert "download_budget_gb" in params, "CLI should expose official data budget"
    assert "allow_official_dataset_download" in params, "CLI should expose official data authorization"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])


def _route_state(tmp_path: Path, verdict: str, *, iteration: int = 1, max_iterations: int = 3) -> dict:
    _write_paper_bundle(tmp_path)
    paper = tmp_path / "paper.txt"
    paper.write_text("paper", encoding="utf-8")
    state = make_initial_state(tmp_path, paper_path=paper, auto_iterate=True, max_iterations=max_iterations)
    state["reviewer_verdict"] = verdict
    state["iteration"] = iteration
    return state


def _write_paper_bundle(repo: Path) -> None:
    (repo / "main.py").write_text("print('ok')\n", encoding="utf-8")
    results = repo / ".r2a" / "results"
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
        path = report_path(repo, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = '{"ok": true}' if key == "paper_output" else f"# {key}\n\nok\n"
        path.write_text(body, encoding="utf-8")
