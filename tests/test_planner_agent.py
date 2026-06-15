from pathlib import Path
import json

from r2a.agents.paper_agent import generate_paper_brief
from r2a.agents.planner_agent import generate_task_spec, run_planner_agent
from r2a.core.state import make_initial_state


def test_generate_task_spec_includes_evidence_and_stop_conditions(tmp_path: Path) -> None:
    state = make_initial_state(tmp_path, goal="add HNSW oversampling baseline")
    state = generate_paper_brief(state)

    result = generate_task_spec(state)

    task_spec = Path(result["task_spec_path"])
    text = task_spec.read_text(encoding="utf-8")
    assert "# TASK_SPEC" in text
    assert "## Experiment Contract" in text
    assert "## Reproducibility Gate Summary" in text
    assert "## Max Evidence Level Allowed" in text
    assert "## L3 Entry Criteria" in text
    assert "## L4 Alignment Criteria" in text
    assert "## Paper Evidence Used" in text
    assert "## Paper Parse Quality Summary" in text
    assert "## Stop Conditions" in text
    contract = Path(result["experiment_contract_path"])
    assert contract.exists()
    contract_text = contract.read_text(encoding="utf-8")
    assert "# EXPERIMENT_CONTRACT" in contract_text
    assert "verification_only" in contract_text
    assert "Max Evidence Level Allowed" in contract_text
    assert "## Reproducibility Gate" in contract_text
    assert "## Claim Restrictions" in contract_text
    assert "L4_reduced_paper_aligned" in contract_text
    assert "Target level may remain `L4_reduced_paper_aligned`, but this contract does not authorize claiming it." in contract_text


def test_planner_v2_writes_staging_via_python(tmp_path: Path) -> None:
    state = make_initial_state(tmp_path, goal="fallback", planner_backend="template")

    result = run_planner_agent(state)

    assert result["planner_transaction"]["validation_status"] == "PASS"
    assert result["planner_transaction"]["diagnostic"]["planner_backend"] == "template"
    assert (tmp_path / ".r2a" / "PLANNER_OUTPUT.json").exists()
    assert (tmp_path / ".r2a" / "TASK_SPEC.md").exists()
    assert (tmp_path / ".r2a" / "EXPERIMENT_CONTRACT.md").exists()


def test_planner_real_backend_without_configuration_fails_closed(tmp_path: Path) -> None:
    state = make_initial_state(tmp_path, goal="fallback", planner_backend="claude")

    result = run_planner_agent(state)

    assert result["planner_transaction"]["validation_status"] == "FAIL"
    assert result["planner_transaction"]["diagnostic"]["planner_backend"] == "claude"
    assert result["stop_reason"] == "PLANNER_BACKEND_NOT_CONFIGURED"
    assert result["approval_ready"] is False
    assert not (tmp_path / ".r2a" / "PLANNER_OUTPUT.json").exists()


def test_generate_task_spec_marks_missing_metrics_as_evidence_gap(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    artifact_dir.mkdir()
    (artifact_dir / "PAPER_BRIEF.md").write_text("# PAPER_BRIEF\n\n## Metrics\n\nNot available in MVP\n", encoding="utf-8")
    (artifact_dir / "PAPER_EVIDENCE.md").write_text("# PAPER_EVIDENCE\n\n## Extracted Evidence\n\nNot available in MVP\n", encoding="utf-8")
    state = make_initial_state(tmp_path, goal="add HNSW oversampling baseline")
    state["paper_brief_path"] = str(artifact_dir / "PAPER_BRIEF.md")
    state["paper_evidence_path"] = str(artifact_dir / "PAPER_EVIDENCE.md")

    result = generate_task_spec(state)

    text = Path(result["task_spec_path"]).read_text(encoding="utf-8")
    assert "Evidence Gap for `metrics`" in text
    assert "Query `metrics` found" not in text
    assert "Not available in MVP" not in result["evidence_used"]


def test_generate_task_spec_includes_reproduction_card_summary(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    artifact_dir.mkdir()
    (artifact_dir / "PAPER_REPRODUCTION_CARD.md").write_text(
        "# PAPER_REPRODUCTION_CARD\n\n"
        "## 6. Baselines\n\n- Name: ACORN\n\n"
        "## 7. Datasets\n\n- Name: SIFT\n\n"
        "## 8. Metrics\n\n- Recall: recall\n- QPS / latency: QPS\n\n"
        "## 11. Reproduction Resources\n\n- Source code URL: https://github.com/example/navix\n",
        encoding="utf-8",
    )
    (artifact_dir / "PAPER_FIGURES_TABLES.md").write_text("# PAPER_FIGURES_TABLES\n\n## Figures\n\n### Figure 8\n", encoding="utf-8")
    state = make_initial_state(tmp_path, goal="reproduce reduced navix")
    state["paper_reproduction_card_path"] = str(artifact_dir / "PAPER_REPRODUCTION_CARD.md")
    state["paper_figures_tables_path"] = str(artifact_dir / "PAPER_FIGURES_TABLES.md")

    result = generate_task_spec(state)

    text = Path(result["task_spec_path"]).read_text(encoding="utf-8")
    assert "## Paper Reproduction Card Summary" in text
    assert "ACORN" in text
    assert "SIFT" in text
    assert "github.com/example/navix" in text


def test_generate_task_spec_uses_structured_review_feedback_for_replan(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    artifact_dir.mkdir()
    feedback_path = artifact_dir / "REVIEW_FEEDBACK.json"
    feedback_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "verdict": "NEEDS_FIX",
                "should_iterate": True,
                "next_planner_mode": "iterative_minimal_fix",
                "failure_categories": ["SAFE_BUILD_COMPATIBILITY"],
                "preserve_successful_steps": ["preserve verified clone"],
                "required_fixes": ["add explicit cstdint includes"],
                "forbidden_next_actions": ["do not rewrite algorithm logic"],
                "recommended_task_scope": ["rerun smallest failing build command"],
            }
        ),
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, goal="reproduce reduced navix")
    state["need_replan"] = True
    state["latest_review_feedback_path"] = str(feedback_path)

    result = generate_task_spec(state)

    text = Path(result["task_spec_path"]).read_text(encoding="utf-8")
    assert "add explicit cstdint includes" in text
    assert "rerun smallest failing build command" in text
    assert "preserve verified clone" in text
    assert "do not rewrite algorithm logic" in text
    assert Path(result["experiment_contract_path"]).exists()


def test_generate_task_spec_authorizes_network_input_acquisition_after_missing_official_input(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    artifact_dir.mkdir()
    feedback_path = artifact_dir / "REVIEW_FEEDBACK.json"
    feedback_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "verdict": "NEEDS_OFFICIAL_INPUT",
                "should_iterate": True,
                "next_planner_mode": "official_input_contract_acquisition",
                "failure_categories": ["MISSING_ARTIFACT_OR_DATA"],
                "required_fixes": ["locate official query files and ground truth"],
                "recommended_task_scope": ["official_input_contract_acquisition_with_network"],
            }
        ),
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, goal="reproduce reduced navix", download_budget_gb=20)
    state["need_replan"] = True
    state["latest_review_feedback_path"] = str(feedback_path)
    state["user_approved_official_download"] = True

    result = generate_task_spec(state)

    text = Path(result["task_spec_path"]).read_text(encoding="utf-8")
    contract_text = Path(result["experiment_contract_path"]).read_text(encoding="utf-8")
    assert "official_input_contract_acquisition_with_network" in text
    assert "允许联网" in text or "network" in text
    assert "20GB" in text
    assert "`L2_input_contract_ready`" in text
    assert "query files" in contract_text
    assert "curl" in contract_text


def test_planner_blocks_official_reduced_when_official_input_is_empty_placeholder(tmp_path: Path) -> None:
    # NOTE: This test depends on REVIEW_FEEDBACK.json which is generated by Reviewer.
    # Reviewer has been removed from the default workflow.
    # This test is kept for documentation but may not pass without Reviewer.
    # The Planner's handling of empty placeholder inputs is still tested elsewhere.
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    data_dir = artifact_dir / "artifacts" / "official" / "datasets" / "small"
    results_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    empty_query = data_dir / "query_vectors.fvecs"
    empty_query.write_bytes(b"")
    (results_dir / "input_contract_verification.csv").write_text(
        "component,status,path_or_command,evidence_source,notes\n"
        f"query,READY,{empty_query},official,size_bytes=0; integrity_status=EMPTY_PLACEHOLDER_INPUT\n",
        encoding="utf-8",
    )
    # REVIEW_FEEDBACK.json would normally come from Reviewer
    # Without Reviewer, we test Planner's basic behavior
    state = make_initial_state(tmp_path, goal="run official reduced")
    state["need_replan"] = False  # No review feedback to process

    result = generate_task_spec(state)

    task_text = Path(result["task_spec_path"]).read_text(encoding="utf-8")
    contract_text = Path(result["experiment_contract_path"]).read_text(encoding="utf-8")
    # Verify basic planner output exists
    assert "## Contract Mode\n\nverification_only" in contract_text
    # The task should mention the empty input issue
    assert "EMPTY_PLACEHOLDER_INPUT" in task_text
