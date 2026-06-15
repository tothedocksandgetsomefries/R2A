from __future__ import annotations

import json
from pathlib import Path

import pytest

from r2a.agents.planner_agent import run_planner_agent
from r2a.core.paths import artifact_dir, report_path
from r2a.core.state import make_initial_state
from r2a.workflow.nodes import human_approval_node


def _transaction(repo: Path) -> dict:
    return json.loads((artifact_dir(repo) / "logs" / "planner_transaction.json").read_text(encoding="utf-8"))


def test_planner_v2_diagnostic_success_to_approval(tmp_path: Path) -> None:
    planned = run_planner_agent(make_initial_state(tmp_path, planner_backend="template", auto_approve=True))
    approved = human_approval_node(planned)
    tx = _transaction(tmp_path)
    diagnostic = tx["diagnostic"]

    assert approved["approved"] is True
    assert report_path(tmp_path, "planner_output").exists()
    assert report_path(tmp_path, "task").exists()
    assert report_path(tmp_path, "experiment_contract").exists()
    assert (artifact_dir(tmp_path) / "staging" / "planner" / "iter_001" / "attempt_001" / "PLANNER_OUTPUT.json").exists()
    assert diagnostic["planner_backend"] == "template"
    assert diagnostic["planner_schema_version"] == "2.0"
    assert diagnostic["staging_planner_output_written"] is True
    assert diagnostic["planner_validation_passed"] is True
    assert diagnostic["planner_committed"] is True
    assert diagnostic["approval_passed"] is True
    assert diagnostic["failure_category"] == ""


def test_planner_v2_model_failure_stops_before_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_call(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr("r2a.agents.planner_agent.call_planner_model", fake_call)

    planned = run_planner_agent(make_initial_state(tmp_path, planner_backend="claude", auto_approve=True))
    approved = human_approval_node(planned)
    diagnostic = _transaction(tmp_path)["diagnostic"]

    assert approved["stopped"] is True
    assert planned["failed_stage"] == "planner"
    assert planned["approval_ready"] is False
    assert diagnostic["planner_status"] == "failed"
    assert diagnostic["planner_validation_passed"] is False
    assert diagnostic["planner_committed"] is False
    assert diagnostic["approval_passed"] is False


def test_planner_v2_schema_failure_stops_before_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_call(*args, **kwargs):
        return {"schema_version": "2.0"}

    monkeypatch.setattr("r2a.agents.planner_agent.call_planner_model", fake_call)

    planned = run_planner_agent(make_initial_state(tmp_path, planner_backend="claude", auto_approve=True))
    approved = human_approval_node(planned)
    diagnostic = _transaction(tmp_path)["diagnostic"]

    assert approved["stopped"] is True
    assert planned["loop_status"] == "planner_failed"
    assert diagnostic["failure_category"]
    assert not report_path(tmp_path, "task").exists()


def test_planner_v2_no_tool_call_backend_problem_classification(tmp_path: Path) -> None:
    planned = run_planner_agent(make_initial_state(tmp_path, planner_backend="claude", auto_approve=True))
    diagnostic = _transaction(tmp_path)["diagnostic"]

    assert planned["approval_ready"] is False
    assert planned["stop_reason"] == "PLANNER_BACKEND_NOT_CONFIGURED"
    assert diagnostic["failure_category"] == "PLANNER_BACKEND_NOT_CONFIGURED"
    assert "is_claude_ccr_call_problem" not in diagnostic
    assert "allowed_tools" not in diagnostic
