from __future__ import annotations

import json
from pathlib import Path

from r2a.core.paths import report_path
from r2a.core.state import R2AState
from r2a.tools.workflow_decision import aggregate_terminal_decision, is_terminal_decision


def approval_router(state: R2AState) -> str:
    decision = _attach_decision(state)
    if is_terminal_decision(decision) or decision.get("typed_decision") == "retry_backend":
        return "final"
    return "engineer"


def route_after_paper(state: R2AState) -> str:
    decision = _attach_decision(state)
    if decision.get("typed_decision") == "continue_iteration":
        return "planner"
    return "final"


def route_after_planner(state: R2AState) -> str:
    """Planner 后路由：必须进入 Engineer。

    简化版：Planner 成功后必须进入 Engineer。
    不再根据 evidence level 判断是否停止。
    """
    decision = _attach_decision(state)
    # Planner 后的 terminal decision 只能是 backend failure 或 blocker
    # 这些情况下进入 Final
    if decision.get("typed_decision") in {"terminal_failed", "request_paper", "request_source", "request_dataset", "request_network_authorization", "request_approval"}:
        return "final"
    # 其他情况下必须进入 Engineer
    transaction = state.get("planner_transaction", {}) or {}
    if state.get("approval_ready") or (transaction.get("validation_status") == "PASS" and transaction.get("committed")):
        return "approval"
    return "final"  # Planner 未成功提交


def route_after_engineer(state: R2AState) -> str:
    decision = _attach_decision(state)
    if decision.get("typed_decision") == "continue_iteration":
        return "manager"
    return "final"


def route_after_reviewer(state: R2AState) -> str:
    """Route after Reviewer through the single deterministic decision status."""
    feedback = _review_feedback(state)
    if feedback and not state.get("structured_review_feedback"):
        state["structured_review_feedback"] = feedback
    decision = _attach_decision(state)
    if decision.get("typed_decision") == "continue_iteration":
        return "prepare_next_iteration"
    return "final"


def _review_feedback(state: R2AState) -> dict:
    direct = state.get("structured_review_feedback")
    if isinstance(direct, dict):
        return direct
    candidates: list[Path] = []
    explicit = str(state.get("latest_review_feedback_path") or state.get("review_feedback_path") or "")
    if explicit:
        candidates.append(Path(explicit))
    if state.get("repo_path"):
        candidates.append(report_path(state["repo_path"], "review_feedback"))
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def _attach_decision(state: R2AState) -> dict:
    decision = aggregate_terminal_decision(state)
    state["decision_status"] = decision
    return decision
