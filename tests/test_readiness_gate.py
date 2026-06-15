from __future__ import annotations

import json
from pathlib import Path

import pytest

from r2a.core.paths import report_path
from r2a.core.state import make_initial_state
from r2a.tools.readiness_gate import check_engineer_readiness, check_paper_readiness
from r2a.tools.workflow_decision import PAPER_STRUCTURED_KEYS, aggregate_terminal_decision
from r2a.workflow.router import route_after_paper


def test_missing_paper_not_ready_and_routes_to_final(tmp_path: Path) -> None:
    """Test that missing paper blocks only when there are NO usable paper inputs."""
    state = make_initial_state(tmp_path, auto_iterate=True)

    readiness = check_paper_readiness(state)
    decision = aggregate_terminal_decision({**state, "paper_readiness": readiness})

    # With no paper_path and no artifacts, should block
    assert readiness["ready"] is False
    assert decision["typed_decision"] == "request_paper"
    assert route_after_paper({**state, "paper_readiness": readiness}) == "final"


def test_invalid_paper_output_no_longer_blocks(tmp_path: Path) -> None:
    """Test that missing PAPER_OUTPUT.json does NOT block if Markdown artifacts exist."""
    paper = _paper(tmp_path)
    # Write all artifacts EXCEPT paper_output.json
    for key in PAPER_STRUCTURED_KEYS:
        if key == "paper_output":
            continue  # Skip paper_output
        path = report_path(tmp_path, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {key}\n\nok\n", encoding="utf-8")
    state = make_initial_state(tmp_path, paper_path=paper)

    readiness = check_paper_readiness(state)
    decision = aggregate_terminal_decision({**state, "paper_readiness": readiness})

    # With Markdown artifacts, should be ready even without PAPER_OUTPUT.json
    assert readiness["ready"] is True
    assert readiness["reason_code"] == "PAPER_INPUTS_AVAILABLE"
    # Should NOT be blocked as missing paper; current routing may still request source next.
    assert decision["typed_decision"] != "request_paper"


def test_no_paper_artifacts_at_all_blocks(tmp_path: Path) -> None:
    """Test that having NO paper inputs at all still blocks."""
    # No paper_path, no artifacts
    state = make_initial_state(tmp_path, auto_iterate=True)

    readiness = check_paper_readiness(state)
    decision = aggregate_terminal_decision({**state, "paper_readiness": readiness})

    assert readiness["ready"] is False
    assert readiness["reason_code"] == "MISSING_PAPER"
    assert decision["typed_decision"] == "request_paper"


def test_markdown_artifacts_only_sufficient(tmp_path: Path) -> None:
    """Test that Markdown artifacts alone (no paper_path, no PAPER_OUTPUT.json) are sufficient."""
    # No paper_path, but have Markdown artifacts
    for key in ("paper_context", "paper", "paper_evidence", "paper_reproduction_card", "paper_text"):
        path = report_path(tmp_path, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {key}\n\nok\n", encoding="utf-8")

    state = make_initial_state(tmp_path, auto_iterate=True)  # No paper_path

    readiness = check_paper_readiness(state)
    decision = aggregate_terminal_decision({**state, "paper_readiness": readiness})

    # Should be ready with just Markdown artifacts
    assert readiness["ready"] is True
    assert readiness["reason_code"] == "PAPER_INPUTS_AVAILABLE"
    assert decision["typed_decision"] != "request_paper"


def _warning_text(readiness: dict) -> str:
    warning_items: list[str] = []
    for key in ("warnings", "readiness_warnings", "non_blocking_warnings"):
        value = readiness.get(key) or []
        warning_items.extend(str(item) for item in value)
    diagnostics = readiness.get("diagnostics") or {}
    if isinstance(diagnostics, dict):
        warning_items.extend(str(item) for item in diagnostics.get("plan_quality_warnings") or [])
    return "\n".join(warning_items)


def _assert_engineer_ready_with_warning(readiness: dict, needle: str) -> None:
    assert readiness["ready"] is True
    assert readiness["reason_code"] == "ENGINEER_READY"
    assert needle in _warning_text(readiness)


def test_engineer_readiness_warns_for_placeholder_task_without_terminal(tmp_path: Path) -> None:
    planner_output = _planner_output("clone github.com/X and run experiment")
    r2a = tmp_path / ".r2a"
    r2a.mkdir()
    report_path(tmp_path, "planner_output").write_text(json.dumps(planner_output), encoding="utf-8")
    report_path(tmp_path, "task").write_text("# TASK\n\nclone github.com/X\n", encoding="utf-8")
    paper = _write_paper_bundle(tmp_path)
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    state = make_initial_state(tmp_path, paper_path=paper)
    state.update({"decision_status": {"typed_decision": "continue_iteration"}})

    readiness = check_engineer_readiness(state)
    decision = aggregate_terminal_decision({**state, "engineer_readiness": readiness})

    _assert_engineer_ready_with_warning(readiness, "github")
    assert "run experiment" in _warning_text(readiness)
    assert decision["typed_decision"] != "terminal_failed"
    assert decision["reason_code"] != "PLACEHOLDER_TASK"


def test_engineer_readiness_warns_for_placeholder_rule_in_stop_conditions(tmp_path: Path) -> None:
    source_root = _write_source_inventory(tmp_path)
    placeholder_rule = "No placeholder text (TBD, TODO, FIXME) in any CSV"
    planner_output = _planner_output(
        "Record concrete CSV verification status rows.",
        stop_conditions=[placeholder_rule],
    )
    state = _write_engineer_ready_inputs(
        tmp_path,
        "Record concrete CSV verification status rows.",
        source_root,
        planner_output=planner_output,
        task_text=f"# TASK_SPEC\n\n## Stop Conditions\n\n- {placeholder_rule}\n",
    )

    readiness = check_engineer_readiness(state)

    _assert_engineer_ready_with_warning(readiness, "TODO")


def test_engineer_readiness_warns_for_placeholder_rule_in_acceptance_criteria(tmp_path: Path) -> None:
    source_root = _write_source_inventory(tmp_path)
    placeholder_rule = "No placeholder text (TBD, TODO, FIXME)"
    planner_output = _planner_output(
        "Record concrete CSV verification status rows.",
        acceptance_criteria=["Concrete rows are written", placeholder_rule],
    )
    state = _write_engineer_ready_inputs(
        tmp_path,
        "Record concrete CSV verification status rows.",
        source_root,
        planner_output=planner_output,
    )

    readiness = check_engineer_readiness(state)

    _assert_engineer_ready_with_warning(readiness, "TODO")


def test_engineer_readiness_warns_for_no_placeholder_content_policy_without_terminal(tmp_path: Path) -> None:
    source_root = _write_source_inventory(tmp_path)
    placeholder_rule = "No placeholder content (TBD, TODO, etc.) in evidence files"
    planner_output = _planner_output(
        "Record concrete CSV verification status rows.",
        acceptance_criteria=["Concrete rows are written", placeholder_rule],
    )
    state = _write_engineer_ready_inputs(
        tmp_path,
        "Record concrete CSV verification status rows.",
        source_root,
        planner_output=planner_output,
    )

    readiness = check_engineer_readiness(state)
    decision = aggregate_terminal_decision({**state, "engineer_readiness": readiness})

    _assert_engineer_ready_with_warning(readiness, "TODO")
    assert decision["typed_decision"] != "terminal_failed"
    assert decision["reason_code"] != "PLACEHOLDER_TASK"


def test_engineer_readiness_warns_for_todo_in_action(tmp_path: Path) -> None:
    source_root = _write_source_inventory(tmp_path)
    state = _write_engineer_ready_inputs(tmp_path, "TODO: implement benchmark runner", source_root)

    readiness = check_engineer_readiness(state)

    _assert_engineer_ready_with_warning(readiness, "TODO")


def test_engineer_readiness_warns_for_real_todo_in_acceptance_criteria(tmp_path: Path) -> None:
    source_root = _write_source_inventory(tmp_path)
    planner_output = _planner_output(
        "Record concrete CSV verification status rows.",
        acceptance_criteria=["TODO: fill concrete acceptance criteria"],
    )
    state = _write_engineer_ready_inputs(
        tmp_path,
        "Record concrete CSV verification status rows.",
        source_root,
        planner_output=planner_output,
    )

    readiness = check_engineer_readiness(state)

    _assert_engineer_ready_with_warning(readiness, "TODO")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "TBD"),
        ("objective", "TBD"),
        ("objective", "FIXME"),
    ],
)
def test_engineer_readiness_warns_for_placeholder_in_title_or_objective(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    source_root = _write_source_inventory(tmp_path)
    planner_output = _planner_output(
        "Record concrete status rows.",
        title=value if field == "title" else "Concrete Task",
        objective=value if field == "objective" else "Record concrete status rows.",
    )
    state = _write_engineer_ready_inputs(
        tmp_path,
        "Record concrete status rows.",
        source_root,
        planner_output=planner_output,
    )

    readiness = check_engineer_readiness(state)

    _assert_engineer_ready_with_warning(readiness, value)


def test_engineer_readiness_warns_for_download_dataset_from_url_action(tmp_path: Path) -> None:
    source_root = _write_source_inventory(tmp_path)
    state = _write_engineer_ready_inputs(tmp_path, "download dataset from url", source_root)

    readiness = check_engineer_readiness(state)

    _assert_engineer_ready_with_warning(readiness, "download dataset from url")


def test_engineer_readiness_warns_for_github_x_action(tmp_path: Path) -> None:
    source_root = _write_source_inventory(tmp_path)
    state = _write_engineer_ready_inputs(tmp_path, "curl https://github.com/x/example", source_root)

    readiness = check_engineer_readiness(state)

    _assert_engineer_ready_with_warning(readiness, "github")


def test_engineer_readiness_warns_for_missing_setup_py_command(tmp_path: Path) -> None:
    source_root = _write_source_inventory(tmp_path)
    state = _write_engineer_ready_inputs(tmp_path, "Run: python setup.py test", source_root)

    readiness = check_engineer_readiness(state)

    _assert_engineer_ready_with_warning(readiness, "setup.py")


def test_engineer_readiness_accepts_project_tests_skipped_reason_without_setup_or_tests(tmp_path: Path) -> None:
    source_root = _write_source_inventory(tmp_path)
    action = (
        "Record project_tests=SKIPPED_WITH_REASON, "
        "reason=No setup.py/tests/known project test entrypoint found in source inventory."
    )
    state = _write_engineer_ready_inputs(tmp_path, action, source_root)

    readiness = check_engineer_readiness(state)

    assert readiness["ready"] is True
    assert readiness["reason_code"] == "ENGINEER_READY"


def test_engineer_readiness_warns_for_conditional_missing_setup_py_task(tmp_path: Path) -> None:
    source_root = _write_source_inventory(tmp_path)
    state = _write_engineer_ready_inputs(
        tmp_path,
        "If setup.py is present, run: python setup.py test",
        source_root,
    )

    readiness = check_engineer_readiness(state)

    _assert_engineer_ready_with_warning(readiness, "setup.py")


def test_engineer_readiness_accepts_inventory_confirmed_script(tmp_path: Path) -> None:
    source_root = _write_source_inventory(tmp_path)
    (source_root / "benchmark.py").write_text("print('ok')\n", encoding="utf-8")
    state = _write_engineer_ready_inputs(tmp_path, "Run: python benchmark.py --help", source_root)

    readiness = check_engineer_readiness(state)

    assert readiness["ready"] is True
    assert readiness["reason_code"] == "ENGINEER_READY"


def test_engineer_readiness_rejects_invalid_planner_schema_without_placeholder_reason(tmp_path: Path) -> None:
    source_root = _write_source_inventory(tmp_path)
    state = _write_engineer_ready_inputs(tmp_path, "Record concrete status rows.", source_root)
    report_path(tmp_path, "planner_output").write_text(
        json.dumps({"schema_version": "2.0", "tasks": [{"task_id": "T001"}]}),
        encoding="utf-8",
    )

    readiness = check_engineer_readiness(state)

    assert readiness["ready"] is False
    assert readiness["reason_code"] == "INVALID_PLANNER_OUTPUT"
    assert readiness["reason_code"] != "PLACEHOLDER_TASK"
    assert "schema validation" in readiness["summary"]


def test_engineer_readiness_rejects_no_tasks_without_placeholder_reason(tmp_path: Path) -> None:
    source_root = _write_source_inventory(tmp_path)
    planner_output = _planner_output("Record concrete status rows.")
    planner_output["tasks"] = []
    state = _write_engineer_ready_inputs(
        tmp_path,
        "Record concrete status rows.",
        source_root,
        planner_output=planner_output,
    )

    readiness = check_engineer_readiness(state)

    assert readiness["ready"] is False
    assert readiness["reason_code"] == "INVALID_PLANNER_OUTPUT"
    assert readiness["reason_code"] != "PLACEHOLDER_TASK"
    assert "tasks" in readiness["summary"]


def _paper(repo: Path) -> Path:
    paper = repo / "paper.txt"
    paper.write_text("paper text", encoding="utf-8")
    return paper


def _write_paper_bundle(repo: Path) -> Path:
    paper = _paper(repo)
    for key in PAPER_STRUCTURED_KEYS:
        path = report_path(repo, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"source_or_artifact_urls":[]}' if key == "paper_output" else f"# {key}\n\nok\n", encoding="utf-8")
    return paper


def _planner_output(
    action: str,
    *,
    title: str = "Task",
    objective: str = "test",
    rationale: str = "test",
    expected_outputs: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
    stop_conditions: list[str] | None = None,
) -> dict:
    return {
        "schema_version": "2.0",
        "iteration": 1,
        "planning_mode": "initial",
        "iteration_strategy": "PROGRESS_ONLY",
        "objective": "smoke",
        "contract_mode": "verification_only",
        "max_evidence_level_allowed": "L2_input_contract_ready",
        "current_status_summary": "smoke",
        "completed_capabilities": [],
        "blocking_issues": [],
        "evidence_used": [{"claim": "x", "source": "test", "status": "SUPPORTED", "notes": ""}],
        "evidence_gaps": [],
        "tasks": [
            {
                "task_id": "T001",
                "title": title,
                "objective": objective,
                "rationale": rationale,
                "actions": [action],
                "depends_on": [],
                "run_if": None,
                "expected_outputs": expected_outputs
                if expected_outputs is not None
                else [".r2a/results/reproduction_status.csv"],
                "acceptance_criteria": acceptance_criteria
                if acceptance_criteria is not None
                else ["status is written"],
                "stop_conditions": stop_conditions if stop_conditions is not None else ["stop"],
                "allowed_write_paths": [".r2a/results/**"],
                "allow_network": False,
                "allow_docker": False,
                "requires_manual_approval": False,
            }
        ],
        "claim_restrictions": ["no full reproduction"],
        "manual_approval_points": [],
        "preserve_outputs": [],
        "planner_notes": [],
    }


def _write_source_inventory(repo: Path) -> Path:
    source_root = repo / ".r2a" / "artifacts" / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "README.md").write_text("# Source\n", encoding="utf-8")
    (source_root / "Dockerfile").write_text("FROM python:3.11\n", encoding="utf-8")
    report_path(repo, "source_acquisition").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "source_status": "available",
                "local_path": str(source_root),
            }
        ),
        encoding="utf-8",
    )
    return source_root


def _write_engineer_ready_inputs(
    repo: Path,
    action: str,
    source_root: Path,
    *,
    planner_output: dict | None = None,
    task_text: str | None = None,
) -> dict:
    report_path(repo, "planner_output").write_text(
        json.dumps(planner_output if planner_output is not None else _planner_output(action)),
        encoding="utf-8",
    )
    report_path(repo, "task").write_text(
        task_text if task_text is not None else f"# TASK_SPEC\n\n- Actions:\n  - {action}\n",
        encoding="utf-8",
    )
    state = make_initial_state(repo, paper_path=_paper(repo))
    state.update(
        {
            "decision_status": {"typed_decision": "continue_iteration"},
            "source_acquisition": {
                "schema_version": 2,
                "source_status": "available",
                "local_path": str(source_root),
            },
        }
    )
    return state
