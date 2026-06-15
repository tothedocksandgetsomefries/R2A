from __future__ import annotations

import json
from pathlib import Path

import pytest

from r2a.agents.planner_agent import run_planner_agent
from r2a.core.paths import report_path
from r2a.core.planner_schema import PlannerOutput
from r2a.core.state import make_initial_state
from r2a.tools.csv_schemas import csv_header
from r2a.tools.planner_input_builder import build_planner_input
from r2a.tools.planner_model_client import _template_planner_output
from r2a.workflow.nodes import human_approval_node


def test_planner_output_schema_valid() -> None:
    data = _template_planner_output({"iteration": 1, "goal": "planner v2 smoke", "paper_bundle": {}})
    parsed = PlannerOutput.model_validate(data)
    assert parsed.schema_version == "2.0"
    assert parsed.planning_mode == "initial"


def test_planner_output_schema_rejects_missing_fields() -> None:
    data = _template_planner_output({"iteration": 1, "goal": "planner v2 smoke", "paper_bundle": {}})
    data.pop("tasks")
    with pytest.raises(Exception):
        PlannerOutput.model_validate(data)


def test_planner_output_schema_rejects_invalid_strategy() -> None:
    data = _template_planner_output({"iteration": 1, "goal": "planner v2 smoke", "paper_bundle": {}})
    data["iteration_strategy"] = "iterative_minimal_fix"
    with pytest.raises(Exception):
        PlannerOutput.model_validate(data)


def test_planner_initial_mode_from_paper_bundle(tmp_path: Path) -> None:
    (tmp_path / ".r2a").mkdir()
    (tmp_path / ".r2a" / "PAPER_BRIEF.md").write_text("# PAPER_BRIEF\n", encoding="utf-8")
    state = make_initial_state(tmp_path, goal="source verification")

    bundle = build_planner_input(state)
    result = run_planner_agent(state)

    assert bundle["planning_mode"] == "initial"
    assert json.loads(report_path(tmp_path, "planner_output").read_text(encoding="utf-8"))["planning_mode"] == "initial"
    assert result["approval_ready"] is True


def test_initial_mode_generates_bounded_work_package(tmp_path: Path) -> None:
    result = run_planner_agent(make_initial_state(tmp_path, goal="bounded initial"))
    planner = PlannerOutput.model_validate_json(report_path(tmp_path, "planner_output").read_text(encoding="utf-8"))

    assert 1 <= len(planner.tasks) <= 3
    assert planner.contract_mode == "verification_only"
    assert "full" not in " ".join(task.title.lower() for task in planner.tasks)
    assert result["planner_transaction"]["committed_files"] == [
        ".r2a/PLANNER_OUTPUT.json",
        ".r2a/TASK_SPEC.md",
        ".r2a/EXPERIMENT_CONTRACT.md",
    ]


def test_planner_requires_input_contract_verification_output(tmp_path: Path) -> None:
    run_planner_agent(make_initial_state(tmp_path, goal="bounded initial"))
    planner = PlannerOutput.model_validate_json(report_path(tmp_path, "planner_output").read_text(encoding="utf-8"))
    expected_outputs = "\n".join(planner.tasks[0].expected_outputs)

    assert ".r2a/results/project_tests.csv" in expected_outputs
    assert ".r2a/results/source_verification.csv" in expected_outputs
    assert ".r2a/results/build_smoke.csv" in expected_outputs
    assert ".r2a/results/runtime_smoke.csv" in expected_outputs
    assert ".r2a/results/input_contract_verification.csv" in expected_outputs
    assert "NEEDS_INPUT" in expected_outputs


def test_iterative_progress_uses_review_feedback(tmp_path: Path) -> None:
    r2a = tmp_path / ".r2a"
    r2a.mkdir()
    feedback = {
        "schema_version": 1,
        "verdict": "NEEDS_FIX",
        "failure_categories": ["SAFE_BUILD_COMPATIBILITY"],
        "preserve_successful_steps": ["preserve verified clone"],
        "required_fixes": ["add explicit include and rerun smallest build"],
    }
    (r2a / "REVIEW_FEEDBACK.json").write_text(json.dumps(feedback), encoding="utf-8")
    state = make_initial_state(tmp_path, goal="iterative")
    state["iteration"] = 2
    state["need_replan"] = True
    state["latest_review_feedback_path"] = str(r2a / "REVIEW_FEEDBACK.json")

    run_planner_agent(state)
    planner = PlannerOutput.model_validate_json(report_path(tmp_path, "planner_output").read_text(encoding="utf-8"))

    assert planner.planning_mode == "iterative_progress"
    assert planner.iteration_strategy == "FIX_AND_PROGRESS"
    assert planner.completed_capabilities == ["preserve verified clone"]
    assert len(planner.tasks) >= 2
    assert planner.tasks[1].depends_on == ["T001"]


def test_iterative_progress_blocks_when_manual_approval_required(tmp_path: Path) -> None:
    r2a = tmp_path / ".r2a"
    r2a.mkdir()
    (r2a / "REVIEW_FEEDBACK.json").write_text(
        json.dumps({"verdict": "NEEDS_INPUT_OR_BUDGET", "required_fixes": ["run full benchmark"]}),
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, goal="manual", allow_full_benchmark=False)
    state["iteration"] = 2
    state["latest_review_feedback_path"] = str(r2a / "REVIEW_FEEDBACK.json")

    run_planner_agent(state)
    planner = PlannerOutput.model_validate_json(report_path(tmp_path, "planner_output").read_text(encoding="utf-8"))

    assert planner.iteration_strategy == "BLOCKED_OR_NEEDS_APPROVAL"
    assert planner.tasks[0].requires_manual_approval is True


def test_input_contract_ready_without_official_input_authorization_waits_for_input(tmp_path: Path) -> None:
    data = _template_planner_output(
        {
            "iteration": 4,
            "goal": "advance from L2 to L4",
            "target_reproduction_level": "L4_reduced_paper_aligned",
            "allow_official_dataset_download": False,
            "download_budget_gb": 0,
            "structured_review_feedback": {
                "verdict": "INPUT_CONTRACT_READY",
                "current_level": "L2_input_contract_ready",
                "next_level": "L3_official_reduced_run",
                "preserve_successful_steps": [
                    "source verification passed",
                    "build/runtime smoke passed",
                    "input contract schema documented",
                ],
                "missing_l3_requirements": ["official dataset", "official query", "ground truth"],
                "required_fixes": [],
            },
        }
    )
    planner = PlannerOutput.model_validate(data)
    task_text = json.dumps(planner.tasks[0].model_dump(), ensure_ascii=False)

    assert planner.iteration_strategy == "BLOCKED_OR_NEEDS_APPROVAL"
    assert planner.contract_mode == "verification_only"
    assert planner.max_evidence_level_allowed == "L2_input_contract_ready"
    assert planner.completed_capabilities == [
        "source verification passed",
        "build/runtime smoke passed",
        "input contract schema documented",
    ]
    assert planner.tasks[0].requires_manual_approval is True
    assert "Request manual approval" in planner.tasks[0].title
    assert "Fix current blocker" not in task_text
    assert "Verify source, artifacts" not in task_text


def test_planner_input_bundle_summarizes_progress_and_required_authorization(tmp_path: Path) -> None:
    r2a = tmp_path / ".r2a"
    r2a.mkdir()
    feedback = {
        "schema_version": 1,
        "verdict": "INPUT_CONTRACT_READY",
        "current_level": "L2_input_contract_ready",
        "preserve_successful_steps": ["project tests passed", "input contract documented"],
        "active_blockers": ["official SIFT query and ground truth are not available locally"],
        "missing_l3_requirements": ["official dataset", "official query", "ground truth"],
    }
    (r2a / "REVIEW_FEEDBACK.json").write_text(json.dumps(feedback), encoding="utf-8")
    (r2a / "MANAGER_DECISION.json").write_text(
        json.dumps({"blocking_errors": [], "warnings": ["input rows are NEEDS_INPUT"]}),
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, goal="continue after L2", allow_official_dataset_download=False, download_budget_gb=0)
    state["iteration"] = 4
    state["need_replan"] = True
    state["latest_review_feedback_path"] = str(r2a / "REVIEW_FEEDBACK.json")

    bundle = build_planner_input(state)

    assert bundle["current_evidence_level"] == "L2_input_contract_ready"
    assert bundle["completed_tasks"] == ["project tests passed", "input contract documented"]
    assert bundle["reviewer_blockers"] == ["official SIFT query and ground truth are not available locally"]
    assert any(item.get("id") == "missing_paper_structured_bundle" for item in bundle["active_blockers"])
    assert bundle["manager_warnings"] == ["input rows are NEEDS_INPUT"]
    assert bundle["required_authorizations"] == [
        "official_input_download_or_local_path: official dataset; official query; ground truth"
    ]


def test_planner_input_uses_paper_text_fallback_when_structured_bundle_missing(tmp_path: Path) -> None:
    r2a = tmp_path / ".r2a"
    r2a.mkdir()
    report_path(tmp_path, "paper_text").write_text("# PAPER_TEXT\n\nExtracted paper method text.\n", encoding="utf-8")
    report_path(tmp_path, "paper_sections").write_text("# PAPER_SECTIONS\n\n## Method\n\nDetails.\n", encoding="utf-8")
    state = make_initial_state(tmp_path, goal="plan from extracted text")

    bundle = build_planner_input(state)

    assert bundle["paper_bundle_status"]["status"] == "partial_with_text_fallback"
    assert bundle["paper_bundle"]["paper_text"]["available"] == "yes"
    assert "Extracted paper method text" in bundle["paper_bundle"]["paper_text"]["excerpt"]


def test_planner_replan_renders_l4_alignment_schema(tmp_path: Path) -> None:
    r2a = tmp_path / ".r2a"
    r2a.mkdir()
    feedback = {
        "schema_version": 1,
        "verdict": "PASS_REDUCED_METHOD_ONLY",
        "should_iterate": True,
        "preserve_successful_steps": ["official reduced metrics"],
    }
    (r2a / "REVIEW_FEEDBACK.json").write_text(json.dumps(feedback), encoding="utf-8")
    state = make_initial_state(tmp_path, goal="advance to L4", allow_official_dataset_download=True, download_budget_gb=1)
    state["iteration"] = 2
    state["need_replan"] = True
    state["latest_review_feedback_path"] = str(r2a / "REVIEW_FEEDBACK.json")
    state["planner_readiness"] = {
        "ready": True,
        "reason_code": "PLANNER_READY",
        "constraints": {
            "target_level": "L4_reduced_paper_aligned",
            "contract_mode": "official_reduced",
            "max_target_level": "L4_reduced_paper_aligned",
        },
        "blockers": [],
    }

    run_planner_agent(state)

    task_spec = report_path(tmp_path, "task").read_text(encoding="utf-8")
    planner = PlannerOutput.model_validate_json(report_path(tmp_path, "planner_output").read_text(encoding="utf-8"))
    assert planner.max_evidence_level_allowed == "L4_reduced_paper_aligned"
    assert csv_header("paper_alignment.csv") in task_spec
    assert "reduced_setting" in task_spec
    assert "PARTIAL_MATCH" in task_spec
    assert "legacy `PARTIAL` or `GAP` as match_status" in task_spec


def test_template_progress_after_l3_plans_paper_alignment_contract() -> None:
    data = _template_planner_output(
        {
            "iteration": 2,
            "goal": "continue to L4",
            "allow_official_dataset_download": True,
            "download_budget_gb": 1,
            "structured_review_feedback": {
                "verdict": "PASS_REDUCED_METHOD_ONLY",
                "should_iterate": True,
                "preserve_successful_steps": ["official reduced method run"],
            },
        }
    )
    planner = PlannerOutput.model_validate(data)
    task_text = json.dumps(planner.tasks[0].model_dump(), ensure_ascii=False)

    assert planner.planning_mode == "iterative_progress"
    assert planner.iteration_strategy == "PROGRESS_ONLY"
    assert planner.contract_mode == "official_reduced"
    assert planner.max_evidence_level_allowed == "L4_reduced_paper_aligned"
    assert ".r2a/results/paper_alignment.csv" in task_text
    assert csv_header("paper_alignment.csv") in task_text
    assert "reduced_setting" in task_text
    assert "PARTIAL_MATCH" in task_text
    assert "NOT_AVAILABLE" in task_text
    assert "legacy `PARTIAL` or `GAP`" not in task_text
    assert "verified_setting" not in planner.tasks[0].expected_outputs[0]


def test_template_progress_after_l4_plans_minimal_baseline_contract() -> None:
    data = _template_planner_output(
        {
            "iteration": 2,
            "goal": "continue to L5",
            "allow_external_baselines": True,
            "network_authorized": True,
            "structured_review_feedback": {
                "verdict": "PASS_REDUCED_ALIGNED",
                "should_iterate": True,
            },
        }
    )
    planner = PlannerOutput.model_validate(data)
    task_text = json.dumps(planner.tasks[0].model_dump(), ensure_ascii=False)

    assert planner.contract_mode == "official_reduced"
    assert planner.max_evidence_level_allowed == "L5_minimal_baseline_comparison"
    assert ".r2a/results/baseline_comparison.csv" in task_text
    assert csv_header("baseline_comparison.csv") in task_text
    assert planner.tasks[0].allow_network is True


def test_template_progress_after_l4_without_network_authorization_disables_network() -> None:
    data = _template_planner_output(
        {
            "iteration": 2,
            "goal": "continue to L5",
            "allow_external_baselines": True,
            "network_authorized": False,
            "structured_review_feedback": {
                "verdict": "PASS_REDUCED_ALIGNED",
                "should_iterate": True,
            },
        }
    )
    planner = PlannerOutput.model_validate(data)

    assert planner.contract_mode == "official_reduced"
    assert planner.tasks[0].allow_network is False


def test_planner_failure_does_not_enter_approval(tmp_path: Path, monkeypatch) -> None:
    def fail_model(*args, **kwargs):
        raise RuntimeError("bad json")

    monkeypatch.setattr("r2a.agents.planner_agent.call_planner_model", fail_model)
    result = run_planner_agent(make_initial_state(tmp_path, goal="fail", auto_approve=True))
    approved = human_approval_node(result)

    assert result["loop_status"] == "planner_failed"
    assert result["approval_ready"] is False
    assert approved["stopped"] is True
    assert not report_path(tmp_path, "task").exists()


def test_planner_does_not_use_tool_calls_or_stage_guard() -> None:
    source = Path("r2a/agents/planner_agent.py").read_text(encoding="utf-8")
    assert "run_codex_stage" not in source
    assert "run_claude_stage" not in source
    assert "stage_guard" not in source
    assert "TOOL_CALL_PARSE_FAILURE" not in source
