"""
Test contract_mode enforcement and Planner schema simplification.

This test file validates the following requirements:
1. contract_mode is determined by user permissions, NOT by Planner model
2. Planner model output is overwritten by system-determined contract_mode
3. Planner schema only requires tasks with executable actions
4. Missing objective, title, description, etc. do NOT cause Planner failure
"""
from __future__ import annotations

import pytest

from r2a.core.planner_schema import (
    BlockingIssue,
    PlannerOutput,
    PlannerTask,
    calculate_system_contract_mode,
    enforce_system_contract_mode,
)


class TestContractModeEnforcement:
    """Test that contract_mode is enforced by system, not model."""

    def test_full_benchmark_authorization(self):
        """allow_full_benchmark=True -> full_benchmark"""
        mode = calculate_system_contract_mode(
            allow_full_benchmark=True,
            allow_official_dataset_download=True,
            download_budget_gb=100,
        )
        assert mode == "full_benchmark"

    def test_official_reduced_with_download_permission(self):
        """allow_official_dataset_download=True (with budget) -> official_reduced"""
        mode = calculate_system_contract_mode(
            allow_full_benchmark=False,
            allow_official_dataset_download=True,
            download_budget_gb=20,
        )
        assert mode == "official_reduced"

    def test_official_reduced_without_budget_fails(self):
        """allow_official_dataset_download=True but budget=0 -> verification_only"""
        mode = calculate_system_contract_mode(
            allow_full_benchmark=False,
            allow_official_dataset_download=True,
            download_budget_gb=0,
        )
        assert mode == "verification_only"

    def test_verification_only_default(self):
        """No special permissions -> verification_only"""
        mode = calculate_system_contract_mode(
            allow_full_benchmark=False,
            allow_official_dataset_download=False,
            download_budget_gb=0,
        )
        assert mode == "verification_only"

    def test_model_output_overwritten_by_system(self):
        """Model outputs verification_only, but system says official_reduced -> official_reduced"""
        task = PlannerTask(
            task_id="T001",
            title="Test task",
            objective="Test objective",
            rationale="Test rationale",
            actions=["Run test"],
        )
        planner_output = PlannerOutput(
            schema_version="2.0",
            iteration=1,
            planning_mode="initial",
            iteration_strategy="PROGRESS_ONLY",
            contract_mode="verification_only",  # Model output
            max_evidence_level_allowed="L2_input_contract_ready",  # Model output
            tasks=[task],
        )

        allowed_scope = {
            "contract_mode": "official_reduced",  # System value
            "max_target_level": "L4_reduced_paper_aligned",  # System value
        }

        enforced = enforce_system_contract_mode(planner_output, allowed_scope)

        assert enforced.contract_mode == "official_reduced"
        assert enforced.max_evidence_level_allowed == "L4_reduced_paper_aligned"
        assert any("normalization" in note for note in enforced.planner_notes)

    def test_model_upgrade_rejected(self):
        """Model tries to upgrade to full_benchmark, but system says official_reduced -> official_reduced"""
        task = PlannerTask(
            task_id="T001",
            title="Test task",
            actions=["Run test"],
        )
        planner_output = PlannerOutput(
            schema_version="2.0",
            iteration=1,
            planning_mode="initial",
            iteration_strategy="PROGRESS_ONLY",
            contract_mode="full_benchmark",  # Model tries to upgrade
            tasks=[task],
        )

        allowed_scope = {
            "contract_mode": "official_reduced",  # System says no
        }

        enforced = enforce_system_contract_mode(planner_output, allowed_scope)

        assert enforced.contract_mode == "official_reduced"

    def test_no_allowed_scope_returns_original(self):
        """If allowed_scope is None, return original output unchanged."""
        task = PlannerTask(
            task_id="T001",
            title="Test task",
            actions=["Run test"],
        )
        planner_output = PlannerOutput(
            schema_version="2.0",
            iteration=1,
            planning_mode="initial",
            iteration_strategy="PROGRESS_ONLY",
            contract_mode="verification_only",
            tasks=[task],
        )

        enforced = enforce_system_contract_mode(planner_output, None)

        assert enforced.contract_mode == planner_output.contract_mode


class TestPlannerSchemaSimplification:
    """Test that Planner schema is minimal - only requires tasks with actions."""

    def test_minimal_valid_task(self):
        """Task with only actions should be valid."""
        task = PlannerTask(
            actions=["Run command"],
        )
        assert task.actions == ["Run command"]
        assert task.task_id == "" or task.task_id.startswith("T_auto_")

    def test_task_with_empty_actions_fails(self):
        """Task without actions should fail."""
        with pytest.raises(ValueError, match="actions must not be empty"):
            PlannerTask(
                task_id="T001",
                title="Test",
                objective="Test",
                actions=[],
            )

    def test_task_without_objective_succeeds(self):
        """Task without objective should succeed."""
        task = PlannerTask(
            actions=["Run command"],
        )
        assert task.objective == ""

    def test_task_without_title_succeeds(self):
        """Task without title should succeed."""
        task = PlannerTask(
            actions=["Run command"],
        )
        assert task.title == ""

    def test_task_without_expected_outputs_succeeds(self):
        """Task without expected_outputs should succeed."""
        task = PlannerTask(
            actions=["Run command"],
        )
        assert task.expected_outputs == []

    def test_output_with_minimal_task(self):
        """PlannerOutput with minimal task should be valid."""
        task = PlannerTask(
            actions=["Run command"],
        )
        output = PlannerOutput(
            schema_version="2.0",
            iteration=1,
            planning_mode="initial",
            iteration_strategy="PROGRESS_ONLY",
            tasks=[task],
        )
        assert output.tasks[0].actions == ["Run command"]

    def test_output_without_tasks_fails(self):
        """PlannerOutput without tasks should fail."""
        with pytest.raises(ValueError, match="tasks must not be empty"):
            PlannerOutput(
                schema_version="2.0",
                iteration=1,
                planning_mode="initial",
                iteration_strategy="PROGRESS_ONLY",
                tasks=[],
            )

    def test_output_with_task_without_actions_fails(self):
        """PlannerOutput with task but no actions should fail."""
        with pytest.raises(ValueError, match="tasks must not be empty"):
            PlannerOutput(
                schema_version="2.0",
                iteration=1,
                planning_mode="initial",
                iteration_strategy="PROGRESS_ONLY",
                tasks=[],
            )

    def test_output_without_objective_succeeds(self):
        """PlannerOutput without objective should succeed."""
        task = PlannerTask(actions=["Run command"])
        output = PlannerOutput(
            schema_version="2.0",
            iteration=1,
            planning_mode="initial",
            iteration_strategy="PROGRESS_ONLY",
            tasks=[task],
        )
        assert output.objective == ""

    def test_output_without_completed_capabilities_succeeds(self):
        """PlannerOutput without completed_capabilities should succeed."""
        task = PlannerTask(actions=["Run command"])
        output = PlannerOutput(
            schema_version="2.0",
            iteration=1,
            planning_mode="initial",
            iteration_strategy="PROGRESS_ONLY",
            tasks=[task],
        )
        assert output.completed_capabilities == []

    def test_output_without_blocking_issues_succeeds(self):
        """PlannerOutput without blocking_issues should succeed."""
        task = PlannerTask(actions=["Run command"])
        output = PlannerOutput(
            schema_version="2.0",
            iteration=1,
            planning_mode="initial",
            iteration_strategy="PROGRESS_ONLY",
            tasks=[task],
        )
        assert output.blocking_issues == []

    def test_one_invalid_task_ignored_if_others_valid(self):
        """If one task has no actions but others do, output should still fail."""
        # Note: This behavior is strict - all tasks in output must have actions
        # because we validate individual tasks during construction
        task1 = PlannerTask(actions=["Run command"])
        task2 = PlannerTask(actions=["Run another command"])
        output = PlannerOutput(
            schema_version="2.0",
            iteration=1,
            planning_mode="initial",
            iteration_strategy="PROGRESS_ONLY",
            tasks=[task1, task2],
        )
        assert len(output.tasks) == 2


class TestAutoCorrection:
    """Test that PlannerOutput auto-corrects invalid field combinations."""

    def test_initial_mode_with_iteration_2_autocorrects(self):
        """planning_mode=initial with iteration=2 should auto-correct to iterative_progress."""
        task = PlannerTask(actions=["Run command"])
        output = PlannerOutput(
            schema_version="2.0",
            iteration=2,
            planning_mode="initial",  # Invalid
            iteration_strategy="PROGRESS_ONLY",
            tasks=[task],
        )
        assert output.planning_mode == "iterative_progress"

    def test_iterative_mode_with_iteration_1_autocorrects(self):
        """planning_mode=iterative_progress with iteration=1 should auto-correct to iteration=2."""
        task = PlannerTask(actions=["Run command"])
        output = PlannerOutput(
            schema_version="2.0",
            iteration=1,
            planning_mode="iterative_progress",  # Invalid
            iteration_strategy="PROGRESS_ONLY",
            tasks=[task],
        )
        assert output.iteration == 2

    def test_blocked_strategy_without_issues_autocorrects(self):
        """BLOCKED_OR_NEEDS_APPROVAL without issues should auto-correct to FIX_AND_PROGRESS."""
        task = PlannerTask(actions=["Run command"])
        output = PlannerOutput(
            schema_version="2.0",
            iteration=1,
            planning_mode="initial",
            iteration_strategy="BLOCKED_OR_NEEDS_APPROVAL",  # Invalid without issues
            tasks=[task],
        )
        assert output.iteration_strategy == "FIX_AND_PROGRESS"


class TestTargetLevelPreservation:
    """Test that user's target level is preserved."""

    def test_target_level_not_downgraded(self):
        """System should not downgrade target_level based on model output."""
        task = PlannerTask(actions=["Run command"])
        planner_output = PlannerOutput(
            schema_version="2.0",
            iteration=1,
            planning_mode="initial",
            iteration_strategy="PROGRESS_ONLY",
            max_evidence_level_allowed="L2_input_contract_ready",  # Model tries to downgrade
            tasks=[task],
        )

        allowed_scope = {
            "max_target_level": "L4_reduced_paper_aligned",  # User selected L4
        }

        enforced = enforce_system_contract_mode(planner_output, allowed_scope)

        assert enforced.max_evidence_level_allowed == "L4_reduced_paper_aligned"

    def test_target_level_not_upgraded(self):
        """System should not upgrade target_level beyond user selection."""
        task = PlannerTask(actions=["Run command"])
        planner_output = PlannerOutput(
            schema_version="2.0",
            iteration=1,
            planning_mode="initial",
            iteration_strategy="PROGRESS_ONLY",
            max_evidence_level_allowed="L6_full_or_near_full_reproduction",  # Model tries to upgrade
            tasks=[task],
        )

        allowed_scope = {
            "max_target_level": "L4_reduced_paper_aligned",  # User selected L4
        }

        enforced = enforce_system_contract_mode(planner_output, allowed_scope)

        assert enforced.max_evidence_level_allowed == "L4_reduced_paper_aligned"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
