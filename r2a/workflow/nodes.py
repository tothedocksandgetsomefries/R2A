from __future__ import annotations

from r2a.agents.engineer_agent import run_engineer_agent
from r2a.agents.manager_agent import run_manager_agent
from r2a.agents.paper_agent import run_paper_agent
from r2a.agents.planner_agent import run_planner_agent
from r2a.agents.reviewer_agent import run_reviewer_agent
from r2a.core.paths import report_path
from r2a.core.state import R2AState
from r2a.core.run_manifest import mark_stage_finished, mark_stage_started, write_run_manifest
from r2a.tools.final_writer import read_final_writer_metadata
from r2a.tools.iteration import archive_current_iteration, archive_final_iteration, prepare_next_iteration, write_final_report, write_iteration_state
from r2a.tools.readiness_gate import check_engineer_readiness, check_paper_readiness, check_planner_readiness
from r2a.tools.source_acquisition import acquire_source
from r2a.tools.source_inspection import inspect_source
from r2a.tools.stage_transaction import write_planner_transaction_metadata
from r2a.tools.workflow_decision import aggregate_terminal_decision


PLANNER_FAILURE_REASONS = {
    "PLANNER_FORBIDDEN_WRITE",
    "BACKEND_TRANSIENT_FAILURE",
    "PLANNER_BACKEND_FAILURE",
    "PLANNER_BACKEND_NOT_CONFIGURED",
    "PLANNER_MODEL_FAILURE",
    "PLANNER_SCHEMA_VALIDATION_FAILED",
    "PLANNER_TRANSACTION_FAILED",
    "PLANNER_MISSING_REQUIRED_OUTPUT",
    "PLANNER_STALE_OUTPUT",
    "PLANNER_CONTRACT_VALIDATION_FAILED",
    "planner_stage_failed",
}


def paper_node(state: R2AState) -> R2AState:
    result = _run_stage(state, "paper", run_paper_agent)
    readiness = check_paper_readiness(result)
    result = {**result, "paper_readiness": readiness}
    if not readiness.get("ready"):
        result["decision_status"] = aggregate_terminal_decision(result)
        return result
    result = acquire_source(result)
    acquisition = result.get("source_acquisition", {})
    if isinstance(acquisition, dict) and acquisition.get("source_status") == "available":
        result = inspect_source(result)
    result["decision_status"] = aggregate_terminal_decision(result)
    return result


def planner_node(state: R2AState) -> R2AState:
    readiness = check_planner_readiness(state)
    if not readiness.get("ready"):
        blocked = {
            **state,
            "planner_readiness": readiness,
            "planner_status": "blocked",
            "approval_ready": False,
        }
        blocked["decision_status"] = aggregate_terminal_decision(blocked)
        return blocked
    return _run_stage({**state, "planner_readiness": readiness}, "planner", run_planner_agent)


def human_approval_node(state: R2AState) -> R2AState:
    state = mark_stage_started(state, "approval")
    if state.get("stopped"):
        return mark_stage_finished(state, "approval", status="SKIPPED", errors=state.get("errors", []), warnings=state.get("warnings", []))
    if state.get("auto_approve") or state.get("approved"):
        planner_transaction = dict(state.get("planner_transaction", {}) or {})
        if planner_transaction:
            diagnostic = {
                **dict(planner_transaction.get("diagnostic", {}) or {}),
                "approval_passed": True,
            }
            planner_transaction["diagnostic"] = diagnostic
            write_planner_transaction_metadata(state["repo_path"], planner_transaction)
        approved = {**state, "approved": True, "stopped": False, "planner_transaction": planner_transaction or state.get("planner_transaction")}
        return mark_stage_finished(approved, "approval", status="PASS", errors=approved.get("errors", []), warnings=approved.get("warnings", []))
    errors = [*state.get("errors", []), "Human approval is required before Engineer Stage."]
    rejected = {**state, "approved": False, "stopped": True, "errors": errors}
    return mark_stage_finished(rejected, "approval", status="FAIL", errors=errors, warnings=rejected.get("warnings", []))


def engineer_node(state: R2AState) -> R2AState:
    readiness = check_engineer_readiness(state)
    if not readiness.get("ready"):
        blocked = {
            **state,
            "engineer_readiness": readiness,
            "engineer_status": "BLOCKED",
            "engineer_passed": False,
            "engineer_executor_failed": False,
        }
        blocked["decision_status"] = aggregate_terminal_decision(blocked)
        return blocked
    return _run_stage({**state, "engineer_readiness": readiness}, "engineer", run_engineer_agent)


def manager_node(state: R2AState) -> R2AState:
    return _run_stage(state, "manager", run_manager_agent)


def reviewer_node(state: R2AState) -> R2AState:
    return _run_stage(state, "reviewer", run_reviewer_agent)


def prepare_next_iteration_node(state: R2AState) -> R2AState:
    """Prepare for the next iteration by archiving current results and updating state."""
    state = mark_stage_started(state, "prepare_next_iteration")
    # Archive current iteration results
    archived = archive_current_iteration(state)
    # Prepare next iteration state
    next_state = prepare_next_iteration(archived)
    return mark_stage_finished(
        next_state,
        "prepare_next_iteration",
        status="PASS",
        errors=next_state.get("errors", []),
        warnings=next_state.get("warnings", []),
    )


def final_node(state: R2AState) -> R2AState:
    state = mark_stage_started(state, "final")
    decision = dict(state.get("decision_status", {}) or {})
    if not decision or (
        str(decision.get("typed_decision", "") or "") == "continue_iteration"
        and str(decision.get("reason_code", "") or "") != "READY_FOR_NEXT_ITERATION"
    ):
        decision = aggregate_terminal_decision(state)
    typed_decision = str(decision.get("typed_decision", "") or "terminal_failed")
    stop_reason = str(decision.get("reason_code", "") or typed_decision)
    summary = _decision_summary(decision)
    loop_status = _loop_status_from_decision(decision)
    planner_failure = _planner_failure_reason(state)
    updated = {
        **state,
        "decision_status": decision,
        "final_report": summary,
        "loop_status": loop_status,
        "stop_reason": stop_reason,
    }
    if typed_decision == "terminal_failed" or planner_failure:
        updated["failure_category"] = stop_reason if typed_decision == "terminal_failed" else planner_failure
    updated = _mark_unexecuted_stages(updated, stop_reason)
    updated = mark_stage_finished(
        updated,
        "final",
        status="PASS",
        errors=updated.get("errors", []),
        warnings=updated.get("warnings", []),
        artifacts=[
            str(report_path(updated["repo_path"], "final_decision")),
            str(report_path(updated["repo_path"], "final_narrative")),
            str(report_path(updated["repo_path"], "final_writer_metadata")),
            str(report_path(updated["repo_path"], "final")),
        ],
    )
    write_run_manifest(updated)
    final_path = write_final_report(updated)
    updated["final_report_path"] = str(final_path)
    final_writer_metadata = read_final_writer_metadata(updated["repo_path"])
    if final_writer_metadata:
        metadata = dict(updated.get("metadata", {}) or {})
        metadata["final_writer"] = final_writer_metadata
        updated["metadata"] = metadata
        updated["final_writer_metadata_path"] = str(report_path(updated["repo_path"], "final_writer_metadata"))
        updated["final_writer_output_path"] = str(final_writer_metadata.get("output_path", "") or "")
    final_decision = report_path(updated["repo_path"], "final_decision")
    if final_decision.exists():
        updated["final_decision_path"] = str(final_decision)
    updated = archive_final_iteration(updated)
    write_iteration_state(updated)
    write_run_manifest(updated)
    return updated


def _decision_summary(decision: dict) -> str:
    typed = str(decision.get("typed_decision", "") or "unknown")
    reason = str(decision.get("reason_code", "") or decision.get("reason", "") or "UNKNOWN")
    detail = str(decision.get("reason", "") or "").strip()
    if detail and detail != reason:
        return f"R2A workflow finalized with decision {typed}: {detail}"
    return f"R2A workflow finalized with decision {typed}: {reason}"


def _loop_status_from_decision(decision: dict) -> str:
    typed = str(decision.get("typed_decision", "") or "")
    if typed == "stop_success":
        return "completed"
    if typed == "stop_evidence_cap":
        return "completed"
    if typed in {"terminal_failed", "request_paper", "request_source", "request_dataset", "request_approval", "retry_backend"}:
        return "completed_with_failure"
    return "completed"


def _mark_unexecuted_stages(state: R2AState, blocker: str) -> R2AState:
    decision = dict(state.get("decision_status", {}) or {})
    typed_decision = str(decision.get("typed_decision", "") or "")
    if typed_decision == "request_paper":
        stages = ("planner", "approval", "engineer", "manager", "reviewer")
        blocked_by = "paper"
    elif typed_decision == "request_source":
        stages = ("planner", "approval", "engineer", "manager", "reviewer")
        blocked_by = "source"
    elif not state.get("stopped"):
        return state
    else:
        planner_failure = _planner_failure_reason(state)
        if planner_failure:
            stages = ("approval", "engineer", "manager", "reviewer")
            blocked_by = "planner"
        elif (state.get("stop_reason") or blocker) == "human_approval_rejected" or state.get("approved") is False:
            stages = ("engineer", "manager", "reviewer")
            blocked_by = "approval"
        else:
            return state
    updated = state
    warning = f"Stage skipped because {blocked_by} blocked the workflow: {blocker}."
    for stage in stages:
        updated = mark_stage_finished(updated, stage, status="SKIPPED", errors=[], warnings=[warning], artifacts=[])
    return updated


def _planner_failure_reason(state: R2AState) -> str:
    transaction = dict(state.get("planner_transaction", {}) or {})
    diagnostic = dict(transaction.get("diagnostic", {}) or {})
    metadata = dict(state.get("metadata", {}) or {})
    planner_stage_failure = metadata.get("planner_stage_failure", {}) if isinstance(metadata.get("planner_stage_failure"), dict) else {}
    candidates = [
        transaction.get("failure_category"),
        transaction.get("execution_status"),
        diagnostic.get("failure_category"),
        planner_stage_failure.get("failure_category"),
        planner_stage_failure.get("execution_status"),
        state.get("stop_reason"),
    ]
    transaction_failed = bool(
        transaction
        and (
            transaction.get("validation_status") == "FAIL"
            or transaction.get("committed") is False
            or diagnostic.get("planner_validation_passed") is False
            or diagnostic.get("planner_committed") is False
        )
    )
    if transaction_failed:
        return _first_planner_failure(candidates) or "PLANNER_TRANSACTION_FAILED"
    if state.get("loop_status") == "planner_failed":
        return _first_planner_failure(candidates) or "planner_stage_failed"
    return _first_planner_failure([state.get("stop_reason")])


def _first_planner_failure(candidates) -> str:
    for value in candidates:
        reason = str(value or "").strip()
        if reason in PLANNER_FAILURE_REASONS or reason.startswith("PLANNER_"):
            return reason
    return ""


def _run_stage(state: R2AState, stage: str, fn) -> R2AState:
    started = mark_stage_started(state, stage)
    try:
        result = fn(started)
    except Exception as exc:
        failed = {
            **started,
            "errors": [*started.get("errors", []), f"{stage} stage failed: {type(exc).__name__}: {exc}"],
        }
        mark_stage_finished(failed, stage, status="FAIL", errors=failed.get("errors", []), warnings=failed.get("warnings", []))
        raise
    status = _stage_status(stage, result)
    return mark_stage_finished(result, stage, status=status, errors=result.get("errors", []), warnings=result.get("warnings", []))


def _stage_status(stage: str, state: R2AState) -> str:
    if state.get("stopped") and stage in {"paper", "planner", "approval"}:
        return "FAIL"
    if stage == "planner" and state.get("planner_status"):
        return str(state.get("planner_status")).upper()
    if stage == "manager":
        return str(state.get("manager_status", "") or "UNKNOWN")
    if stage == "reviewer":
        return str(state.get("reviewer_verdict", "") or "UNKNOWN")
    if stage == "engineer" and str(state.get("engineer_status", "")).upper() == "BLOCKED":
        return "BLOCKED"
    if stage == "engineer" and state.get("engineer_status"):
        return str(state.get("engineer_status"))
    if stage == "engineer" and state.get("engineer_executor_failed"):
        return "FAIL"
    if stage == "engineer" and state.get("clarification_needed"):
        return "WARNING"
    return "PASS"


def _reviewer_blocks_success(verdict: str) -> bool:
    return verdict in {
        "REJECT",
        "NEEDS_FIX",
        "NEEDS_INPUT",
        "NEEDS_OFFICIAL_INPUT",
        "NEEDS_INPUT_OR_BUDGET",
        "BORDERLINE",
    }


def _reviewer_stop_reason(verdict: str) -> str:
    return {
        "REJECT": "reviewer_rejected",
        "NEEDS_FIX": "reviewer_needs_fix",
        "NEEDS_INPUT": "reviewer_needs_input",
        "NEEDS_OFFICIAL_INPUT": "reviewer_needs_official_input",
        "NEEDS_INPUT_OR_BUDGET": "reviewer_needs_input_or_budget",
        "BORDERLINE": "reviewer_borderline",
    }.get(verdict, "reviewer_blocked_success")
