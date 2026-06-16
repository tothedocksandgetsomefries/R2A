"""Tests for Web UI stage status bar fix."""

from r2a_web import app


_normalize_stage_status_for_ui = app._normalize_stage_status_for_ui


def test_success_maps_to_done():
    """SUCCESS should map to Done."""
    assert _normalize_stage_status_for_ui("SUCCESS") == "Done"
    assert _normalize_stage_status_for_ui("success") == "Done"
    assert _normalize_stage_status_for_ui("SuCcEsS") == "Done"


def test_pass_maps_to_done():
    """PASS should map to DONE."""
    assert _normalize_stage_status_for_ui("PASS") == "Done"
    assert _normalize_stage_status_for_ui("pass") == "Done"


def test_ok_maps_to_done():
    """OK should map to Done."""
    assert _normalize_stage_status_for_ui("OK") == "Done"
    assert _normalize_stage_status_for_ui("ok") == "Done"


def test_done_maps_to_done():
    """DONE should map to Done."""
    assert _normalize_stage_status_for_ui("DONE") == "Done"
    assert _normalize_stage_status_for_ui("done") == "Done"


def test_completed_maps_to_done():
    """COMPLETED should map to Done."""
    assert _normalize_stage_status_for_ui("COMPLETED") == "Done"
    assert _normalize_stage_status_for_ui("completed") == "Done"


def test_completed_success_maps_to_done():
    """COMPLETED_SUCCESS should map to Done."""
    assert _normalize_stage_status_for_ui("COMPLETED_SUCCESS") == "Done"
    assert _normalize_stage_status_for_ui("completed_success") == "Done"


def test_fail_maps_to_failed():
    """FAIL should map to Failed."""
    assert _normalize_stage_status_for_ui("FAIL") == "Failed"
    assert _normalize_stage_status_for_ui("fail") == "Failed"


def test_failed_maps_to_failed():
    """FAILED should map to Failed."""
    assert _normalize_stage_status_for_ui("FAILED") == "Failed"
    assert _normalize_stage_status_for_ui("failed") == "Failed"


def test_failure_maps_to_failed():
    """FAILURE should map to Failed."""
    assert _normalize_stage_status_for_ui("FAILURE") == "Failed"
    assert _normalize_stage_status_for_ui("failure") == "Failed"


def test_error_maps_to_failed():
    """ERROR should map to Failed."""
    assert _normalize_stage_status_for_ui("ERROR") == "Failed"
    assert _normalize_stage_status_for_ui("error") == "Failed"


def test_completed_with_failure_maps_to_failed():
    """COMPLETED_WITH_FAILURE should map to Failed."""
    assert _normalize_stage_status_for_ui("COMPLETED_WITH_FAILURE") == "Failed"
    assert _normalize_stage_status_for_ui("completed_with_failure") == "Failed"


def test_reviewer_invalid_verdict_maps_to_failed():
    """REVIEWER_INVALID_VERDICT should map to Failed."""
    assert _normalize_stage_status_for_ui("REVIEWER_INVALID_VERDICT") == "Failed"
    assert _normalize_stage_status_for_ui("reviewer_invalid_verdict") == "Failed"


def test_reviewer_feedback_validation_failed_maps_to_failed():
    """REVIEWER_FEEDBACK_VALIDATION_FAILED should map to Failed."""
    assert _normalize_stage_status_for_ui("REVIEWER_FEEDBACK_VALIDATION_FAILED") == "Failed"
    assert _normalize_stage_status_for_ui("reviewer_feedback_validation_failed") == "Failed"


def test_reviewer_needs_fix_maps_to_actionable_status():
    """NEEDS_FIX should not map to Unknown."""
    assert _normalize_stage_status_for_ui("NEEDS_FIX") == "Needs Fix"
    assert _normalize_stage_status_for_ui("needs_fix") == "Needs Fix"


def test_reviewer_needs_input_statuses_map_to_needs_input():
    """Reviewer input statuses should not map to Unknown."""
    assert _normalize_stage_status_for_ui("NEEDS_INPUT_OR_BUDGET") == "Needs Input"
    assert _normalize_stage_status_for_ui("NEEDS_OFFICIAL_INPUT") == "Needs Input"


def test_input_contract_ready_maps_to_neutral_label():
    """INPUT_CONTRACT_READY should not map to Unknown or Done."""
    assert _normalize_stage_status_for_ui("INPUT_CONTRACT_READY") == "Input Contract Ready"
    assert _normalize_stage_status_for_ui("INPUT_READY") == "Input Contract Ready"
    assert _normalize_stage_status_for_ui("CONTRACT_READY") == "Input Contract Ready"
    assert _normalize_stage_status_for_ui("REVIEW_INPUT_READY") == "Input Contract Ready"


def test_reviewer_safety_failed_statuses_map_to_failed():
    """Reviewer safety statuses should not map to Unknown."""
    assert _normalize_stage_status_for_ui("REVIEWER_SAFETY_VALIDATION_FAILED") == "Failed"
    assert _normalize_stage_status_for_ui("REVIEWER_INPUT_INTEGRITY_BLOCKED_L3") == "Failed"


def test_running_maps_to_running():
    """RUNNING should map to Running."""
    assert _normalize_stage_status_for_ui("RUNNING") == "Running"
    assert _normalize_stage_status_for_ui("running") == "Running"


def test_in_progress_maps_to_running():
    """IN_PROGRESS should map to Running."""
    assert _normalize_stage_status_for_ui("IN_PROGRESS") == "Running"
    assert _normalize_stage_status_for_ui("in_progress") == "Running"


def test_pending_maps_to_pending():
    """PENDING should map to Pending."""
    assert _normalize_stage_status_for_ui("PENDING") == "Pending"
    assert _normalize_stage_status_for_ui("pending") == "Pending"


def test_waiting_maps_to_pending():
    """WAITING should map to Pending."""
    assert _normalize_stage_status_for_ui("WAITING") == "Pending"
    assert _normalize_stage_status_for_ui("waiting") == "Pending"


def test_skipped_maps_to_skipped():
    """SKIPPED should map to Skipped."""
    assert _normalize_stage_status_for_ui("SKIPPED") == "Skipped"
    assert _normalize_stage_status_for_ui("skipped") == "Skipped"


def test_omitted_maps_to_skipped():
    """OMITTED should map to Skipped."""
    assert _normalize_stage_status_for_ui("OMITTED") == "Skipped"
    assert _normalize_stage_status_for_ui("omitted") == "Skipped"


def test_unknown_status_maps_to_unknown():
    """Unknown status should map to Unknown."""
    assert _normalize_stage_status_for_ui("") == "Unknown"
    assert _normalize_stage_status_for_ui("UNKNOWN_STATUS") == "Unknown"
    assert _normalize_stage_status_for_ui("random_text") == "Unknown"
    assert _normalize_stage_status_for_ui(None) == "Unknown"
    assert _normalize_stage_status_for_ui("  ") == "Unknown"


def test_planner_success_shows_checkmark():
    """Planner status=SUCCESS should display ✓ Planner (Done)."""
    status = _normalize_stage_status_for_ui("SUCCESS")
    assert status == "Done"
    # UI should render st.success(f"✓ Planner") when status == "Done"


def test_reviewer_invalid_verdict_shows_x():
    """Reviewer status=REVIEWER_INVALID_VERDICT should display ✗ Reviewer (Failed)."""
    status = _normalize_stage_status_for_ui("REVIEWER_INVALID_VERDICT")
    assert status == "Failed"
    # UI should render st.error(f"✗ Reviewer") when status == "Failed"


def test_final_completed_with_failure_shows_x():
    """Final status=completed_with_failure should display Failed (not Done)."""
    status = _normalize_stage_status_for_ui("completed_with_failure")
    assert status == "Failed"
    # UI should render st.error(f"✗ Final") when status == "Failed"


def test_case_insensitive_mapping():
    """All mappings should be case-insensitive."""
    # Test a sample from each category
    assert _normalize_stage_status_for_ui("success") == "Done"
    assert _normalize_stage_status_for_ui("SuCcEsS") == "Done"
    assert _normalize_stage_status_for_ui("FAIL") == "Failed"
    assert _normalize_stage_status_for_ui("Fail") == "Failed"
    assert _normalize_stage_status_for_ui("fAiL") == "Failed"
    assert _normalize_stage_status_for_ui("running") == "Running"
    assert _normalize_stage_status_for_ui("RUNNING") == "Running"
    assert _normalize_stage_status_for_ui("pending") == "Pending"
    assert _normalize_stage_status_for_ui("PENDING") == "Pending"


def test_record_fallback_uses_workflow_stage_order():
    statuses = app._concise_stage_statuses_from_sources(
        record={"status": "running", "current_stage": "reviewer"}
    )

    assert statuses["paper"] == "Done"
    assert statuses["planner"] == "Done"
    assert statuses["approval"] == "Done"
    assert statuses["engineer"] == "Done"
    assert statuses["manager"] == "Done"
    assert statuses["reviewer"] == "Running"
    assert statuses["final"] == "Pending"


def test_prepare_next_iteration_marks_reviewer_done_not_unknown():
    statuses = app._concise_stage_statuses_from_sources(
        manifest={
            "status": "RUNNING",
            "current_stage": "prepare_next_iteration_node",
            "stages": {
                "paper": {"status": "SUCCESS"},
                "planner": {"status": "PENDING"},
                "approval": {"status": "SUCCESS"},
                "engineer": {"status": "SUCCESS"},
                "manager": {"status": "SUCCESS"},
                "reviewer": {"status": ""},
                "final": {"status": "PENDING"},
            },
        }
    )

    assert statuses["planner"] == "Running"
    assert statuses["reviewer"] == "Done"
    assert statuses["final"] == "Pending"


def test_manifest_current_stage_masks_future_stale_statuses():
    statuses = app._concise_stage_statuses_from_sources(
        manifest={
            "status": "RUNNING",
            "current_stage": "engineer",
            "iteration": 2,
            "stages": {
                "paper": {"status": "PASS"},
                "planner": {"status": "SUCCESS"},
                "approval": {"status": "APPROVED"},
                "engineer": {"status": "RUNNING"},
                "manager": {"status": "PASS"},
                "reviewer": {"status": "INPUT_CONTRACT_READY"},
                "final": {"status": "PENDING"},
            },
        }
    )

    assert statuses["paper"] == "Done"
    assert statuses["planner"] == "Done"
    assert statuses["approval"] == "Done"
    assert statuses["engineer"] == "Running"
    assert statuses["manager"] == "Pending"
    assert statuses["reviewer"] == "Pending"
    assert statuses["final"] == "Pending"


def test_manifest_reviewer_input_contract_ready_is_not_unknown_when_current():
    statuses = app._concise_stage_statuses_from_sources(
        manifest={
            "status": "completed_with_failure",
            "current_stage": "final",
            "stages": {
                "paper": {"status": "PASS"},
                "planner": {"status": "SUCCESS"},
                "approval": {"status": "PASS"},
                "engineer": {"status": "PASS"},
                "manager": {"status": "PASS"},
                "reviewer": {"status": "INPUT_CONTRACT_READY"},
                "final": {"status": "PASS"},
            },
        }
    )

    assert statuses["reviewer"] == "Input Contract Ready"
    assert statuses["reviewer"] != "Unknown"
    assert statuses["reviewer"] != "Done"


def test_manifest_reviewer_needs_fix_does_not_show_unknown():
    statuses = app._concise_stage_statuses_from_sources(
        manifest={
            "status": "completed_with_failure",
            "current_stage": "final",
            "stages": {
                "paper": {"status": "PASS"},
                "planner": {"status": "SUCCESS"},
                "approval": {"status": "PASS"},
                "engineer": {"status": "PASS"},
                "manager": {"status": "PASS"},
                "reviewer": {"status": "NEEDS_FIX"},
                "final": {"status": "PASS"},
            },
        }
    )

    assert statuses["reviewer"] == "Needs Fix"
    assert statuses["reviewer"] != "Unknown"


def test_workflow_completed_with_failure_overrides_final_pass():
    statuses = app._concise_stage_statuses_from_sources(
        manifest={
            "status": "completed_with_failure",
            "current_stage": "final",
            "stages": {
                "paper": {"status": "PASS"},
                "planner": {"status": "SUCCESS"},
                "approval": {"status": "PASS"},
                "engineer": {"status": "PASS"},
                "manager": {"status": "PASS"},
                "reviewer": {"status": "NEEDS_FIX"},
                "final": {"status": "PASS"},
            },
            "final_decision": {"final_status": "completed_with_failure"},
        }
    )

    assert statuses["final"] == "Failed"
    assert statuses["final"] != "Done"


def test_terminal_failed_decision_overrides_final_pass():
    statuses = app._concise_stage_statuses_from_sources(
        manifest={
            "status": "RUNNING",
            "current_stage": "final",
            "stages": {"final": {"status": "PASS"}},
            "decision_status": {"typed_decision": "terminal_failed"},
        }
    )

    assert statuses["final"] == "Failed"
