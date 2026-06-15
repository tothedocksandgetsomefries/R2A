import json
from pathlib import Path

from r2a.core.state import make_initial_state
from r2a.core.run_manifest import mark_stage_finished, mark_stage_started
from r2a.agents.planner_agent import run_planner_agent
from r2a.cli import _cli_final_summary
from r2a.core.paths import report_path
from r2a.tools.workflow_decision import PAPER_STRUCTURED_KEYS
from r2a.workflow.graph import build_workflow_graph
from r2a.workflow.nodes import final_node, human_approval_node


def test_final_report_is_generated_with_iteration_summary(tmp_path: Path) -> None:
    graph = build_workflow_graph()
    state = _state_with_paper(tmp_path, goal="add HNSW oversampling baseline", executor="shell", auto_approve=True)

    result = graph.invoke(state)

    final_report = Path(result["final_report_path"])
    text = final_report.read_text(encoding="utf-8")
    assert "# FINAL_REPORT" in text
    assert "## Run Summary" in text
    assert "## Evidence Ladder" in text
    assert "## Total Iterations" in text
    assert "## Stop Reason" in text
    assert "## Final Verdict" in text
    manifest = tmp_path / ".r2a" / "latest" / "RUN_MANIFEST.json"
    assert manifest.exists()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["run_id"] == result["run_id"]
    assert data["target_level"].startswith("L4")
    assert data["accepted_level"] == "UNASSESSED"
    assert data["stage_models"]["final_writer"]["backend"] == "template"
    assert "paper" in data["stages"]
    assert "final" in data["stages"]
    assert data["openclaw"]["provider"] == "ai-coding-plan"
    assert data["openclaw"]["stage_profiles"]["engineer"]["provider"] == "deepseek"
    assert data["openclaw"]["model"] == "glm-5"
    assert data["openclaw"]["stage_profiles"]["engineer"]["model"] == "deepseek-chat"
    assert (tmp_path / ".r2a" / "latest" / "FINAL_REPORT.md").exists()
    assert (tmp_path / ".r2a" / "runs" / "iter_001" / "FINAL_REPORT.md").exists()


def test_final_report_includes_source_input_and_reduced_experiment_summaries(tmp_path: Path) -> None:
    _write_l3_evidence(tmp_path)
    report_path(tmp_path, "source_acquisition").write_text(
        json.dumps(
            {
                "source_status": "available",
                "source_type": "official_implementation_repo",
                "selected_source": {"url": "https://github.com/example/paper-code"},
                "candidates": [
                    {
                        "url": "https://github.com/example/paper-code",
                        "candidate_type": "official_implementation_repo",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    state = _state_with_paper(tmp_path)

    result = final_node(state)

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "## Inputs And Source" in text
    assert "source_status: available" in text
    assert "candidate_types: official_implementation_repo: 1" in text
    assert "input_contract_rows: 7" in text
    assert "reduced_metrics_rows: 1" in text
    assert "command_manifest.csv present: yes" in text


def test_final_writer_disabled_uses_template_and_records_metadata(tmp_path: Path) -> None:
    _write_check(tmp_path, "PASS")
    state = _state_with_paper(tmp_path)

    result = final_node({**state, "reviewer_verdict": "PASS_WITH_LIMITATIONS", "manager_status": "PASS"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    metadata = json.loads(report_path(tmp_path, "final_writer_metadata").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / ".r2a" / "latest" / "RUN_MANIFEST.json").read_text(encoding="utf-8"))
    assert metadata["backend"] == "template"
    assert metadata["model"] == "none"
    assert manifest["stage_models"]["final_writer"]["backend"] == "template"
    assert "Final Writer backend: template" in text
    assert "Final Writer did not alter formal decision: yes" in text


def test_final_writer_openclaw_failure_falls_back_to_template(tmp_path: Path, monkeypatch) -> None:
    def fake_fail(*args, **kwargs):
        return {
            "success": False,
            "failure_category": "FINAL_WRITER_BACKEND_FAILURE",
            "error": "writer unavailable",
        }

    monkeypatch.setattr("r2a.tools.final_writer.openclaw_stage_runner.run_openclaw_stage", fake_fail)
    _write_check(tmp_path, "PASS")
    state = _state_with_paper(tmp_path, final_writer_backend="openclaw")

    result = final_node({**state, "reviewer_verdict": "PASS_WITH_LIMITATIONS", "manager_status": "PASS"})

    metadata = json.loads(report_path(tmp_path, "final_writer_metadata").read_text(encoding="utf-8"))
    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert metadata["requested_backend"] == "openclaw"
    assert metadata["backend"] == "template"
    assert metadata["model"] == "none"
    assert "writer unavailable" in metadata["fallback_reason"]
    assert "Final Writer backend: template" in text


def test_final_writer_openclaw_success_records_stage_model_and_preserves_decision(tmp_path: Path, monkeypatch) -> None:
    def fake_success(repo_path, stage, input_path, allowed_outputs, **kwargs):
        assert stage == "final_writer"
        assert ".r2a/FINAL_NARRATIVE_CN.md" in allowed_outputs
        narrative_path = Path(repo_path) / ".r2a" / "FINAL_NARRATIVE_CN.md"
        narrative_path.write_text("# FINAL_NARRATIVE_CN\n\n模型生成的中文叙述。\n", encoding="utf-8")
        return {
            "success": True,
            "provider": kwargs.get("provider"),
            "model": kwargs.get("model"),
            "runner": kwargs.get("runner"),
            "configured_provider": kwargs.get("provider"),
            "configured_model": kwargs.get("model"),
            "configured_runner": kwargs.get("runner"),
            "invocation_id": "final-writer-test",
            "invocation_manifest_path": str(Path(repo_path) / ".r2a" / "logs" / "invocation.json"),
            "token_usage": {"total_tokens": 10},
        }

    monkeypatch.setattr("r2a.tools.final_writer.openclaw_stage_runner.run_openclaw_stage", fake_success)
    _write_check(tmp_path, "PASS")
    state = _state_with_paper(
        tmp_path,
        final_writer_backend="openclaw",
        stage_model_selection={
            "final_writer": {
                "backend": "openclaw",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "profile": "report-writer",
            }
        },
    )

    result = final_node({**state, "reviewer_verdict": "PASS_WITH_LIMITATIONS", "manager_status": "PASS"})

    final_decision = json.loads(report_path(tmp_path, "final_decision").read_text(encoding="utf-8"))
    metadata = json.loads(report_path(tmp_path, "final_writer_metadata").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / ".r2a" / "latest" / "RUN_MANIFEST.json").read_text(encoding="utf-8"))
    assert final_decision["schema_version"] == 1
    assert metadata["did_alter_formal_decision"] is False
    assert metadata["backend"] == "openclaw"
    assert metadata["provider"] == "deepseek"
    assert metadata["model"] == "deepseek-chat"
    assert manifest["stage_models"]["final_writer"]["backend"] == "openclaw"
    assert manifest["stage_models"]["final_writer"]["model"] == "deepseek-chat"
    assert "模型生成的中文叙述" in Path(result["final_report_path"]).read_text(encoding="utf-8")


def test_final_node_reports_demo_only_without_claiming_reproduction(tmp_path: Path) -> None:
    # Reviewer has been removed from the default workflow.
    # This test now verifies that Final correctly handles the case where
    # manager_passed is False and auto_iterate is disabled.
    state = _state_with_paper(tmp_path)
    result = final_node(state)

    assert result["decision_status"]["typed_decision"] == "continue_iteration"
    assert result["stop_reason"] == "READY_FOR_NEXT_STAGE"
    # Final report should be generated
    assert result["final_report_path"]


def test_final_node_reports_official_input_blocker(tmp_path: Path) -> None:
    # Reviewer has been removed from the default workflow.
    # This test now verifies that Final correctly handles NEEDS_INPUT evidence.
    # When evidence level is L2_input_contract_ready with missing inputs,
    # Final should report the current state without claiming completion.
    _write_l3_evidence(tmp_path)
    state = _state_with_paper(tmp_path)
    result = final_node(state)

    assert result["decision_status"]["typed_decision"] == "continue_iteration"
    assert result["stop_reason"] == "READY_FOR_NEXT_STAGE"
    assert result["final_report_path"]


def test_final_report_pass_reduced_aligned_warning_is_limited_pass(tmp_path: Path) -> None:
    _write_l4_evidence(tmp_path)
    _write_check(tmp_path, "WARNING", warnings="- provenance cleanup warning\n")
    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    # Reviewer removed: pass manager_status instead of reviewer_verdict
    result = final_node({**state, "manager_status": "WARNING"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "## Executive Summary" in text
    assert "Current Level:" in text
    assert "Target Level:" in text
    assert "Final Verdict:" in text
    assert "Result Type:" in text
    assert "Full Reproduction Claim:" in text
    assert "## What Was Actually Done" in text
    assert "## Provenance" in text
    # L4 evidence should be reported
    assert "L4" in text
    # With WARNING, should indicate limitations
    assert "PASS_WITH_LIMITATIONS" in text or "limitation" in text.lower() or "warning" in text.lower()
    assert "Full Reproduction Claim: No." in text
    cli_summary = _cli_final_summary(result)
    assert "Executive Summary" in cli_summary
    assert "- Current:" in cli_summary
    assert "- Target:" in cli_summary
    assert "- Result Type:" in cli_summary
    assert "- Full Reproduction Claim:" in cli_summary
    assert "- Next Action:" in cli_summary
    assert "L4" in cli_summary


def test_final_report_check_fail_still_needs_fix(tmp_path: Path) -> None:
    _write_l4_evidence(tmp_path)
    _write_check(tmp_path, "FAIL", errors="- schema error\n")
    state = _state_with_paper(tmp_path)

    result = final_node({**state, "reviewer_verdict": "PASS_REDUCED_ALIGNED", "manager_status": "FAIL"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert result["decision_status"]["typed_decision"] == "final"
    assert "Manager reported FAIL" in text
    assert "accepted_level: UNASSESSED" in text


def test_final_report_distinguishes_observed_from_accepted_level_on_manager_fail(tmp_path: Path) -> None:
    _write_l4_evidence(tmp_path)
    _write_check(tmp_path, "FAIL", errors="- schema error\n")
    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    result = final_node(
        {
            **state,
            "reviewer_verdict": "PASS_REDUCED_ALIGNED",
            "manager_status": "FAIL",
            "manager_max_level_allowed": "L2_input_contract_ready",
        }
    )

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "observed_level: L4_reduced_paper_aligned" in text
    assert "accepted_level: UNASSESSED" in text
    assert "Observed Evidence Level: L4: Reduced paper-aligned evidence (L4_reduced_paper_aligned)" in text
    assert "Accepted Level After Quality Gates: UNASSESSED (UNASSESSED)" in text
    assert "Observed candidate evidence reaches L4_reduced_paper_aligned, but it is not formally accepted." in text


def test_planner_missing_output_stops_before_engineer_and_preserves_failure(tmp_path: Path) -> None:
    logs = tmp_path / ".r2a" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    transaction = {
        "stage": "planner",
        "committed": False,
        "validation_status": "FAIL",
        "failure_category": "PLANNER_MISSING_REQUIRED_OUTPUT",
        "execution_status": "PLANNER_MISSING_REQUIRED_OUTPUT",
        "diagnostic": {
            "planner_validation_passed": False,
            "planner_committed": False,
            "approval_passed": False,
            "failure_category": "PLANNER_MISSING_REQUIRED_OUTPUT",
        },
    }
    (logs / "planner_transaction.json").write_text(json.dumps(transaction), encoding="utf-8")
    state = _state_with_paper(tmp_path, auto_approve=True)
    state = mark_stage_started(state, "planner")
    state = mark_stage_finished(
        {**state, "stopped": True, "loop_status": "planner_failed", "planner_transaction": transaction},
        "planner",
        status="FAIL",
        errors=["Planner missing required outputs."],
    )

    result = final_node(state)

    assert result["decision_status"]["typed_decision"] == "terminal_failed"
    assert result["stop_reason"] == "PLANNER_MISSING_REQUIRED_OUTPUT"
    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "PLANNER_MISSING_REQUIRED_OUTPUT" in text
    assert "EXECUTION_REPORT.md:" not in text
    assert "Engineer stage: SKIPPED" in text
    manifest = json.loads((tmp_path / ".r2a" / "latest" / "RUN_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["stages"]["planner"]["status"] == "FAIL"
    assert manifest["stages"]["engineer"]["status"] == "SKIPPED"
    assert manifest["stages"]["manager"]["status"] == "SKIPPED"


def test_approval_rejected_stops_before_engineer_without_planner_failure(tmp_path: Path) -> None:
    state = _state_with_paper(tmp_path, auto_approve=False)
    state = mark_stage_started(state, "planner")
    state = mark_stage_finished({**state, "approval_ready": True}, "planner", status="PASS")
    rejected = human_approval_node(state)

    result = final_node(rejected)

    assert result["decision_status"]["typed_decision"] == "request_approval"
    assert result["stop_reason"] == "APPROVAL_REQUIRED_OR_REJECTED"
    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "APPROVAL_REQUIRED_OR_REJECTED" in text
    assert "Engineer stage: SKIPPED" in text
    manifest = json.loads((tmp_path / ".r2a" / "latest" / "RUN_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["stages"]["approval"]["status"] == "FAIL"
    assert manifest["stages"]["engineer"]["status"] == "SKIPPED"


def test_final_report_reviewer_reject_blocks_success_even_when_manager_passes(tmp_path: Path) -> None:
    _write_check(tmp_path, "PASS")
    state = _state_with_paper(tmp_path)

    result = final_node({**state, "reviewer_verdict": "REJECT", "manager_status": "PASS", "manager_passed": True})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert result["loop_status"] == "completed"
    assert result["decision_status"]["typed_decision"] == "final"
    assert "## Final Verdict\n\nREJECT" in text
    assert "accepted_level: UNASSESSED" in text


def test_final_report_demo_only_never_displays_l3_or_l4(tmp_path: Path) -> None:
    # Reviewer removed: test that without L3/L4 evidence, Final doesn't claim them
    state = _state_with_paper(tmp_path)

    result = final_node(state)

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    # Without L3/L4 evidence, should not claim those levels
    assert "Current: L3: Official reduced run (L3_official_reduced_run)" not in text
    assert "Current: L4: Reduced paper-aligned evidence (L4_reduced_paper_aligned)" not in text


def test_final_report_reduced_metrics_without_alignment_displays_l3(tmp_path: Path) -> None:
    _write_l3_evidence(tmp_path)
    _write_check(tmp_path, "PASS")
    state = _state_with_paper(tmp_path)

    # Reviewer removed: test L3 evidence without L4
    result = final_node({**state, "manager_status": "PASS"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    # L3 evidence should be reported
    assert "L3" in text
    # L4 should not be claimed without paper_alignment.csv
    assert "Current: L4: Reduced paper-aligned evidence (L4_reduced_paper_aligned)" not in text


def test_final_report_l4_limited_evidence_with_needs_fix_gets_cleanup_status(tmp_path: Path) -> None:
    _write_l4_evidence(tmp_path)
    _write_check(tmp_path, "WARNING", warnings="- closure cleanup warning\n")
    state = _state_with_paper(tmp_path, auto_iterate=True, max_iterations=2)

    # Reviewer removed: test L4 with WARNING and max_iterations reached
    result = final_node({**state, "iteration": 2, "manager_status": "WARNING"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert result["decision_status"]["typed_decision"] == "final"
    assert result["stop_reason"] == "MAX_ITERATIONS_REACHED"
    # L4 evidence should be reported
    assert "L4" in text


def test_final_report_generates_l4_alignment_summary(tmp_path: Path) -> None:
    _write_l4_evidence(tmp_path)
    _write_check(tmp_path, "WARNING", warnings="- closure cleanup warning\n")
    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    result = final_node({**state, "reviewer_verdict": "PASS_REDUCED_ALIGNED", "manager_status": "WARNING"})

    summary_path = tmp_path / ".r2a" / "results" / "L4_ALIGNMENT_SUMMARY.md"
    final_text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    summary_text = summary_path.read_text(encoding="utf-8")
    assert summary_path.exists()
    assert "L4_ALIGNMENT_SUMMARY.md" in final_text
    assert "Claim: reduced paper-aligned evidence, not full reproduction" in summary_text
    assert "This is not full-paper reproduction." in summary_text


def test_final_report_displays_backend_retry_success(tmp_path: Path) -> None:
    _write_check(tmp_path, "PASS")
    _write_backend_attempt_logs(tmp_path, "planner", first_parse=True, second_returncode=0, second_freshness_ok=True)
    state = _state_with_paper(tmp_path)

    result = final_node({**state, "reviewer_verdict": "PASS_WITH_LIMITATIONS", "manager_status": "PASS"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "was recovered by retry" in text
    assert "not a paper reproduction failure" in text


def test_final_report_displays_backend_retry_failure(tmp_path: Path) -> None:
    _write_check(tmp_path, "PASS")
    _write_backend_attempt_logs(tmp_path, "planner", first_parse=True, second_returncode=1, second_freshness_ok=False, second_parse=True)
    state = _state_with_paper(tmp_path)

    result = final_node({**state, "reviewer_verdict": "NEEDS_FIX", "manager_status": "PASS"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "backend transient failure persisted after retry" in text
    assert "not evidence that the paper is unreproducible" in text


def test_final_report_displays_retry_freshness_failure(tmp_path: Path) -> None:
    _write_check(tmp_path, "PASS")
    _write_backend_attempt_logs(tmp_path, "planner", first_parse=True, second_returncode=0, second_freshness_ok=False)
    state = _state_with_paper(tmp_path)

    result = final_node({**state, "reviewer_verdict": "NEEDS_FIX", "manager_status": "PASS"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "output freshness validation failed" in text
    assert "partial outputs may exist from the failed attempt" in text


def test_final_report_verification_only_reduced_metrics_are_l2_limited(tmp_path: Path) -> None:
    _write_verification_only_reduced_evidence(tmp_path)
    _write_check(tmp_path, "WARNING", warnings="- Project has no formal tests; used build/import/runtime smoke instead.\n")
    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    # Reviewer removed: test verification_only contract caps at L2
    result = final_node({**state, "manager_status": "WARNING"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    # verification_only should cap at L2
    assert "L2" in text
    # Should not claim L3/L4
    assert "Current: L3: Official reduced run (L3_official_reduced_run)" not in text
    assert "Full Reproduction Claim: No." in text
    assert "SCHEMA_FIXED" not in text


def test_final_report_does_not_promote_fixed_status_rows_to_remaining_issues(tmp_path: Path) -> None:
    _write_check(tmp_path, "FAIL", errors="- current paper_alignment.match_status error\n")
    results = tmp_path / ".r2a" / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / "reproduction_status.csv").write_text(
        "status,reason,evidence_source,next_action\n"
        "FIXED,CSV_PARSE_ERROR resolved in previous iteration,CHECK_REPORT.md,none\n",
        encoding="utf-8",
    )
    state = _state_with_paper(tmp_path)

    result = final_node({**state, "reviewer_verdict": "NEEDS_FIX", "manager_status": "FAIL"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "current paper_alignment.match_status error" in text
    assert "CSV_PARSE_ERROR resolved in previous iteration" not in text


def test_final_report_caps_unlabeled_verification_only_reduced_metrics(tmp_path: Path) -> None:
    _write_verification_only_reduced_evidence(tmp_path)
    (tmp_path / ".r2a" / "results" / "reduced_metrics.csv").write_text(
        "command_id,dataset,method,k,ground_truth_source,metric_definition,input_provenance,recall,latency_ms,notes\n"
        "reduced-cmd,official_small,Curator,10,gt.tsv,recall@10,README official_small,0.91,12.5,unlabeled row\n",
        encoding="utf-8",
    )
    _write_check(tmp_path, "WARNING")
    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    result = final_node({**state, "reviewer_verdict": "PASS_SMOKE_ONLY", "manager_status": "WARNING"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "accepted_level: UNASSESSED" in text
    assert "reduced_metrics_rows: 1" in text
    assert "Current: L3: Official reduced run (L3_official_reduced_run)" not in text


def test_final_report_shows_contract_cap_without_reduced_metrics(tmp_path: Path) -> None:
    r2a = tmp_path / ".r2a"
    r2a.mkdir(parents=True, exist_ok=True)
    (r2a / "EXPERIMENT_CONTRACT.md").write_text(
        "# EXPERIMENT_CONTRACT\n\n## Contract Mode\n\nverification_only\n",
        encoding="utf-8",
    )
    (r2a / "TASK_SPEC.md").write_text("# TASK_SPEC\n\nMode: verification_only\n", encoding="utf-8")
    _write_check(tmp_path, "PASS")
    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    result = final_node({**state, "reviewer_verdict": "INPUT_CONTRACT_READY", "manager_status": "PASS"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "accepted_level: UNASSESSED" in text
    assert "verification_only" in text
    assert "Current: L3: Official reduced run (L3_official_reduced_run)" not in text


def test_final_report_shows_planner_approval_diagnostics(tmp_path: Path) -> None:
    logs = tmp_path / ".r2a" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "planner_transaction.json").write_text(
        json.dumps(
            {
                "stage": "planner",
                "backend": "claude",
                "committed": True,
                "validation_status": "PASS",
                "diagnostic": {
                    "planner_backend": "claude",
                    "prompt_file": str(logs / "claude_planner_prompt.md"),
                    "prompt_size": 1234,
                    "allowed_tools": "Read,Write,Edit,MultiEdit",
                    "staging_task_spec_written": True,
                    "staging_experiment_contract_written": True,
                    "planner_validation_passed": True,
                    "planner_committed": True,
                    "approval_passed": True,
                    "is_claude_ccr_call_problem": False,
                },
            }
        ),
        encoding="utf-8",
    )
    _write_check(tmp_path, "PASS")
    state = _state_with_paper(tmp_path)

    result = final_node({**state, "reviewer_verdict": "INPUT_CONTRACT_READY", "manager_status": "PASS"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "## Planner / Approval Diagnostics" in text
    assert "Planner backend: claude" in text
    assert "Prompt size: 1234" in text
    assert "Allowed tools: Read,Write,Edit,MultiEdit" in text
    assert "Planner committed: yes" in text
    assert "Approval passed: yes" in text


def test_final_report_shows_template_planner_approval_diagnostics(tmp_path: Path) -> None:
    r2a = tmp_path / ".r2a"
    r2a.mkdir(parents=True, exist_ok=True)
    (r2a / "TASK_SPEC.md").write_text("# TASK_SPEC\n", encoding="utf-8")
    (r2a / "EXPERIMENT_CONTRACT.md").write_text("# EXPERIMENT_CONTRACT\n", encoding="utf-8")
    _write_check(tmp_path, "PASS")
    state = _state_with_paper(tmp_path, planner_backend="template", auto_approve=True)

    result = final_node({**state, "approved": True, "reviewer_verdict": "INPUT_CONTRACT_READY", "manager_status": "PASS"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "Planner backend: template" in text
    assert "Planner committed: yes" in text
    assert "Approval passed: yes" in text
    assert "Planner transaction metadata: not applicable for deterministic template Planner paths." in text


def test_final_report_backend_retry_evidence_is_summarized(tmp_path: Path) -> None:
    _write_check(tmp_path, "PASS")
    _write_backend_attempt_logs(tmp_path, "planner", first_parse=True, second_returncode=1, second_freshness_ok=False, second_parse=True)
    for iteration in range(1, 9):
        logs = tmp_path / ".r2a" / "runs" / f"iter_{iteration:03d}" / "logs"
        logs.mkdir(parents=True)
        (logs / "claude_planner_attempt_1_stdout.log").write_text("The model's tool call could not be parsed (retry also failed).", encoding="utf-8")
        (logs / "claude_planner_attempt_1_stderr.log").write_text("", encoding="utf-8")
        (logs / "claude_planner_attempt_2_stdout.log").write_text("The model's tool call could not be parsed (retry also failed).", encoding="utf-8")
        (logs / "claude_planner_attempt_2_stderr.log").write_text("", encoding="utf-8")
    state = _state_with_paper(tmp_path)

    result = final_node({**state, "reviewer_verdict": "NEEDS_FIX", "manager_status": "PASS"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "Additional attempt logs available under `.r2a/runs/`" in text
    assert text.count("claude_planner_attempt") < 12


def test_final_report_simplified_chinese_headings(tmp_path: Path) -> None:
    _write_check(tmp_path, "PASS")
    state = _state_with_paper(tmp_path, language="zh")

    result = final_node({**state, "reviewer_verdict": "PASS_WITH_LIMITATIONS", "manager_status": "PASS"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "## 执行摘要" in text
    assert "## 当前复现等级" in text
    assert "## 实际完成内容" in text
    assert "## 剩余问题" in text
    assert "## 溯源文件" in text
    assert "PASS_WITH_LIMITATIONS" in text
    assert "鎵" not in text
    assert "鍓" not in text


def test_final_report_shows_empty_placeholder_input_blocks_l3(tmp_path: Path) -> None:
    results = tmp_path / ".r2a" / "results"
    data = tmp_path / ".r2a" / "artifacts" / "official" / "datasets" / "small"
    results.mkdir(parents=True)
    data.mkdir(parents=True)
    empty_query = data / "query_vectors.fvecs"
    empty_query.write_bytes(b"")
    (results / "input_contract_verification.csv").write_text(
        "component,status,path_or_command,evidence_source,notes\n"
        f"query,EMPTY_PLACEHOLDER_INPUT,{empty_query},official,size_bytes=0; integrity_status=EMPTY_PLACEHOLDER_INPUT\n",
        encoding="utf-8",
    )
    _write_check(tmp_path, "WARNING", warnings="- input integrity warning\n")
    state = _state_with_paper(tmp_path, target_reproduction_level="L4_reduced_paper_aligned")

    result = final_node({**state, "reviewer_verdict": "NEEDS_OFFICIAL_INPUT", "manager_status": "WARNING"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "Empty placeholder files or invalid required inputs block `official_reduced` / L3" in text
    assert "EMPTY_PLACEHOLDER_INPUT" in text
    assert "Current: L3: Official reduced run (L3_official_reduced_run)" not in text


def test_final_report_shows_planner_transaction_failure(tmp_path: Path) -> None:
    logs = tmp_path / ".r2a" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "planner_transaction.json").write_text(
        json.dumps(
            {
                "stage": "planner",
                "iteration": 1,
                "attempt": 1,
                "staging_dir": str(tmp_path / ".r2a" / "staging" / "planner" / "iter_001" / "attempt_001"),
                "committed": False,
                "validation_status": "FAIL",
                "failure_category": "PLANNER_TRANSACTION_FAILED",
                "execution_status": "PLANNER_MISSING_REQUIRED_OUTPUT",
                "issues": ["Missing required planner candidate output: TASK_SPEC.md."],
            }
        ),
        encoding="utf-8",
    )
    _write_check(tmp_path, "PASS")
    state = _state_with_paper(tmp_path)

    result = final_node({**state, "reviewer_verdict": "NEEDS_FIX", "manager_status": "PASS"})

    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "Planner candidate outputs were rejected by the transaction validator" in text
    assert "No official TASK_SPEC / EXPERIMENT_CONTRACT was committed" in text
    assert "not a paper reproduction failure" in text


def test_planner_failure_preserves_manager_status_and_terminal_manifest(tmp_path: Path, monkeypatch) -> None:
    def bad_model(*args, **kwargs):
        return {"schema_version": "2.0"}

    r2a = tmp_path / ".r2a"
    r2a.mkdir(parents=True)
    (r2a / "CHECK_REPORT.md").write_text("# CHECK_REPORT\n\n## Status\n\nWARNING\n", encoding="utf-8")
    (r2a / "MANAGER_DECISION.json").write_text(
        json.dumps({"status": "WARNING", "max_level_allowed": "L0_project_health"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("r2a.agents.planner_agent.call_planner_model", bad_model)
    state = _state_with_paper(tmp_path, planner_backend="claude", auto_approve=True)
    state["manager_status"] = "WARNING"

    planned = run_planner_agent(state)
    result = final_node(planned)

    assert planned["manager_status"] == "WARNING"
    text = Path(result["final_report_path"]).read_text(encoding="utf-8")
    assert "Manager status is FAIL" not in text
    manifest = json.loads((tmp_path / ".r2a" / "latest" / "RUN_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["status"] != "RUNNING"
    assert manifest["manager_status"] == "WARNING"


def _state_with_paper(tmp_path: Path, **kwargs) -> dict:
    paper = tmp_path / "paper.txt"
    paper.write_text("paper text", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    results = tmp_path / ".r2a" / "results"
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
        path = report_path(tmp_path, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = '{"ok": true}' if key == "paper_output" else f"# {key}\n\nok\n"
        path.write_text(body, encoding="utf-8")
    return make_initial_state(tmp_path, paper_path=paper, **kwargs)


def _write_check(repo: Path, status: str, *, errors: str = "- None\n", warnings: str = "- None\n") -> None:
    r2a = repo / ".r2a"
    r2a.mkdir(exist_ok=True)
    (r2a / "CHECK_REPORT.md").write_text(
        "# CHECK_REPORT\n\n"
        f"## Status\n\n{status}\n\n"
        "## Errors\n\n"
        f"{errors}\n"
        "## Warnings\n\n"
        f"{warnings}\n",
        encoding="utf-8",
    )


def _write_backend_attempt_logs(
    repo: Path,
    stage: str,
    *,
    first_parse: bool,
    second_returncode: int,
    second_freshness_ok: bool,
    second_parse: bool = False,
) -> None:
    logs = repo / ".r2a" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    first_body = "The model's tool call could not be parsed (retry also failed)." if first_parse else "ordinary failure"
    second_body = "The model's tool call could not be parsed (retry also failed)." if second_parse else "ok"
    (logs / f"claude_{stage}_attempt_1_stdout.log").write_text(
        "returncode: 1\nfreshness_ok: false\n\n" + first_body,
        encoding="utf-8",
    )
    (logs / f"claude_{stage}_attempt_1_stderr.log").write_text("", encoding="utf-8")
    (logs / f"claude_{stage}_attempt_2_stdout.log").write_text(
        f"returncode: {second_returncode}\nfreshness_ok: {str(second_freshness_ok).lower()}\n\n{second_body}",
        encoding="utf-8",
    )
    (logs / f"claude_{stage}_attempt_2_stderr.log").write_text("", encoding="utf-8")


def _write_l3_evidence(repo: Path) -> None:
    results = repo / ".r2a" / "results"
    logs = repo / ".r2a" / "logs"
    results.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    (results / "source_verification.csv").write_text(
        "status,artifact_url,source_path,branch,commit,tag,readme_found,build_docs_found,experiment_scripts_found,data_scripts_found,notes\n"
        "PASS,https://example.test/official,artifact,main,abc123,,yes,yes,yes,yes,official source verified\n",
        encoding="utf-8",
    )
    (results / "build_smoke.csv").write_text(
        "status,command,exit_code,duration_sec,component,notes\n"
        "PASS,python -m official --help,0,1.0,cli,smoke passed\n",
        encoding="utf-8",
    )
    (results / "input_contract_verification.csv").write_text(
        "component,status,path_or_command,evidence_source,notes\n"
        "dataset,FOUND,official_small,README,official reduced dataset located\n"
        "query,FOUND,queries.tsv,README,official reduced query file located\n"
        "ground_truth,FOUND,gt.tsv,README,official reduced ground truth located\n"
        "metric_definition,READY,recall@10 latency_ms,paper,metric definitions verified\n"
        "method,READY,Curator,README,paper method selected\n"
        "command,READY,python run_reduced.py,README,reduced command found\n"
        "parameters,READY,k=10 ef=40 selectivity=0.1,README,parameters verified\n",
        encoding="utf-8",
    )
    (logs / "reduced.log").write_text("official reduced command measured recall and latency\n", encoding="utf-8")
    (results / "reduced_metrics.csv").write_text(
        "command_id,dataset,method,k,ground_truth_source,metric_definition,input_provenance,recall,latency_ms,query_count,repetitions\n"
        "reduced-cmd,official_small,Curator,10,gt.tsv,recall@10 latency_ms,README official_small,0.91,12.5,100,1\n",
        encoding="utf-8",
    )
    (results / "command_manifest.csv").write_text(
        "command_id,command,exit_code,duration_sec,log_path,artifact_path,artifact_hash,input_provenance,notes\n"
        "reduced-cmd,python run_reduced.py --input official_small,0,12.5,reduced.log,.r2a/results/reduced_metrics.csv,sha256:reduced,README official_small,ok\n",
        encoding="utf-8",
    )


def _write_l4_evidence(repo: Path) -> None:
    _write_l3_evidence(repo)
    (repo / ".r2a" / "results" / "paper_alignment.csv").write_text(
        "paper_item,setting_name,paper_setting,reduced_setting,match_status,evidence_source,notes\n"
        "Table 2,dataset scale,full benchmark,official_small,PARTIAL_MATCH,paper/table,scale differs\n"
        "Table 2,hardware,paper server,WSL CPU,PARTIAL_MATCH,paper/table,hardware differs\n"
        "Table 2,runtime budget,full run,60s reduced,PARTIAL_MATCH,task spec,budget differs\n"
        "Table 2,parameters,k=10 ef=40,k=10 ef=40,MATCH,command,parameters match\n"
        "Table 2,number of repeats,not stated,1,NOT_AVAILABLE,paper,repeat count unavailable\n"
        "Table 2,baselines,all paper baselines,missing,PARTIAL_MATCH,paper,baselines not included\n"
        "Table 2,metric definition,recall@10 and latency_ms,recall@10 and latency_ms,MATCH,paper,metric definition matches\n"
        "Table 2,input source,official full data,official reduced small,PARTIAL_MATCH,artifact,input source is official reduced\n"
        "Table 2,known evidence gaps,none stated,full-scale values unavailable,NEEDS_HUMAN_VERIFICATION,review,gaps remain\n",
        encoding="utf-8",
    )


def _write_verification_only_reduced_evidence(repo: Path) -> None:
    _write_l3_evidence(repo)
    r2a = repo / ".r2a"
    (r2a / "EXPERIMENT_CONTRACT.md").write_text("# EXPERIMENT_CONTRACT\n\n## Contract Mode\n\nverification_only\n", encoding="utf-8")
    (r2a / "TASK_SPEC.md").write_text("# TASK_SPEC\n\nMode: verification_only\n", encoding="utf-8")
    (r2a / "results" / "reduced_metrics.csv").write_text(
        "command_id,dataset,method,k,ground_truth_source,metric_definition,input_provenance,recall,latency_ms,notes\n"
        "reduced-cmd,official_small,Curator,10,gt.tsv,recall@10,README official_small,0.91,12.5,VERIFICATION_ONLY; CEILING=L2\n",
        encoding="utf-8",
    )
    (r2a / "results" / "reproduction_status.csv").write_text(
        "status,reason,evidence_source,next_action\n"
        "SCHEMA_FIXED,old schema fix,reduced_metrics.csv,ignore after final verification\n",
        encoding="utf-8",
    )
