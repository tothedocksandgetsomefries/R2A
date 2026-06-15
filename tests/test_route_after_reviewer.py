from __future__ import annotations

import json
from pathlib import Path

from r2a.core.paths import report_path
from r2a.core.state import make_initial_state
from r2a.tools.workflow_decision import PAPER_STRUCTURED_KEYS
from r2a.workflow.nodes import final_node
from r2a.workflow.router import route_after_reviewer


def _state(
    tmp_path: Path,
    verdict: str,
    *,
    target: str = "L4_reduced_paper_aligned",
    auto_iterate: bool = True,
    iteration: int = 1,
    max_iterations: int = 2,
    **kwargs,
) -> dict:
    _write_paper_bundle(tmp_path)
    paper = tmp_path / "paper.txt"
    paper.write_text("paper", encoding="utf-8")
    state = make_initial_state(
        tmp_path,
        paper_path=paper,
        auto_iterate=auto_iterate,
        max_iterations=max_iterations,
        target_reproduction_level=target,
        **kwargs,
    )
    state["reviewer_verdict"] = verdict
    state["iteration"] = iteration
    return state


def test_needs_fix_continues_when_auto_iteration_has_budget(tmp_path: Path) -> None:
    assert route_after_reviewer(_state(tmp_path, "NEEDS_FIX")) == "prepare_next_iteration"


def test_needs_fix_stops_when_auto_iteration_disabled(tmp_path: Path) -> None:
    assert route_after_reviewer(_state(tmp_path, "NEEDS_FIX", auto_iterate=False)) == "final"


def test_needs_fix_stops_at_max_iterations(tmp_path: Path) -> None:
    assert route_after_reviewer(_state(tmp_path, "NEEDS_FIX", iteration=2, max_iterations=2)) == "final"


def test_stopped_state_routes_final(tmp_path: Path) -> None:
    state = _state(tmp_path, "NEEDS_FIX")
    state["stopped"] = True

    assert route_after_reviewer(state) == "final"


def test_terminal_verdicts_route_final(tmp_path: Path) -> None:
    for verdict in ("PASS", "REJECT", "NEEDS_INPUT", "MANAGER_CLASSIFICATION_CONFLICT"):
        assert route_after_reviewer(_state(tmp_path, verdict)) == "prepare_next_iteration"


def test_reviewer_transaction_failure_routes_final_even_with_auto_iteration(tmp_path: Path) -> None:
    state = _state(tmp_path, "NEEDS_FIX", auto_iterate=True, max_iterations=4)
    state["stopped"] = True
    state["loop_status"] = "reviewer_transaction_failed"
    state["stop_reason"] = "REVIEWER_SAFETY_VALIDATION_FAILED"
    state["reviewer_transaction"] = {
        "validation_status": "FAIL",
        "failure_category": "REVIEWER_SAFETY_VALIDATION_FAILED",
    }

    assert route_after_reviewer(state) == "final"


def test_progress_verdict_continues_until_target_level_is_reached(tmp_path: Path) -> None:
    assert route_after_reviewer(_state(tmp_path, "PASS_SMOKE_ONLY", target="L2_input_contract_ready")) == "prepare_next_iteration"
    assert (
        route_after_reviewer(
            _state(
                tmp_path,
                "INPUT_CONTRACT_READY",
                target="L4_reduced_paper_aligned",
                allow_official_dataset_download=True,
                download_budget_gb=1,
            )
        )
        == "prepare_next_iteration"
    )
    assert route_after_reviewer(_state(tmp_path, "PASS_REDUCED_METHOD_ONLY", target="L4_reduced_paper_aligned")) == "prepare_next_iteration"
    assert route_after_reviewer(_state(tmp_path, "PASS_REDUCED_ALIGNED", target="L5_minimal_baseline_comparison")) == "prepare_next_iteration"


def test_progress_verdict_continues_even_when_target_level_is_reached(tmp_path: Path) -> None:
    _write_l4_evidence(tmp_path)
    assert route_after_reviewer(_state(tmp_path, "PASS_SMOKE_ONLY", target="L1_source_artifact_verified")) == "prepare_next_iteration"
    assert route_after_reviewer(_state(tmp_path, "PASS_REDUCED_METHOD_ONLY", target="L3_official_reduced_run")) == "prepare_next_iteration"
    assert route_after_reviewer(_state(tmp_path, "PASS_REDUCED_ALIGNED", target="L4_reduced_paper_aligned")) == "prepare_next_iteration"


def test_input_contract_ready_stops_without_official_input_authorization(tmp_path: Path) -> None:
    state = _state(
        tmp_path,
        "INPUT_CONTRACT_READY",
        target="L4_reduced_paper_aligned",
        allow_official_dataset_download=False,
        download_budget_gb=20,
    )

    _write_missing_dataset_decision(tmp_path)
    assert route_after_reviewer(state) == "final"


def test_review_feedback_can_veto_iteration(tmp_path: Path) -> None:
    state = _state(tmp_path, "NEEDS_FIX")
    state["structured_review_feedback"] = {"verdict": "NEEDS_FIX", "should_iterate": False}

    assert route_after_reviewer(state) == "prepare_next_iteration"


def test_decision_aggregator_request_user_input_vetoes_iteration(tmp_path: Path) -> None:
    state = _state(tmp_path, "NEEDS_FIX")
    _write_missing_dataset_decision(tmp_path)

    assert route_after_reviewer(state) == "final"


def test_review_feedback_dataset_guidance_continues_to_next_iteration(tmp_path: Path) -> None:
    state = _state(
        tmp_path,
        "NEEDS_FIX",
        auto_iterate=True,
        iteration=1,
        max_iterations=8,
        allow_official_dataset_download=True,
        download_budget_gb=1,
    )
    state["reviewer_executed"] = True
    state["structured_review_feedback"] = {
        "verdict": "NEEDS_FIX",
        "required_fixes": [
            "Prepare the official dataset subset, query files, and ground truth in the next iteration.",
        ],
    }

    assert route_after_reviewer(state) == "prepare_next_iteration"
    assert state["decision_status"]["typed_decision"] == "continue_iteration"
    assert state["decision_status"]["requires_user_input"] is False


def test_final_node_preserves_router_continue_iteration_decision(tmp_path: Path) -> None:
    state = _state(tmp_path, "NEEDS_FIX", auto_iterate=True, iteration=1, max_iterations=8)
    state["reviewer_executed"] = True
    assert route_after_reviewer(state) == "prepare_next_iteration"

    result = final_node(state)

    assert result["decision_status"]["typed_decision"] == "continue_iteration"


def test_review_feedback_file_is_used_when_state_verdict_is_missing(tmp_path: Path) -> None:
    r2a = tmp_path / ".r2a"
    r2a.mkdir()
    feedback = {
        "verdict": "PASS_REDUCED_METHOD_ONLY",
        "should_iterate": True,
        "next_level": "L4_reduced_paper_aligned",
        "recommended_task_scope": ["write paper_alignment.csv"],
    }
    path = report_path(tmp_path, "review_feedback")
    path.write_text(json.dumps(feedback), encoding="utf-8")
    state = _state(tmp_path, "", target="L4_reduced_paper_aligned")
    state["latest_review_feedback_path"] = str(path)

    assert route_after_reviewer(state) == "prepare_next_iteration"


def test_official_input_progress_requires_budget_or_approval(tmp_path: Path) -> None:
    blocked = _state(
        tmp_path,
        "NEEDS_OFFICIAL_INPUT",
        target="L3_official_reduced_run",
        allow_official_dataset_download=False,
        download_budget_gb=0,
    )
    allowed = _state(
        tmp_path,
        "NEEDS_OFFICIAL_INPUT",
        target="L3_official_reduced_run",
        allow_official_dataset_download=True,
        download_budget_gb=1,
    )

    _write_missing_dataset_decision(tmp_path)
    assert route_after_reviewer(blocked) == "final"
    assert route_after_reviewer(allowed) == "prepare_next_iteration"


def _write_paper_bundle(repo: Path) -> None:
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
    for key in PAPER_STRUCTURED_KEYS:
        path = report_path(repo, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = '{"ok": true}' if key == "paper_output" else f"# {key}\n\nok\n"
        path.write_text(body, encoding="utf-8")


def _write_missing_dataset_decision(repo: Path) -> None:
    report_path(repo, "manager_decision").write_text(
        json.dumps(
            {
                "status": "FAIL",
                "blockers": [
                    {
                        "blocker_id": "missing_dataset:official",
                        "type": "missing_dataset",
                        "reason_code": "OFFICIAL_DATASET_NOT_AVAILABLE",
                        "requires_user_input": True,
                        "last_message": "Official dataset is missing.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_l4_evidence(repo: Path) -> None:
    results = repo / ".r2a" / "results"
    logs = repo / ".r2a" / "logs"
    results.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    (results / "input_contract_verification.csv").write_text(
        "component,status,path_or_command,evidence_source,notes\n"
        "dataset,FOUND,official_small,README,official dataset documented\n"
        "query,FOUND,queries.tsv,README,official query documented\n"
        "ground_truth,FOUND,gt.tsv,README,official ground truth documented\n"
        "metric_definition,READY,recall@10,paper,metric documented\n",
        encoding="utf-8",
    )
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
