from pathlib import Path
import json

from r2a.agents.reviewer_agent import run_reviewer_agent
from r2a.core.paths import ensure_artifact_dir, report_path
from r2a.core.state import make_initial_state


def _write_ai_reviewer_candidate(repo_path: str | Path, allowed_outputs: list[str], verdict: str) -> None:
    repo = Path(repo_path)
    report = repo / allowed_outputs[0]
    feedback = repo / allowed_outputs[1]
    report.parent.mkdir(parents=True, exist_ok=True)
    feedback.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(f"# REVIEW_REPORT\n\n## Verdict\n\n{verdict}\n", encoding="utf-8")
    feedback.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "iteration": 1,
                "verdict": verdict,
                "should_iterate": False,
                "current_level": "L0_project_health",
                "next_level": "L0_project_health",
                "max_evidence_level_allowed": "L2_input_contract_ready",
                "claim_allowed": "limited_or_unresolved",
                "next_planner_mode": "none",
                "execution_status": "UNKNOWN",
                "failure_categories": [],
                "missing_l3_requirements": [],
                "missing_l4_alignment": [],
                "l4_alignment_status": "not_achieved",
                "l4_alignment_summary_path": str(repo / ".r2a" / "results" / "L4_ALIGNMENT_SUMMARY.md"),
                "preserve_successful_steps": [],
                "required_fixes": [],
                "forbidden_next_actions": [],
                "recommended_task_scope": [],
                "suggested_next_action": "continue",
                "evidence": {},
            }
        ),
        encoding="utf-8",
    )


def test_reviewer_does_not_pass_when_check_report_fails(tmp_path: Path) -> None:
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "paper").write_text("# PAPER_BRIEF\n\n## Metrics\n\nqps\n", encoding="utf-8")
    report_path(tmp_path, "paper_evidence").write_text("# PAPER_EVIDENCE\n\n## Missing Evidence\n\nDatasets\n", encoding="utf-8")
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n\nMock executor completed\n", encoding="utf-8")
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nFAIL\n", encoding="utf-8")
    state = make_initial_state(tmp_path, goal="add HNSW oversampling baseline")

    result = run_reviewer_agent(state)

    text = Path(result["review_report_path"]).read_text(encoding="utf-8")
    assert "## Verdict\n\nNEEDS_FIX" in text
    assert "## Reproduction Limitations" in text
    assert result["reviewer_executed"] is True


def test_reviewer_stops_auto_iteration_for_missing_source_and_input(tmp_path: Path) -> None:
    ensure_artifact_dir(tmp_path)
    results_dir = tmp_path / ".r2a" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    report_path(tmp_path, "paper").write_text("# PAPER_BRIEF\n\nEvidence-limited paper brief.\n", encoding="utf-8")
    report_path(tmp_path, "paper_evidence").write_text("# PAPER_EVIDENCE\n\nOfficial source not found.\n", encoding="utf-8")
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n\n- status: passed\n", encoding="utf-8")
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nWARNING\n", encoding="utf-8")
    (results_dir / "ENGINEER_DONE.txt").write_text("NEEDS_INPUT\n", encoding="utf-8")
    (results_dir / "source_verification.csv").write_text(
        "status,artifact_url,source_path,branch,commit,notes\n"
        "NOT_AVAILABLE,,,,,no official source URL was provided\n",
        encoding="utf-8",
    )
    (results_dir / "input_contract_verification.csv").write_text(
        "component,status,path_or_command,evidence_source,notes\n"
        "dataset,NEEDS_INPUT,,paper,official dataset missing\n"
        "query,NEEDS_INPUT,,paper,official query missing\n"
        "ground_truth,NEEDS_INPUT,,paper,official ground truth missing\n",
        encoding="utf-8",
    )

    result = run_reviewer_agent(make_initial_state(tmp_path, auto_iterate=True, max_iterations=4))
    feedback = json.loads(report_path(tmp_path, "review_feedback").read_text(encoding="utf-8"))

    assert result["reviewer_verdict"] == "NEEDS_OFFICIAL_INPUT"
    assert result["need_replan"] is False
    assert feedback["should_iterate"] is False
    assert feedback["workflow_decision"]["kind"] == "request_user_input"
    assert "official_source_url_or_local_source_path" in feedback["workflow_decision"]["required_inputs"]


def test_reviewer_feedback_separates_active_blockers_from_resolved_issues(tmp_path: Path) -> None:
    ensure_artifact_dir(tmp_path)
    feedback_path = report_path(tmp_path, "review_feedback")
    feedback_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "iteration": 5,
                "active_blockers": ["CSV_PARSE_ERROR: build_smoke.csv field count issue"],
                "required_fixes": ["CSV_PARSE_ERROR: input_contract_verification.csv field count issue"],
            }
        ),
        encoding="utf-8",
    )
    report_path(tmp_path, "paper").write_text("# PAPER_BRIEF\n", encoding="utf-8")
    report_path(tmp_path, "paper_evidence").write_text("# PAPER_EVIDENCE\n", encoding="utf-8")
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n\nMock executor completed\n", encoding="utf-8")
    current_error = "CSV: .r2a/results/paper_alignment.csv: Invalid match_status value(s): GAP, PARTIAL"
    report_path(tmp_path, "check").write_text(
        "# CHECK_REPORT\n\n"
        "## Status\n\nFAIL\n\n"
        "## Errors\n\n"
        f"- {current_error}\n\n"
        "## Warnings\n\n- None\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, goal="fix active blocker")
    state["latest_review_feedback_path"] = str(feedback_path)

    result = run_reviewer_agent(state)

    feedback = json.loads(Path(result["review_feedback_path"]).read_text(encoding="utf-8"))
    assert feedback["active_blockers"] == [current_error]
    assert all("CSV_PARSE_ERROR" not in item for item in feedback["active_blockers"])
    assert any("CSV_PARSE_ERROR" in item for item in feedback["resolved_issues"])
    assert feedback["history"][-1]["active_blockers"] == [current_error]


def test_reviewer_marks_placeholder_paper_evidence_as_limitation(tmp_path: Path) -> None:
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "paper").write_text("# PAPER_BRIEF\n\n## Metrics\n\nNot available in MVP\n", encoding="utf-8")
    report_path(tmp_path, "paper_evidence").write_text("# PAPER_EVIDENCE\n\n## Extracted Evidence\n\nNot available in MVP\n", encoding="utf-8")
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n\nMock executor completed\n", encoding="utf-8")
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nPASS\n", encoding="utf-8")
    state = make_initial_state(tmp_path, goal="add HNSW oversampling baseline")

    result = run_reviewer_agent(state)

    text = Path(result["review_report_path"]).read_text(encoding="utf-8")
    assert "Evidence missing for `metrics`" in text
    assert "metrics: Evidence Gap" in text
    assert "metrics: evidence found" not in text


def test_reviewer_codex_prompt_includes_explicit_paths_and_excerpts(tmp_path: Path, monkeypatch) -> None:
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "paper").write_text("# PAPER_BRIEF\n\n## Method\n\nHNSW\n", encoding="utf-8")
    report_path(tmp_path, "paper_evidence").write_text("# PAPER_EVIDENCE\n\n## Metrics\n\nrecall and qps\n", encoding="utf-8")
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n\n## Goal\n\nRun reduced experiment\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n\nExecuted reduced run\n", encoding="utf-8")
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nPASS\n", encoding="utf-8")
    captured = {}

    def fake_run(repo_path, stage, prompt, allowed_outputs, **kwargs):
        captured["prompt"] = prompt
        _write_ai_reviewer_candidate(repo_path, allowed_outputs, "PASS_WITH_LIMITATIONS")
        return {
            "stage": stage,
            "returncode": 0,
            "stdout_log_path": "",
            "stderr_log_path": "",
            "stdout_tail": "",
            "stderr_tail": "",
            "allowed_outputs": allowed_outputs,
            "success": True,
            "unexpected_modifications": [],
            "stage_guard_ok": True,
            "guard_available": True,
            "stage_guard_error": "",
            "stage_guard_warning": "",
        }

    monkeypatch.setattr("r2a.agents.reviewer_agent.codex_stage_runner.run_codex_stage", fake_run)
    state = make_initial_state(tmp_path, reviewer_backend="codex")

    run_reviewer_agent(state)

    prompt = captured["prompt"]
    assert str(report_path(tmp_path, "task")) in prompt
    assert ".r2a\\staging\\reviewer" in prompt or ".r2a/staging/reviewer" in prompt
    assert str(report_path(tmp_path, "review_feedback")) not in prompt
    assert str(report_path(tmp_path, "execution")) in prompt
    assert str(report_path(tmp_path, "paper_evidence")) in prompt
    assert str(report_path(tmp_path, "check")) in prompt
    assert "Run reduced experiment" in prompt
    assert "Executed reduced run" in prompt
    assert "recall and qps" in prompt
    assert "You must read TASK_SPEC" in prompt


def test_reviewer_codex_prompt_marks_missing_context(tmp_path: Path, monkeypatch) -> None:
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nPASS\n", encoding="utf-8")
    captured = {}

    def fake_run(repo_path, stage, prompt, allowed_outputs, **kwargs):
        captured["prompt"] = prompt
        _write_ai_reviewer_candidate(repo_path, allowed_outputs, "BORDERLINE")
        return {
            "stage": stage,
            "returncode": 0,
            "stdout_log_path": "",
            "stderr_log_path": "",
            "stdout_tail": "",
            "stderr_tail": "",
            "allowed_outputs": allowed_outputs,
            "success": True,
            "unexpected_modifications": [],
            "stage_guard_ok": True,
            "guard_available": True,
            "stage_guard_error": "",
            "stage_guard_warning": "",
        }

    monkeypatch.setattr("r2a.agents.reviewer_agent.codex_stage_runner.run_codex_stage", fake_run)
    state = make_initial_state(tmp_path, reviewer_backend="codex")

    run_reviewer_agent(state)

    assert "MISSING:" in captured["prompt"]
    assert "TASK_SPEC.md" in captured["prompt"]
    assert "PAPER_EVIDENCE.md" in captured["prompt"]


def test_reviewer_openclaw_uses_staging_input_and_transaction(tmp_path: Path, monkeypatch) -> None:
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "paper").write_text("# PAPER_BRIEF\n\n## Method\n\nHNSW\n", encoding="utf-8")
    report_path(tmp_path, "paper_evidence").write_text("# PAPER_EVIDENCE\n\n## Metrics\n\nrecall and qps\n", encoding="utf-8")
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n\n## Goal\n\nRun reduced experiment\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n\nExecuted reduced run\n", encoding="utf-8")
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nPASS\n", encoding="utf-8")
    captured = {}

    def fake_openclaw(repo_path, input_path, allowed_outputs, **kwargs):
        captured["input_path"] = Path(input_path)
        captured["input_text"] = Path(input_path).read_text(encoding="utf-8")
        captured["allowed_outputs"] = allowed_outputs
        _write_ai_reviewer_candidate(repo_path, allowed_outputs, "PASS_WITH_LIMITATIONS")
        return {
            "stage": "reviewer",
            "backend": "openclaw",
            "returncode": 0,
            "stdout_log_path": "",
            "stderr_log_path": "",
            "stdout_tail": "",
            "stderr_tail": "",
            "allowed_outputs": allowed_outputs,
            "success": True,
            "unexpected_modifications": [],
            "stage_guard_ok": True,
            "guard_available": True,
            "stage_guard_error": "",
            "stage_guard_warning": "",
            "stdout_json": True,
            "provider": "deepseek",
            "model": "deepseek-chat",
            "runner": "embedded",
            "fallbackUsed": False,
        }

    monkeypatch.setattr("r2a.agents.reviewer_agent._run_openclaw_reviewer_stage", fake_openclaw)
    state = make_initial_state(tmp_path, reviewer_backend="openclaw")

    result = run_reviewer_agent(state)

    assert captured["input_path"].name == "OPENCLAW_INPUT.md"
    assert "R2A Reviewer OpenClaw Stage" in captured["input_text"]
    assert "provider: `ai-coding-plan`" in captured["input_text"]
    assert "model: `glm-5`" in captured["input_text"]
    assert "Run reduced experiment" in captured["input_text"]
    assert "Hard-blocker consistency rule" in captured["input_text"]
    assert "`verdict` MUST NOT be pass-like" in captured["input_text"]
    assert captured["allowed_outputs"][0].endswith("REVIEW_REPORT.md")
    assert captured["allowed_outputs"][1].endswith("REVIEW_FEEDBACK.json")
    assert result["reviewer_verdict"] == "PASS_WITH_LIMITATIONS"
    assert Path(result["review_report_path"]).exists()
    transaction = json.loads((tmp_path / ".r2a" / "logs" / "reviewer_transaction.json").read_text(encoding="utf-8"))
    assert transaction["validation_status"] == "PASS"
    assert transaction["committed"] is True


def test_reviewer_limitation_mentions_limited_pdf_extraction(tmp_path: Path) -> None:
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "paper").write_text("# PAPER_BRIEF\n\n## Metrics\n\nNot available in MVP\n", encoding="utf-8")
    report_path(tmp_path, "paper_evidence").write_text("# PAPER_EVIDENCE\n\n## Extracted Evidence\n\nNot available in MVP\n", encoding="utf-8")
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n\nMock executor completed\n", encoding="utf-8")
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nPASS\n", encoding="utf-8")
    state = make_initial_state(tmp_path, goal="add HNSW oversampling baseline")

    result = run_reviewer_agent(state)

    text = Path(result["review_report_path"]).read_text(encoding="utf-8")
    assert "Limited PDF text extraction is available" in text
    assert "Paper parsing is not implemented in MVP" not in text


def test_reviewer_classifies_blocked_engineer_outcome_for_iteration(tmp_path: Path) -> None:
    ensure_artifact_dir(tmp_path)
    results_dir = tmp_path / ".r2a" / "results"
    results_dir.mkdir(exist_ok=True)
    report_path(tmp_path, "paper").write_text("# PAPER_BRIEF\n\n## Metrics\n\nrecall and latency\n", encoding="utf-8")
    report_path(tmp_path, "paper_evidence").write_text("# PAPER_EVIDENCE\n\n## Metrics\n\nrecall and latency\n", encoding="utf-8")
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n\n## Goal\n\nRun reduced experiment\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n\nANN_SEARCH failed with query vector type mismatch.\n", encoding="utf-8")
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nWARNING\n", encoding="utf-8")
    (results_dir / "ENGINEER_DONE.txt").write_text("BLOCKED\n", encoding="utf-8")
    (results_dir / "reproduction_status.csv").write_text(
        "status,reason,evidence_source,next_action\n"
        "BLOCKED,query vector type mismatch,.r2a/results/ENGINEER_NOTES.md,verify API semantics\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, auto_iterate=True, max_iterations=2)

    result = run_reviewer_agent(state)

    text = Path(result["review_report_path"]).read_text(encoding="utf-8")
    assert result["reviewer_verdict"] == "NEEDS_FIX"
    assert result["need_replan"] is False
    assert "API_OR_ALGORITHM_SEMANTICS" in text
    assert "Do not ask Engineer to redesign algorithm/API semantics by default" in text
    feedback = json.loads((tmp_path / ".r2a" / "REVIEW_FEEDBACK.json").read_text(encoding="utf-8"))
    assert feedback["verdict"] == "NEEDS_FIX"
    assert feedback["should_iterate"] is False
    assert "API_OR_ALGORITHM_SEMANTICS" in feedback["failure_categories"]
    assert any("do not rewrite algorithm/API" in item for item in feedback["forbidden_next_actions"])


def test_reviewer_marks_demo_only_without_claiming_reproduction(tmp_path: Path) -> None:
    ensure_artifact_dir(tmp_path)
    results_dir = tmp_path / ".r2a" / "results"
    results_dir.mkdir(exist_ok=True)
    report_path(tmp_path, "paper").write_text("# PAPER_BRIEF\n\n## Metrics\n\nrecall and latency\n", encoding="utf-8")
    report_path(tmp_path, "paper_evidence").write_text("# PAPER_EVIDENCE\n\n## Metrics\n\nrecall and latency\n", encoding="utf-8")
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n\n## Experiment Contract\n\nsynthetic_demo\n", encoding="utf-8")
    report_path(tmp_path, "experiment_contract").write_text("# EXPERIMENT_CONTRACT\n\n## Contract Mode\n\nsynthetic_demo\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n\nSynthetic harness run completed.\n", encoding="utf-8")
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nPASS\n", encoding="utf-8")
    (results_dir / "ENGINEER_DONE.txt").write_text("DONE\n", encoding="utf-8")
    (results_dir / "reduced_demo_metrics.csv").write_text(
        "dataset,method,k,efs,selectivity,latency_ms,recall,query_count,ground_truth_source,input_level,result_level,notes\n"
        "synthetic_tiny,HNSW,10,40,0.1,3.2,1.0,5,bruteforce,SYNTHETIC_INPUT,DEMO_ONLY,NOT_PAPER_REPRODUCTION\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path)

    result = run_reviewer_agent(state)

    text = Path(result["review_report_path"]).read_text(encoding="utf-8")
    assert result["reviewer_verdict"] == "PASS_DEMO_ONLY"
    assert result["need_replan"] is False
    assert "DEMO_ONLY" in text
    feedback = json.loads((tmp_path / ".r2a" / "REVIEW_FEEDBACK.json").read_text(encoding="utf-8"))
    assert feedback["verdict"] == "PASS_DEMO_ONLY"
    assert feedback["next_planner_mode"] == "none"


def test_reviewer_needs_official_input_stops_without_user_approval(tmp_path: Path) -> None:
    ensure_artifact_dir(tmp_path)
    results_dir = tmp_path / ".r2a" / "results"
    results_dir.mkdir(exist_ok=True)
    report_path(tmp_path, "paper").write_text("# PAPER_BRIEF\n\n## Metrics\n\nrecall and latency\n", encoding="utf-8")
    report_path(tmp_path, "paper_evidence").write_text("# PAPER_EVIDENCE\n\n## Metrics\n\nrecall and latency\n", encoding="utf-8")
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n\n## Experiment Contract\n\nofficial_reduced\n", encoding="utf-8")
    report_path(tmp_path, "experiment_contract").write_text("# EXPERIMENT_CONTRACT\n\n## Contract Mode\n\nofficial_reduced\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n\nOfficial inputs missing.\n", encoding="utf-8")
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nWARNING\n", encoding="utf-8")
    (results_dir / "ENGINEER_DONE.txt").write_text("NEEDS_OFFICIAL_INPUT\n", encoding="utf-8")
    (results_dir / "input_contract_verification.csv").write_text(
        "component,status,path_or_command,evidence_source,notes\n"
        "ground_truth,NEEDS_OFFICIAL_INPUT,not found,README,official ground truth missing\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, auto_iterate=True, max_iterations=2)

    result = run_reviewer_agent(state)

    assert result["reviewer_verdict"] == "NEEDS_OFFICIAL_INPUT"
    assert result["need_replan"] is False
    feedback = json.loads((tmp_path / ".r2a" / "REVIEW_FEEDBACK.json").read_text(encoding="utf-8"))
    assert feedback["next_planner_mode"] == "none"
    assert feedback["next_reproduction_level"] == "UNASSESSED"
    assert "MISSING_ARTIFACT_OR_DATA" in feedback["failure_categories"]


def test_reviewer_needs_official_input_can_iterate_after_user_approval(tmp_path: Path) -> None:
    ensure_artifact_dir(tmp_path)
    results_dir = tmp_path / ".r2a" / "results"
    results_dir.mkdir(exist_ok=True)
    report_path(tmp_path, "paper").write_text("# PAPER_BRIEF\n\n## Metrics\n\nrecall and latency\n", encoding="utf-8")
    report_path(tmp_path, "paper_evidence").write_text("# PAPER_EVIDENCE\n\n## Metrics\n\nrecall and latency\n", encoding="utf-8")
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n\n## Experiment Contract\n\nofficial_reduced\n", encoding="utf-8")
    report_path(tmp_path, "experiment_contract").write_text("# EXPERIMENT_CONTRACT\n\n## Contract Mode\n\nofficial_reduced\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n\nOfficial inputs missing.\n", encoding="utf-8")
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nWARNING\n", encoding="utf-8")
    (results_dir / "ENGINEER_DONE.txt").write_text("NEEDS_OFFICIAL_INPUT\n", encoding="utf-8")
    (results_dir / "input_contract_verification.csv").write_text(
        "component,status,path_or_command,evidence_source,notes\n"
        "ground_truth,NEEDS_OFFICIAL_INPUT,not found,README,official ground truth missing\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, auto_iterate=True, max_iterations=2)
    state["user_approved_official_download"] = True

    result = run_reviewer_agent(state)

    assert result["reviewer_verdict"] == "NEEDS_OFFICIAL_INPUT"
    assert result["need_replan"] is False
    feedback = json.loads((tmp_path / ".r2a" / "REVIEW_FEEDBACK.json").read_text(encoding="utf-8"))
    assert feedback["next_planner_mode"] == "none"
    assert feedback["next_reproduction_level"] == "UNASSESSED"


def test_reviewer_demo_only_continues_when_target_is_reduced(tmp_path: Path) -> None:
    ensure_artifact_dir(tmp_path)
    results_dir = tmp_path / ".r2a" / "results"
    results_dir.mkdir(exist_ok=True)
    report_path(tmp_path, "paper").write_text("# PAPER_BRIEF\n\n## Metrics\n\nrecall and latency\n", encoding="utf-8")
    report_path(tmp_path, "paper_evidence").write_text("# PAPER_EVIDENCE\n\n## Metrics\n\nrecall and latency\n", encoding="utf-8")
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n\n## Experiment Contract\n\nsynthetic_demo\n", encoding="utf-8")
    report_path(tmp_path, "experiment_contract").write_text("# EXPERIMENT_CONTRACT\n\n## Contract Mode\n\nsynthetic_demo\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n\nSynthetic harness run completed.\n", encoding="utf-8")
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nPASS\n", encoding="utf-8")
    (results_dir / "ENGINEER_DONE.txt").write_text("DONE\n", encoding="utf-8")
    (results_dir / "reduced_demo_metrics.csv").write_text(
        "dataset,method,k,latency_ms,recall,input_level,result_level,notes\n"
        "synthetic_tiny,HNSW,10,3.2,1.0,SYNTHETIC_INPUT,DEMO_ONLY,NOT_PAPER_REPRODUCTION\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, auto_iterate=True, max_iterations=3)

    result = run_reviewer_agent(state)

    assert result["reviewer_verdict"] == "PASS_DEMO_ONLY"
    assert result["need_replan"] is False
    feedback = json.loads((tmp_path / ".r2a" / "REVIEW_FEEDBACK.json").read_text(encoding="utf-8"))
    assert feedback["next_planner_mode"] == "none"
    assert feedback["next_reproduction_level"] == "UNASSESSED"


def test_reviewer_prefers_official_input_limitation_over_demo_pass(tmp_path: Path) -> None:
    ensure_artifact_dir(tmp_path)
    results_dir = tmp_path / ".r2a" / "results"
    results_dir.mkdir(exist_ok=True)
    report_path(tmp_path, "paper").write_text("# PAPER_BRIEF\n\n## Metrics\n\nrecall and latency\n", encoding="utf-8")
    report_path(tmp_path, "paper_evidence").write_text("# PAPER_EVIDENCE\n\n## Metrics\n\nrecall and latency\n", encoding="utf-8")
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n\n## Experiment Contract\n\nofficial_reduced\n", encoding="utf-8")
    report_path(tmp_path, "experiment_contract").write_text("# EXPERIMENT_CONTRACT\n\n## Contract Mode\n\nofficial_reduced\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n\nDemo smoke ran, but official inputs are missing.\n", encoding="utf-8")
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nWARNING\n", encoding="utf-8")
    (results_dir / "ENGINEER_DONE.txt").write_text("NEEDS_OFFICIAL_INPUT\n", encoding="utf-8")
    (results_dir / "reduced_demo_metrics.csv").write_text(
        "dataset,method,k,latency_ms,recall,input_level,result_level,notes\n"
        "synthetic_tiny,HNSW,10,3.2,1.0,SYNTHETIC_INPUT,DEMO_ONLY,NOT_PAPER_REPRODUCTION\n",
        encoding="utf-8",
    )
    (results_dir / "reproduction_status.csv").write_text(
        "status,reason,evidence_source,next_action\n"
        "NEEDS_OFFICIAL_INPUT,official query and ground truth missing after bounded network acquisition,official docs,wait for official inputs\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, auto_iterate=True, max_iterations=3)

    result = run_reviewer_agent(state)

    assert result["reviewer_verdict"] == "NEEDS_OFFICIAL_INPUT"
    assert result["need_replan"] is False
    feedback = json.loads((tmp_path / ".r2a" / "REVIEW_FEEDBACK.json").read_text(encoding="utf-8"))
    assert feedback["verdict"] == "NEEDS_OFFICIAL_INPUT"
    assert feedback["forbidden_next_actions"] == [
        "do not fabricate metrics, datasets, baselines, commands, or figure/table values",
        "do not run full-scale benchmarks unless explicitly authorized by the user",
    ]


def test_reduced_metrics_without_provenance_does_not_reach_l3(tmp_path: Path) -> None:
    results_dir, _logs_dir = _write_reviewer_progression_base(tmp_path)
    (results_dir / "reduced_metrics.csv").write_text(
        "dataset,method,recall,latency_ms\n"
        "official_small,HNSW,0.91,12.5\n",
        encoding="utf-8",
    )

    result = run_reviewer_agent(make_initial_state(tmp_path))

    assert result["reviewer_verdict"] == "INPUT_CONTRACT_READY"


def test_reviewer_feedback_does_not_downgrade_l2_for_capped_reduced_metrics(tmp_path: Path) -> None:
    results_dir, logs_dir = _write_reviewer_progression_base(tmp_path)
    _write_l3_reduced_evidence(results_dir, logs_dir)
    (tmp_path / ".r2a" / "TASK_SPEC.md").write_text("# TASK_SPEC\n\nMode: verification_only\n", encoding="utf-8")
    (tmp_path / ".r2a" / "EXPERIMENT_CONTRACT.md").write_text("# EXPERIMENT_CONTRACT\n\nverification_only\n", encoding="utf-8")
    (results_dir / "reduced_metrics.csv").write_text(
        "command_id,dataset,method,k,ground_truth_source,metric_definition,input_provenance,recall,latency_ms,query_count,repetitions,notes\n"
        "reduced-cmd,official_small,Curator,10,gt.tsv,recall@10 latency_ms,README official_small,0.91,12.5,100,1,VERIFICATION_ONLY; CEILING=L2\n",
        encoding="utf-8",
    )

    result = run_reviewer_agent(make_initial_state(tmp_path))

    assert result["reviewer_verdict"] == "INPUT_CONTRACT_READY"
    feedback = json.loads((tmp_path / ".r2a" / "REVIEW_FEEDBACK.json").read_text(encoding="utf-8"))
    assert feedback["current_level"] == "UNASSESSED"
    assert feedback["reproduction_level"] == "UNASSESSED"
    assert feedback["next_level"] == "UNASSESSED"


def test_reviewer_caps_unlabeled_reduced_metrics_when_contract_is_verification_only(tmp_path: Path) -> None:
    results_dir, logs_dir = _write_reviewer_progression_base(tmp_path)
    _write_l3_reduced_evidence(results_dir, logs_dir)
    (tmp_path / ".r2a" / "TASK_SPEC.md").write_text("# TASK_SPEC\n\nMode: verification_only\n", encoding="utf-8")
    (tmp_path / ".r2a" / "EXPERIMENT_CONTRACT.md").write_text(
        "# EXPERIMENT_CONTRACT\n\n## Contract Mode\n\nverification_only\n",
        encoding="utf-8",
    )

    result = run_reviewer_agent(make_initial_state(tmp_path))

    assert result["reviewer_verdict"] == "INPUT_CONTRACT_READY"
    feedback = json.loads((tmp_path / ".r2a" / "REVIEW_FEEDBACK.json").read_text(encoding="utf-8"))
    assert feedback["current_level"] == "UNASSESSED"
    assert feedback["reproduction_level"] == "UNASSESSED"
    assert any("capped at L2 because contract mode is verification_only" in item for item in feedback["missing_l3_requirements"])


def test_demo_only_reduced_metrics_does_not_reach_l3(tmp_path: Path) -> None:
    results_dir, _logs_dir = _write_reviewer_progression_base(tmp_path)
    (results_dir / "reduced_metrics.csv").write_text(
        "dataset,method,recall,latency_ms,input_level,result_level,notes\n"
        "synthetic_tiny,HNSW,1.0,1.2,SYNTHETIC_INPUT,DEMO_ONLY,NOT_PAPER_REPRODUCTION\n",
        encoding="utf-8",
    )

    result = run_reviewer_agent(make_initial_state(tmp_path))

    assert result["reviewer_verdict"] == "PASS_DEMO_ONLY"


def test_complete_l3_evidence_reaches_reduced_method_only(tmp_path: Path) -> None:
    results_dir, logs_dir = _write_reviewer_progression_base(tmp_path)
    _write_l3_reduced_evidence(results_dir, logs_dir)

    result = run_reviewer_agent(make_initial_state(tmp_path))

    assert result["reviewer_verdict"] == "PASS_REDUCED_METHOD_ONLY"


def test_paper_alignment_file_without_required_schema_does_not_reach_l4(tmp_path: Path) -> None:
    results_dir, logs_dir = _write_reviewer_progression_base(tmp_path)
    _write_l3_reduced_evidence(results_dir, logs_dir)
    (results_dir / "paper_alignment.csv").write_text(
        "figure,status,notes\n"
        "Figure 1,PASS,alignment file exists but lacks dataset parameters metric definition and difference fields\n",
        encoding="utf-8",
    )

    result = run_reviewer_agent(make_initial_state(tmp_path))

    assert result["reviewer_verdict"] == "PASS_REDUCED_METHOD_ONLY"


def test_complete_paper_alignment_schema_reaches_l4(tmp_path: Path) -> None:
    results_dir, logs_dir = _write_reviewer_progression_base(tmp_path)
    _write_l3_reduced_evidence(results_dir, logs_dir)
    (results_dir / "paper_alignment.csv").write_text(
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

    result = run_reviewer_agent(make_initial_state(tmp_path))

    assert result["reviewer_verdict"] == "PASS_REDUCED_ALIGNED"
    feedback = Path(result["review_feedback_path"]).read_text(encoding="utf-8")
    assert '"l4_alignment_status": "achieved_with_limitations"' in feedback
    assert "L4_ALIGNMENT_SUMMARY.md" in feedback
    review = Path(result["review_report_path"]).read_text(encoding="utf-8")
    assert "## L3 Satisfaction" in review
    assert "## L4 Satisfaction" in review
    assert "## Why This Is Not Full Reproduction" in review


def test_paper_alignment_without_match_or_partial_does_not_reach_l4(tmp_path: Path) -> None:
    results_dir, logs_dir = _write_reviewer_progression_base(tmp_path)
    _write_l3_reduced_evidence(results_dir, logs_dir)
    (results_dir / "paper_alignment.csv").write_text(
        "paper_item,setting_name,paper_setting,reduced_setting,match_status,evidence_source,notes\n"
        "Table 2,dataset scale,full benchmark,unknown,NOT_AVAILABLE,paper/table,unknown\n"
        "Table 2,hardware,paper server,unknown,NEEDS_HUMAN_VERIFICATION,paper/table,needs check\n"
        "Table 2,runtime budget,full run,unknown,NOT_AVAILABLE,task spec,unknown\n"
        "Table 2,parameters,k=10,unknown,NOT_AVAILABLE,command,unknown\n"
        "Table 2,number of repeats,not stated,unknown,NOT_AVAILABLE,paper,unknown\n"
        "Table 2,baselines,all paper baselines,unknown,NOT_AVAILABLE,paper,unknown\n"
        "Table 2,metric definition,recall@10,unknown,NOT_AVAILABLE,paper,unknown\n"
        "Table 2,input source,official full data,unknown,NOT_AVAILABLE,artifact,unknown\n"
        "Table 2,known evidence gaps,none stated,unknown,NEEDS_HUMAN_VERIFICATION,review,gaps remain\n",
        encoding="utf-8",
    )

    result = run_reviewer_agent(make_initial_state(tmp_path))

    assert result["reviewer_verdict"] == "PASS_REDUCED_METHOD_ONLY"


def test_baseline_comparison_with_different_input_or_environment_does_not_reach_l5(tmp_path: Path) -> None:
    results_dir, logs_dir = _write_reviewer_progression_base(tmp_path)
    _write_l3_reduced_evidence(results_dir, logs_dir)
    (logs_dir / "baseline_a.log").write_text("baseline a measured\n", encoding="utf-8")
    (logs_dir / "baseline_b.log").write_text("baseline b measured\n", encoding="utf-8")
    (results_dir / "baseline_comparison.csv").write_text(
        "method,baseline_method,reduced_input_id,metric,environment,budget_notes,command_id,command,exit_code,duration_sec,log_path,artifact_hash,input_provenance\n"
        "Curator,HNSW,official_small_a,recall@10,wsl-cpu,60s budget,base-a,python baseline.py,0,10,baseline_a.log,sha256:a,official_small_a\n"
        "Curator,HNSW,official_small_b,recall@10,windows-mingw,60s budget,base-b,python baseline.py,0,11,baseline_b.log,sha256:b,official_small_b\n",
        encoding="utf-8",
    )

    result = run_reviewer_agent(make_initial_state(tmp_path))

    assert result["reviewer_verdict"] == "PASS_REDUCED_METHOD_ONLY"


def test_complete_baseline_fairness_schema_with_provenance_reaches_l5(tmp_path: Path) -> None:
    results_dir, logs_dir = _write_reviewer_progression_base(tmp_path)
    _write_l3_reduced_evidence(results_dir, logs_dir)
    (logs_dir / "baseline.log").write_text("baseline comparison measured\n", encoding="utf-8")
    (results_dir / "baseline_comparison.csv").write_text(
        "method,baseline_method,reduced_input_id,metric,environment,budget_notes,command_id,command,exit_code,duration_sec,log_path,artifact_hash,input_provenance\n"
        "Curator,HNSW,official_small,recall@10,wsl-cpu,60s budget,baseline-cmd,python baseline.py,0,10,baseline.log,sha256:baseline,official_small\n",
        encoding="utf-8",
    )

    result = run_reviewer_agent(make_initial_state(tmp_path))

    assert result["reviewer_verdict"] == "PASS_REDUCED_COMPARISON"


def _write_reviewer_progression_base(tmp_path: Path) -> tuple[Path, Path]:
    ensure_artifact_dir(tmp_path)
    results_dir = tmp_path / ".r2a" / "results"
    logs_dir = tmp_path / ".r2a" / "logs"
    results_dir.mkdir(exist_ok=True)
    logs_dir.mkdir(exist_ok=True)
    report_path(tmp_path, "paper").write_text("# PAPER_BRIEF\n\n## Metrics\n\nrecall and latency\n", encoding="utf-8")
    report_path(tmp_path, "paper_evidence").write_text("# PAPER_EVIDENCE\n\n## Metrics\n\nrecall and latency\n", encoding="utf-8")
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n\n## Experiment Contract\n\nofficial_reduced\n", encoding="utf-8")
    report_path(tmp_path, "experiment_contract").write_text("# EXPERIMENT_CONTRACT\n\n## Contract Mode\n\nofficial_reduced\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n\nOfficial reduced command completed.\n", encoding="utf-8")
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nPASS\n", encoding="utf-8")
    (results_dir / "ENGINEER_DONE.txt").write_text("DONE\n", encoding="utf-8")
    (results_dir / "source_verification.csv").write_text(
        "status,artifact_url,source_path,branch,commit,tag,readme_found,build_docs_found,experiment_scripts_found,data_scripts_found,notes\n"
        "PASS,https://example.test/official,artifact,main,abc123,,yes,yes,yes,yes,official source verified\n",
        encoding="utf-8",
    )
    (results_dir / "build_smoke.csv").write_text(
        "status,command,exit_code,duration_sec,component,notes\n"
        "PASS,python -m official --help,0,1.0,cli,build/import smoke passed\n",
        encoding="utf-8",
    )
    (results_dir / "input_contract_verification.csv").write_text(
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
    return results_dir, logs_dir


def _write_l3_reduced_evidence(results_dir: Path, logs_dir: Path) -> None:
    (logs_dir / "reduced.log").write_text("official reduced command measured recall and latency\n", encoding="utf-8")
    (results_dir / "reduced_metrics.csv").write_text(
        "command_id,dataset,method,k,ground_truth_source,metric_definition,input_provenance,recall,latency_ms\n"
        "reduced-cmd,official_small,Curator,10,gt.tsv,recall@10 latency_ms,README official_small,0.91,12.5\n",
        encoding="utf-8",
    )
    (results_dir / "command_manifest.csv").write_text(
        "command_id,command,exit_code,duration_sec,log_path,artifact_path,artifact_hash,input_provenance,notes\n"
        "reduced-cmd,python run_reduced.py --input official_small,0,12.5,reduced.log,.r2a/results/reduced_metrics.csv,sha256:reduced,README official_small,ok\n",
        encoding="utf-8",
    )
