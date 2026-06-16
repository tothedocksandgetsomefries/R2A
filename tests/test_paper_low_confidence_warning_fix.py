"""Tests for Paper LOW_CONFIDENCE warning text paths."""

from r2a.agents import paper_agent
from r2a.core.paths import report_path


def test_claude_reader_caption_only_low_confidence_uses_numeric_alignment_warning(tmp_path, monkeypatch):
    """The real Claude reader branch should not claim caption-only limits Planner scope."""
    paper = tmp_path / "paper.txt"
    paper.write_text("Figure 1 reports a curve. Table 1 reports recall.\n", encoding="utf-8")

    def fake_run_claude_stage(repo, stage, prompt, allowed_outputs, **kwargs):
        report_path(repo, "paper").write_text("# PAPER_BRIEF\n", encoding="utf-8")
        report_path(repo, "paper_parse_quality").write_text(
            "LOW_CONFIDENCE\nfigure_1: caption_only\n",
            encoding="utf-8",
        )
        return {"success": True, "returncode": 0, "unexpected_modifications": []}

    monkeypatch.setattr(paper_agent.claude_stage_runner, "run_claude_stage", fake_run_claude_stage)

    result = paper_agent.run_paper_claude_reader(
        {
            "repo_path": str(tmp_path),
            "paper_path": str(paper),
            "goal": "test caption-only figure warning",
            "language": "en",
        }
    )

    warnings = "\n".join(result["warnings"])
    assert result["paper_quality"] == "LOW_CONFIDENCE"
    assert "figure-level numeric alignment evidence" in warnings
    assert "does not restrict Planner scope" in warnings
    assert "Planner scope may be restricted" not in warnings
    assert "Planner scope must be restricted" not in warnings


def test_caption_only_warning_constant_documents_the_canonical_text():
    warning = paper_agent.CAPTION_ONLY_LOW_CONFIDENCE_WARNING
    assert "caption-only" in warning
    assert "figure-level numeric alignment evidence" in warning
    assert "does not restrict Planner scope" in warning
    assert "Planner scope may be restricted" not in warning


def test_local_fallback_warning_is_distinct_from_caption_only_planner_scope():
    """Fallback/incomplete extraction may stay cautious without blaming caption-only figures."""
    warning = paper_agent.LOCAL_FALLBACK_LOW_CONFIDENCE_WARNING
    assert "local fallback or incomplete extraction" in warning
    assert "verification/discovery context" in warning
    assert "caption-only" not in warning
    assert "Planner scope may be restricted" not in warning
