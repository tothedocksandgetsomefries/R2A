"""Tests for UI stage status during Reviewer-driven iteration."""
from __future__ import annotations

from pathlib import Path
import pytest

st = pytest.importorskip("streamlit")

from r2a.workflow.router import route_after_planner
from r2a.workflow.router import route_after_reviewer


def test_route_after_reviewer_matches_ui_logic(tmp_path) -> None:
    """Verify route_after_reviewer logic matches expected behavior."""
    # NEEDS_FIX + can iterate
    state1 = {"stopped": False, "reviewer_verdict": "NEEDS_FIX", "auto_iterate": True, "iteration": 1, "max_iterations": 2}
    assert route_after_reviewer(state1) == "prepare_next_iteration"

    # NEEDS_FIX + at max
    state2 = {"stopped": False, "reviewer_verdict": "NEEDS_FIX", "auto_iterate": True, "iteration": 2, "max_iterations": 2}
    assert route_after_reviewer(state2) == "final"

    # NEEDS_FIX + auto_iterate=False
    state3 = {"stopped": False, "reviewer_verdict": "NEEDS_FIX", "auto_iterate": False, "iteration": 1, "max_iterations": 2}
    assert route_after_reviewer(state3) == "final"

    # NEEDS_FIX + stopped
    state4 = {"stopped": True, "reviewer_verdict": "NEEDS_FIX", "auto_iterate": True, "iteration": 1, "max_iterations": 2}
    assert route_after_reviewer(state4) == "final"

    # PASS
    state5 = {"stopped": False, "reviewer_verdict": "PASS", "auto_iterate": True, "iteration": 1, "max_iterations": 2}
    assert route_after_reviewer(state5) == "final"


def test_ui_uses_route_after_reviewer_for_reviewer_node(tmp_path, monkeypatch) -> None:
    """Verify _mark_next_stage_running calls route_after_reviewer for reviewer_node."""
    from r2a_web import app

    call_log = []

    def fake_route(state):
        call_log.append(state.copy())
        return route_after_reviewer(state)

    monkeypatch.setattr(app, "route_after_reviewer", fake_route)

    # Mock session_state as dict-like object
    class MockSessionState(dict):
        pass

    mock_state = MockSessionState()
    mock_state["stage_runtime"] = {
        "current_iteration": 1,
        "stages": {
            "paper": {"label": "Paper", "status": "done", "iteration": 1},
            "planner": {"label": "Planner", "status": "done", "iteration": 1},
            "approval": {"label": "Approval", "status": "done", "iteration": 1},
            "engineer": {"label": "Engineer", "status": "done", "iteration": 1},
            "manager": {"label": "Manager", "status": "done", "iteration": 1},
            "reviewer": {"label": "Reviewer", "status": "done", "iteration": 1},
            "final": {"label": "Final", "status": "pending"},
        },
    }
    monkeypatch.setattr(st, "session_state", mock_state)

    state = {
        "stopped": False,
        "reviewer_verdict": "NEEDS_FIX",
        "auto_iterate": True,
        "iteration": 1,
        "max_iterations": 2,
    }

    app._mark_next_stage_running("reviewer_node", state)

    # Verify route_after_reviewer was called
    assert len(call_log) == 1
    assert call_log[0]["reviewer_verdict"] == "NEEDS_FIX"

    # Verify final is NOT running (because we're iterating)
    assert mock_state["stage_runtime"]["stages"]["final"]["status"] == "pending"


def test_ui_uses_route_after_planner_for_planner_failure(tmp_path, monkeypatch) -> None:
    from r2a_web import app

    call_log = []

    def fake_route(state):
        call_log.append(state.copy())
        return route_after_planner(state)

    monkeypatch.setattr(app, "route_after_planner", fake_route)

    class MockSessionState(dict):
        pass

    mock_state = MockSessionState()
    mock_state["stage_runtime"] = {
        "current_iteration": 1,
        "stages": {
            "paper": {"label": "Paper", "status": "done", "iteration": 1},
            "planner": {"label": "Planner", "status": "done", "iteration": 1},
            "approval": {"label": "Approval", "status": "pending", "iteration": 1},
            "engineer": {"label": "Engineer", "status": "pending", "iteration": 1},
            "manager": {"label": "Manager", "status": "pending", "iteration": 1},
            "reviewer": {"label": "Reviewer", "status": "pending", "iteration": 1},
            "final": {"label": "Final", "status": "pending"},
        },
    }
    monkeypatch.setattr(st, "session_state", mock_state)

    state = {
        "stopped": True,
        "loop_status": "planner_failed",
        "approval_ready": False,
        "planner_transaction": {"validation_status": "FAIL", "committed": False},
    }

    app._mark_next_stage_running("planner_node", state)

    assert len(call_log) == 1
    assert mock_state["stage_runtime"]["stages"]["approval"]["status"] == "pending"
    assert mock_state["stage_runtime"]["stages"]["final"]["status"] == "running"


def test_ui_marks_final_running_when_not_iterating(tmp_path, monkeypatch) -> None:
    """Verify _mark_next_stage_running marks final as running when not iterating."""
    from r2a_web import app

    monkeypatch.setattr(app, "route_after_reviewer", route_after_reviewer)

    class MockSessionState(dict):
        pass

    mock_state = MockSessionState()
    mock_state["stage_runtime"] = {
        "current_iteration": 1,
        "stages": {
            "paper": {"label": "Paper", "status": "done", "iteration": 1},
            "planner": {"label": "Planner", "status": "done", "iteration": 1},
            "approval": {"label": "Approval", "status": "done", "iteration": 1},
            "engineer": {"label": "Engineer", "status": "done", "iteration": 1},
            "manager": {"label": "Manager", "status": "done", "iteration": 1},
            "reviewer": {"label": "Reviewer", "status": "done", "iteration": 1},
            "final": {"label": "Final", "status": "pending"},
        },
    }
    monkeypatch.setattr(st, "session_state", mock_state)

    # PASS verdict should go to final
    state = {
        "stopped": False,
        "reviewer_verdict": "PASS",
        "auto_iterate": True,
        "iteration": 1,
        "max_iterations": 2,
    }

    app._mark_next_stage_running("reviewer_node", state)

    # Verify final IS running
    assert mock_state["stage_runtime"]["stages"]["final"]["status"] == "running"
