from pathlib import Path

from r2a.core.paths import report_path
from r2a.core.state import make_initial_state
from r2a.tools.workflow_decision import PAPER_STRUCTURED_KEYS
from r2a.workflow.nodes import final_node


def test_final_report_displays_planner_boundary_violation_as_stage_failure(tmp_path: Path) -> None:
    logs = tmp_path / ".r2a" / "logs"
    logs.mkdir(parents=True)
    (logs / "planner_stderr.log").write_text(
        "Stage Guard:\n"
        "failure_category: STAGE_BOUNDARY_VIOLATION\n"
        "execution_status: PLANNER_FORBIDDEN_WRITE\n",
        encoding="utf-8",
    )
    state = _state_with_paper(tmp_path)

    result = final_node(
        {
            **state,
            "stopped": True,
            "stop_reason": "PLANNER_FORBIDDEN_WRITE",
            "reviewer_verdict": "NEEDS_FIX",
            "manager_status": "FAIL",
        }
    )

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "Planner stage boundary violation" in text
    assert "not a paper reproduction failure" in text
    assert result["stop_reason"] == "PLANNER_FORBIDDEN_WRITE"


def test_final_report_displays_csv_parse_error_without_l3_l4_claim(tmp_path: Path) -> None:
    r2a = tmp_path / ".r2a"
    r2a.mkdir()
    (r2a / "CHECK_REPORT.md").write_text(
        "# CHECK_REPORT\n\n"
        "## Status\n\nFAIL\n\n"
        "## Errors\n\n"
        "- CSV: .r2a/results/input_contract_verification.csv: CSV_PARSE_ERROR: Expected 5 field(s) from header, saw 6 at line 7.\n",
        encoding="utf-8",
    )
    state = _state_with_paper(tmp_path)

    result = final_node({**state, "reviewer_verdict": "NEEDS_FIX", "manager_status": "FAIL"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "CSV_PARSE_ERROR" in text
    assert "Current: L3: Official reduced run (L3_official_reduced_run)" not in text
    assert "Current: L4: Reduced paper-aligned evidence (L4_reduced_paper_aligned)" not in text


def _state_with_paper(tmp_path: Path) -> dict:
    paper = tmp_path / "paper.txt"
    paper.write_text("paper text", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    results = tmp_path / ".r2a" / "results"
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
        path = report_path(tmp_path, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = '{"ok": true}' if key == "paper_output" else f"# {key}\n\nok\n"
        path.write_text(body, encoding="utf-8")
    return make_initial_state(tmp_path, paper_path=paper)
