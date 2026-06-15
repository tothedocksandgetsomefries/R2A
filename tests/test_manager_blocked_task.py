from pathlib import Path

from r2a.agents.manager_agent import run_manager_agent
from r2a.core.paths import report_path
from r2a.core.state import make_initial_state


def test_manager_does_not_require_csv_for_explicit_blocked_task(tmp_path: Path) -> None:
    task_spec = report_path(tmp_path, "task")
    task_spec.parent.mkdir(parents=True)
    task_spec.write_text(
        "# TASK_SPEC\n\n"
        "## Purpose\n\nBlocked reproduction report.\n\n"
        "No experiments are authorized because paper evidence and source code are unavailable.\n\n"
        "## Allowed Files\n\n- .r2a/EXECUTION_REPORT.md\n\n"
        "## Forbidden Files\n\n- results/\n\n"
        "## Acceptance Criteria\n\n- The execution report states the blocker.\n\n"
        "## Stop Conditions\n\n- Stop before fabricating results.\n",
        encoding="utf-8",
    )
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n\nBlocked.\n", encoding="utf-8")
    state = make_initial_state(tmp_path, auto_approve=True)
    state["task_spec_path"] = str(task_spec)
    state["execution_report_path"] = str(report_path(tmp_path, "execution"))

    result = run_manager_agent(state)

    text = Path(result["check_report_path"]).read_text(encoding="utf-8")
    assert result["manager_status"] == "FAIL"
    assert "Engineer did not execute" in text
    assert "Missing required result CSV files" not in text
