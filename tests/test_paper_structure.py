from __future__ import annotations

from r2a.tools.paper_structure import (
    build_parse_quality_report,
    extract_baselines,
    extract_critical_tables,
    extract_datasets,
    extract_figures_and_tables,
    extract_metrics,
    extract_paper_metadata,
    extract_urls,
)


SAMPLE_TEXT = """
NaviX: Adaptive Filtered Graph Search
Jane Doe, John Smith
PVLDB 2025
arXiv:2501.12345v2

Abstract
We propose NaviX for filtered approximate nearest neighbor search over HNSW indexes.

PVLDB Artifact Availability: source code and artifact are available at https://github.com/example/navix-artifact.
Datasets can be downloaded from https://datasets.example.org/navix.

Figure 1: HNSW Index structure and graph navigation.
Figure 8: Vector search time vs selectivity for NaviX and ACORN.
Table 1: Summary of search heuristics including Kuzu-blind, Kuzu-directed, and NaviX.
Table 2: Datasets. SIFT has 1M vectors, dimension 128, L2 distance.

Evaluation compares NaviX against ACORN, DiskANN, Weaviate, and Milvus.
Metrics include recall, QPS, vector search time, index build time, index size, and distance computations.
"""


def test_extract_paper_metadata_arxiv() -> None:
    metadata = extract_paper_metadata(SAMPLE_TEXT)

    assert metadata["title"] == "NaviX: Adaptive Filtered Graph Search"
    assert metadata["arxiv_id_or_doi"] == "2501.12345v2"
    assert "PVLDB" in metadata["venue_or_source"]


def test_extract_urls_classifies_artifact_and_dataset() -> None:
    urls = extract_urls(SAMPLE_TEXT)

    assert any(item["kind"] in {"source_code", "artifact"} and "github.com/example/navix-artifact" in item["url"] for item in urls)
    assert any(item["kind"] == "dataset" and "datasets.example.org/navix" in item["url"] for item in urls)


def test_extract_figures_and_tables() -> None:
    items = extract_figures_and_tables(SAMPLE_TEXT)

    assert any(item["id"] == "1" and "HNSW Index" in item["caption"] for item in items["figures"])
    assert any(item["id"] == "2" and "Datasets" in item["caption"] for item in items["tables"])


def test_extract_baselines_datasets_metrics() -> None:
    baselines = extract_baselines(SAMPLE_TEXT)
    datasets = extract_datasets(SAMPLE_TEXT)
    metrics = extract_metrics(SAMPLE_TEXT)

    assert {"NaviX", "ACORN", "DiskANN", "Weaviate", "Milvus"}.issubset({item["name"] for item in baselines})
    assert any(item["name"].upper() == "SIFT" for item in datasets)
    assert "recall" in metrics
    assert "QPS" in metrics


def test_no_fabrication_for_missing_fields() -> None:
    metadata = extract_paper_metadata("Short note without URLs or venue.")
    urls = extract_urls("Short note without URLs or venue.")

    assert metadata["arxiv_id_or_doi"] == "Not available"
    assert urls == []


def test_extract_critical_tables_preserves_structured_values() -> None:
    text = """
Table 3: Reduced benchmark metrics for official sample.
Method  Recall  Latency_ms
PaperMethod  0.91  12.5
Baseline  0.85  15.2

The evaluation uses recall and latency on the official sample.
"""

    tables = extract_critical_tables(text)

    assert tables
    assert tables[0]["parse_quality"] == "structured"
    assert "Recall" in tables[0]["extracted_table"]
    assert "Latency_ms" in tables[0]["extracted_table"]


def test_parse_quality_report_marks_caption_only_as_gap() -> None:
    text = "Table 4: Dataset and hardware configuration for the full benchmark."

    report = build_parse_quality_report(text)

    assert "Reproduction-critical tables detected: 1" in report
    assert "Parse quality: `caption_only`" in report
    assert "Evidence Gap" in report
