from pathlib import Path

from r2a.tools.paper_lookup import paper_lookup


def test_paper_lookup_finds_metrics_in_brief(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    artifact_dir.mkdir()
    (artifact_dir / "PAPER_BRIEF.md").write_text("# PAPER_BRIEF\n\n## Metrics\n\nqps and recall\n", encoding="utf-8")

    result = paper_lookup(str(tmp_path), "metrics")

    assert result["found"] is True
    assert result["evidence_quality"] == "usable"
    assert "PAPER_BRIEF.md" in result["sources"]
    assert any("qps" in snippet for snippet in result["snippets"])


def test_paper_lookup_missing_brief_returns_not_found(tmp_path: Path) -> None:
    result = paper_lookup(str(tmp_path), "metrics")

    assert result["found"] is False
    assert result["evidence_quality"] == "missing"
    assert result["snippets"] == []


def test_paper_lookup_unknown_query_returns_not_found(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    artifact_dir.mkdir()
    (artifact_dir / "PAPER_BRIEF.md").write_text("# PAPER_BRIEF\n\n## Metrics\n\nqps\n", encoding="utf-8")

    result = paper_lookup(str(tmp_path), "nonexistent-content")

    assert result["found"] is False
    assert result["evidence_quality"] == "missing"


def test_paper_lookup_does_not_treat_placeholder_metrics_as_evidence(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    artifact_dir.mkdir()
    (artifact_dir / "PAPER_BRIEF.md").write_text("# PAPER_BRIEF\n\n## Metrics\n\nNot available in MVP\n", encoding="utf-8")

    result = paper_lookup(str(tmp_path), "metrics")

    assert result["found"] is False
    assert result["evidence_quality"] != "usable"
    assert "placeholder/missing evidence" in result["limitations"]


def test_paper_lookup_treats_non_placeholder_metrics_as_usable(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    artifact_dir.mkdir()
    (artifact_dir / "PAPER_BRIEF.md").write_text(
        "# PAPER_BRIEF\n\n## Metrics\n\nThe paper reports recall and QPS.\n",
        encoding="utf-8",
    )

    result = paper_lookup(str(tmp_path), "metrics")

    assert result["found"] is True
    assert result["evidence_quality"] == "usable"
    assert any("recall and QPS" in snippet for snippet in result["snippets"])


def test_paper_lookup_prefers_reproduction_card_for_source_code(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    artifact_dir.mkdir()
    (artifact_dir / "PAPER_REPRODUCTION_CARD.md").write_text(
        "# PAPER_REPRODUCTION_CARD\n\n## 11. Reproduction Resources\n\n- Source code URL: https://github.com/example/navix\n",
        encoding="utf-8",
    )

    result = paper_lookup(str(tmp_path), "source code")

    assert result["found"] is True
    assert "PAPER_REPRODUCTION_CARD.md" in result["sources"]
    assert "github.com/example/navix" in result["snippets"][0]


def test_paper_lookup_finds_datasets_in_reproduction_card(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    artifact_dir.mkdir()
    (artifact_dir / "PAPER_REPRODUCTION_CARD.md").write_text(
        "# PAPER_REPRODUCTION_CARD\n\n## 7. Datasets\n\n- Name: SIFT\n- Number of vectors: 1M\n",
        encoding="utf-8",
    )

    result = paper_lookup(str(tmp_path), "datasets")

    assert result["found"] is True
    assert "SIFT" in result["snippets"][0]


def test_paper_lookup_finds_figures_in_figures_tables_report(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".r2a"
    artifact_dir.mkdir()
    (artifact_dir / "PAPER_FIGURES_TABLES.md").write_text(
        "# PAPER_FIGURES_TABLES\n\n## Figures\n\n### Figure 8\n\n- Caption: Vector search time vs selectivity.\n",
        encoding="utf-8",
    )

    result = paper_lookup(str(tmp_path), "figures")

    assert result["found"] is True
    assert "PAPER_FIGURES_TABLES.md" in result["sources"]
