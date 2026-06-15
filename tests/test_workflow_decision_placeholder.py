from __future__ import annotations

import json
from pathlib import Path

from r2a.core.paths import report_path
from r2a.core.state import make_initial_state
from r2a.tools.workflow_decision import aggregate_terminal_decision


def test_placeholder_task_blocker_does_not_terminal_even_when_repeated(tmp_path: Path) -> None:
    _write_paper_artifacts(tmp_path)
    paper = tmp_path / "paper.txt"
    paper.write_text("paper", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    source_root = tmp_path / "source"
    source_root.mkdir()
    state = make_initial_state(tmp_path, paper_path=paper, auto_iterate=True, max_iterations=5)
    state.update(
        {
            "paper_readiness": {"ready": True},
            "source_acquisition": {
                "schema_version": 2,
                "source_status": "available",
                "local_path": str(source_root),
            },
            "engineer_readiness": {
                "ready": False,
                "reason_code": "PLACEHOLDER_TASK",
                "summary": "Planner output contains placeholder-like plan-quality text.",
                "blockers": [
                    {
                        "id": "placeholder_task:plan_quality",
                        "type": "placeholder_task",
                        "reason_code": "PLACEHOLDER_TASK",
                        "message": "Planner output contains placeholder-like plan-quality text.",
                        "source": "readiness_gate",
                    }
                ],
            },
        }
    )

    first = aggregate_terminal_decision({**state, "iteration": 1})
    second = aggregate_terminal_decision({**state, "iteration": 2})
    third = aggregate_terminal_decision({**state, "iteration": 3})

    assert first["typed_decision"] != "terminal_failed"
    assert second["typed_decision"] != "terminal_failed"
    assert third["typed_decision"] != "terminal_failed"
    assert third["reason_code"] != "PLACEHOLDER_TASK"


def _write_paper_artifacts(repo: Path) -> None:
    for key in ("paper_context", "paper", "paper_evidence", "paper_reproduction_card", "paper_text"):
        path = report_path(repo, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {key}\n\nok\n", encoding="utf-8")
    report_path(repo, "source_acquisition").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "source_status": "available",
                "local_path": str(repo / "source"),
            }
        ),
        encoding="utf-8",
    )
