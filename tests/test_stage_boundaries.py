from pathlib import Path

from r2a.agents.planner_agent import run_planner_agent
from r2a.core.paths import report_path
from r2a.core.state import make_initial_state
from r2a.workflow.nodes import human_approval_node


def test_planner_v2_model_failure_stops_without_template_fallback(tmp_path: Path, monkeypatch) -> None:
    artifact_dir = tmp_path / ".r2a"
    artifact_dir.mkdir()
    (artifact_dir / "PAPER_CONTEXT.md").write_text("# PAPER_CONTEXT\n", encoding="utf-8")

    def fake_call(*args, **kwargs):
        raise RuntimeError("planner backend failed")

    monkeypatch.setattr("r2a.agents.planner_agent.call_planner_model", fake_call)
    state = make_initial_state(tmp_path, planner_backend="claude", auto_approve=True)

    result = run_planner_agent(state)

    assert result["stopped"] is True
    assert result["auto_approve"] is False
    assert result["failed_stage"] == "planner"
    assert result["loop_status"] == "planner_failed"
    assert not report_path(tmp_path, "task").exists()


def test_human_approval_preserves_upstream_stop_even_with_auto_approve(tmp_path: Path) -> None:
    state = make_initial_state(tmp_path, auto_approve=True)
    state["stopped"] = True
    state["stop_reason"] = "PLANNER_FORBIDDEN_WRITE"

    result = human_approval_node(state)

    assert result["stopped"] is True
    assert result["stop_reason"] == "PLANNER_FORBIDDEN_WRITE"
