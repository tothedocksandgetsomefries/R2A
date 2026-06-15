from pathlib import Path
from types import SimpleNamespace

from r2a.agents.paper_agent import generate_paper_brief
from r2a.core.state import make_initial_state


def test_generate_paper_brief_writes_brief_and_evidence(tmp_path: Path) -> None:
    state = make_initial_state(tmp_path, goal="add HNSW oversampling baseline")

    result = generate_paper_brief(state)

    brief = Path(result["paper_brief_path"])
    evidence = Path(result["paper_evidence_path"])
    paper_text = Path(result["paper_text_path"])
    paper_context = Path(result["paper_context_path"])
    assert brief.exists()
    assert evidence.exists()
    assert paper_text.exists()
    assert paper_context.exists()
    assert "# PAPER_BRIEF" in brief.read_text(encoding="utf-8")
    assert "# PAPER_EVIDENCE" in evidence.read_text(encoding="utf-8")
    assert "Evidence Constraints" in paper_context.read_text(encoding="utf-8")


def test_generate_paper_brief_without_paper_marks_low_confidence(tmp_path: Path) -> None:
    state = make_initial_state(tmp_path, goal="add HNSW oversampling baseline")

    result = generate_paper_brief(state)
    brief_text = Path(result["paper_brief_path"]).read_text(encoding="utf-8")

    assert "Low" in brief_text
    assert "This brief is generated from user goal and available context only." in brief_text


def test_generate_paper_brief_extracts_pdf_text_into_evidence(tmp_path: Path, monkeypatch) -> None:
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(
        "r2a.agents.paper_agent.extract_pdf_text",
        lambda path, max_chars=12000: SimpleNamespace(ok=True, pages_checked=2, text="The paper reports recall and qps on SIFT."),
    )
    state = make_initial_state(tmp_path, goal="reproduce metrics", paper_path=paper)

    result = generate_paper_brief(state)

    evidence_text = Path(result["paper_evidence_path"]).read_text(encoding="utf-8")
    context_text = Path(result["paper_context_path"]).read_text(encoding="utf-8")
    assert "PDF text extraction succeeded for 2 page(s)" in evidence_text
    assert "The paper reports recall and qps on SIFT." in evidence_text
    assert "The paper reports recall and qps on SIFT." in context_text


def test_generate_paper_brief_marks_pdf_extraction_failure(tmp_path: Path, monkeypatch) -> None:
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(
        "r2a.agents.paper_agent.extract_pdf_text",
        lambda path, max_chars=12000: SimpleNamespace(ok=False, pages_checked=0, text="", error="PdfReadError: broken xref"),
    )
    state = make_initial_state(tmp_path, goal="reproduce metrics", paper_path=paper)

    result = generate_paper_brief(state)

    evidence_text = Path(result["paper_evidence_path"]).read_text(encoding="utf-8")
    assert "PDF extraction failed: PdfReadError: broken xref" in evidence_text
    assert "Extraction failed: PdfReadError: broken xref" in evidence_text
