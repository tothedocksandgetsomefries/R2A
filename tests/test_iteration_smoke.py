"""Graph-level smoke tests for decision-status iteration control."""
from __future__ import annotations

from pathlib import Path

from r2a.core.paths import ensure_artifact_dir, report_path
from r2a.core.state import make_initial_state
from r2a.workflow.graph import build_workflow_graph


def test_graph_auto_iteration_runs_until_max_iterations(tmp_path: Path) -> None:
    """Reviewer feedback is advisory; max_iterations counts complete Reviewer cycles.

    Setup:
    - Reviewer returns NEEDS_FIX and asks for replan
    - auto_iterate=True, max_iterations=2

    Expected:
    - target/verdict does not stop early
    - exactly 2 Reviewer cycles run before MAX_ITERATIONS_REACHED
    """
    # Create minimal readable paper context.
    paper = tmp_path / "paper.txt"
    paper.write_text("Paper text with enough context for the smoke workflow.", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")

    # Ensure artifact dirs
    ensure_artifact_dir(tmp_path)

    # Track iteration count
    iteration_count = [0]

    # Monkeypatch reviewer in nodes module (where it's imported)
    import r2a.workflow.nodes as nodes

    original_reviewer = nodes.run_reviewer_agent

    def controlled_reviewer(state):
        iteration_count[0] += 1
        result = original_reviewer(state)
        # Reviewer verdicts no longer authorize workflow routing by themselves.
        if iteration_count[0] == 1:
            result["reviewer_verdict"] = "NEEDS_FIX"
            result["need_replan"] = True
        else:
            result["reviewer_verdict"] = "PASS"
            result["need_replan"] = False
        return result

    nodes.run_reviewer_agent = controlled_reviewer

    try:
        # Build and run workflow
        graph = build_workflow_graph()

        initial_state = make_initial_state(
            tmp_path,
            paper_path=paper,
            goal="test iteration",
            paper_backend="preprocess",
            planner_backend="template",
            engineer_executor="mock",
            manager_backend="rules",
            reviewer_backend="rules",
            auto_iterate=True,
            max_iterations=2,
            auto_approve=True,
        )

        result = graph.invoke(initial_state)

        # Reviewer NEEDS_FIX/PASS is advisory; max_iterations controls final routing.
        assert result.get("iteration") == 2, f"Final iteration should follow decision_status, got {result.get('iteration')}"
        assert iteration_count[0] == 2
        assert result.get("decision_status", {}).get("typed_decision") == "final"
        assert result.get("decision_status", {}).get("reason_code") == "MAX_ITERATIONS_REACHED"
        assert result.get("decision_status", {}).get("completed_review_iterations") == 2

        # Verify paper_brief_path exists (Paper executed at least once)
        assert result.get("paper_brief_path"), "Paper should have executed and generated paper_brief_path"

    finally:
        # Restore original functions
        nodes.run_reviewer_agent = original_reviewer


def test_graph_max_iterations_stops_at_final(tmp_path: Path) -> None:
    """Verify workflow stops at max_iterations without second iteration.

    Setup:
    - reviewer_verdict = NEEDS_FIX
    - auto_iterate = True
    - iteration = 1
    - max_iterations = 1

    Expected:
    - Goes directly to final
    - No prepare_next_iteration
    - No second iteration
    """
    from r2a.core.paths import ensure_artifact_dir, report_path
    from r2a.workflow.graph import build_workflow_graph

    # Track stage execution
    prepare_count = [0]

    # Create minimal paper
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4")
    ensure_artifact_dir(tmp_path)

    # Create minimal outputs
    task_path = report_path(tmp_path, "task")
    contract_path = report_path(tmp_path, "experiment_contract")
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(
        "# TASK_SPEC\n\n"
        "## Reproducibility Gate Summary\nok\n\n"
        "## Max Evidence Level Allowed\nL2_input_contract_ready\n\n",
        encoding="utf-8",
    )
    contract_path.write_text(
        "# EXPERIMENT_CONTRACT\n\n"
        "## Contract Mode\nverification_only\n\n",
        encoding="utf-8",
    )

    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "result.csv").write_text("dataset,method,qps\ntest,method,100\n", encoding="utf-8")

    check_path = report_path(tmp_path, "check")
    check_path.write_text("# CHECK_REPORT\n\n## Status\nPASS\n", encoding="utf-8")

    review_path = report_path(tmp_path, "review")
    review_path.write_text("# REVIEW_REPORT\n\n## Verdict\nNEEDS_FIX\n", encoding="utf-8")

    feedback_path = report_path(tmp_path, "review_feedback")
    feedback_path.write_text('{"verdict": "NEEDS_FIX"}', encoding="utf-8")

    # Monkeypatch to track prepare_next_iteration
    import r2a.tools.iteration as iteration_module
    original_prepare = iteration_module.prepare_next_iteration

    def tracked_prepare(state):
        prepare_count[0] += 1
        return original_prepare(state)

    iteration_module.prepare_next_iteration = tracked_prepare

    try:
        graph = build_workflow_graph()

        initial_state = make_initial_state(
            tmp_path,
            paper_path=paper,
            goal="test max iterations",
            paper_backend="preprocess",
            planner_backend="template",
            engineer_executor="mock",
            manager_backend="rules",
            reviewer_backend="rules",
            auto_iterate=True,
            max_iterations=1,
            auto_approve=True,
        )

        result = graph.invoke(initial_state)

        # Verify no prepare_next_iteration was called
        assert prepare_count[0] == 0, f"prepare_next_iteration should not be called, got {prepare_count[0]}"

        # Verify iteration remains 1
        assert result.get("iteration") == 1, f"Iteration should remain 1, got {result.get('iteration')}"

    finally:
        iteration_module.prepare_next_iteration = original_prepare


def test_graph_stopped_prevents_iteration(tmp_path: Path) -> None:
    """Verify stopped=True prevents iteration even with NEEDS_FIX.

    Setup:
    - reviewer_verdict = NEEDS_FIX
    - auto_iterate = True
    - iteration < max_iterations
    - stopped = True

    Expected:
    - Goes directly to final
    - No prepare_next_iteration
    """
    from r2a.core.paths import ensure_artifact_dir, report_path
    from r2a.workflow.graph import build_workflow_graph

    # Track stage execution
    prepare_count = [0]

    # Create minimal paper
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4")
    ensure_artifact_dir(tmp_path)

    # Create minimal outputs
    task_path = report_path(tmp_path, "task")
    contract_path = report_path(tmp_path, "experiment_contract")
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text("# TASK_SPEC\n\n## Reproducibility Gate Summary\nok\n", encoding="utf-8")
    contract_path.write_text("# EXPERIMENT_CONTRACT\n\n## Contract Mode\nverification_only\n", encoding="utf-8")

    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "result.csv").write_text("dataset,method,qps\ntest,method,100\n", encoding="utf-8")

    check_path = report_path(tmp_path, "check")
    check_path.write_text("# CHECK_REPORT\n\n## Status\nPASS\n", encoding="utf-8")

    review_path = report_path(tmp_path, "review")
    review_path.write_text("# REVIEW_REPORT\n\n## Verdict\nNEEDS_FIX\n", encoding="utf-8")

    feedback_path = report_path(tmp_path, "review_feedback")
    feedback_path.write_text('{"verdict": "NEEDS_FIX"}', encoding="utf-8")

    # Monkeypatch to track prepare_next_iteration
    import r2a.tools.iteration as iteration_module
    original_prepare = iteration_module.prepare_next_iteration

    def tracked_prepare(state):
        prepare_count[0] += 1
        return original_prepare(state)

    iteration_module.prepare_next_iteration = tracked_prepare

    try:
        graph = build_workflow_graph()

        initial_state = make_initial_state(
            tmp_path,
            paper_path=paper,
            goal="test stopped",
            paper_backend="preprocess",
            planner_backend="template",
            engineer_executor="mock",
            manager_backend="rules",
            reviewer_backend="rules",
            auto_iterate=True,
            max_iterations=2,
            auto_approve=True,
        )
        initial_state["stopped"] = True

        result = graph.invoke(initial_state)

        # Verify no prepare_next_iteration was called
        assert prepare_count[0] == 0, f"prepare_next_iteration should not be called with stopped=True"

    finally:
        iteration_module.prepare_next_iteration = original_prepare
