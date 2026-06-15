from __future__ import annotations

import json
from pathlib import Path

from r2a.core.paths import report_path
from r2a.core.state import make_initial_state
from r2a.tools.iteration import prepare_next_iteration
from r2a.tools.planner_input_builder import build_planner_input
from r2a.tools.source_acquisition import acquire_source
from r2a.tools.source_inspection import inspect_source
from r2a.tools.workflow_decision import PAPER_STRUCTURED_KEYS


def test_planner_input_contains_source_acquisition_and_inspection(tmp_path: Path) -> None:
    state = _ready_state(tmp_path)

    bundle = build_planner_input(state)

    assert bundle["source_acquisition"]["source_status"] == "available"
    assert bundle["source_acquisition"]["local_path"]
    assert "main.py" in bundle["source_inspection"]["entrypoints"]
    assert "requirements.txt" in bundle["source_inspection"]["environment_files"]
    assert bundle["allowed_scope"]["contract_mode"] == "verification_only"
    assert bundle["allowed_scope"]["max_target_level"] == "L2_input_contract_ready"


def test_prepare_next_iteration_writes_context_and_planner_reads_guidance(tmp_path: Path) -> None:
    state = _ready_state(tmp_path)
    state.update(
        {
            "reviewer_verdict": "NEEDS_FIX",
            "structured_review_feedback": {
                "iteration_summary": "previous blocker",
                "next_iteration_guidance": ["inspect exact entrypoint before running metrics"],
                "do_not_repeat": ["clone repository again"],
                "suggested_plan_constraints": {"max_commands": 3},
                "resolved_issues": ["paper parsed"],
            },
            "decision_status": {
                "typed_decision": "continue_iteration",
                "evidence_summary": {"accepted_level": "L1_source_artifact_verified"},
                "active_blockers": [{"type": "fixable_engineering_failure", "reason_code": "FIXABLE_ENGINEERING_FAILURE"}],
            },
        }
    )

    next_state = prepare_next_iteration(state)
    context = json.loads(report_path(tmp_path, "next_planner_context").read_text(encoding="utf-8"))
    bundle = build_planner_input(next_state)

    assert context["next_iteration"] == 2
    assert "inspect exact entrypoint before running metrics" in bundle["reviewer_guidance"]
    assert "clone repository again" in bundle["do_not_repeat"]
    assert bundle["previous_iteration_context"]["suggested_plan_constraints"]["max_commands"] == 3


def test_dataset_missing_limits_allowed_scope(tmp_path: Path) -> None:
    state = _ready_state(tmp_path)

    bundle = build_planner_input(state)

    assert bundle["source_inspection"]["dataset_requirements"]
    assert bundle["allowed_scope"]["contract_mode"] == "verification_only"
    assert bundle["allowed_scope"]["max_target_level"] == "L2_input_contract_ready"


def _ready_state(repo: Path) -> dict:
    paper = repo / "paper.txt"
    paper.write_text("paper text", encoding="utf-8")
    for key in PAPER_STRUCTURED_KEYS:
        path = report_path(repo, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"source_or_artifact_urls":[]}' if key == "paper_output" else f"# {key}\n\nok\n", encoding="utf-8")
    (repo / "README.md").write_text("Dataset SIFT requires query and ground truth files.\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (repo / "main.py").write_text("print('ok')\n", encoding="utf-8")
    state = make_initial_state(repo, paper_path=paper, auto_iterate=True, max_iterations=3)
    state["paper_readiness"] = {"ready": True, "reason_code": "PAPER_READY", "blockers": []}
    state = acquire_source(state)
    state = inspect_source(state)
    state["planner_readiness"] = {
        "ready": True,
        "reason_code": "PLANNER_READY",
        "blockers": [],
        "constraints": {
            "target_level": "L4_reduced_paper_aligned",
            "contract_mode": "verification_only",
            "max_target_level": "L2_input_contract_ready",
        },
    }
    return state
