"""
专项测试：TARGET_EVIDENCE_REACHED 保护逻辑

测试场景：
1. manager_status = "pending" → 不得 stop_success
2. manager_status = "failed" → 不得 stop_success
3. 只有旧的 check_report_path，当前轮 Manager 未完成 → 不得 stop_success
4. Engineer 完成、Manager 未完成 → 不得 stop_success
5. 当前轮 Manager 明确完成，但 accepted level 未达到 target → 不得 stop_success
6. 当前轮 Manager 明确完成，实际文件 evidence 达到 target，也不得自动 stop_success
7. L0 目标也不得自动 stop_success
"""
from __future__ import annotations

import json
from pathlib import Path

from r2a.core.paths import report_path
from r2a.core.state import make_initial_state
from r2a.core.run_manifest import mark_stage_finished, mark_stage_started
from r2a.tools.workflow_decision import aggregate_terminal_decision
from r2a.tools.iteration import _manifest_stages, _stage_status_from_manifest


def test_manager_pending_not_stop_success(tmp_path: Path) -> None:
    """
    测试 1：manager_status = "pending" → 不得 stop_success
    """
    _write_paper_bundle(tmp_path)
    _write_l4_evidence(tmp_path)

    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    # Manager 状态为 PENDING（未完成）
    state = mark_stage_started(state, "manager")
    # 不调用 mark_stage_finished，保持 PENDING 状态

    decision = aggregate_terminal_decision(state)

    # 验证：不能是 stop_success
    assert decision["typed_decision"] != "stop_success", \
        f"Manager PENDING 状态不应返回 stop_success，实际返回: {decision['typed_decision']}"


def test_manager_failed_not_stop_success(tmp_path: Path) -> None:
    """
    测试 2：manager_status = "failed" → 不得 stop_success
    """
    _write_paper_bundle(tmp_path)
    _write_l4_evidence(tmp_path)

    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    # Manager 状态为 FAIL
    state = mark_stage_started(state, "manager")
    state = mark_stage_finished(state, "manager", status="FAIL", errors=["test failure"])

    decision = aggregate_terminal_decision(state)

    # 验证：不能是 stop_success
    assert decision["typed_decision"] != "stop_success", \
        f"Manager FAIL 状态不应返回 stop_success，实际返回: {decision['typed_decision']}"


def test_old_check_report_path_not_stop_success(tmp_path: Path) -> None:
    """
    测试 3：只有旧的 check_report_path，当前轮 Manager 未完成 → 不得 stop_success
    """
    _write_paper_bundle(tmp_path)
    _write_l4_evidence(tmp_path)

    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    # 创建旧的 check_report_path
    check_path = report_path(tmp_path, "check")
    check_path.parent.mkdir(parents=True, exist_ok=True)
    check_path.write_text("# Old Check Report\n\nPASS", encoding="utf-8")

    # 设置旧的路径（模拟旧迭代残留）
    state["check_report_path"] = str(check_path)

    # 但当前轮 Manager 未完成（RUN_MANIFEST 中没有 manager stage）
    # decision 应该不返回 stop_success

    decision = aggregate_terminal_decision(state)

    # 验证：不能是 stop_success（因为 MANIFEST 中 manager 未完成）
    assert decision["typed_decision"] != "stop_success", \
        f"旧路径不应触发 stop_success，实际返回: {decision['typed_decision']}"


def test_engineer_done_manager_not_done_not_stop_success(tmp_path: Path) -> None:
    """
    测试 4：Engineer 完成、Manager 未完成 → 不得 stop_success
    """
    _write_paper_bundle(tmp_path)
    _write_l4_evidence(tmp_path)

    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    # Engineer 完成
    state = mark_stage_started(state, "engineer")
    state = mark_stage_finished(state, "engineer", status="PASS")

    # Manager 未完成
    state = mark_stage_started(state, "manager")
    # 不调用 mark_stage_finished

    decision = aggregate_terminal_decision(state)

    # 验证：不能是 stop_success
    assert decision["typed_decision"] != "stop_success", \
        f"Manager 未完成不应返回 stop_success，实际返回: {decision['typed_decision']}"


def test_manager_done_but_level_not_reached_not_stop_success(tmp_path: Path) -> None:
    """
    测试 5：当前轮 Manager 明确完成，但 accepted level 未达到 target → 不得 stop_success
    """
    _write_paper_bundle(tmp_path)
    # 只写 L0 evidence，不是 L4
    _write_l0_evidence_only(tmp_path)

    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    # Manager 完成
    state = mark_stage_started(state, "manager")
    state = mark_stage_finished(state, "manager", status="PASS")

    decision = aggregate_terminal_decision(state)

    # 验证：不能是 stop_success（因为 evidence 只有 L0）
    assert decision["typed_decision"] != "stop_success", \
        f"evidence 未达到 target 不应返回 stop_success，实际返回: {decision['typed_decision']}"

    # 验证：decision_status 不从文件推断 accepted_level
    evidence = decision.get("evidence_summary", {})
    assert evidence.get("accepted_level") == "UNASSESSED", \
        f"Reviewer 未判级时 accepted_level 应为 UNASSESSED，实际是: {evidence.get('accepted_level')}"


def test_manager_done_level_reached_does_not_auto_stop_success(tmp_path: Path) -> None:
    """
    测试 6：当前轮 Manager 明确完成，实际文件 evidence 达到 target，也不得自动 stop_success
    """
    _write_paper_bundle(tmp_path)
    _write_l4_evidence(tmp_path)

    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    # Engineer 完成
    state = mark_stage_started(state, "engineer")
    state = mark_stage_finished(state, "engineer", status="PASS")

    # Manager 完成
    state = mark_stage_started(state, "manager")
    state = mark_stage_finished(state, "manager", status="PASS")

    # 创建必要的报告文件
    report_path(tmp_path, "execution").parent.mkdir(parents=True, exist_ok=True)
    report_path(tmp_path, "execution").write_text("# Execution Report\n\nPASS", encoding="utf-8")
    report_path(tmp_path, "check").write_text("# Check Report\n\nPASS", encoding="utf-8")

    decision = aggregate_terminal_decision(state)

    # 验证：当前策略不因 target reached 自动停止
    assert decision["typed_decision"] == "continue_iteration", \
        f"Manager 完成 + 文件 evidence 达到 target 也不应自动停止，实际返回: {decision['typed_decision']}"

    assert decision["reason_code"] != "TARGET_EVIDENCE_REACHED", \
        f"不应返回 TARGET_EVIDENCE_REACHED，实际返回: {decision['reason_code']}"


def test_l0_target_not_affected(tmp_path: Path) -> None:
    """
    测试 7：L0 目标的现有行为不要被误伤
    """
    _write_paper_bundle(tmp_path)
    _write_l0_evidence_only(tmp_path)

    state = _state_with_paper(tmp_path, target_reproduction_level="L0_project_health")

    # L0 目标，不需要 Manager 完成
    decision = aggregate_terminal_decision(state)

    # 验证：L0 目标也不自动 stop_success
    assert decision["typed_decision"] == "continue_iteration", \
        f"L0 目标不应自动 stop_success，实际返回: {decision['typed_decision']}"

    assert decision["reason_code"] != "TARGET_EVIDENCE_REACHED", \
        f"L0 目标不应返回 TARGET_EVIDENCE_REACHED，实际返回: {decision['reason_code']}"


def test_manager_running_not_stop_success(tmp_path: Path) -> None:
    """
    测试 8：Manager RUNNING 状态 → 不得 stop_success
    """
    _write_paper_bundle(tmp_path)
    _write_l4_evidence(tmp_path)

    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    # Manager 状态为 RUNNING
    state = mark_stage_started(state, "manager")
    # 不调用 mark_stage_finished

    decision = aggregate_terminal_decision(state)

    # 验证：不能是 stop_success
    assert decision["typed_decision"] != "stop_success", \
        f"Manager RUNNING 状态不应返回 stop_success，实际返回: {decision['typed_decision']}"


def test_manager_skipped_not_stop_success(tmp_path: Path) -> None:
    """
    测试 9：Manager SKIPPED 状态 → 不得 stop_success
    """
    _write_paper_bundle(tmp_path)
    _write_l4_evidence(tmp_path)

    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    # Manager 状态为 SKIPPED
    state = mark_stage_started(state, "manager")
    state = mark_stage_finished(state, "manager", status="SKIPPED")

    decision = aggregate_terminal_decision(state)

    # 验证：不能是 stop_success
    assert decision["typed_decision"] != "stop_success", \
        f"Manager SKIPPED 状态不应返回 stop_success，实际返回: {decision['typed_decision']}"


def test_manifest_stage_status_authoritative(tmp_path: Path) -> None:
    """
    测试 10：RUN_MANIFEST stage status 是权威来源
    """
    _write_paper_bundle(tmp_path)
    _write_l4_evidence(tmp_path)

    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    # Manager 在 MANIFEST 中完成
    state = mark_stage_started(state, "manager")
    state = mark_stage_finished(state, "manager", status="PASS")

    # 验证：可以从 MANIFEST 读取 stage status
    stages = _manifest_stages(tmp_path, state)
    manager_status = _stage_status_from_manifest(stages, "manager")

    assert manager_status == "PASS", \
        f"MANIFEST 中 manager status 应该是 PASS，实际是: {manager_status}"

    # 创建必要的报告文件
    report_path(tmp_path, "execution").parent.mkdir(parents=True, exist_ok=True)
    report_path(tmp_path, "execution").write_text("# Execution Report\n\nPASS", encoding="utf-8")
    report_path(tmp_path, "check").write_text("# Check Report\n\nPASS", encoding="utf-8")

    decision = aggregate_terminal_decision(state)

    # 验证：manifest PASS 仍不触发 target-reached auto-stop
    assert decision["typed_decision"] == "continue_iteration", \
        f"MANIFEST 权威状态是 PASS，但不应自动 stop_success，实际返回: {decision['typed_decision']}"
    assert decision["reason_code"] != "TARGET_EVIDENCE_REACHED"


# === 辅助函数 ===

def _state_with_paper(tmp_path: Path, **kwargs) -> dict:
    paper = tmp_path / "paper.txt"
    paper.write_text("paper text", encoding="utf-8")
    return make_initial_state(tmp_path, paper_path=paper, **kwargs)


def _write_paper_bundle(repo: Path) -> None:
    from r2a.tools.workflow_decision import PAPER_STRUCTURED_KEYS
    _write_source_fixture(repo)
    for key in PAPER_STRUCTURED_KEYS:
        path = report_path(repo, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = '{"ok": true}' if key == "paper_output" else f"# {key}\n\nok\n"
        path.write_text(body, encoding="utf-8")


def _write_source_fixture(repo: Path) -> None:
    (repo / "main.py").write_text("print('ok')\n", encoding="utf-8")
    results = repo / ".r2a" / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / "source_verification.csv").write_text(
        "status,artifact_url,source_path,branch,commit,notes\n"
        "PASS,https://example.test/repo,.,main,abc123,official source verified\n",
        encoding="utf-8",
    )


def _write_l0_evidence_only(repo: Path) -> None:
    """只写 L0 evidence（source_verification.csv），不写 L1-L4 evidence"""
    results = repo / ".r2a" / "results"
    results.mkdir(parents=True, exist_ok=True)


def _write_l2_evidence(repo: Path) -> None:
    results = repo / ".r2a" / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / "input_contract_verification.csv").write_text(
        "component,status,path_or_command,evidence_source,notes\n"
        "dataset,FOUND,official_small,README,official dataset documented\n"
        "query,FOUND,queries.tsv,README,official query documented\n"
        "ground_truth,FOUND,gt.tsv,README,official ground truth documented\n"
        "metric_definition,READY,recall@10,paper,metric documented\n",
        encoding="utf-8",
    )


def _write_l4_evidence(repo: Path) -> None:
    _write_l2_evidence(repo)
    results = repo / ".r2a" / "results"
    logs = repo / ".r2a" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "reduced.log").write_text("measured\n", encoding="utf-8")
    (results / "reduced_metrics.csv").write_text(
        "command_id,dataset,method,k,ground_truth_source,metric_definition,input_provenance,recall,latency_ms\n"
        "cmd,official_small,Curator,10,gt.tsv,recall@10,README official_small,0.9,1.2\n",
        encoding="utf-8",
    )
    (results / "command_manifest.csv").write_text(
        "command_id,command,exit_code,duration_sec,log_path,artifact_path,artifact_hash,input_provenance,notes\n"
        "cmd,python run.py,0,1.2,reduced.log,.r2a/results/reduced_metrics.csv,sha256:x,README official_small,ok\n",
        encoding="utf-8",
    )
    (results / "paper_alignment.csv").write_text(
        "paper_item,setting_name,paper_setting,reduced_setting,match_status,evidence_source,notes\n"
        "Table 1,dataset scale,full,small,PARTIAL_MATCH,paper,scale differs\n"
        "Table 1,hardware,paper cpu,test cpu,PARTIAL_MATCH,paper,hardware differs\n"
        "Table 1,runtime budget,full,short,PARTIAL_MATCH,paper,budget differs\n"
        "Table 1,parameters,k=10,k=10,MATCH,command,match\n"
        "Table 1,number of repeats,1,1,MATCH,paper,match\n"
        "Table 1,baselines,HNSW,missing,PARTIAL_MATCH,paper,baseline gap\n"
        "Table 1,metric definition,recall@10,recall@10,MATCH,paper,match\n"
        "Table 1,input source,official,official small,PARTIAL_MATCH,artifact,official reduced\n"
        "Table 1,known evidence gaps,full scale missing,full scale missing,NEEDS_HUMAN_VERIFICATION,review,gap\n",
        encoding="utf-8",
    )
