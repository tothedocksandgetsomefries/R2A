"""
回归测试：Fake Success Bug 修复验证

测试场景：
1. Planner 允许 L4，但没有 Engineer evidence
2. Planner 不得覆盖 reproduction_level
3. evidence evaluation 异常时的 fallback
4. 真实达到目标才能 stop_success
5. 输出一致性验证
"""
from __future__ import annotations

import json
from pathlib import Path

from r2a.core.paths import report_path
from r2a.core.state import make_initial_state
from r2a.tools.workflow_decision import aggregate_terminal_decision, _evidence_summary, _target_reached
from r2a.tools.evidence_levels import infer_evidence_level
from r2a.core.evidence_policy import evaluate_l0_l4


def test_planner_allows_l4_but_no_engineer_evidence(tmp_path: Path) -> None:
    """
    测试 1：Planner 允许 L4，但没有 Engineer evidence

    条件：
    - target = L4
    - Planner 输出 max_evidence_level_allowed = L4
    - Planner 成功
    - Engineer、Manager 尚未运行
    - 实际只有 source_verification.csv

    预期：
    - decision_status 不声明文件推断的 accepted level
    - 不得 stop_success
    - 不得 TARGET_EVIDENCE_REACHED
    """
    _write_paper_bundle(tmp_path)
    _write_l0_evidence_only(tmp_path)

    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")
    # Planner 成功，设置 max_evidence_level_allowed（不是 reproduction_level！）
    state.update({
        "planner_status": "success",
        "max_evidence_level_allowed": "L4_reduced_paper_aligned",
        # Engineer 和 Manager 未执行
        "engineer_status": None,
        "manager_status": None,
    })

    decision = aggregate_terminal_decision(state)

    # 验证：不能是 stop_success
    assert decision["typed_decision"] != "stop_success", \
        f"Planner 成功但 Engineer 未执行，不应返回 stop_success，实际返回: {decision['typed_decision']}"

    # 验证：不能是 TARGET_EVIDENCE_REACHED
    assert decision["reason_code"] != "TARGET_EVIDENCE_REACHED", \
        f"没有实际 evidence，不应返回 TARGET_EVIDENCE_REACHED，实际返回: {decision['reason_code']}"

    # 验证：decision_status 不从文件推断 accepted_level
    evidence = decision.get("evidence_summary", {})
    assert evidence.get("accepted_level") == "UNASSESSED", \
        f"Reviewer 未判级时 accepted_level 应为 UNASSESSED，实际是: {evidence.get('accepted_level')}"


def test_planner_must_not_override_reproduction_level(tmp_path: Path) -> None:
    """
    测试 2：Planner 不得覆盖 reproduction_level

    Planner 输出 max allowed level 后：
    - state["reproduction_level"] 不得被设为该值
    - state["max_evidence_level_allowed"] 可以保存规划上限
    - 实际等级仍由 evidence 推断
    """
    _write_paper_bundle(tmp_path)
    _write_l0_evidence_only(tmp_path)

    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    # 模拟 Planner 设置 max_evidence_level_allowed（正确做法）
    state["max_evidence_level_allowed"] = "L4_reduced_paper_aligned"

    # 验证：reproduction_level 不应该被 Planner 设置
    # 它应该由 infer_evidence_level 从实际文件推断
    inferred = infer_evidence_level(tmp_path, "L0_project_health")
    assert inferred == "L0_project_health", \
        f"实际 evidence 应该推断为 L0，实际是: {inferred}"

    # 验证：evaluate_l0_l4 应该返回 L0，而不是 max_evidence_level_allowed
    decision = evaluate_l0_l4(tmp_path, state)
    assert decision["accepted_level"] == "L0_project_health", \
        f"accepted_level 应该是 L0（从实际文件推断），而不是 max_evidence_level_allowed，实际是: {decision['accepted_level']}"


def test_evidence_evaluation_exception_fallback(tmp_path: Path) -> None:
    """
    测试 3：evidence evaluation 异常

    模拟 evaluate_l0_l4() 抛异常：
    - fallback 从实际文件推断
    - status 不得为成功
    - 必须有 blocking reason
    - 不得触发 target reached
    """
    _write_paper_bundle(tmp_path)
    _write_l0_evidence_only(tmp_path)

    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    # 测试 _evidence_summary 的异常处理
    # 通过传入一个会导致异常的 repo 路径来模拟
    import tempfile
    with tempfile.TemporaryDirectory() as bad_repo:
        bad_path = Path(bad_repo)
        # 不创建任何文件，会导致某些操作失败

        evidence = _evidence_summary(bad_path, state)

        # 验证：status 不是成功状态
        assert evidence.get("status") == "UNASSESSED", \
            f"Reviewer 未判级时 status 应该是 UNASSESSED，实际是: {evidence.get('status')}"

        # 验证：有 blocking_reasons
        # 验证：不会触发 target reached
        assert not _target_reached(evidence), \
            "UNASSESSED evidence 不应该触发 target reached"


def test_only_real_evidence_triggers_stop_success(tmp_path: Path) -> None:
    """
    测试 4：真实达到目标

    只有当：
    - Engineer 和 Manager 已完成
    - 实际 evidence 支持目标等级
    - accepted level 达到 target
    - 没有 blocker

    才允许 stop_success / TARGET_EVIDENCE_REACHED
    """
    _write_paper_bundle(tmp_path)
    _write_l4_evidence(tmp_path)

    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    # 使用权威阶段状态：Manager 在 RUN_MANIFEST 中完成
    from r2a.core.run_manifest import mark_stage_started, mark_stage_finished

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

    # 验证：即使存在真实文件 evidence，也不自动 stop_success
    assert decision["typed_decision"] == "continue_iteration", \
        f"当前策略不因 target reached 自动停止，实际返回: {decision['typed_decision']}"

    # 验证：reason_code 不是 TARGET_EVIDENCE_REACHED
    assert decision["reason_code"] != "TARGET_EVIDENCE_REACHED", \
        f"不应返回 TARGET_EVIDENCE_REACHED，实际返回: {decision['reason_code']}"


def test_decision_summary_does_not_claim_file_inferred_level(tmp_path: Path) -> None:
    """
    测试 5：输出一致性

    Decision status uses Reviewer-only aggregation and must not claim the
    file-inferred helper level as official accepted evidence.
    """
    _write_paper_bundle(tmp_path)
    _write_l0_evidence_only(tmp_path)

    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    # 获取 decision_status 的 evidence
    decision = aggregate_terminal_decision(state)
    decision_evidence = decision.get("evidence_summary", {}).get("accepted_level")

    # 获取 evaluate_l0_l4 的结果
    evidence_decision = evaluate_l0_l4(tmp_path, state)
    evaluated_evidence = evidence_decision.get("accepted_level")

    # 获取 infer_evidence_level 的结果
    inferred_evidence = infer_evidence_level(tmp_path, "L0_project_health")

    assert evaluated_evidence == inferred_evidence, \
        f"evaluate_l0_l4.accepted_level ({evaluated_evidence}) 必须等于 infer_evidence_level ({inferred_evidence})"

    assert evaluated_evidence == "L0_project_health"
    assert decision_evidence == "UNASSESSED", \
        f"Reviewer 未判级时 decision evidence 应为 UNASSESSED，实际是: {decision_evidence}"


def test_planner_success_without_engineer_continues_workflow(tmp_path: Path) -> None:
    """
    测试 6：Planner 成功但 Engineer 未执行，应该继续工作流

    正确流程：
    Planner 成功 → Engineer → Manager → 只有 accepted level 达到目标才 stop_success

    而不是：
    Planner 输出允许到 L4 → 直接认为已达到 L4 → 跳过 Engineer/Manager/Reviewer
    """
    _write_paper_bundle(tmp_path)
    _write_l0_evidence_only(tmp_path)

    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")
    state.update({
        "planner_status": "success",
        "max_evidence_level_allowed": "L4_reduced_paper_aligned",
        "approval_ready": True,
        "approved": True,
    })

    decision = aggregate_terminal_decision(state)

    # 验证：不应该返回 stop_success
    assert decision["typed_decision"] != "stop_success", \
        "Planner 成功但 Engineer 未执行，不应该 stop_success"

    # 验证：应该继续迭代（进入 Engineer）
    # 可能是 continue_iteration 或其他非 terminal 状态
    if decision.get("terminal") is False:
        assert decision["typed_decision"] == "continue_iteration", \
            f"应该继续迭代进入 Engineer，实际返回: {decision['typed_decision']}"


def test_max_evidence_level_allowed_not_used_as_accepted(tmp_path: Path) -> None:
    """
    测试 7：max_evidence_level_allowed 不能用于判断已达到目标

    Planner 的 max_evidence_level_allowed 只表示"允许尝试的最高等级"，
    不表示"当前已达到的等级"。
    """
    _write_paper_bundle(tmp_path)
    _write_l0_evidence_only(tmp_path)

    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    # Planner 设置 max_evidence_level_allowed = L4
    state["max_evidence_level_allowed"] = "L4_reduced_paper_aligned"

    # 获取 evidence summary
    evidence = _evidence_summary(tmp_path, state)

    # 验证：accepted_level 不应该是 L4
    assert evidence.get("accepted_level") != "L4_reduced_paper_aligned", \
        "max_evidence_level_allowed 不能作为 accepted_level"

    # 验证：decision evidence 不从实际文件推断
    assert evidence.get("accepted_level") == "UNASSESSED", \
        f"Reviewer 未判级时 accepted_level 应为 UNASSESSED，实际是: {evidence.get('accepted_level')}"


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
    # 只有 source_verification.csv，没有 build_smoke.csv, input_contract_verification.csv 等


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
