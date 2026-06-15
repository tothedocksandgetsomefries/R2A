import json
from pathlib import Path

from r2a.tools.planner_contract_guard import enforce_planner_contract


def test_planner_contract_guard_overrides_local_only_input_contract(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    artifact_dir.mkdir()
    feedback_path = artifact_dir / "REVIEW_FEEDBACK.json"
    task_path = artifact_dir / "TASK_SPEC.md"
    contract_path = artifact_dir / "EXPERIMENT_CONTRACT.md"

    feedback_path.write_text(
        json.dumps({"verdict": "NEEDS_OFFICIAL_INPUT"}),
        encoding="utf-8",
    )
    task_path.write_text(
        "# TASK_SPEC\n\n"
        "Network access: not authorized\n"
        "Data download budget for this iteration: 0GB\n"
        "Missing query files and ground truth.\n",
        encoding="utf-8",
    )
    contract_path.write_text(
        "# EXPERIMENT_CONTRACT\n\n"
        "Conditional download permission: not authorized\n"
        "Official input is missing.\n",
        encoding="utf-8",
    )
    state = {
        "repo_path": str(tmp_path),
        "latest_review_feedback_path": str(feedback_path),
        "download_budget_gb": 20,
    }

    warnings = enforce_planner_contract(tmp_path, state)

    task_text = task_path.read_text(encoding="utf-8")
    contract_text = contract_path.read_text(encoding="utf-8")
    assert len(warnings) == 2
    assert "Planner Contract Guard Override" in task_text
    assert "official_input_contract_acquisition_with_network" in task_text
    assert "`20GB`" in task_text
    assert "Planner Contract Guard Override" in contract_text
    assert "official_input_contract_acquisition_with_network" in contract_text
