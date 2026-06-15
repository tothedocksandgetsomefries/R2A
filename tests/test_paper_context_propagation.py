from __future__ import annotations

import json
from pathlib import Path

from r2a.agents.reviewer_agent import run_reviewer_agent
from r2a.core.paths import ensure_artifact_dir, report_path
from r2a.core.state import make_initial_state
from r2a.tools.planner_input_builder import build_planner_input
from r2a.tools.paper_lookup import paper_lookup


def test_planner_v2_input_builder_uses_bounded_paper_bundle(tmp_path: Path) -> None:
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "paper").write_text("# PAPER_BRIEF\n\n## Metrics\n\nrecall and qps\n", encoding="utf-8")
    report_path(tmp_path, "paper_parse_quality").write_text("# PAPER_PARSE_QUALITY\n\nok\n", encoding="utf-8")
    report_path(tmp_path, "paper_text").write_text("# PAPER_TEXT\n\nthis full text should not be attached by default\n", encoding="utf-8")

    bundle = build_planner_input(make_initial_state(tmp_path, planner_backend="codex"))

    assert bundle["paper_bundle"]["paper"]["path"] == str(report_path(tmp_path, "paper"))
    assert "recall and qps" in bundle["paper_bundle"]["paper"]["excerpt"]
    assert "paper_parse_quality" in bundle["paper_bundle"]
    assert "paper_text" not in bundle["paper_bundle"]


def test_reviewer_codex_prompt_includes_paper_context(tmp_path: Path, monkeypatch) -> None:
    ensure_artifact_dir(tmp_path)
    report_path(tmp_path, "paper_context").write_text("# PAPER_CONTEXT\n\nEvidence-limited context\n", encoding="utf-8")
    report_path(tmp_path, "paper_evidence").write_text("# PAPER_EVIDENCE\n\nmetrics\n", encoding="utf-8")
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n", encoding="utf-8")
    report_path(tmp_path, "execution").write_text("# EXECUTION_REPORT\n", encoding="utf-8")
    report_path(tmp_path, "check").write_text("# CHECK_REPORT\n\n## Status\n\nPASS\n", encoding="utf-8")
    captured = {}

    def fake_run(repo_path, stage, prompt, allowed_outputs, **kwargs):
        captured["prompt"] = prompt
        repo = Path(repo_path)
        report = repo / allowed_outputs[0]
        feedback = repo / allowed_outputs[1]
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("# REVIEW_REPORT\n\n## Verdict\n\nPASS_WITH_LIMITATIONS\n", encoding="utf-8")
        feedback.write_text(json.dumps({"verdict": "PASS_WITH_LIMITATIONS"}), encoding="utf-8")
        return {"success": True, "guard_available": True, "unexpected_modifications": [], "stage_guard_error": ""}

    monkeypatch.setattr("r2a.agents.reviewer_agent.codex_stage_runner.run_codex_stage", fake_run)

    run_reviewer_agent(make_initial_state(tmp_path, reviewer_backend="codex"))

    assert str(report_path(tmp_path, "paper_context")) in captured["prompt"]
    assert str(report_path(tmp_path, "paper_parse_quality")) in captured["prompt"]
    assert "Evidence-limited context" in captured["prompt"]


def test_paper_lookup_searches_paper_context(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    artifact_dir.mkdir()
    (artifact_dir / "PAPER_CONTEXT.md").write_text("# PAPER_CONTEXT\n\n## Metrics\n\nrecall and qps\n", encoding="utf-8")

    result = paper_lookup(str(tmp_path), "metrics")

    assert result["found"] is True
    assert "PAPER_CONTEXT.md" in result["sources"]
