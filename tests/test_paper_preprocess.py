from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import r2a.agents.paper_agent as paper_agent
from r2a.agents.paper_agent import run_paper_agent
from r2a.core.state import make_initial_state


def test_paper_preprocess_generates_text_and_context_for_pdf(tmp_path: Path, monkeypatch) -> None:
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(
        "r2a.agents.paper_agent.extract_pdf_text",
        lambda path, max_chars=12000: SimpleNamespace(ok=True, pages_checked=1, text="The method reports recall and qps."),
    )

    result = run_paper_agent(make_initial_state(tmp_path, paper_path=paper, goal="reproduce metrics"))

    assert Path(result["paper_text_path"]).exists()
    context = Path(result["paper_context_path"]).read_text(encoding="utf-8")
    assert "Evidence Constraints" in context
    assert "The method reports recall and qps." in context
    assert result["paper_extraction_status"] == "extraction succeeded"


def test_paper_preprocess_without_pdf_writes_no_paper_uploaded(tmp_path: Path) -> None:
    result = run_paper_agent(make_initial_state(tmp_path, goal="plan conservatively"))

    context = Path(result["paper_context_path"]).read_text(encoding="utf-8")
    text = Path(result["paper_text_path"]).read_text(encoding="utf-8")
    assert "no paper uploaded" in context.lower()
    assert "No paper uploaded" in text


def test_paper_preprocess_pdf_failure_does_not_crash(tmp_path: Path, monkeypatch) -> None:
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(
        "r2a.agents.paper_agent.extract_pdf_text",
        lambda path, max_chars=12000: SimpleNamespace(ok=False, pages_checked=0, text="", error="broken pdf"),
    )

    result = run_paper_agent(make_initial_state(tmp_path, paper_path=paper))

    assert result["paper_extraction_status"] == "extraction failed"
    assert "extraction failed" in Path(result["paper_context_path"]).read_text(encoding="utf-8").lower()


def test_paper_preprocess_does_not_call_codex_stage_runner(tmp_path: Path, monkeypatch) -> None:
    assert not hasattr(paper_agent, "codex_stage_runner")

    result = run_paper_agent(make_initial_state(tmp_path))

    assert Path(result["paper_context_path"]).exists()
