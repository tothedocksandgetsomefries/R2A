from __future__ import annotations

import json
import os
from pathlib import Path

from r2a.agents.manager_agent import run_manager_agent
from r2a.agents.paper_agent import run_paper_agent
from r2a.agents.planner_agent import run_planner_agent
from r2a.agents.reviewer_agent import run_reviewer_agent
from r2a.core.paths import ensure_artifact_dir, report_path
from r2a.core.state import make_initial_state


def _ok_result(stage: str) -> dict:
    return {
        "stage": stage,
        "returncode": 0,
        "stdout_log_path": "",
        "stderr_log_path": "",
        "stdout_tail": "",
        "stderr_tail": "",
        "allowed_outputs": [],
        "success": True,
        "unexpected_modifications": [],
        "stage_guard_ok": True,
    }


def _write_planner_candidate(repo_path: str | Path, allowed_outputs: list[str]) -> None:
    repo = Path(repo_path)
    task_path = repo / allowed_outputs[0]
    contract_path = repo / allowed_outputs[1]
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(
        "# TASK_SPEC\n\n"
        "## Reproducibility Gate Summary\nok\n\n"
        "## Max Evidence Level Allowed\nL2_input_contract_ready\n\n"
        "## L3 Entry Criteria\nnot yet\n\n"
        "## L4 Alignment Criteria\nnot yet\n",
        encoding="utf-8",
    )
    contract_path.write_text(
        "# EXPERIMENT_CONTRACT\n\n"
        "## Contract Mode\nverification_only\n\n"
        "## Max Evidence Level Allowed\nL2_input_contract_ready\n\n"
        "## Reproducibility Gate\nok\n\n"
        "## Claim Restrictions\nNo full reproduction claim.\n",
        encoding="utf-8",
    )


def _write_reviewer_candidate(
    repo_path: str | Path,
    allowed_outputs: list[str],
    verdict: str,
    feedback_fields: dict | None = None,
) -> None:
    repo = Path(repo_path)
    report = repo / allowed_outputs[0]
    feedback = repo / allowed_outputs[1]
    report.parent.mkdir(parents=True, exist_ok=True)
    feedback.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(f"# REVIEW_REPORT\n\n## Verdict\n\n{verdict}\n", encoding="utf-8")
    feedback.write_text(json.dumps({"verdict": verdict, **(feedback_fields or {})}), encoding="utf-8")


def test_planner_backend_codex_uses_v2_model_client_not_stage_runner(tmp_path: Path) -> None:
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "paper").write_text("# PAPER_BRIEF\n", encoding="utf-8")
    report_path(tmp_path, "paper_evidence").write_text("# PAPER_EVIDENCE\n", encoding="utf-8")
    state = make_initial_state(tmp_path, planner_backend="codex")

    result = run_planner_agent(state)

    assert result["planner_backend"] == "codex"
    assert result["approval_ready"] is False
    assert result["stop_reason"] == "PLANNER_BACKEND_NOT_CONFIGURED"
    assert result["planner_transaction"]["validation_status"] == "FAIL"
    assert not report_path(tmp_path, "planner_output").exists()
    assert result["planner_transaction"]["diagnostic"]["failure_category"] == "PLANNER_BACKEND_NOT_CONFIGURED"


def test_planner_backend_claude_uses_v2_model_client_not_stage_runner(tmp_path: Path) -> None:
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "paper").write_text("# PAPER_BRIEF\n", encoding="utf-8")
    report_path(tmp_path, "paper_evidence").write_text("# PAPER_EVIDENCE\n", encoding="utf-8")
    state = make_initial_state(
        tmp_path,
        planner_backend="claude",
        stage_api_keys={"planner": "dummy-key-placeholder"},
        stage_api_key_env_vars={"planner": "ANTHROPIC_API_KEY"},
    )

    result = run_planner_agent(state)

    assert result["planner_backend"] == "claude"
    assert result["approval_ready"] is False
    assert result["stop_reason"] == "PLANNER_BACKEND_NOT_CONFIGURED"
    assert result["planner_transaction"]["validation_status"] == "FAIL"
    assert not report_path(tmp_path, "planner_output").exists()
    assert result["planner_transaction"]["diagnostic"]["planner_backend"] == "claude"


def test_reviewer_backend_codex_safety_overrides_pass_when_check_fails(tmp_path: Path, monkeypatch) -> None:
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nFAIL\n", encoding="utf-8")

    def fake_run(repo_path, stage, prompt, allowed_outputs, iteration=None, timeout=7200, **kwargs):
        _write_reviewer_candidate(repo_path, allowed_outputs, "PASS")
        return _ok_result(stage)

    monkeypatch.setattr("r2a.agents.reviewer_agent.codex_stage_runner.run_codex_stage", fake_run)
    state = make_initial_state(tmp_path, reviewer_backend="codex")

    result = run_reviewer_agent(state)

    text = Path(result["review_report_path"]).read_text(encoding="utf-8")
    feedback = json.loads(report_path(tmp_path, "review_feedback").read_text(encoding="utf-8"))
    assert result["reviewer_verdict"] == "NEEDS_FIX"
    assert "## Verdict\n\nNEEDS_FIX" in text
    assert "Reviewer Transaction" in text
    assert "REVIEWER_MANAGER_FAIL_PASS" in text
    assert feedback["verdict"] == "NEEDS_FIX"
    assert feedback["reviewer_transaction"]["committed"] is False


def test_reviewer_backend_codex_safety_overrides_pass_with_limitations_when_check_fails(tmp_path: Path, monkeypatch) -> None:
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nFAIL\n", encoding="utf-8")

    def fake_run(repo_path, stage, prompt, allowed_outputs, iteration=None, timeout=7200, **kwargs):
        _write_reviewer_candidate(repo_path, allowed_outputs, "PASS_WITH_LIMITATIONS")
        return _ok_result(stage)

    monkeypatch.setattr("r2a.agents.reviewer_agent.codex_stage_runner.run_codex_stage", fake_run)
    state = make_initial_state(tmp_path, reviewer_backend="codex")

    result = run_reviewer_agent(state)

    text = Path(result["review_report_path"]).read_text(encoding="utf-8")
    assert result["reviewer_verdict"] == "NEEDS_FIX"
    assert "## Verdict\n\nNEEDS_FIX" in text
    assert "REVIEWER_MANAGER_FAIL_PASS" in text


def test_reviewer_backend_codex_manager_classification_conflict_stops_without_silent_pass(tmp_path: Path, monkeypatch) -> None:
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nFAIL\n", encoding="utf-8")

    def fake_run(repo_path, stage, prompt, allowed_outputs, iteration=None, timeout=7200, **kwargs):
        _write_reviewer_candidate(
            repo_path,
            allowed_outputs,
            "INPUT_CONTRACT_READY",
            {
                "execution_status": "PASS",
                "classification_conflicts": [
                    "Manager FAIL conflicts with schema-valid input-contract and runtime evidence."
                ],
            },
        )
        return _ok_result(stage)

    monkeypatch.setattr("r2a.agents.reviewer_agent.codex_stage_runner.run_codex_stage", fake_run)
    state = make_initial_state(tmp_path, reviewer_backend="codex", auto_iterate=True, max_iterations=4)

    result = run_reviewer_agent(state)

    text = Path(result["review_report_path"]).read_text(encoding="utf-8")
    feedback = json.loads(report_path(tmp_path, "review_feedback").read_text(encoding="utf-8"))
    assert result["reviewer_verdict"] == "MANAGER_CLASSIFICATION_CONFLICT"
    assert result["need_replan"] is False
    assert "## Verdict\n\nMANAGER_CLASSIFICATION_CONFLICT" in text
    assert feedback["verdict"] == "MANAGER_CLASSIFICATION_CONFLICT"
    assert feedback["reviewer_transaction"]["committed"] is True
    assert feedback["reviewer_transaction"]["manager_classification_conflict"] is True


def test_reviewer_backend_codex_safety_overrides_pass_when_engineer_needs_clarification(
    tmp_path: Path, monkeypatch
) -> None:
    artifact_dir = ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nWARNING\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n", encoding="utf-8")
    results_dir = artifact_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "ENGINEER_DONE.txt").write_text("NEEDS_CLARIFICATION\n", encoding="utf-8")

    def fake_run(repo_path, stage, prompt, allowed_outputs, iteration=None, timeout=7200, **kwargs):
        _write_reviewer_candidate(repo_path, allowed_outputs, "PASS")
        return _ok_result(stage)

    monkeypatch.setattr("r2a.agents.reviewer_agent.codex_stage_runner.run_codex_stage", fake_run)
    state = make_initial_state(tmp_path, reviewer_backend="codex")

    result = run_reviewer_agent(state)

    text = Path(result["review_report_path"]).read_text(encoding="utf-8")
    feedback = json.loads(report_path(tmp_path, "review_feedback").read_text(encoding="utf-8"))
    assert result["reviewer_verdict"] == "BORDERLINE"
    assert "## Verdict\n\nBORDERLINE" in text
    assert "NEEDS_CLARIFICATION" in text
    assert feedback["verdict"] == "BORDERLINE"
    assert feedback["execution_status"] == "NEEDS_CLARIFICATION"


def test_reviewer_backend_codex_commits_valid_staging_candidate(tmp_path: Path, monkeypatch) -> None:
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nPASS\n", encoding="utf-8")
    captured = {}

    def fake_run(repo_path, stage, prompt, allowed_outputs, iteration=None, timeout=7200, **kwargs):
        captured["allowed_outputs"] = allowed_outputs
        captured["prompt"] = prompt
        _write_reviewer_candidate(repo_path, allowed_outputs, "PASS_WITH_LIMITATIONS")
        return _ok_result(stage)

    monkeypatch.setattr("r2a.agents.reviewer_agent.codex_stage_runner.run_codex_stage", fake_run)

    result = run_reviewer_agent(make_initial_state(tmp_path, reviewer_backend="codex"))

    transaction = json.loads((tmp_path / ".r2a" / "logs" / "reviewer_transaction.json").read_text(encoding="utf-8"))
    assert transaction["committed"] is True
    assert transaction["committed_files"] == [".r2a/REVIEW_REPORT.md", ".r2a/REVIEW_FEEDBACK.json"]
    assert ".r2a/REVIEW_REPORT.md" not in captured["allowed_outputs"]
    assert ".r2a/REVIEW_FEEDBACK.json" not in captured["allowed_outputs"]
    assert ".r2a/staging/reviewer/iter_001/attempt_001/REVIEW_REPORT.md" in captured["allowed_outputs"]
    assert str(report_path(tmp_path, "review_feedback")) not in captured["prompt"]
    assert Path(result["review_report_path"]).exists()


def test_reviewer_backend_claude_uses_claude_stage_runner(tmp_path: Path, monkeypatch) -> None:
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nPASS\n", encoding="utf-8")
    captured = {}

    def fake_run(repo_path, stage, prompt, allowed_outputs, iteration=None, timeout=7200, claude_executable_path=None, **kwargs):
        captured["stage"] = stage
        captured["allowed_outputs"] = allowed_outputs
        captured["claude_executable_path"] = claude_executable_path
        captured["env"] = kwargs.get("env")
        _write_reviewer_candidate(repo_path, allowed_outputs, "PASS_WITH_LIMITATIONS")
        return _ok_result(stage)

    monkeypatch.setattr("r2a.agents.reviewer_agent.claude_stage_runner.run_claude_stage", fake_run)

    result = run_reviewer_agent(
        make_initial_state(
            tmp_path,
            reviewer_backend="claude",
            claude_executable_path="C:/Tools/ccr.cmd",
            stage_api_keys={"reviewer": "dummy-key-placeholder"},
            stage_api_key_env_vars={"reviewer": "ANTHROPIC_API_KEY"},
        )
    )

    transaction = json.loads((tmp_path / ".r2a" / "logs" / "reviewer_transaction.json").read_text(encoding="utf-8"))
    assert captured["stage"] == "reviewer"
    assert captured["claude_executable_path"] == "C:/Tools/ccr.cmd"
    assert captured["env"]["ANTHROPIC_API_KEY"] == "dummy-key-placeholder"
    assert ".r2a/staging/reviewer/iter_001/attempt_001/REVIEW_REPORT.md" in captured["allowed_outputs"]
    assert transaction["committed"] is True
    assert result["reviewer_verdict"] == "PASS_WITH_LIMITATIONS"


def test_reviewer_backend_codex_malformed_feedback_does_not_commit_or_reuse_old_review(
    tmp_path: Path, monkeypatch
) -> None:
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nPASS\n", encoding="utf-8")
    report_path(tmp_path, "review").write_text("# REVIEW_REPORT\n\nOLD REVIEW\n", encoding="utf-8")

    def fake_run(repo_path, stage, prompt, allowed_outputs, iteration=None, timeout=7200, **kwargs):
        repo = Path(repo_path)
        report = repo / allowed_outputs[0]
        feedback = repo / allowed_outputs[1]
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("# REVIEW_REPORT\n\n## Verdict\n\nPASS_WITH_LIMITATIONS\n", encoding="utf-8")
        feedback.write_text("{bad json", encoding="utf-8")
        return _ok_result(stage)

    monkeypatch.setattr("r2a.agents.reviewer_agent.codex_stage_runner.run_codex_stage", fake_run)

    result = run_reviewer_agent(make_initial_state(tmp_path, reviewer_backend="codex"))

    transaction = json.loads((tmp_path / ".r2a" / "logs" / "reviewer_transaction.json").read_text(encoding="utf-8"))
    text = Path(result["review_report_path"]).read_text(encoding="utf-8")
    assert transaction["committed"] is False
    assert transaction["execution_status"] == "REVIEWER_MALFORMED_FEEDBACK"
    assert result["reviewer_verdict"] == "NEEDS_FIX"
    assert "OLD REVIEW" not in text
    assert "Reviewer Transaction" in text


def test_reviewer_backend_codex_stale_feedback_does_not_commit(tmp_path: Path, monkeypatch) -> None:
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nPASS\n", encoding="utf-8")

    def fake_run(repo_path, stage, prompt, allowed_outputs, iteration=None, timeout=7200, **kwargs):
        _write_reviewer_candidate(repo_path, allowed_outputs, "PASS_WITH_LIMITATIONS")
        repo = Path(repo_path)
        old_time = 1
        for item in allowed_outputs[:2]:
            os.utime(repo / item, (old_time, old_time))
        return _ok_result(stage)

    monkeypatch.setattr("r2a.agents.reviewer_agent.codex_stage_runner.run_codex_stage", fake_run)

    result = run_reviewer_agent(make_initial_state(tmp_path, reviewer_backend="codex"))

    transaction = json.loads((tmp_path / ".r2a" / "logs" / "reviewer_transaction.json").read_text(encoding="utf-8"))
    assert transaction["committed"] is False
    assert transaction["execution_status"] == "REVIEWER_STALE_OUTPUT"
    assert result["reviewer_verdict"] == "NEEDS_FIX"


def test_manager_backend_codex_review_does_not_replace_check_report(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "result.csv").write_text("dataset,method,qps\nsift,hnsw,1\n", encoding="utf-8")
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n\n## Forbidden Files\n\n- .git/\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n", encoding="utf-8")

    def fake_run(repo_path, stage, prompt, allowed_outputs, iteration=None, timeout=7200, **kwargs):
        report_path(repo_path, "manager_codex_review").write_text("# MANAGER_CODEX_REVIEW\n", encoding="utf-8")
        return _ok_result(stage)

    monkeypatch.setattr("r2a.agents.manager_agent.codex_stage_runner.run_codex_stage", fake_run)
    state = make_initial_state(tmp_path, manager_backend="codex_review")

    result = run_manager_agent(state)

    assert Path(result["check_report_path"]).exists()
    assert Path(result["latest_manager_codex_review_path"]).exists()
    assert "CHECK_REPORT" in Path(result["check_report_path"]).read_text(encoding="utf-8")


def test_manager_backend_claude_review_writes_supplemental_review(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "result.csv").write_text("dataset,method,qps\nsift,hnsw,1\n", encoding="utf-8")
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n\n## Forbidden Files\n\n- .git/\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n", encoding="utf-8")

    def fake_run(repo_path, stage, prompt, allowed_outputs, iteration=None, timeout=7200, **kwargs):
        report_path(repo_path, "manager_codex_review").write_text("# MANAGER_CODEX_REVIEW\n", encoding="utf-8")
        return _ok_result(stage)

    monkeypatch.setattr("r2a.agents.manager_agent.claude_stage_runner.run_claude_stage", fake_run)
    state = make_initial_state(tmp_path, manager_backend="claude_review")

    result = run_manager_agent(state)

    assert Path(result["check_report_path"]).exists()
    assert Path(result["latest_manager_codex_review_path"]).exists()


def test_paper_backend_claude_reader_enters_claude_stage_runner(tmp_path: Path, monkeypatch) -> None:
    """Test that paper_backend=claude_reader enters the Claude stage runner."""
    paper = tmp_path / "test_paper.pdf"
    paper.write_bytes(b"%PDF-1.4")
    ensure_artifact_dir(tmp_path)

    call_log = []

    def fake_run(repo_path, stage, prompt, allowed_outputs, iteration=None, timeout=7200, **kwargs):
        call_log.append({
            "repo_path": repo_path,
            "stage": stage,
            "prompt": prompt,
            "allowed_outputs": allowed_outputs,
            "iteration": iteration,
            "timeout": timeout,
            "kwargs": kwargs,
        })
        # Create required outputs
        repo = Path(repo_path)
        brief_path = report_path(repo, "paper")
        evidence_path = report_path(repo, "paper_evidence")
        context_path = report_path(repo, "paper_context")

        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text("# PAPER_BRIEF\n\n## Topic\n\nTest paper\n", encoding="utf-8")
        evidence_path.write_text("# PAPER_EVIDENCE\n\n## Evidence\n\nTest evidence\n", encoding="utf-8")
        context_path.write_text("# PAPER_CONTEXT\n\n## Context\n\nTest context\n", encoding="utf-8")

        return _ok_result(stage)

    monkeypatch.setattr("r2a.agents.paper_agent.claude_stage_runner.run_claude_stage", fake_run)

    state = make_initial_state(
        tmp_path,
        paper_path=paper,
        paper_backend="claude_reader",
        claude_executable_path="ccr",
    )

    result = run_paper_agent(state)

    # Verify Claude stage runner was called with correct parameters
    assert len(call_log) == 1
    assert call_log[0]["stage"] == "paper"
    assert ".r2a/PAPER_BRIEF.md" in call_log[0]["allowed_outputs"]
    assert ".r2a/PAPER_EVIDENCE.md" in call_log[0]["allowed_outputs"]
    assert ".r2a/PAPER_CONTEXT.md" in call_log[0]["allowed_outputs"]

    # Verify result indicates successful Claude reader run
    assert result["paper_backend"] == "claude_reader"
    assert result["fallback_used"] is False
    assert Path(result["paper_brief_path"]).exists()
    assert Path(result["paper_evidence_path"]).exists()


def test_paper_backend_claude_reader_failure_does_not_fallback(tmp_path: Path, monkeypatch) -> None:
    """Test that Paper Claude reader failure does not silently fallback to preprocess."""
    paper = tmp_path / "test_paper.pdf"
    paper.write_bytes(b"%PDF-1.4")
    ensure_artifact_dir(tmp_path)

    def fake_run(repo_path, stage, prompt, allowed_outputs, iteration=None, timeout=7200, **kwargs):
        # Simulate Claude Code failure
        return {
            "stage": stage,
            "returncode": 1,
            "success": False,
            "error": "Claude Code failed",
            "unexpected_modifications": [],
        }

    monkeypatch.setattr("r2a.agents.paper_agent.claude_stage_runner.run_claude_stage", fake_run)

    state = make_initial_state(
        tmp_path,
        paper_path=paper,
        paper_backend="claude_reader",
        claude_executable_path="ccr",
    )

    result = run_paper_agent(state)

    # Verify failure is explicit, not fallback
    assert result["paper_backend"] == "claude_reader"
    assert result["paper_quality"] == "FAILED"
    assert result["fallback_used"] is False
    assert result.get("paper_claude_reader_failed") is True
    assert "Claude Code failed" in result.get("paper_claude_reader_error", "")
