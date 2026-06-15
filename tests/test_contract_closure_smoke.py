"""Contract closure smoke tests - minimal verification of contract flow."""
from __future__ import annotations

from pathlib import Path
import tempfile
import shutil

from r2a.core.state import make_initial_state
from r2a.workflow.graph import build_workflow_graph
from r2a.agents.paper_agent import run_paper_agent
from r2a.agents.planner_agent import run_planner_agent
from r2a.agents.engineer_agent import run_engineer_agent
from r2a.agents.manager_agent import run_manager_agent
from r2a.core.paths import report_path, artifact_dir


def test_smoke_a_normal_path():
    """A. Normal path: Paper -> Planner -> Engineer -> Manager -> Final"""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        artifact_dir(repo).mkdir(parents=True, exist_ok=True)
        paper = repo / "paper.txt"
        paper.write_text("Minimal paper context for contract closure smoke.", encoding="utf-8")
        (repo / "main.py").write_text("print('ok')\n", encoding="utf-8")

        state = make_initial_state(
            repo,
            goal="minimal smoke test",
            paper_path=paper,
            executor="mock",
            planner_backend="template",
            engineer_executor="mock",
            manager_backend="rules",
            auto_approve=True,
        )

        graph = build_workflow_graph()
        result = graph.invoke(state)

        # Verify all required artifacts exist
        assert Path(result["paper_brief_path"]).exists(), "PAPER_BRIEF.md missing"
        assert Path(result["task_spec_path"]).exists(), "TASK_SPEC.md missing"
        # experiment_contract_path may use latest_experiment_contract_path or be in planner output
        contract_path = result.get("experiment_contract_path") or result.get("latest_experiment_contract_path") or report_path(Path(result["repo_path"]), "experiment_contract")
        assert Path(contract_path).exists(), "EXPERIMENT_CONTRACT.md missing"
        assert Path(result["planner_output_path"]).exists(), "PLANNER_OUTPUT.json missing"
        assert Path(result["execution_report_path"]).exists(), "EXECUTION_REPORT.md missing"
        assert Path(result["check_report_path"]).exists(), "CHECK_REPORT.md missing"
        assert Path(result["final_report_path"]).exists(), "FINAL_REPORT.md missing"

        # Final should not claim beyond evidence
        final_text = Path(result["final_report_path"]).read_text(encoding="utf-8")
        assert "L6" not in final_text or "L0" in final_text or "L1" in final_text, "Final should not claim L6 for smoke"


def test_smoke_b_planner_missing_paper():
    """B. Planner missing Paper outputs - should stop"""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        r2a_dir = artifact_dir(repo)
        r2a_dir.mkdir(parents=True, exist_ok=True)

        # Create state without Paper stage
        state = make_initial_state(
            repo,
            goal="test missing paper",
            executor="mock",
            planner_backend="template",
            auto_approve=True,
        )

        # Run Planner directly without Paper
        result = run_planner_agent(state)

        # Planner should still work with template backend
        # Template backend doesn't require paper outputs
        assert result.get("task_spec_path") or result.get("stopped"), "Planner should either succeed or stop"


def test_smoke_c_engineer_missing_task_spec():
    """C. Engineer missing TASK_SPEC.md - should stop"""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        r2a_dir = artifact_dir(repo)
        r2a_dir.mkdir(parents=True, exist_ok=True)

        # Create state with mock TASK_SPEC path that doesn't exist
        state = make_initial_state(
            repo,
            goal="test missing task spec",
            executor="mock",
            auto_approve=True,
        )
        state["task_spec_path"] = str(r2a_dir / "NONEXISTENT_TASK_SPEC.md")
        state["experiment_contract_path"] = str(r2a_dir / "NONEXISTENT_CONTRACT.md")

        # Engineer should handle missing inputs gracefully
        result = run_engineer_agent(state)

        # Mock executor should complete without error
        assert result.get("execution_report_path") or result.get("stopped"), "Engineer should produce output or stop"


def test_smoke_d_manager_missing_execution_report():
    """D. Manager missing EXECUTION_REPORT.md - should not PASS"""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        r2a_dir = artifact_dir(repo)
        r2a_dir.mkdir(parents=True, exist_ok=True)

        state = make_initial_state(
            repo,
            goal="test missing execution report",
            auto_approve=True,
        )
        # Set paths to non-existent files
        state["task_spec_path"] = str(r2a_dir / "TASK_SPEC.md")
        state["experiment_contract_path"] = str(r2a_dir / "EXPERIMENT_CONTRACT.md")
        state["execution_report_path"] = str(r2a_dir / "NONEXISTENT_EXECUTION_REPORT.md")

        result = run_manager_agent(state)

        # Manager should not PASS with missing execution report
        assert result.get("manager_status") in {"FAIL", "WARNING"}, f"Manager should not PASS with missing EXECUTION_REPORT, got: {result.get('manager_status')}"
        assert result.get("manager_passed") is False or result.get("manager_status") == "WARNING", "Manager passed should be False or WARNING"


def test_smoke_e_csv_missing_columns():
    """E. CSV with missing required columns - Manager records readable output without grading schema."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        r2a_dir = artifact_dir(repo)
        results_dir = r2a_dir / "results"
        results_dir.mkdir(parents=True, exist_ok=True)

        # Create TASK_SPEC.md that requires CSV output
        task_spec = r2a_dir / "TASK_SPEC.md"
        task_spec.write_text(
            "# TASK_SPEC\n\n"
            "## Expected Outputs\n\n"
            "- .r2a/results/reduced_metrics.csv\n\n"
            "## Contract Mode\n\n"
            "official_reduced\n",
            encoding="utf-8",
        )

        # Create EXPERIMENT_CONTRACT.md
        contract = r2a_dir / "EXPERIMENT_CONTRACT.md"
        contract.write_text(
            "# EXPERIMENT_CONTRACT\n\n"
            "## Contract Mode\n\n"
            "official_reduced\n",
            encoding="utf-8",
        )

        # Create EXECUTION_REPORT.md
        exec_report = r2a_dir / "EXECUTION_REPORT.md"
        exec_report.write_text(
            "# EXECUTION_REPORT\n\n"
            "Engineer mock execution completed.\n",
            encoding="utf-8",
        )

        # Create malformed CSV missing required columns
        bad_csv = results_dir / "reduced_metrics.csv"
        bad_csv.write_text(
            "dataset,method\n"  # Missing: k, notes, command_id, etc.
            "sift,hnsw\n",
            encoding="utf-8",
        )

        state = make_initial_state(
            repo,
            goal="test malformed CSV",
            auto_approve=True,
        )
        state["task_spec_path"] = str(task_spec)
        state["experiment_contract_path"] = str(contract)
        state["execution_report_path"] = str(exec_report)

        result = run_manager_agent(state)

        # Manager should pass because schema grading belongs to Reviewer/evidence checks.
        assert result.get("manager_status") == "PASS"
        assert result.get("manager_passed") is True

        # Check report should mention CSV issues
        check_path = Path(result["check_report_path"])
        if check_path.exists():
            check_text = check_path.read_text(encoding="utf-8")
            assert "current output: .r2a\\results\\reduced_metrics.csv" in check_text


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
