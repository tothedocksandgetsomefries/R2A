"""Test new evidence level model: current_reproduction_level.

Tests for:
1. Reviewer is the only writer of current_reproduction_level
2. Other modules only read current_reproduction_level
3. Planner success does not trigger stop_success
4. Evidence level from file inference is not official level
5. Target reached does not auto-stop
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from r2a.agents.reviewer_agent import run_reviewer_agent
from r2a.core.state import make_initial_state
from r2a.tools.workflow_decision import aggregate_terminal_decision


def setup_minimal_workspace(tmp_path: Path) -> Path:
    """Create minimal workspace with evidence files.

    设置一个完整的工作区，包括源验证、输入合同验证和 paper artifacts。
    """
    artifact_dir = tmp_path / ".r2a"
    results_dir = artifact_dir / "results"
    results_dir.mkdir(parents=True)

    # 创建一些源代码文件，使仓库不被视为空 scaffold
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Test Project\n", encoding="utf-8")

    # Required files
    (artifact_dir / "TASK_SPEC.md").write_text("# TASK_SPEC\n\n## Goal\n\nBuild and test\n", encoding="utf-8")
    (artifact_dir / "EXECUTION_REPORT.md").write_text("# EXECUTION_REPORT\n\n- status: pass\n", encoding="utf-8")

    # Paper context - 必须存在以避免 missing_paper blocker
    (artifact_dir / "PAPER_CONTEXT.md").write_text("# Paper Context\n\n## Title\n\nTest Paper\n", encoding="utf-8")
    (artifact_dir / "PAPER_BRIEF.md").write_text("# Paper Brief\n\n## Summary\n\nTest paper for reproduction.\n", encoding="utf-8")
    (artifact_dir / "PAPER_REPRODUCTION_CARD.md").write_text("# Card\n\n## Method\n\nTest method.\n", encoding="utf-8")
    # 添加 PAPER_BUNDLE_STATUS.json 表示 paper bundle 有效
    (artifact_dir / "PAPER_BUNDLE_STATUS.json").write_text(
        json.dumps({"status": "valid", "artifacts": ["PAPER_CONTEXT.md", "PAPER_BRIEF.md"]}),
        encoding="utf-8",
    )
    (artifact_dir / "EXPERIMENT_CONTRACT.md").write_text(
        "# EXPERIMENT_CONTRACT\n\n## Contract Mode\n\nofficial_reduced\n",
        encoding="utf-8",
    )

    # Evidence files - 完整的源验证
    (results_dir / "source_verification.csv").write_text(
        "status,artifact_url,source_path,branch,commit,tag,readme_found,build_docs_found,experiment_scripts_found,data_scripts_found,notes\n"
        "PASS,https://example.test/repo,.,main,abc123,,yes,yes,no,no,source cloned successfully\n",
        encoding="utf-8",
    )
    (results_dir / "build_smoke.csv").write_text(
        "status,command,exit_code,duration_sec,component,notes\n"
        "PASS,cmake --build build,0,30,all,Build succeeded\n",
        encoding="utf-8",
    )
    (results_dir / "runtime_smoke.csv").write_text(
        "status,command,exit_code,duration_sec,component,notes\n"
        "PASS,./run_benchmark,0,60,benchmark,Benchmark succeeded\n",
        encoding="utf-8",
    )
    (results_dir / "reduced_metrics.csv").write_text(
        "dataset,method,k,recall,qps\n"
        "test,ACORN,10,0.95,100.0\n",
        encoding="utf-8",
    )
    # 添加 command_manifest.csv 用于 L3 证明
    (results_dir / "command_manifest.csv").write_text(
        "command_id,command,exit_code,duration_sec,log_path,artifact_path,dataset,method,k\n"
        "cmd-001,./run_benchmark,0,60,logs/benchmark.log,results/reduced_metrics.csv,test,ACORN,10\n",
        encoding="utf-8",
    )
    # 添加 input_contract_verification.csv
    (results_dir / "input_contract_verification.csv").write_text(
        "component,status,source,notes\n"
        "dataset,PASS,official,official dataset\n"
        "query,PASS,official,official queries\n"
        "ground_truth,PASS,official,official ground truth\n",
        encoding="utf-8",
    )
    (results_dir / "ENGINEER_DONE.txt").write_text("PASS\n", encoding="utf-8")

    return artifact_dir


def _verdict_for_level(level: str) -> str:
    return {
        "L0_project_health": "PASS_WITH_LIMITATIONS",
        "L1_source_artifact_verified": "PASS_SMOKE_ONLY",
        "L2_input_contract_ready": "INPUT_CONTRACT_READY",
        "L3_official_reduced_run": "PASS_REDUCED_METHOD_ONLY",
        "L4_reduced_paper_aligned": "PASS_REDUCED_ALIGNED",
        "L5_minimal_baseline_comparison": "PASS_REDUCED_COMPARISON",
        "L6_full_or_near_full_reproduction": "PASS",
    }[level]


def _feedback_for_level(
    level: str,
    *,
    reasoning: str = "Reviewer accepted the structured level.",
    supporting_artifacts: list[str] | None = None,
    remaining_gaps: list[str] | None = None,
    verdict: str | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "iteration": 1,
        "verdict": verdict or _verdict_for_level(level),
        "current_reproduction_level": level,
        "accepted_level": level,
        "level_valid": True,
        "level_reasoning": reasoning,
        "supporting_artifacts": list(supporting_artifacts or []),
        "remaining_gaps": list(remaining_gaps or []),
        "required_fixes": [],
        "target_reached": False,
        "should_iterate": False,
    }


def _openclaw_stage_result(allowed_outputs: list[str]) -> dict:
    return {
        "stage": "reviewer",
        "backend": "openclaw",
        "returncode": 0,
        "stdout_log_path": "",
        "stderr_log_path": "",
        "stdout_tail": "",
        "stderr_tail": "",
        "allowed_outputs": allowed_outputs,
        "success": True,
        "unexpected_modifications": [],
        "stage_guard_ok": True,
        "guard_available": True,
        "stage_guard_error": "",
        "stage_guard_warning": "",
        "stdout_json": True,
        "provider": "deepseek",
        "model": "deepseek-chat",
        "runner": "embedded",
        "fallbackUsed": False,
    }


def _write_reviewer_candidate(repo_path: str | Path, allowed_outputs: list[str], feedback: dict) -> None:
    repo = Path(repo_path)
    report = repo / allowed_outputs[0]
    feedback_path = repo / allowed_outputs[1]
    report.parent.mkdir(parents=True, exist_ok=True)
    feedback_path.parent.mkdir(parents=True, exist_ok=True)

    payload = dict(feedback)
    report_verdict = str(payload.pop("report_verdict", payload.get("verdict", "")) or "")
    if report_verdict:
        report.write_text(f"# REVIEW_REPORT\n\n## Verdict\n\n{report_verdict}\n", encoding="utf-8")
    else:
        report.write_text("# REVIEW_REPORT\n\nNo machine verdict was committed in Markdown.\n", encoding="utf-8")
    feedback_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _run_reviewer_with_openclaw_feedback(state: dict, feedback: dict) -> dict:
    import r2a.agents.reviewer_agent as reviewer_module

    original = reviewer_module._run_openclaw_reviewer_stage

    def fake_openclaw(repo_path, input_path, allowed_outputs, **kwargs):
        _write_reviewer_candidate(repo_path, allowed_outputs, feedback)
        return _openclaw_stage_result(allowed_outputs)

    reviewer_module._run_openclaw_reviewer_stage = fake_openclaw
    try:
        return run_reviewer_agent(state)
    finally:
        reviewer_module._run_openclaw_reviewer_stage = original


def _mark_previous_valid_level(state: dict, level: str, *, iteration: int = 1) -> None:
    state["current_reproduction_level"] = level
    state["current_level_iteration"] = iteration
    state["achieved_reproduction_level"] = level
    state["reproduction_level"] = level
    state["level_source"] = "ai_backend"
    state["reviewer_level_valid"] = True


class TestReviewerOnlyWriter:
    """Reviewer is the only writer of current_reproduction_level."""

    def test_reviewer_writes_current_level(self, tmp_path: Path) -> None:
        """Reviewer should write current_reproduction_level when AI backend returns valid level."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        state["reviewer_backend"] = "openclaw"

        result = _run_reviewer_with_openclaw_feedback(
            state,
            _feedback_for_level("L2_input_contract_ready", reasoning="Test reasoning for L2."),
        )

        # Reviewer should write current_reproduction_level
        assert "current_reproduction_level" in result
        assert result["current_reproduction_level"] == "L2_input_contract_ready"
        assert result.get("reviewer_level_valid") == True

    def test_reviewer_writes_current_level_iteration(self, tmp_path: Path) -> None:
        """Reviewer should write current_level_iteration when AI backend returns valid level."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        state["iteration"] = 2
        state["reviewer_backend"] = "openclaw"

        result = _run_reviewer_with_openclaw_feedback(
            state,
            _feedback_for_level("L2_input_contract_ready", reasoning="Test reasoning for L2."),
        )

        # Reviewer should write current_level_iteration
        assert "current_level_iteration" in result
        assert result["current_level_iteration"] == 2

    def test_reviewer_syncs_compatibility_fields(self, tmp_path: Path) -> None:
        """Reviewer should sync compatibility fields."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        state["reviewer_backend"] = "openclaw"

        result = _run_reviewer_with_openclaw_feedback(
            state,
            _feedback_for_level("L3_official_reduced_run", reasoning="Test reasoning for L3."),
        )

        # Compatibility fields should be synced
        current = result["current_reproduction_level"]
        assert result.get("reproduction_level") == current
        assert result.get("achieved_reproduction_level") == current

    def test_rules_backend_does_not_generate_level(self, tmp_path: Path) -> None:
        """rules backend should not generate official level."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        state["reviewer_backend"] = "rules"  # rules backend

        result = run_reviewer_agent(state)

        # rules backend should not update level
        assert result.get("reviewer_level_valid") == False
        assert "rules" in result.get("reviewer_level_rejection_reason", "").lower()


class TestOtherModulesOnlyRead:
    """Other modules only read current_reproduction_level."""

    def test_decision_aggregator_reads_current_level(self, tmp_path: Path) -> None:
        """Decision Aggregator should read current_reproduction_level."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        state["reviewer_executed"] = True
        state["current_reproduction_level"] = "L3_official_reduced_run"
        state["current_level_iteration"] = 1
        state["reviewer_verdict"] = "PASS_L3"
        state["auto_iterate"] = True
        state["max_iterations"] = 12
        state["iteration"] = 1

        decision = aggregate_terminal_decision(state)

        # Decision should be based on current_reproduction_level
        # Not from file inference
        evidence = decision.get("evidence_summary", {})
        assert evidence.get("accepted_level") == "L3_official_reduced_run"

    def test_unassessed_when_reviewer_not_run(self, tmp_path: Path) -> None:
        """current_reproduction_level should be UNASSESSED when Reviewer not run."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        # No reviewer_executed, no current_reproduction_level

        decision = aggregate_terminal_decision(state)

        evidence = decision.get("evidence_summary", {})
        # Should be UNASSESSED, not from file inference
        assert evidence.get("accepted_level") == "UNASSESSED"


class TestPlannerDoesNotTriggerSuccess:
    """Planner success does not trigger stop_success."""

    def test_planner_success_continues_to_engineer(self, tmp_path: Path) -> None:
        """After Planner, should continue to Engineer."""
        from r2a.workflow.router import route_after_planner

        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["planner_status"] = "PASS"
        state["planner_transaction"] = {
            "validation_status": "PASS",
            "committed": True,
        }
        state["approval_ready"] = True
        state["iteration"] = 1
        state["max_iterations"] = 12
        state["auto_iterate"] = True

        route = route_after_planner(state)

        # Should go to approval (then Engineer), not final
        assert route in {"approval", "engineer"}

    def test_no_stop_success_after_planner(self, tmp_path: Path) -> None:
        """Decision after Planner should not be stop_success."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["planner_status"] = "PASS"
        state["iteration"] = 1
        state["max_iterations"] = 12
        state["auto_iterate"] = True

        decision = aggregate_terminal_decision(state)

        # Should not be stop_success (Planner can't determine success)
        assert decision["typed_decision"] not in {"stop_success", "stop_evidence_cap"}


class TestFileInferenceNotOfficial:
    """Evidence level from file inference is not official level."""

    def test_evidence_summary_uses_reviewer_level(self, tmp_path: Path) -> None:
        """Evidence summary should use Reviewer's level, not file inference."""
        from r2a.tools.workflow_decision import _evidence_summary

        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["current_reproduction_level"] = "L2_input_contract_ready"
        state["current_level_iteration"] = 1
        state["reviewer_verdict"] = "PASS_L2"

        # Even though files show L3 evidence, use Reviewer's L2
        evidence = _evidence_summary(tmp_path, state)

        assert evidence["accepted_level"] == "L2_input_contract_ready"

    def test_file_inference_not_used_for_decision(self, tmp_path: Path) -> None:
        """File inference should not be used for routing decisions."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        # Reviewer not run yet
        state["iteration"] = 1
        state["max_iterations"] = 12
        state["auto_iterate"] = True

        decision = aggregate_terminal_decision(state)

        # 应该继续下一阶段（Manager 后继续），或者因为缺少 source blocker 而请求 source
        # 关键：不应该因为文件推断的等级而停止
        # evidence_summary 应该是 UNASSESSED
        evidence = decision.get("evidence_summary", {})
        assert evidence.get("accepted_level") == "UNASSESSED"


class TestTargetReachedDoesNotAutoStop:
    """Target reached does not auto-stop."""

    def test_target_reached_continues_if_iterations_left(self, tmp_path: Path) -> None:
        """Even if target reached, should continue if iterations left and auto_iterate is True."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        state["reviewer_executed"] = True
        state["current_reproduction_level"] = "L4_reduced_paper_aligned"
        state["current_level_iteration"] = 1
        state["reviewer_verdict"] = "PASS_L4"
        state["target_reproduction_level"] = "L4_reduced_paper_aligned"
        state["iteration"] = 1
        state["max_iterations"] = 12
        state["auto_iterate"] = True

        decision = aggregate_terminal_decision(state)

        # Should continue iteration because auto_iterate=True and iteration < max_iterations
        # The key point: reaching target does NOT auto-stop
        assert decision["typed_decision"] == "continue_iteration"

    def test_max_iterations_stops(self, tmp_path: Path) -> None:
        """Should stop when max iterations reached."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        state["reviewer_executed"] = True
        state["current_reproduction_level"] = "L3_official_reduced_run"
        state["current_level_iteration"] = 3
        state["reviewer_verdict"] = "PASS_L3"
        state["target_reproduction_level"] = "L4_reduced_paper_aligned"
        state["iteration"] = 3
        state["max_iterations"] = 3
        state["auto_iterate"] = True

        decision = aggregate_terminal_decision(state)

        # Should stop due to max iterations (normal termination)
        # Use "final" instead of "stop_evidence_cap"
        assert decision["typed_decision"] == "final"
        assert decision["reason_code"] == "MAX_ITERATIONS_REACHED"

    def test_auto_iterate_false_stops(self, tmp_path: Path) -> None:
        """Should stop if auto_iterate is False."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        state["reviewer_executed"] = True
        state["current_reproduction_level"] = "L3_official_reduced_run"
        state["current_level_iteration"] = 1
        state["reviewer_verdict"] = "PASS_L3"
        state["target_reproduction_level"] = "L4_reduced_paper_aligned"
        state["iteration"] = 1
        state["max_iterations"] = 12
        state["auto_iterate"] = False

        decision = aggregate_terminal_decision(state)

        # Should stop because auto_iterate is False (normal termination)
        # Use "final" instead of "stop_evidence_cap"
        assert decision["typed_decision"] == "final"
        assert decision["reason_code"] == "AUTO_ITERATE_DISABLED"


class TestOldFieldsCompatibility:
    """Old fields compatibility tests."""

    def test_iteration_state_uses_current_level(self, tmp_path: Path) -> None:
        """ITERATION_STATE.json should use current_reproduction_level."""
        from r2a.tools.iteration import write_iteration_state

        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["current_reproduction_level"] = "L3_official_reduced_run"
        state["current_level_iteration"] = 2
        state["iteration"] = 2
        state["max_iterations"] = 12
        state["auto_iterate"] = True

        path = write_iteration_state(state)

        import json
        data = json.loads(path.read_text(encoding="utf-8"))

        # Should use new field
        assert data.get("current_reproduction_level") == "L3_official_reduced_run"
        assert data.get("current_level_iteration") == 2
        # Compatibility field should be synced
        assert data.get("reproduction_level") == "L3_official_reduced_run"
        assert data.get("achieved_reproduction_level") == "L3_official_reduced_run"

    def test_manifest_uses_current_level(self, tmp_path: Path) -> None:
        """RUN_MANIFEST should use current_reproduction_level."""
        from r2a.core.run_manifest import write_run_manifest

        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["current_reproduction_level"] = "L3_official_reduced_run"
        state["current_level_iteration"] = 2
        state["reviewer_verdict"] = "PASS_L3"
        state["reviewer_executed"] = True
        state["iteration"] = 2

        path = write_run_manifest(state)

        import json
        data = json.loads(path.read_text(encoding="utf-8"))

        # Should use new field
        assert data.get("current_level") == "L3_official_reduced_run"
        assert data.get("current_level_iteration") == 2
        # Compatibility field should be synced
        assert data.get("achieved_level") == "L3_official_reduced_run"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestInvalidOutputHandling:
    """Test invalid output handling - no auto fallback to L0."""

    def test_invalid_level_not_updated(self, tmp_path: Path) -> None:
        """Invalid level (L7) should not update current_reproduction_level."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        state["reviewer_backend"] = "openclaw"
        _mark_previous_valid_level(state, "L2_input_contract_ready", iteration=1)
        feedback = {
            "schema_version": 1,
            "iteration": 1,
            "verdict": "INPUT_CONTRACT_READY",
            "current_reproduction_level": "L7_invalid",  # Invalid level
            "level_reasoning": "Invalid level test.",
            "supporting_artifacts": [],
            "remaining_gaps": [],
            "report_verdict": "",
        }

        result = _run_reviewer_with_openclaw_feedback(state, feedback)

        # Should not update level
        assert result.get("reviewer_level_valid") == False
        assert result["current_reproduction_level"] == "L2_input_contract_ready"  # Preserved
        assert result["current_level_iteration"] == 1  # Preserved

    def test_empty_legacy_reasoning_is_not_required_by_review_verdict(self, tmp_path: Path) -> None:
        """Current REVIEW_VERDICT schema does not require legacy level_reasoning."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        state["reviewer_backend"] = "openclaw"
        _mark_previous_valid_level(state, "L3_official_reduced_run", iteration=1)

        result = _run_reviewer_with_openclaw_feedback(
            state,
            _feedback_for_level("L4_reduced_paper_aligned", reasoning=""),
        )

        assert result.get("reviewer_level_valid") == True
        assert result["current_reproduction_level"] == "L4_reduced_paper_aligned"

    def test_needs_fix_verdict_without_accepted_level_is_unassessed(self, tmp_path: Path) -> None:
        """A NEEDS_FIX structured verdict is unassessed rather than preserving old level."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        state["reviewer_backend"] = "openclaw"
        _mark_previous_valid_level(state, "L2_input_contract_ready", iteration=1)
        feedback = {
            "schema_version": 1,
            "iteration": 1,
            "verdict": "NEEDS_FIX",
            "required_fixes": ["No accepted level was committed."],
            "should_iterate": True,
        }

        result = _run_reviewer_with_openclaw_feedback(state, feedback)

        assert result.get("reviewer_level_valid") == False
        assert result["current_reproduction_level"] is None
        assert result["current_level_iteration"] == 0

    def test_no_auto_fallback_to_L0(self, tmp_path: Path) -> None:
        """Invalid output should NOT auto fallback to L0."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        state["reviewer_backend"] = "openclaw"
        # No previous level
        state["structured_review_feedback"] = {
            "current_reproduction_level": "invalid",
            "level_reasoning": "test",
            "supporting_artifacts": [],
            "remaining_gaps": [],
        }

        result = run_reviewer_agent(state)

        # Should NOT auto fallback to L0
        assert result.get("reviewer_level_valid") == False
        # Should not have L0 as auto fallback
        assert result.get("current_reproduction_level") != "L0_project_health"


class TestNoHardcodedLevelMapping:
    """Test that there's no hardcoded level mapping from verdict or file names."""

    def test_verdict_pass_not_auto_L6(self, tmp_path: Path) -> None:
        """verdict=PASS should not automatically become L6."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        state["reviewer_backend"] = "openclaw"

        result = _run_reviewer_with_openclaw_feedback(
            state,
            _feedback_for_level(
                "L3_official_reduced_run",
                reasoning="Only reduced method completed.",
                verdict="PASS",
            ),
        )

        # Should use AI's L3, not auto-map PASS to L6
        assert result["current_reproduction_level"] == "L3_official_reduced_run"
        assert result.get("reviewer_level_valid") == True

    def test_file_names_not_used_for_level(self, tmp_path: Path) -> None:
        """File names should not determine level."""
        setup_minimal_workspace(tmp_path)

        # Create files with names that might trigger level mapping
        results_dir = tmp_path / ".r2a" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)

        # Create files with "full_reproduction" in name
        (results_dir / "full_reproduction_metrics.csv").write_text("metric,value\nacc,0.9\n")
        (results_dir / "baseline_comparison.csv").write_text("method,acc\nbaseline,0.85\n")

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        state["reviewer_backend"] = "openclaw"

        result = _run_reviewer_with_openclaw_feedback(
            state,
            _feedback_for_level(
                "L2_input_contract_ready",
                reasoning="Only input contract ready, no actual metrics yet.",
            ),
        )

        # Should use AI's L2, not auto-upgrade based on file names
        assert result["current_reproduction_level"] == "L2_input_contract_ready"


class TestValidLevelOutput:
    """Test valid level output scenarios."""

    def test_ai_returns_L2_written_as_L2(self, tmp_path: Path) -> None:
        """AI returns L2, should be written as L2."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        state["reviewer_backend"] = "openclaw"
        state["iteration"] = 1

        result = _run_reviewer_with_openclaw_feedback(
            state,
            _feedback_for_level(
                "L2_input_contract_ready",
                reasoning="Input contract established successfully.",
                supporting_artifacts=[".r2a/results/input_contract.csv"],
                remaining_gaps=["Official reduced metrics not produced"],
            ),
        )

        assert result["current_reproduction_level"] == "L2_input_contract_ready"
        assert result["current_level_iteration"] == 1
        assert result.get("reviewer_level_valid") == True

    def test_ai_returns_L4_written_as_L4(self, tmp_path: Path) -> None:
        """AI returns L4, should be written as L4."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        state["reviewer_backend"] = "openclaw"
        state["iteration"] = 2

        result = _run_reviewer_with_openclaw_feedback(
            state,
            _feedback_for_level(
                "L4_reduced_paper_aligned",
                reasoning="Reduced metrics aligned with paper objectives.",
                supporting_artifacts=[".r2a/results/reduced_metrics.csv", ".r2a/results/paper_alignment.csv"],
                remaining_gaps=["Baseline comparison not performed"],
            ),
        )

        assert result["current_reproduction_level"] == "L4_reduced_paper_aligned"
        assert result["current_level_iteration"] == 2

    def test_ai_returns_L6_written_as_L6(self, tmp_path: Path) -> None:
        """AI returns L6, should be written as L6."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        state["reviewer_backend"] = "openclaw"
        state["iteration"] = 5

        result = _run_reviewer_with_openclaw_feedback(
            state,
            _feedback_for_level(
                "L6_full_or_near_full_reproduction",
                reasoning="Full reproduction completed with all main experiments.",
                supporting_artifacts=[".r2a/results/full_metrics.csv", ".r2a/results/baseline_comparison.csv"],
            ),
        )

        assert result["current_reproduction_level"] == "L6_full_or_near_full_reproduction"
        assert result["current_level_iteration"] == 5


class TestSupportingArtifactsNotBlocking:
    """Test that missing supporting artifact paths don't cause level rejection."""

    def test_nonexistent_artifact_path_not_blocking(self, tmp_path: Path) -> None:
        """Non-existent supporting artifact path should not reject level."""
        setup_minimal_workspace(tmp_path)

        state = make_initial_state(tmp_path)
        state["engineer_status"] = "PASS"
        state["manager_status"] = "PASS"
        state["manager_executed"] = True
        state["reviewer_backend"] = "openclaw"

        result = _run_reviewer_with_openclaw_feedback(
            state,
            _feedback_for_level(
                "L3_official_reduced_run",
                reasoning="Reduced run completed.",
                supporting_artifacts=[".r2a/results/nonexistent.csv"],
            ),
        )

        # Should still accept the level
        assert result["current_reproduction_level"] == "L3_official_reduced_run"
        assert result.get("reviewer_level_valid") == True
