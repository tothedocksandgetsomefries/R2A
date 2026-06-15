from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import r2a.agents.paper_agent as paper_agent
from r2a.agents.paper_agent import run_paper_agent
from r2a.core.state import make_initial_state


def test_paper_reproduction_card_and_figures_tables_generated(tmp_path: Path, monkeypatch) -> None:
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4")
    text = """
NaviX: Adaptive Filtered Graph Search
PVLDB 2025
arXiv:2501.12345
Source code: https://github.com/example/navix
Figure 8: Vector search time vs selectivity.
Table 2: Datasets. SIFT has 1M vectors, dimension 128, L2 distance.
Evaluation compares NaviX with ACORN and DiskANN. Metrics include recall and QPS.
"""
    monkeypatch.setattr(
        "r2a.agents.paper_agent.extract_pdf_text",
        lambda path, max_chars=12000: SimpleNamespace(ok=True, pages_checked=2, text=text),
    )

    result = run_paper_agent(make_initial_state(tmp_path, paper_path=paper))

    card = Path(result["paper_reproduction_card_path"]).read_text(encoding="utf-8")
    figures = Path(result["paper_figures_tables_path"]).read_text(encoding="utf-8")
    parse_quality = Path(result["paper_parse_quality_path"]).read_text(encoding="utf-8")
    assert "## 1. Bibliographic Info" in card
    assert "## 5. Figures and Tables Summary" in card
    assert "## 6. Baselines" in card
    assert "## 7. Datasets" in card
    assert "## 8. Metrics" in card
    assert "## 11. Reproduction Resources" in card
    assert "## 13. Recommended R2A Reproduction Plan" in card
    assert "Figure 8" in figures
    assert "Table 2" in figures
    assert "## Critical Table Ledger" in parse_quality
    assert "Reproduction-critical tables detected" in parse_quality


def test_paper_reproduction_card_without_pdf_uses_not_available(tmp_path: Path) -> None:
    result = run_paper_agent(make_initial_state(tmp_path))

    card = Path(result["paper_reproduction_card_path"]).read_text(encoding="utf-8")
    parse_quality = Path(result["paper_parse_quality_path"]).read_text(encoding="utf-8")
    assert "Not available" in card
    assert "Extraction status: no paper uploaded" in card
    assert "Figure image internals parsed: no" in parse_quality


def test_paper_reproduction_card_does_not_call_codex(tmp_path: Path) -> None:
    assert not hasattr(paper_agent, "codex_stage_runner")

    result = run_paper_agent(make_initial_state(tmp_path))

    assert Path(result["paper_reproduction_card_path"]).exists()
