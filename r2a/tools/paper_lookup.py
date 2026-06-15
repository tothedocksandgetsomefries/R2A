from __future__ import annotations

from pathlib import Path
import re

from r2a.core.paths import report_path, resolve_repo_path

PLACEHOLDER_PATTERNS = (
    "not available",
    "not available in mvp",
    "not implemented",
    "not implemented in mvp",
    "missing",
    "tbd",
    "unknown",
    "placeholder",
    "no paper text was parsed",
    "pdf parsing is not implemented in mvp",
    "evidence gap",
)


def paper_lookup(repo_path: str, query: str, max_snippets: int = 5) -> dict:
    repo = resolve_repo_path(repo_path)
    sources = [
        report_path(repo, "paper_analysis"),
        report_path(repo, "paper_reproduction_card"),
        report_path(repo, "paper_figures_tables"),
        report_path(repo, "paper_parse_quality"),
        report_path(repo, "paper_context"),
        report_path(repo, "paper_evidence"),
        report_path(repo, "paper"),
        report_path(repo, "paper_sections"),
        report_path(repo, "paper_captions"),
        report_path(repo, "paper_text"),
        report_path(repo, "paper_pages"),
    ]
    available = [path for path in sources if path.exists()]
    if not available:
        return {
            "query": query,
            "found": False,
            "snippets": [],
            "sources": [],
            "limitations": "Paper evidence not available. MVP keyword lookup only.",
            "evidence_quality": "missing",
        }

    query_terms = _query_terms(query)
    snippets: list[str] = []
    source_names: list[str] = []
    placeholder_sources: list[str] = []
    for path in available:
        for heading, body in _markdown_sections(path):
            if not _matches_query(heading, body, query_terms):
                continue
            if _is_placeholder_evidence(body):
                if path.name not in placeholder_sources:
                    placeholder_sources.append(path.name)
                continue
            snippet = f"{heading}\n{body.strip()}" if heading else body.strip()
            if snippet:
                snippets.append(snippet)
                if path.name not in source_names:
                    source_names.append(path.name)
            if len(snippets) >= max_snippets:
                break
        if len(snippets) >= max_snippets:
            break
    if snippets:
        return {
            "query": query,
            "found": True,
            "snippets": snippets,
            "sources": source_names,
            "limitations": "MVP keyword lookup only",
            "evidence_quality": "usable",
        }

    limitations = "MVP keyword lookup only"
    evidence_quality = "missing"
    sources_out: list[str] = []
    if placeholder_sources:
        limitations = "Matched section exists but contains placeholder/missing evidence. MVP keyword lookup only."
        evidence_quality = "placeholder"
        sources_out = placeholder_sources
    return {
        "query": query,
        "found": False,
        "snippets": [],
        "sources": sources_out,
        "limitations": limitations,
        "evidence_quality": evidence_quality,
    }


def _query_terms(query: str) -> list[str]:
    terms = [term.lower() for term in re.findall(r"[A-Za-z0-9_/-]+", query)]
    return terms or [query.lower()]


def _matches_query(heading: str, body: str, query_terms: list[str]) -> bool:
    heading_text = heading.lower()
    body_text = body.lower()
    expanded: list[str] = []
    for term in query_terms:
        expanded.append(term)
        if term.endswith("s") and len(term) > 3:
            expanded.append(term[:-1])
        else:
            expanded.append(f"{term}s")
    return any(term in heading_text or term in body_text for term in expanded)


def _is_placeholder_evidence(body: str) -> bool:
    normalized = re.sub(r"\s+", " ", body.strip().lower())
    if not normalized:
        return True
    if any(pattern in normalized for pattern in PLACEHOLDER_PATTERNS):
        return True
    return False


def _markdown_sections(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    sections: list[tuple[str, str]] = []
    current_heading = ""
    body_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            if current_heading or body_lines:
                sections.append((current_heading, "\n".join(body_lines).strip()))
            current_heading = line.strip()
            body_lines = []
            continue
        body_lines.append(line)
    if current_heading or body_lines:
        sections.append((current_heading, "\n".join(body_lines).strip()))
    return sections
