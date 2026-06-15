from __future__ import annotations

from collections import OrderedDict
import re
from typing import Any

NOT_AVAILABLE = "Not available"

BASELINE_TERMS = (
    "Kuzu-onehop-s",
    "Kuzu-blind",
    "Kuzu-directed",
    "Kuzu-ag",
    "NaviX",
    "ACORN",
    "FAISS-Navix",
    "FAISS",
    "Weaviate",
    "Milvus",
    "DiskANN",
    "FilteredDiskANN",
    "iRangeGraph",
    "PGVectorscale",
    "VBase",
)

METRIC_TERMS = (
    "recall",
    "latency",
    "qps",
    "queries per second",
    "vector search time",
    "end-to-end time",
    "execution time",
    "index build time",
    "index size",
    "distance computations",
    "memory",
    "throughput",
    "p95",
    "p99",
)

CRITICAL_TABLE_TERMS = (
    "dataset",
    "baseline",
    "metric",
    "recall",
    "qps",
    "latency",
    "index",
    "parameter",
    "configuration",
    "hardware",
    "setup",
    "selectivity",
    "dimension",
    "vectors",
    "memory",
    "time",
)

DATASET_HINTS = (
    "sift",
    "gist",
    "deep",
    "glove",
    "msong",
    "audio",
    "crawl",
    "yandex",
    "openai",
    "laion",
)


def extract_paper_metadata(text: str) -> dict[str, str]:
    head = _normalize(text[:8000])
    lines = [line.strip() for line in head.splitlines() if line.strip()]
    title = _first_title(lines)
    authors = _authors(lines, title)
    year = _first_match(head, r"\b(20[0-3][0-9]|19[8-9][0-9])\b")
    arxiv_id = _first_match(head, r"arXiv[:\s]*([0-9]{4}\.[0-9]{4,5}(?:v[0-9]+)?)")
    doi = _first_match(head, r"\bdoi[:\s]*(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")
    venue = _venue(head)
    abstract = _abstract(head)
    return {
        "title": title,
        "authors": authors,
        "year_or_version": year or NOT_AVAILABLE,
        "venue_or_source": venue,
        "arxiv_id_or_doi": arxiv_id or doi or NOT_AVAILABLE,
        "paper_url": _paper_url(extract_urls(text)),
        "abstract": abstract,
    }


def extract_urls(text: str) -> list[dict[str, str]]:
    urls: OrderedDict[str, dict[str, str]] = OrderedDict()
    pattern = re.compile(r"https?://[^\s<>)\]}\"']+", re.IGNORECASE)
    for match in pattern.finditer(text or ""):
        url = match.group(0).rstrip(".,;:")
        context = _window(text, match.start(), match.end(), 180)
        urls.setdefault(
            url,
            {
                "url": url,
                "kind": _classify_url(url, context),
                "context": _compact(context, 300),
            },
        )
    return list(urls.values())


def extract_figures_and_tables(text: str) -> dict[str, list[dict[str, str]]]:
    return {
        "figures": _extract_caption_items(text, r"\b(?:Figure|Fig\.)\s*([0-9]+[A-Za-z]?)\s*[:.\-]?\s*"),
        "tables": _extract_caption_items(text, r"\bTable\s*([0-9]+[A-Za-z]?)\s*[:.\-]?\s*"),
    }


def extract_critical_tables(text: str) -> list[dict[str, str]]:
    tables = extract_figures_and_tables(text)["tables"]
    blocks = _table_blocks(text)
    critical: list[dict[str, str]] = []
    for table in tables:
        combined = f"{table['caption']} {table['nearby_context']}".lower()
        if not any(term in combined for term in CRITICAL_TABLE_TERMS):
            continue
        block = blocks.get(table["id"], "")
        parsed = _parse_table_block(block)
        if parsed:
            quality = "structured"
            extracted = parsed
        elif block and len(block.splitlines()) >= 3:
            quality = "raw_text_only"
            extracted = f"```text\n{_compact_preserve_lines(block, 1800)}\n```"
        else:
            quality = "caption_only"
            extracted = "Not available beyond caption/nearby text."
        critical.append(
            {
                "id": table["id"],
                "caption": table["caption"],
                "parse_quality": quality,
                "why_critical": _critical_table_reason(combined),
                "extracted_table": extracted,
                "nearby_context": table["nearby_context"],
            }
        )
    return critical


def extract_experiment_sections(text: str) -> dict[str, str]:
    sections = _named_sections(text)
    setup = _section_by_keywords(sections, ("experimental setup", "experiment setup", "implementation", "hardware"))
    results = _section_by_keywords(sections, ("evaluation", "experiments", "results"))
    ablation = _section_by_keywords(sections, ("ablation", "sensitivity"))
    return {
        "experimental_setup": _compact(setup, 1800) or NOT_AVAILABLE,
        "key_results": _compact(results, 1800) or NOT_AVAILABLE,
        "ablation": _compact(ablation, 1200) or NOT_AVAILABLE,
        "hardware": _extract_hardware(text),
    }


def extract_baselines(text: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    lowered = text.lower()
    for term in BASELINE_TERMS:
        idx = lowered.find(term.lower())
        if idx < 0:
            continue
        context = _window(text, idx, idx + len(term), 240)
        found.append(
            {
                "name": term,
                "type": _baseline_type(term),
                "role": _role_from_context(context),
                "implementation_availability": _availability_from_context(context),
                "notes": _compact(context, 350),
            }
        )
    return found


def extract_datasets(text: str) -> list[dict[str, str]]:
    datasets: OrderedDict[str, dict[str, str]] = OrderedDict()
    tableish = _table_dataset_candidates(text)
    for item in tableish:
        datasets[item["name"]] = item
    lowered = text.lower()
    for name in DATASET_HINTS:
        idx = lowered.find(name)
        if idx >= 0 and name.upper() not in datasets and name.capitalize() not in datasets:
            context = _window(text, idx, idx + len(name), 220)
            display = name.upper() if len(name) <= 5 else name
            datasets.setdefault(
                display,
                {
                    "name": display,
                    "number_of_vectors": _first_match(context, r"([0-9]+(?:\.[0-9]+)?\s*[KMB]?)\s+(?:vectors|points|items)") or NOT_AVAILABLE,
                    "dimension": _first_match(context, r"(?:dimension|dimensionality|dim)\s*[:=]?\s*([0-9]+)") or NOT_AVAILABLE,
                    "distance_function": _first_match(context, r"(L2|Euclidean|cosine|angular|inner product)") or NOT_AVAILABLE,
                    "source": NOT_AVAILABLE,
                    "publicly_available": "Not available",
                    "notes": _compact(context, 350),
                },
            )
    return list(datasets.values())


def extract_metrics(text: str) -> list[str]:
    lowered = text.lower()
    metrics: list[str] = []
    for term in METRIC_TERMS:
        if term in lowered and _canonical_metric(term) not in metrics:
            metrics.append(_canonical_metric(term))
    return metrics


def build_reproduction_card(
    *,
    text: str,
    extraction_status: str,
    text_length: int,
    truncated: bool,
    goal: str = "",
    paper_path: str = "",
) -> str:
    metadata = extract_paper_metadata(text)
    urls = extract_urls(text)
    figures_tables = extract_figures_and_tables(text)
    experiments = extract_experiment_sections(text)
    baselines = extract_baselines(text)
    datasets = extract_datasets(text)
    metrics = extract_metrics(text)
    source_urls = [item for item in urls if item["kind"] in {"source_code", "artifact", "dataset"}]
    return "\n".join(
        [
            _bibliographic_section(metadata, urls, paper_path),
            _problem_section(text, goal),
            _core_idea_section(text),
            _method_section(text),
            _figures_tables_summary(figures_tables),
            _baselines_section(baselines),
            _datasets_section(datasets),
            _metrics_section(metrics),
            _experimental_setup_section(experiments),
            _key_results_section(experiments),
            _resources_section(source_urls),
            _difficulty_section(text, source_urls, datasets, baselines),
            _recommended_plan_section(metrics, datasets, baselines),
            _evidence_quality_section(extraction_status, text_length, truncated, figures_tables),
        ]
    )


def build_figures_tables_report(text: str) -> str:
    items = extract_figures_and_tables(text)
    critical_tables = extract_critical_tables(text)
    lines = [
        "## Extraction Notes",
        "",
        "- Figures/tables are extracted from captions and nearby text only.",
        "- Image internals, plotted curves, and image-only values are not parsed or estimated.",
        "- Reproduction-critical tables are parsed more aggressively when their text is extractable.",
        "- Table structure is best-effort from PDF text extraction; raw text is preserved when structure is ambiguous.",
        "",
        "## Figures",
        "",
    ]
    lines.extend(_caption_report_items(items["figures"], "Figure"))
    lines.extend(["", "## Tables", ""])
    lines.extend(_caption_report_items(items["tables"], "Table"))
    lines.extend(["", "## Critical Tables", ""])
    lines.extend(_critical_table_report_items(critical_tables))
    return "\n".join(lines)


def build_parse_quality_report(text: str) -> str:
    items = extract_figures_and_tables(text)
    critical_tables = extract_critical_tables(text)
    structured = sum(1 for item in critical_tables if item["parse_quality"] == "structured")
    raw = sum(1 for item in critical_tables if item["parse_quality"] == "raw_text_only")
    caption_only = sum(1 for item in critical_tables if item["parse_quality"] == "caption_only")
    lines = [
        "## Summary",
        "",
        f"- Figures detected: {len(items['figures'])}",
        f"- Tables detected: {len(items['tables'])}",
        f"- Reproduction-critical tables detected: {len(critical_tables)}",
        f"- Critical tables structured: {structured}",
        f"- Critical tables raw text only: {raw}",
        f"- Critical tables caption only: {caption_only}",
        "- Figure image internals parsed: no",
        "- Curve/bar values estimated from images: no",
        "",
        "## Policy",
        "",
        "- Complete image parsing is not required for Paper stage.",
        "- Tables that may contain datasets, baselines, parameters, hardware, or metrics must be attempted.",
        "- If a critical table is `caption_only`, Planner must treat exact values as an Evidence Gap.",
        "- If a critical table is `raw_text_only`, Planner may ask Engineer to verify values against artifact scripts or the original paper before using them as acceptance criteria.",
        "",
        "## Critical Table Ledger",
        "",
    ]
    if not critical_tables:
        lines.append("- No reproduction-critical table text was detected.")
    for item in critical_tables:
        lines.extend(
            [
                f"### Table {item['id']}",
                "",
                f"- Parse quality: `{item['parse_quality']}`",
                f"- Why critical: {item['why_critical']}",
                f"- Caption: {item['caption']}",
                "",
                item["extracted_table"],
                "",
            ]
        )
    return "\n".join(lines)


def summarize_structure(text: str) -> dict[str, Any]:
    urls = extract_urls(text)
    items = extract_figures_and_tables(text)
    return {
        "metadata": extract_paper_metadata(text),
        "urls": urls,
        "figures": items["figures"],
        "tables": items["tables"],
        "baselines": extract_baselines(text),
        "datasets": extract_datasets(text),
        "metrics": extract_metrics(text),
        "experiment_sections": extract_experiment_sections(text),
        "source_or_artifact_urls": [item for item in urls if item["kind"] in {"source_code", "artifact", "dataset"}],
    }


def _bibliographic_section(metadata: dict[str, str], urls: list[dict[str, str]], paper_path: str) -> str:
    source_url = _first_url_kind(urls, ("source_code", "artifact"))
    dataset_url = _first_url_kind(urls, ("dataset",))
    paper_url = metadata["paper_url"]
    return f"""## 1. Bibliographic Info
- Title: {metadata['title']}
- Authors: {metadata['authors']}
- Year / Version: {metadata['year_or_version']}
- Venue / Source: {metadata['venue_or_source']}
- arXiv ID / DOI: {metadata['arxiv_id_or_doi']}
- Paper URL: {paper_url}
- Artifact / Source Code URL: {source_url}
- Dataset / Artifact URL: {dataset_url}
- Project / System Name: {_system_name(metadata['title'])}
"""


def _problem_section(text: str, goal: str) -> str:
    abstract = extract_paper_metadata(text).get("abstract", NOT_AVAILABLE)
    return f"""## 2. Problem Setting
- Target problem: {_compact(abstract, 500) or goal or NOT_AVAILABLE}
- Input: {_first_sentence_with(text, ('input', 'dataset', 'vectors', 'graph'))}
- Output: {_first_sentence_with(text, ('output', 'returns', 'result', 'top-k'))}
- Query type: {_first_sentence_with(text, ('query', 'nearest neighbor', 'filter'))}
- System setting: {_first_sentence_with(text, ('system', 'database', 'engine'))}
- Why this problem matters: {_first_sentence_with(text, ('important', 'challenge', 'problem', 'performance'))}
"""


def _core_idea_section(text: str) -> str:
    return f"""## 3. Core Idea
- Main idea: {_first_sentence_with(text, ('main idea', 'we propose', 'we present', 'propose'))}
- Key intuition: {_first_sentence_with(text, ('intuition', 'observation', 'key'))}
- Main algorithm: {_first_sentence_with(text, ('algorithm', 'procedure', 'heuristic'))}
- What is new compared with prior work: {_first_sentence_with(text, ('unlike', 'compared', 'prior work', 'novel'))}
"""


def _method_section(text: str) -> str:
    return f"""## 4. Method / Algorithm Details
- Index type: {_first_sentence_with(text, ('hnsw', 'index', 'graph index'))}
- Search procedure: {_first_sentence_with(text, ('search procedure', 'search', 'traversal'))}
- Filtering strategy: {_first_sentence_with(text, ('filter', 'prefilter', 'postfilter'))}
- Heuristics: {_first_sentence_with(text, ('heuristic', 'blind', 'directed', 'adaptive'))}
- Adaptive decision rule: {_first_sentence_with(text, ('adaptive', 'decision', 'selectivity'))}
- Important parameters: {_first_sentence_with(text, ('parameter', 'ef', 'm=', 'recall target'))}
- Complexity / cost drivers: {_first_sentence_with(text, ('cost', 'complexity', 'distance computation'))}
- Implementation details: {_first_sentence_with(text, ('implementation', 'implemented', 'source code'))}
"""


def _figures_tables_summary(items: dict[str, list[dict[str, str]]]) -> str:
    figure_lines = _brief_caption_lines(items["figures"][:8], "Figure")
    table_lines = _brief_caption_lines(items["tables"][:8], "Table")
    return f"""## 5. Figures and Tables Summary
- Important figures:
{figure_lines}
- Important tables:
{table_lines}
- What each figure/table shows: See PAPER_FIGURES_TABLES.md.
- Which figures/tables matter for reproduction: Dataset, baseline, heuristic, setup, and performance result figures/tables are reproduction-critical when present.
"""


def _baselines_section(baselines: list[dict[str, str]]) -> str:
    lines = ["## 6. Baselines"]
    if not baselines:
        lines.append(_baseline_block())
    else:
        for item in baselines:
            lines.append(_baseline_block(item))
    return "\n".join(lines) + "\n"


def _datasets_section(datasets: list[dict[str, str]]) -> str:
    lines = ["## 7. Datasets"]
    if not datasets:
        lines.append(_dataset_block())
    else:
        for item in datasets:
            lines.append(_dataset_block(item))
    return "\n".join(lines) + "\n"


def _metrics_section(metrics: list[str]) -> str:
    metric_set = {metric.lower(): metric for metric in metrics}
    return f"""## 8. Metrics
- Recall: {_present(metric_set, 'recall')}
- QPS / latency: {_present(metric_set, 'qps') or _present(metric_set, 'latency') or NOT_AVAILABLE}
- Vector search time: {_present(metric_set, 'vector search time')}
- End-to-end time: {_present(metric_set, 'end-to-end time') or _present(metric_set, 'execution time')}
- Index build time: {_present(metric_set, 'index build time')}
- Index size: {_present(metric_set, 'index size')}
- Distance computations: {_present(metric_set, 'distance computations')}
- Other metrics: {', '.join(metrics) if metrics else NOT_AVAILABLE}
"""


def _experimental_setup_section(experiments: dict[str, str]) -> str:
    setup = experiments["experimental_setup"]
    hardware = experiments["hardware"]
    return f"""## 9. Experimental Setup
- Hardware: {hardware}
- CPU / GPU: {_first_match(setup, r'((?:Intel|AMD|NVIDIA|GPU|CPU)[^.\\n]+)') or NOT_AVAILABLE}
- Memory: {_first_match(setup, r'([0-9]+\\s*(?:GB|GiB|TB)\\s*(?:RAM|memory)?)') or NOT_AVAILABLE}
- Threads: {_first_match(setup, r'([0-9]+\\s*threads?)') or NOT_AVAILABLE}
- Dataset scale: {_first_sentence_with(setup, ('dataset', 'vectors', 'scale'))}
- Query workload: {_first_sentence_with(setup, ('query', 'queries', 'workload'))}
- Number of repeats: {_first_sentence_with(setup, ('repeat', 'runs', 'trials'))}
- Warmup: {_first_sentence_with(setup, ('warmup', 'warm-up'))}
- Recall target: {_first_sentence_with(setup, ('recall target', 'recall'))}
- Selectivity levels: {_first_sentence_with(setup, ('selectivity',))}
- Correlation settings: {_first_sentence_with(setup, ('correlation', 'correlated'))}
- Index parameters: {_first_sentence_with(setup, ('index parameter', 'ef', 'm='))}
"""


def _key_results_section(experiments: dict[str, str]) -> str:
    results = experiments["key_results"]
    return f"""## 10. Key Experimental Results
- Main claims: {_first_sentence_with(results, ('outperform', 'speedup', 'improve', 'faster', 'best'))}
- Best-performing method: {_first_sentence_with(results, ('best', 'outperform', 'wins'))}
- Where it wins: {_first_sentence_with(results, ('wins', 'outperform', 'faster'))}
- Where it loses: {_first_sentence_with(results, ('loses', 'worse', 'overhead'))}
- Important numerical results: {_first_sentence_with(results, ('%', 'x ', 'times', 'qps', 'ms'))}
- Important caveats: {_first_sentence_with(results, ('however', 'limitation', 'except', 'caveat'))}
"""


def _resources_section(urls: list[dict[str, str]]) -> str:
    source = _first_url_kind(urls, ("source_code", "artifact"))
    dataset = _first_url_kind(urls, ("dataset",))
    return f"""## 11. Reproduction Resources
- Source code URL: {source}
- Artifact URL: {_first_url_kind(urls, ('artifact',))}
- Dataset URL: {dataset}
- Required dependencies: {NOT_AVAILABLE}
- Expected build system: {_build_hint(urls)}
- Required hardware: {NOT_AVAILABLE}
- Missing resources: {"No explicit source/artifact URL extracted." if source == NOT_AVAILABLE else "Not available"}
"""


def _difficulty_section(text: str, urls: list[dict[str, str]], datasets: list[dict[str, str]], baselines: list[dict[str, str]]) -> str:
    has_resources = bool(urls)
    return f"""## 12. Reproduction Difficulty Assessment
- Easy parts: {"Use extracted source/artifact URLs and smoke tests." if has_resources else "Paper text/context artifact generation."}
- Hard parts: Full-scale benchmarking, exact hardware matching, and figure-level result reproduction.
- Missing information: {_missing_info(datasets, baselines)}
- Risk factors: PDF extraction may miss tables/figures/formulas; source/data availability needs human verification.
- What can be reproduced in MVP: Minimal build/import test and reduced metric-shape run when source/data are available.
- What requires full-scale benchmark: Paper-level performance claims, ablations, and all dataset/baseline comparisons.
"""


def _recommended_plan_section(metrics: list[str], datasets: list[dict[str, str]], baselines: list[dict[str, str]]) -> str:
    dataset_name = datasets[0]["name"] if datasets else NOT_AVAILABLE
    baseline_name = baselines[0]["name"] if baselines else NOT_AVAILABLE
    metric_text = ", ".join(metrics) if metrics else "qps, recall, and runtime shape checks if supported"
    return f"""## 13. Recommended R2A Reproduction Plan
- Minimal smoke test: Verify source/artifact acquisition, build/import, and one tiny query workload.
- Medium-scale reproduction: Run one public or sampled dataset ({dataset_name}) with one baseline ({baseline_name}) and collect {metric_text}.
- Full-scale reproduction: Recreate all reported datasets, baselines, selectivity/correlation settings, and performance figures.
- Strong baselines to implement: {', '.join(item['name'] for item in baselines[:5]) if baselines else NOT_AVAILABLE}
- Metrics to collect: {metric_text}
- Acceptance criteria: Results files include numeric metrics and are labeled smoke/reduced/full.
- What not to overclaim: Do not claim full paper performance reproduction from smoke or reduced runs.
"""


def _evidence_quality_section(extraction_status: str, text_length: int, truncated: bool, figures_tables: dict[str, list[dict[str, str]]]) -> str:
    return f"""## 14. Evidence Quality
- Extraction status: {extraction_status}
- Text coverage: {text_length} characters extracted.
- Tables parsed: {len(figures_tables['tables'])} caption/context entries; best-effort text only.
- Figures parsed: {len(figures_tables['figures'])} caption/context entries; image content is not parsed.
- Known limitations: No OCR, no figure image parsing, limited table reconstruction, possible two-column ordering errors.
- Human verification needed: Yes.
"""


def _caption_report_items(items: list[dict[str, str]], label: str) -> list[str]:
    if not items:
        return [f"- {label}: {NOT_AVAILABLE}"]
    lines: list[str] = []
    for item in items:
        lines.extend(
            [
                f"### {label} {item['id']}",
                "",
                f"- Caption: {item['caption']}",
                f"- Inferred role: {item['inferred_role']}",
                f"- Reproduction relevance: {item['reproduction_relevance']}",
                "- Interpretation: Caption-only interpretation from extracted text; image content is not parsed.",
                "",
                "Nearby context:",
                "",
                "```text",
                item["nearby_context"],
                "```",
                "",
            ]
        )
    return lines


def _critical_table_report_items(items: list[dict[str, str]]) -> list[str]:
    if not items:
        return ["- No reproduction-critical table text was detected."]
    lines: list[str] = []
    for item in items:
        lines.extend(
            [
                f"### Table {item['id']}",
                "",
                f"- Parse quality: `{item['parse_quality']}`",
                f"- Why critical: {item['why_critical']}",
                f"- Caption: {item['caption']}",
                "",
                "Extracted table/text:",
                "",
                item["extracted_table"],
                "",
            ]
        )
    return lines


def _extract_caption_items(text: str, prefix_pattern: str) -> list[dict[str, str]]:
    items: OrderedDict[str, dict[str, str]] = OrderedDict()
    pattern = re.compile(
        prefix_pattern + r"(.{0,500}?)(?=\s+(?:Figure|Fig\.|Table)\s+[0-9]+|References|ACKNOWLEDG|$)",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(text or ""):
        item_id = match.group(1)
        raw_caption = match.group(2)
        caption = _clean_caption(raw_caption)
        if len(caption) < 8:
            continue
        key = item_id
        if key in items:
            continue
        context = _window(text, match.start(), match.end(), 450)
        items[key] = {
            "id": item_id,
            "caption": caption,
            "nearby_context": _compact(context, 900),
            "inferred_role": _infer_role(caption, context),
            "reproduction_relevance": _relevance(caption, context),
        }
    return list(items.values())


def _table_blocks(text: str) -> dict[str, str]:
    lines = (text or "").splitlines()
    blocks: dict[str, str] = {}
    for index, line in enumerate(lines):
        match = re.search(r"\bTable\s*([0-9]+[A-Za-z]?)\s*[:.\-]?\s*", line, re.IGNORECASE)
        if not match:
            continue
        block_lines = [line.strip()]
        for follow in lines[index + 1 : index + 25]:
            stripped = follow.strip()
            if not stripped:
                if len(block_lines) > 3:
                    break
                continue
            if re.match(r"^\s*(?:Figure|Fig\.|Table)\s+[0-9]+", stripped, re.IGNORECASE):
                break
            if _section_like(stripped) and len(block_lines) > 3:
                break
            block_lines.append(stripped)
        blocks.setdefault(match.group(1), "\n".join(block_lines).strip())
    return blocks


def _parse_table_block(block: str) -> str:
    rows: list[list[str]] = []
    for raw_line in (block or "").splitlines():
        line = raw_line.strip().strip("|")
        if not line:
            continue
        if re.match(r"^\s*Table\s+[0-9]+", line, re.IGNORECASE):
            continue
        if "|" in raw_line:
            cells = [cell.strip() for cell in raw_line.strip().strip("|").split("|")]
        else:
            cells = [cell.strip() for cell in re.split(r"\s{2,}|\t+", line) if cell.strip()]
        if len(cells) >= 2:
            rows.append(cells)
    if len(rows) < 2:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    if not any(cell.strip() for cell in header):
        return ""
    body = normalized[1:12]
    markdown = [
        "| " + " | ".join(_escape_table_cell(cell) for cell in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    markdown.extend("| " + " | ".join(_escape_table_cell(cell) for cell in row) + " |" for row in body)
    return "\n".join(markdown)


def _critical_table_reason(combined: str) -> str:
    hits = [term for term in CRITICAL_TABLE_TERMS if term in combined]
    return "contains reproduction-relevant terms: " + ", ".join(hits[:8]) if hits else "caption suggests reproduction relevance"


def _section_like(line: str) -> bool:
    return bool(re.match(r"^(?:[0-9]+(?:\.[0-9]+)*\.?\s+)?[A-Z][A-Za-z /\-]{3,60}$", line))


def _escape_table_cell(text: str) -> str:
    return re.sub(r"\s+", " ", text).replace("|", "\\|").strip()


def _compact_preserve_lines(text: str, limit: int) -> str:
    cleaned = "\n".join(re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines() if line.strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "\n..."


def _clean_caption(raw: str) -> str:
    text = re.split(r"\s+(?:Figure|Fig\.|Table)\s+[0-9]+|References|ACKNOWLEDG", raw, maxsplit=1, flags=re.IGNORECASE)[0]
    text = re.split(r"(?<=[.!?])\s+(?=[A-Z][A-Za-z]{3,}\s)", text, maxsplit=1)[0]
    return _compact(text, 450)


def _infer_role(caption: str, context: str) -> str:
    lowered = f"{caption} {context}".lower()
    if any(term in lowered for term in ("dataset", "vectors", "dimension")):
        return "dataset / workload description"
    if any(term in lowered for term in ("baseline", "heuristic", "algorithm", "method")):
        return "method taxonomy / baseline comparison"
    if any(term in lowered for term in ("qps", "latency", "time", "recall", "performance", "speed")):
        return "key performance result"
    if any(term in lowered for term in ("hnsw", "index", "graph")):
        return "background / index structure"
    return "paper explanation"


def _relevance(caption: str, context: str) -> str:
    lowered = f"{caption} {context}".lower()
    if any(term in lowered for term in ("dataset", "baseline", "metric", "qps", "recall", "latency", "time", "hardware")):
        return "reproduction-critical"
    if any(term in lowered for term in ("algorithm", "heuristic", "method", "index")):
        return "useful for implementation planning"
    return "background context"


def _named_sections(text: str) -> dict[str, str]:
    matches = list(re.finditer(r"(?m)^\s*([0-9]+(?:\.[0-9]+)?\s+)?([A-Z][A-Za-z][A-Za-z /\-]{3,60})\s*$", text or ""))
    sections: dict[str, str] = {}
    for idx, match in enumerate(matches):
        heading = match.group(2).strip().lower()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            sections[heading] = body
    return sections


def _section_by_keywords(sections: dict[str, str], keywords: tuple[str, ...]) -> str:
    chunks = [body for heading, body in sections.items() if any(keyword in heading for keyword in keywords)]
    return "\n\n".join(chunks)


def _table_dataset_candidates(text: str) -> list[dict[str, str]]:
    candidates: OrderedDict[str, dict[str, str]] = OrderedDict()
    for table in extract_figures_and_tables(text)["tables"]:
        if "dataset" not in f"{table['caption']} {table['nearby_context']}".lower():
            continue
        context = table["nearby_context"]
        names = re.findall(r"\b([A-Z][A-Za-z0-9_-]{2,20})\b", context)
        for name in names:
            if name.lower() in {"table", "dataset", "distance", "function", "dimension", "vectors"}:
                continue
            candidates.setdefault(
                name,
                {
                    "name": name,
                    "number_of_vectors": _first_match(context, rf"{re.escape(name)}[^.\n]{{0,120}}?([0-9]+(?:\.[0-9]+)?\s*[KMB]?)") or NOT_AVAILABLE,
                    "dimension": _first_match(context, r"(?:dim(?:ension)?\s*)[:=]?\s*([0-9]+)") or NOT_AVAILABLE,
                    "distance_function": _first_match(context, r"(L2|Euclidean|cosine|angular|inner product)") or NOT_AVAILABLE,
                    "source": "Table caption/context",
                    "publicly_available": "Not available",
                    "notes": _compact(context, 350),
                },
            )
    return list(candidates.values())


def _first_title(lines: list[str]) -> str:
    for line in lines[:20]:
        if len(line) < 8:
            continue
        if re.search(r"abstract|proceedings|arxiv|university|department|@|http", line, re.IGNORECASE):
            continue
        if len(line.split()) >= 3:
            return line
    return NOT_AVAILABLE


def _authors(lines: list[str], title: str) -> str:
    if title in lines:
        idx = lines.index(title)
        candidates = lines[idx + 1 : idx + 5]
    else:
        candidates = lines[1:5]
    authorish = [line for line in candidates if not re.search(r"abstract|http|arxiv|pvl|doi", line, re.IGNORECASE)]
    return "; ".join(authorish[:2]) if authorish else NOT_AVAILABLE


def _venue(text: str) -> str:
    for pattern in (r"\bPVLDB\b[^.\n]*", r"\bVLDB\b[^.\n]*", r"\bSIGMOD\b[^.\n]*", r"\bICDE\b[^.\n]*", r"\bNeurIPS\b[^.\n]*", r"\bICML\b[^.\n]*"):
        found = _first_match(text, pattern)
        if found:
            return found
    return NOT_AVAILABLE


def _abstract(text: str) -> str:
    match = re.search(r"\bAbstract\b\s*(.{100,1800}?)(?:\n\s*(?:1\s+)?Introduction\b|\n\s*Keywords\b)", text, re.IGNORECASE | re.DOTALL)
    return _compact(match.group(1), 1200) if match else NOT_AVAILABLE


def _paper_url(urls: list[dict[str, str]]) -> str:
    return _first_url_kind(urls, ("paper",)) or NOT_AVAILABLE


def _classify_url(url: str, context: str) -> str:
    lowered = f"{url} {context}".lower()
    url_lowered = url.lower()
    if "github" in url_lowered or "gitlab" in url_lowered:
        return "source_code"
    if "dataset" in url_lowered or "data" in url_lowered:
        return "dataset"
    if any(term in lowered for term in ("dataset", "datasets", "data", "benchmark")):
        return "dataset"
    if any(term in lowered for term in ("source code", "github", "gitlab", "code")):
        return "source_code"
    if any(term in lowered for term in ("artifact", "pvlDB artifact".lower(), "reproducibility")):
        return "artifact"
    if any(term in lowered for term in ("arxiv", "doi", "paper")):
        return "paper"
    return "other"


def _first_url_kind(urls: list[dict[str, str]], kinds: tuple[str, ...]) -> str:
    for item in urls:
        if item["kind"] in kinds:
            return item["url"]
    return NOT_AVAILABLE


def _system_name(title: str) -> str:
    if title == NOT_AVAILABLE:
        return NOT_AVAILABLE
    match = re.search(r"\b([A-Z][A-Za-z0-9-]{2,})\b", title)
    return match.group(1) if match else NOT_AVAILABLE


def _baseline_block(item: dict[str, str] | None = None) -> str:
    item = item or {}
    return f"""For each baseline:
- Name: {item.get('name', NOT_AVAILABLE)}
- Type: {item.get('type', NOT_AVAILABLE)}
- Role in comparison: {item.get('role', NOT_AVAILABLE)}
- Implementation availability: {item.get('implementation_availability', NOT_AVAILABLE)}
- Notes: {item.get('notes', NOT_AVAILABLE)}
"""


def _dataset_block(item: dict[str, str] | None = None) -> str:
    item = item or {}
    return f"""For each dataset:
- Name: {item.get('name', NOT_AVAILABLE)}
- Number of vectors: {item.get('number_of_vectors', NOT_AVAILABLE)}
- Dimension: {item.get('dimension', NOT_AVAILABLE)}
- Distance function: {item.get('distance_function', NOT_AVAILABLE)}
- Source: {item.get('source', NOT_AVAILABLE)}
- Whether publicly available: {item.get('publicly_available', NOT_AVAILABLE)}
- Notes: {item.get('notes', NOT_AVAILABLE)}
"""


def _brief_caption_lines(items: list[dict[str, str]], label: str) -> str:
    if not items:
        return f"  - {label}: {NOT_AVAILABLE}"
    return "\n".join(f"  - {label} {item['id']}: {item['caption']} ({item['reproduction_relevance']})" for item in items)


def _baseline_type(term: str) -> str:
    lowered = term.lower()
    if any(db in lowered for db in ("kuzu", "weaviate", "milvus", "pgvector", "vbase")):
        return "system baseline"
    if any(alg in lowered for alg in ("acorn", "diskann", "faiss", "irange")):
        return "algorithm/index baseline"
    return "method baseline"


def _role_from_context(context: str) -> str:
    lowered = context.lower()
    if "compare" in lowered or "baseline" in lowered:
        return "comparison baseline"
    if "ablation" in lowered:
        return "ablation variant"
    return "mentioned method"


def _availability_from_context(context: str) -> str:
    lowered = context.lower()
    if "source" in lowered or "github" in lowered or "artifact" in lowered:
        return "Possible source/artifact mention nearby; verify manually"
    return NOT_AVAILABLE


def _canonical_metric(term: str) -> str:
    lowered = term.lower()
    if lowered == "queries per second":
        return "QPS"
    return {
        "qps": "QPS",
        "p95": "p95 latency",
        "p99": "p99 latency",
    }.get(lowered, term)


def _present(metrics: dict[str, str], key: str) -> str:
    return metrics.get(key.lower(), NOT_AVAILABLE)


def _extract_hardware(text: str) -> str:
    match = re.search(r"((?:Intel|AMD|NVIDIA|CPU|GPU|RAM|memory|server|machine)[^.]{0,220})", text or "", re.IGNORECASE)
    return _compact(match.group(1), 300) if match else NOT_AVAILABLE


def _build_hint(urls: list[dict[str, str]]) -> str:
    joined = " ".join(item["url"].lower() for item in urls)
    if "github" in joined:
        return "Likely git-based project; verify README/build instructions."
    return NOT_AVAILABLE


def _missing_info(datasets: list[dict[str, str]], baselines: list[dict[str, str]]) -> str:
    missing: list[str] = []
    if not datasets:
        missing.append("structured dataset details")
    if not baselines:
        missing.append("baseline implementation details")
    missing.extend(["exact hardware", "full table/figure values"])
    return ", ".join(missing)


def _first_sentence_with(text: str, terms: tuple[str, ...]) -> str:
    if not text or text == NOT_AVAILABLE:
        return NOT_AVAILABLE
    sentences = re.split(r"(?<=[.!?])\s+", _normalize(text))
    for sentence in sentences:
        lowered = sentence.lower()
        if any(term.lower() in lowered for term in terms):
            return _compact(sentence, 350)
    return NOT_AVAILABLE


def _first_match(text: str, pattern: str) -> str:
    match = re.search(pattern, text or "", re.IGNORECASE)
    return _compact(match.group(1) if match.lastindex else match.group(0), 300) if match else ""


def _window(text: str, start: int, end: int, radius: int) -> str:
    return (text or "")[max(0, start - radius) : min(len(text or ""), end + radius)]


def _compact(text: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


def _normalize(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")
