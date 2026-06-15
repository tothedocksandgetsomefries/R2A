from __future__ import annotations

from pathlib import Path

from r2a.core.paths import report_path
from r2a.core.state import make_initial_state
from r2a.tools.source_acquisition import acquire_source
from r2a.tools.source_inspection import inspect_source, read_source_inspection
from r2a.tools.workflow_decision import PAPER_STRUCTURED_KEYS


def test_source_inspection_records_repo_structure_for_planner(tmp_path: Path) -> None:
    paper = _write_paper_bundle(tmp_path)
    (tmp_path / "README.md").write_text("Use the SIFT dataset with query and ground truth files.\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("torch\npytest\n", encoding="utf-8")
    (tmp_path / "train.py").write_text("print('train')\n", encoding="utf-8")
    (tmp_path / "test_smoke.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    state = make_initial_state(tmp_path, paper_path=paper)

    state = acquire_source(state)
    state = inspect_source(state)
    inspection = read_source_inspection(tmp_path)

    assert inspection["inspection_status"] == "complete"
    assert "requirements.txt" in inspection["environment_files"]
    assert "train.py" in inspection["entrypoints"]
    assert "python -m pytest" in inspection["test_commands"]
    assert inspection["dataset_requirements"][0]["available"] is False
    assert any("Dataset is required" in item for item in inspection["planner_hints"])


def test_source_inspection_blocks_empty_repo(tmp_path: Path) -> None:
    paper = _write_paper_bundle(tmp_path)
    state = make_initial_state(tmp_path, paper_path=paper)
    state["source_acquisition"] = {
        "source_status": "available",
        "local_path": str(tmp_path / "missing-source"),
        "blockers": [],
    }

    state = inspect_source(state)

    assert state["source_inspection"]["inspection_status"] == "blocked"
    assert state["source_inspection"]["blockers"][0]["type"] == "empty_repo"


def _write_paper_bundle(repo: Path) -> Path:
    paper = repo / "paper.txt"
    paper.write_text("paper text", encoding="utf-8")
    for key in PAPER_STRUCTURED_KEYS:
        path = report_path(repo, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"source_or_artifact_urls":[]}' if key == "paper_output" else f"# {key}\n\nok\n", encoding="utf-8")
    return paper
