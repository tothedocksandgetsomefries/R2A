from __future__ import annotations

import json
from pathlib import Path

from r2a.core.paths import report_path
from r2a.core.state import make_initial_state
from r2a.tools.workflow_decision import PAPER_STRUCTURED_KEYS, aggregate_terminal_decision, normalize_blocker
from r2a.workflow.router import route_after_paper, route_after_planner, route_after_reviewer


def test_manager_fail_with_missing_dataset_is_not_terminal_failed(tmp_path: Path) -> None:
    _write_paper_bundle(tmp_path)
    _write_manager_decision(
        tmp_path,
        {
            "blocker_id": "missing_dataset:official",
            "type": "missing_dataset",
            "reason_code": "OFFICIAL_DATASET_NOT_AVAILABLE",
            "requires_user_input": True,
            "required_inputs": ["official_dataset_or_subset", "query_files", "ground_truth"],
            "last_message": "Official dataset is missing.",
        },
    )
    state = _state_with_paper(tmp_path, auto_iterate=True, max_iterations=3)
    state.update(
        {
            "manager_executed": True,
            "manager_status": "FAIL",
            "manager_max_level_allowed": "L2_input_contract_ready",
            "reproduction_level": "L2_input_contract_ready",
        }
    )

    decision = aggregate_terminal_decision(state)

    assert decision["typed_decision"] == "request_dataset"
    assert decision["typed_decision"] != "terminal_failed"


def test_explicit_user_input_type_is_not_overridden_by_input_contract_id() -> None:
    blocker = {
        "id": "missing_official_input_contract",
        "type": "user_input_required",
        "required_inputs": ["official_dataset_or_subset", "query_files", "ground_truth"],
    }

    normalized = normalize_blocker(blocker)

    assert normalized["type"] == "user_input_required"
    assert normalized["type"] != "missing_input_contract"


def test_reviewer_needs_fix_does_not_override_request_dataset_route(tmp_path: Path) -> None:
    _write_paper_bundle(tmp_path)
    _write_manager_decision(
        tmp_path,
        {
            "blocker_id": "missing_dataset:official",
            "type": "missing_dataset",
            "reason_code": "OFFICIAL_DATASET_NOT_AVAILABLE",
            "requires_user_input": True,
            "last_message": "Official dataset is missing.",
        },
    )
    state = _state_with_paper(tmp_path, auto_iterate=True, max_iterations=4)
    state.update({"reviewer_executed": True, "reviewer_verdict": "NEEDS_FIX", "manager_executed": True})

    assert route_after_reviewer(state) == "final"
    assert state["decision_status"]["typed_decision"] == "request_dataset"


def test_authorized_missing_dataset_decision_defers_to_auto_iteration(tmp_path: Path) -> None:
    _write_paper_bundle(tmp_path)
    _write_manager_decision(
        tmp_path,
        {
            "blocker_id": "missing_dataset:official",
            "type": "missing_dataset",
            "reason_code": "OFFICIAL_DATASET_NOT_AVAILABLE",
            "requires_user_input": True,
            "required_inputs": ["official_dataset_or_subset", "query_files", "ground_truth"],
            "last_message": "Official dataset still needs to be prepared by the next iteration.",
        },
    )
    state = _state_with_paper(
        tmp_path,
        auto_iterate=True,
        max_iterations=8,
        allow_official_dataset_download=True,
        download_budget_gb=1,
    )
    state.update({"reviewer_executed": True, "reviewer_verdict": "NEEDS_FIX", "manager_executed": True})

    decision = aggregate_terminal_decision(state)

    assert decision["typed_decision"] == "continue_iteration"
    assert decision["requires_user_input"] is False
    assert route_after_reviewer(state) == "prepare_next_iteration"


def test_missing_paper_routes_to_final_and_requests_paper(tmp_path: Path) -> None:
    state = make_initial_state(tmp_path, auto_iterate=True)

    decision = aggregate_terminal_decision(state)

    assert decision["typed_decision"] == "request_paper"
    assert decision["terminal"] is True
    assert decision["requires_user_input"] is True
    assert route_after_paper(state) == "final"


def test_planner_backend_failure_has_retry_limit(tmp_path: Path) -> None:
    _write_paper_bundle(tmp_path)
    state = _state_with_paper(tmp_path, auto_iterate=True, max_iterations=4)
    state["planner_transaction"] = _planner_backend_failure_transaction()

    first = aggregate_terminal_decision({**state, "iteration": 1})
    second = aggregate_terminal_decision({**state, "iteration": 2})

    assert first["typed_decision"] == "retry_backend"
    assert second["typed_decision"] == "terminal_failed"
    assert second["reason_code"] == "BACKEND_RETRY_LIMIT_EXCEEDED"


def test_repeated_missing_source_converges_to_request_source(tmp_path: Path) -> None:
    _write_paper_bundle(tmp_path)
    _write_manager_decision(
        tmp_path,
        {
            "blocker_id": "missing_source:official",
            "type": "missing_source",
            "reason_code": "OFFICIAL_SOURCE_NOT_AVAILABLE",
            "requires_user_input": True,
            "last_message": "Official source is missing.",
        },
    )
    state = _state_with_paper(tmp_path, auto_iterate=True, max_iterations=5)
    state["manager_executed"] = True

    aggregate_terminal_decision({**state, "iteration": 1})
    aggregate_terminal_decision({**state, "iteration": 2})
    third = aggregate_terminal_decision({**state, "iteration": 3})

    assert third["typed_decision"] == "request_source"
    assert third["active_blockers"][0]["count"] == 3
    assert route_after_reviewer({**state, "iteration": 3, "reviewer_executed": True}) == "final"


def test_needs_network_authorization_becomes_terminal_user_input_decision(tmp_path: Path) -> None:
    _write_paper_bundle(tmp_path)
    _write_network_authorization_required(tmp_path)
    state = _state_with_paper(tmp_path, auto_iterate=True, max_iterations=4)
    state.update({"engineer_status": "FAIL", "reviewer_executed": True, "approval_ready": True})

    decision = aggregate_terminal_decision(state)

    assert decision["typed_decision"] == "request_network_authorization"
    assert decision["terminal"] is True
    assert decision["requires_user_input"] is True
    assert decision["retryable"] is False
    assert "network_authorization" in decision["required_inputs"]
    assert any("algorithm_dependency_network_authorization" in item for item in decision["active_blocker_ids"])
    assert route_after_reviewer(state) == "final"
    assert route_after_planner(state) == "final"


def test_needs_network_authorization_is_ignored_after_explicit_authorization(tmp_path: Path) -> None:
    _write_paper_bundle(tmp_path)
    _write_network_authorization_required(tmp_path)
    state = _state_with_paper(tmp_path, auto_iterate=True, max_iterations=4)
    state.update(
        {
            "engineer_status": "FAIL",
            "reviewer_executed": True,
            "network_authorized": True,
            "allow_network": True,
            "allowed_network_scope": ["external_git_clone_for_algorithm_dependencies"],
        }
    )

    decision = aggregate_terminal_decision(state)

    assert decision["typed_decision"] != "request_network_authorization"
    assert not any("algorithm_dependency_network_authorization" in item for item in decision["active_blocker_ids"])


def test_target_evidence_level_reached_stops_success(tmp_path: Path) -> None:
    """测试目标达到时的行为。

    简化后：达到目标不自动停止。
    默认情况下，只要还有迭代次数并且 auto_iterate=True，允许继续完善。
    """
    _write_paper_bundle(tmp_path)
    _write_l4_evidence(tmp_path)
    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    # 使用权威阶段状态：Manager 在 RUN_MANIFEST 中完成
    from r2a.core.run_manifest import mark_stage_started, mark_stage_finished
    state = mark_stage_started(state, "manager")
    state = mark_stage_finished(state, "manager", status="PASS")

    # 创建必要的报告文件
    from r2a.core.paths import report_path
    report_path(tmp_path, "execution").parent.mkdir(parents=True, exist_ok=True)
    report_path(tmp_path, "execution").write_text("# Execution Report\n\nPASS", encoding="utf-8")
    report_path(tmp_path, "check").write_text("# Check Report\n\nPASS", encoding="utf-8")

    # 设置 Reviewer 已执行和等级
    state["reviewer_executed"] = True
    state["current_reproduction_level"] = "L4_reduced_paper_aligned"
    state["current_level_iteration"] = 1
    state["reviewer_verdict"] = "PASS_L4"
    state["auto_iterate"] = True
    state["max_iterations"] = 12
    state["iteration"] = 1

    decision = aggregate_terminal_decision(state)

    # 简化后：达到目标但仍有迭代次数时，继续迭代
    assert decision["typed_decision"] == "continue_iteration"
    assert decision["terminal"] is False


def test_max_iterations_stops_instead_of_continuing(tmp_path: Path) -> None:
    _write_paper_bundle(tmp_path)
    _write_l2_evidence(tmp_path)
    state = _state_with_paper(tmp_path, auto_iterate=True, max_iterations=1)
    state.update(
        {
            "iteration": 1,
            "manager_executed": True,
            "reviewer_executed": True,
            "reproduction_level": "L2_input_contract_ready",
            "target_reproduction_level": "L4_reduced_paper_aligned",
        }
    )

    decision = aggregate_terminal_decision(state)

    # Normal termination: use "final" instead of "stop_evidence_cap"
    assert decision["typed_decision"] == "final"
    assert decision["reason_code"] == "MAX_ITERATIONS_REACHED"
    assert route_after_reviewer(state) == "final"


def _state_with_paper(tmp_path: Path, **kwargs) -> dict:
    paper = tmp_path / "paper.txt"
    paper.write_text("paper text", encoding="utf-8")
    return make_initial_state(tmp_path, paper_path=paper, **kwargs)


def _write_paper_bundle(repo: Path) -> None:
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
    (results / "build_smoke.csv").write_text(
        "status,command,exit_code,duration_sec,component,notes\n"
        "PASS,python main.py,0,0.1,main,smoke passed\n",
        encoding="utf-8",
    )


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


def _write_network_authorization_required(repo: Path) -> None:
    results = repo / ".r2a" / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / "input_contract_verification.csv").write_text(
        "component,status,path_or_command,evidence_source,notes\n"
        "fanns_algorithm_binaries,NEEDS_NETWORK_AUTHORIZATION,git clone + cmake,ENGINEER_NOTES,"
        "FANNS algorithm binaries require network authorization for external git clone + local CMake build\n",
        encoding="utf-8",
    )


def _write_manager_decision(repo: Path, blocker: dict) -> None:
    path = report_path(repo, "manager_decision")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"status": "FAIL", "blockers": [blocker]}, indent=2), encoding="utf-8")


def _planner_backend_failure_transaction() -> dict:
    return {
        "stage": "planner",
        "committed": False,
        "validation_status": "FAIL",
        "failure_category": "PLANNER_BACKEND_FAILURE",
        "execution_status": "PLANNER_BACKEND_FAILURE",
        "diagnostic": {"failure_category": "PLANNER_BACKEND_FAILURE"},
    }
