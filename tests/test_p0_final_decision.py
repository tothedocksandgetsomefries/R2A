import json
from pathlib import Path

from r2a.agents import reviewer_agent
from r2a.agents.reviewer_agent import (
    _with_evidence_decision,
    _write_review_verdict_from_committed_outputs,
    _write_transaction_failure_review_verdict,
)
from r2a.core.final_decision import build_final_decision
from r2a.core.paths import report_path
from r2a.core import run_manifest
from r2a.core.review_verdict import review_verdict_path
from r2a.core.verdicts import PASS_LIKE_VERDICTS, is_pass_like_verdict
from r2a.tools import iteration, workflow_decision
from r2a.tools.iteration import write_final_report


def test_pass_reduced_verdict_sets_are_shared() -> None:
    for verdict in (
        "PASS",
        "PASS_REDUCED_METHOD_ONLY",
        "PASS_REDUCED_ALIGNED",
        "PASS_REDUCED_COMPARISON",
        "PASS_DEMO_ONLY",
    ):
        assert is_pass_like_verdict(verdict)
        assert verdict in PASS_LIKE_VERDICTS
        assert verdict in reviewer_agent.PASS_LIKE_VERDICTS
        assert verdict in iteration.PASS_VERDICTS
        assert verdict in workflow_decision.PASS_VERDICTS
        assert verdict in run_manifest.PASS_VERDICTS

    for verdict in ("NEEDS_FIX", "FAIL", "BLOCKED"):
        assert not is_pass_like_verdict(verdict)


def test_final_decision_accepts_valid_pass_reduced_aligned_l4(tmp_path: Path) -> None:
    _write_evidence_decision(
        tmp_path,
        {
            "current_reproduction_level": "L4_reduced_paper_aligned",
            "level_valid": True,
            "level_source": "ai_backend",
            "verdict": "PASS_REDUCED_ALIGNED",
            "level_reasoning": "Reduced metrics and paper alignment are valid.",
        },
    )
    _write_l4_candidate_artifacts(tmp_path, command_manifest=True)
    state = _state(tmp_path, reviewer_verdict="PASS_REDUCED_ALIGNED")

    decision = build_final_decision(state)
    report = write_final_report(state)
    text = report.read_text(encoding="utf-8")

    assert decision["accepted_level"] == "L4_reduced_paper_aligned"
    assert decision["accepted_level_valid"] is True
    assert decision["target_reached"] is True
    assert decision["final_status"] == "completed_success"
    assert "UNASSESSED" not in text


def test_final_decision_keeps_invalid_evidence_unassessed_but_observes_l4(tmp_path: Path) -> None:
    _write_evidence_decision(
        tmp_path,
        {
            "current_reproduction_level": None,
            "level_valid": False,
            "level_source": "unassessed",
            "verdict": "NEEDS_FIX",
            "level_reasoning": "Safety Override triggered.",
        },
    )
    _write_l4_candidate_artifacts(tmp_path, command_manifest=False)
    state = _state(tmp_path, reviewer_verdict="NEEDS_FIX", safety_override_triggered=True)

    decision = build_final_decision(state)
    manifest_path = run_manifest.write_run_manifest({**state, "current_reproduction_level": "L4_reduced_paper_aligned"})
    report = write_final_report(state)
    text = report.read_text(encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert decision["accepted_level"] == "UNASSESSED"
    assert decision["accepted_level_valid"] is False
    assert decision["observed_level"] == "L4_reduced_paper_aligned"
    assert "Observed Evidence Level: L4: Reduced paper-aligned evidence (L4_reduced_paper_aligned)" in text
    assert "Accepted Level After Quality Gates: UNASSESSED (UNASSESSED)" in text
    assert "Accepted L4" not in text
    assert "command_manifest.csv: missing warning" in text
    assert ".r2a/results/command_manifest.csv" not in text
    assert manifest["accepted_level"] == "UNASSESSED"
    assert manifest["achieved_level"] == ""
    assert manifest["observed_level"] == "L4_reduced_paper_aligned"


def test_review_verdict_valid_pass_reduced_aligned_writes_l4_evidence_decision(tmp_path: Path) -> None:
    _write_review_verdict(
        tmp_path,
        verdict="PASS_REDUCED_ALIGNED",
        accepted_level="L4_reduced_paper_aligned",
        level_valid=True,
        target_reached=True,
    )

    result = _with_evidence_decision(_state(tmp_path, reviewer_verdict="PASS_REDUCED_ALIGNED"))
    evidence = json.loads(report_path(tmp_path, "evidence_decision").read_text(encoding="utf-8"))

    assert result["current_reproduction_level"] == "L4_reduced_paper_aligned"
    assert result["level_source"] == "reviewer_structured_verdict"
    assert evidence["current_reproduction_level"] == "L4_reduced_paper_aligned"
    assert evidence["level_valid"] is True
    assert evidence["verdict"] == "PASS_REDUCED_ALIGNED"


def test_review_verdict_valid_pass_with_limitations_does_not_safety_override(tmp_path: Path) -> None:
    _write_review_verdict(
        tmp_path,
        verdict="PASS_WITH_LIMITATIONS",
        accepted_level="L0_project_health",
        level_valid=True,
        target_reached=False,
    )

    result = _with_evidence_decision(_state(tmp_path, reviewer_verdict="PASS_WITH_LIMITATIONS"))
    evidence = json.loads(report_path(tmp_path, "evidence_decision").read_text(encoding="utf-8"))

    assert result["reviewer_level_valid"] is True
    assert result["current_reproduction_level"] == "L0_project_health"
    assert evidence["verdict"] == "PASS_WITH_LIMITATIONS"
    assert evidence["level_source"] == "reviewer_structured_verdict"


def test_review_verdict_valid_needs_fix_is_unassessed(tmp_path: Path) -> None:
    _write_review_verdict(
        tmp_path,
        verdict="NEEDS_FIX",
        accepted_level="UNASSESSED",
        level_valid=False,
        target_reached=False,
        needs_fix_reasons=["reduced metrics missing"],
    )

    result = _with_evidence_decision(_state(tmp_path, reviewer_verdict="NEEDS_FIX"))
    evidence = json.loads(report_path(tmp_path, "evidence_decision").read_text(encoding="utf-8"))

    assert result["current_reproduction_level"] is None
    assert result["reviewer_level_valid"] is False
    assert evidence["current_reproduction_level"] == "UNASSESSED"
    assert evidence["level_valid"] is False
    assert evidence["verdict"] == "NEEDS_FIX"


def test_invalid_review_verdict_falls_back_to_markdown_parser(tmp_path: Path) -> None:
    path = review_verdict_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "verdict": "NOT_A_VERDICT"}), encoding="utf-8")
    report_path(tmp_path, "review").write_text("# REVIEW_REPORT\n\n## 判决: PASS_REDUCED_ALIGNED\n", encoding="utf-8")

    result = _with_evidence_decision(_state(tmp_path, reviewer_verdict="PASS_REDUCED_ALIGNED"))
    evidence = json.loads(report_path(tmp_path, "evidence_decision").read_text(encoding="utf-8"))

    assert result["current_reproduction_level"] == "L4_reduced_paper_aligned"
    assert result["level_source"] == "legacy_markdown_parser"
    assert evidence["level_source"] == "legacy_markdown_parser"
    assert any("legacy Markdown verdict parser" in item for item in evidence["warnings"])


def test_missing_review_verdict_falls_back_to_markdown_parser(tmp_path: Path) -> None:
    report_path(tmp_path, "review").parent.mkdir(parents=True, exist_ok=True)
    report_path(tmp_path, "review").write_text("# REVIEW_REPORT\n\n**Verdict**: `PASS_WITH_LIMITATIONS`\n", encoding="utf-8")

    result = _with_evidence_decision(_state(tmp_path, reviewer_verdict="PASS_WITH_LIMITATIONS"))
    evidence = json.loads(report_path(tmp_path, "evidence_decision").read_text(encoding="utf-8"))

    assert result["current_reproduction_level"] == "L0_project_health"
    assert result["level_source"] == "legacy_markdown_parser"
    assert evidence["verdict"] == "PASS_WITH_LIMITATIONS"
    assert evidence["level_valid"] is True


def test_reviewer_transaction_failure_verdict_preserves_proposed_feedback(tmp_path: Path) -> None:
    staging = tmp_path / ".r2a" / "staging" / "reviewer" / "iter_005" / "attempt_001"
    staging.mkdir(parents=True)
    (staging / "REVIEW_FEEDBACK.json").write_text(
        json.dumps(
            {
                "verdict": "PASS_REDUCED_METHOD_ONLY",
                "current_reproduction_level": "L3_official_reduced_run",
                "target_reached": False,
            }
        ),
        encoding="utf-8",
    )
    transaction = {
        "validation_status": "FAIL",
        "failure_category": "REVIEWER_SAFETY_VALIDATION_FAILED",
        "execution_status": "REVIEWER_INPUT_INTEGRITY_BLOCKED_L3",
        "committed": False,
        "candidate_verdict": "PASS_REDUCED_METHOD_ONLY",
        "staging_dir": str(staging),
    }

    _write_transaction_failure_review_verdict(
        tmp_path,
        state=_state(tmp_path, reviewer_verdict="NEEDS_FIX"),
        transaction=transaction,
        issues=["Official input integrity blocker prevents AI Reviewer from committing L3/L4 verdicts."],
        failure_category="REVIEWER_SAFETY_VALIDATION_FAILED",
        execution_status="REVIEWER_INPUT_INTEGRITY_BLOCKED_L3",
    )
    verdict = json.loads(review_verdict_path(tmp_path).read_text(encoding="utf-8"))
    result = _with_evidence_decision(_state(tmp_path, reviewer_verdict="NEEDS_FIX"))
    evidence = json.loads(report_path(tmp_path, "evidence_decision").read_text(encoding="utf-8"))

    assert verdict["verdict"] == "NEEDS_FIX"
    assert verdict["accepted_level"] == "UNASSESSED"
    assert verdict["level_valid"] is False
    assert verdict["committed"] is False
    assert verdict["validation_status"] == "FAIL"
    assert verdict["proposed_verdict"] == "PASS_REDUCED_METHOD_ONLY"
    assert verdict["proposed_accepted_level"] == "L3_official_reduced_run"
    assert result["reviewer_level_valid"] is False
    assert evidence["review_verdict_source"] == "reviewer_transaction_failure"
    assert evidence["verdict"] == "NEEDS_FIX"
    assert not any("legacy Markdown verdict parser" in item for item in evidence["warnings"])


def test_invalid_report_machine_verdict_falls_back_to_feedback_payload(tmp_path: Path) -> None:
    report = tmp_path / ".r2a" / "REVIEW_REPORT.md"
    feedback = tmp_path / ".r2a" / "REVIEW_FEEDBACK.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        "# REVIEW_REPORT\n\n"
        "## Machine Verdict JSON\n\n"
        "```json\n"
        '{"verdict":"NEEDS_FIX","accepted_level":"L3_official_reduced_run","level_valid":true,"target_reached":false}\n'
        "```\n",
        encoding="utf-8",
    )
    feedback.write_text(
        json.dumps(
            {
                "verdict": "NEEDS_FIX",
                "current_reproduction_level": "L3_official_reduced_run",
                "required_fixes": ["manager verification failed"],
            }
        ),
        encoding="utf-8",
    )

    validation = _write_review_verdict_from_committed_outputs(
        tmp_path,
        report,
        feedback,
        backend="openclaw",
        target="L4_reduced_paper_aligned",
    )
    verdict = json.loads(review_verdict_path(tmp_path).read_text(encoding="utf-8"))

    assert validation.valid is True
    assert verdict["source"] == "ai_backend_structured"
    assert verdict["verdict"] == "NEEDS_FIX"
    assert verdict["accepted_level"] == "UNASSESSED"
    assert verdict["level_valid"] is False
    assert verdict["needs_fix_reasons"] == ["manager verification failed"]


def _state(tmp_path: Path, *, reviewer_verdict: str, safety_override_triggered: bool = False) -> dict:
    return {
        "repo_path": str(tmp_path),
        "run_id": "test-run",
        "iteration": 3,
        "max_iterations": 3,
        "target_reproduction_level": "L4_reduced_paper_aligned",
        "reviewer_executed": True,
        "reviewer_backend": "openclaw",
        "reviewer_verdict": reviewer_verdict,
        "safety_override_triggered": safety_override_triggered,
        "decision_status": {
            "typed_decision": "final",
            "reason_code": "MAX_ITERATIONS_REACHED",
            "terminal": True,
        },
        "loop_status": "completed",
        "language": "en",
        "iteration_history": [],
    }


def _write_evidence_decision(repo: Path, payload: dict) -> None:
    path = report_path(repo, "evidence_decision")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_review_verdict(
    repo: Path,
    *,
    verdict: str,
    accepted_level: str,
    level_valid: bool,
    target_reached: bool,
    needs_fix_reasons: list[str] | None = None,
) -> None:
    path = review_verdict_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "verdict": verdict,
                "accepted_level": accepted_level,
                "level_valid": level_valid,
                "target_level": "L4_reduced_paper_aligned",
                "target_reached": target_reached,
                "evidence_files": ["results/reduced_metrics.csv"] if level_valid else [],
                "limitations": ["reduced reproduction only"] if level_valid else [],
                "needs_fix_reasons": needs_fix_reasons or [],
                "backend": "openclaw",
                "source": "reviewer_structured_verdict",
            }
        ),
        encoding="utf-8",
    )


def _write_l4_candidate_artifacts(repo: Path, *, command_manifest: bool) -> None:
    results = repo / ".r2a" / "results"
    logs = repo / ".r2a" / "logs"
    results.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    (repo / ".r2a" / "CHECK_REPORT.md").write_text("# CHECK_REPORT\n\n## Status\n\nWARNING\n", encoding="utf-8")
    (repo / ".r2a" / "TASK_SPEC.md").write_text("# TASK_SPEC\n", encoding="utf-8")
    (repo / ".r2a" / "EXPERIMENT_CONTRACT.md").write_text("# EXPERIMENT_CONTRACT\n", encoding="utf-8")
    (repo / ".r2a" / "REVIEW_REPORT.md").write_text("# REVIEW_REPORT\n", encoding="utf-8")
    (logs / "reduced.log").write_text("ok\n", encoding="utf-8")
    (results / "reduced_metrics.csv").write_text(
        "command_id,dataset,method,k,recall,qps,input_provenance\n"
        "cmd1,official_small,method,10,0.99,123.4,paper\n",
        encoding="utf-8",
    )
    (results / "paper_alignment.csv").write_text(
        "paper_item,setting_name,paper_setting,reduced_setting,match_status,evidence_source,notes\n"
        "Table 1,metric definition,recall,recall,MATCH,paper,ok\n",
        encoding="utf-8",
    )
    (results / "L4_ALIGNMENT_SUMMARY.md").write_text("# L4\n", encoding="utf-8")
    if command_manifest:
        (results / "command_manifest.csv").write_text(
            "command_id,command,exit_code,duration_sec,log_path,artifact_path,artifact_hash,input_provenance\n"
            "cmd1,python run.py,0,1,reduced.log,.r2a/results/reduced_metrics.csv,sha256:x,paper\n",
            encoding="utf-8",
        )
