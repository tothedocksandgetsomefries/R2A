"""Tests for UI stage status during Reviewer-driven iteration."""
from __future__ import annotations

from pathlib import Path
import pytest

st = pytest.importorskip("streamlit")

from r2a.workflow.router import route_after_planner
from r2a.workflow.router import route_after_reviewer


def _make_minimal_state_for_iteration(
    iteration: int,
    max_iterations: int,
    auto_iterate: bool = True,
    reviewer_verdict: str = "NEEDS_FIX",
    stopped: bool = False,
) -> dict:
    """Create minimal state for iteration testing.

    This helper creates a state that will pass blocker checks in aggregate_terminal_decision,
    allowing the test to focus on iteration logic rather than blocker handling.
    """
    return {
        "stopped": stopped,
        "reviewer_verdict": reviewer_verdict,
        "auto_iterate": auto_iterate,
        "iteration": iteration,
        "max_iterations": max_iterations,
        # Indicate that paper is available
        "paper_path": "/dev/null",  # Placeholder, will be overridden by mock
        "repo_path": "/dev/null",  # Placeholder, will be overridden by mock
        # Indicate that review is completed
        "reviewer_executed": True,
        # Indicate that source is available
        "source_acquisition": {"status": "OK"},
    }


def test_route_after_reviewer_with_missing_paper_blocker_goes_to_final(tmp_path) -> None:
    """Verify route_after_reviewer goes to final when missing paper blocker exists.

    This test verifies that blocker priority is higher than iteration logic.
    Even if auto_iterate=True and iteration < max_iterations,
    if there's a missing_paper blocker, route_after_reviewer should return 'final'.
    """
    # State missing paper_path and paper artifacts
    state = {
        "stopped": False,
        "reviewer_verdict": "NEEDS_FIX",
        "auto_iterate": True,
        "iteration": 1,
        "max_iterations": 2,
        # No paper_path, no repo_path, no paper artifacts
    }

    # Should route to final because of missing_paper blocker
    result = route_after_reviewer(state)
    assert result == "final"


def test_route_after_reviewer_matches_ui_logic(tmp_path, monkeypatch) -> None:
    """Verify route_after_reviewer logic matches expected behavior when no blockers exist.

    This test uses a mock to focus on iteration logic, assuming no blockers.
    """
    from r2a.workflow import router

    # Track which states were passed to aggregate_terminal_decision
    call_log = []

    def mock_aggregate_terminal_decision(state):
        """Mock aggregate_terminal_decision to return continue_iteration when appropriate.

        This mock simulates the behavior when:
        - No blockers exist
        - Review is completed
        - iteration < max_iterations and auto_iterate=True
        """
        call_log.append(state.copy())

        iteration = state.get("iteration", 1)
        max_iterations = state.get("max_iterations", 1)
        auto_iterate = state.get("auto_iterate", False)
        stopped = state.get("stopped", False)
        reviewer_verdict = state.get("reviewer_verdict", "")

        # Simulate blocker priority: if stopped, go to final
        if stopped:
            return {
                "typed_decision": "final",
                "terminal": True,
                "reason_code": "WORKFLOW_STOPPED",
            }

        # Simulate iteration logic
        if reviewer_verdict == "PASS":
            # PASS goes to final
            return {
                "typed_decision": "final",
                "terminal": True,
                "reason_code": "PASS_VERDICT",
            }

        if iteration >= max_iterations or not auto_iterate:
            return {
                "typed_decision": "final",
                "terminal": True,
                "reason_code": "MAX_ITERATIONS_REACHED" if iteration >= max_iterations else "AUTO_ITERATE_DISABLED",
            }

        # Continue iteration
        return {
            "typed_decision": "continue_iteration",
            "terminal": False,
            "reason_code": "READY_FOR_NEXT_ITERATION",
        }

    # Must patch in the router module where it's imported
    monkeypatch.setattr(
        router,
        "aggregate_terminal_decision",
        mock_aggregate_terminal_decision,
    )

    # NEEDS_FIX + can iterate
    state1 = _make_minimal_state_for_iteration(iteration=1, max_iterations=2, auto_iterate=True, reviewer_verdict="NEEDS_FIX")
    assert route_after_reviewer(state1) == "prepare_next_iteration"

    # NEEDS_FIX + at max
    state2 = _make_minimal_state_for_iteration(iteration=2, max_iterations=2, auto_iterate=True, reviewer_verdict="NEEDS_FIX")
    assert route_after_reviewer(state2) == "final"

    # NEEDS_FIX + auto_iterate=False
    state3 = _make_minimal_state_for_iteration(iteration=1, max_iterations=2, auto_iterate=False, reviewer_verdict="NEEDS_FIX")
    assert route_after_reviewer(state3) == "final"

    # NEEDS_FIX + stopped
    state4 = _make_minimal_state_for_iteration(iteration=1, max_iterations=2, auto_iterate=True, reviewer_verdict="NEEDS_FIX", stopped=True)
    assert route_after_reviewer(state4) == "final"

    # PASS
    state5 = _make_minimal_state_for_iteration(iteration=1, max_iterations=2, auto_iterate=True, reviewer_verdict="PASS")
    assert route_after_reviewer(state5) == "final"


def test_ui_uses_route_after_reviewer_for_reviewer_node(tmp_path, monkeypatch) -> None:
    """Verify _mark_next_stage_running calls route_after_reviewer for reviewer_node.

    This test is split into two scenarios:
    1. route_after_reviewer returns 'prepare_next_iteration' -> final should NOT be running
    2. route_after_reviewer returns 'final' -> final should be running
    """
    from r2a_web import app
    from r2a.workflow import router

    # === Scenario 1: route_after_reviewer returns 'prepare_next_iteration' ===
    call_log_scenario1 = []

    def mock_aggregate_for_iteration(state):
        """Mock that returns continue_iteration (no blockers, can iterate)."""
        call_log_scenario1.append(state.copy())
        return {
            "typed_decision": "continue_iteration",
            "terminal": False,
            "reason_code": "READY_FOR_NEXT_ITERATION",
        }

    # Must patch in the router module where it's imported
    monkeypatch.setattr(
        router,
        "aggregate_terminal_decision",
        mock_aggregate_for_iteration,
    )

    class MockSessionState1(dict):
        pass

    mock_state1 = MockSessionState1()
    mock_state1["stage_runtime"] = {
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
    monkeypatch.setattr(st, "session_state", mock_state1)

    state1 = _make_minimal_state_for_iteration(iteration=1, max_iterations=2, auto_iterate=True, reviewer_verdict="NEEDS_FIX")

    app._mark_next_stage_running("reviewer_node", state1)

    # Verify route_after_reviewer was called
    assert len(call_log_scenario1) == 1
    assert call_log_scenario1[0]["reviewer_verdict"] == "NEEDS_FIX"

    # Verify final is NOT running (because we're iterating)
    assert mock_state1["stage_runtime"]["stages"]["final"]["status"] == "pending"

    # === Scenario 2: route_after_reviewer returns 'final' (has blocker) ===
    call_log_scenario2 = []

    def mock_aggregate_for_final(state):
        """Mock that returns final (has blocker or terminal)."""
        call_log_scenario2.append(state.copy())
        return {
            "typed_decision": "request_paper",
            "terminal": True,
            "reason_code": "MISSING_PAPER",
        }

    # Must patch in the router module where it's imported
    monkeypatch.setattr(
        router,
        "aggregate_terminal_decision",
        mock_aggregate_for_final,
    )

    class MockSessionState2(dict):
        pass

    mock_state2 = MockSessionState2()
    mock_state2["stage_runtime"] = {
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
    monkeypatch.setattr(st, "session_state", mock_state2)

    # State with missing paper (will route to final)
    state2 = {
        "stopped": False,
        "reviewer_verdict": "NEEDS_FIX",
        "auto_iterate": True,
        "iteration": 1,
        "max_iterations": 2,
        # No paper_path, no repo_path
    }

    app._mark_next_stage_running("reviewer_node", state2)

    # Verify route_after_reviewer was called
    assert len(call_log_scenario2) == 1

    # Verify final IS running (because route is 'final')
    assert mock_state2["stage_runtime"]["stages"]["final"]["status"] == "running"


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
    from r2a.workflow import router

    # Mock aggregate_terminal_decision to return final for PASS verdict
    def mock_aggregate_for_pass(state):
        return {
            "typed_decision": "final",
            "terminal": True,
            "reason_code": "PASS_VERDICT",
        }

    # Must patch in the router module where it's imported
    monkeypatch.setattr(
        router,
        "aggregate_terminal_decision",
        mock_aggregate_for_pass,
    )

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
    state = _make_minimal_state_for_iteration(iteration=1, max_iterations=2, auto_iterate=True, reviewer_verdict="PASS")

    app._mark_next_stage_running("reviewer_node", state)

    # Verify final IS running
    assert mock_state["stage_runtime"]["stages"]["final"]["status"] == "running"
