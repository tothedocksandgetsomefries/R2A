from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class PdfTextExtraction:
    text: str
    pages_checked: int
    error: str

    @property
    def ok(self) -> bool:
        return bool(self.text.strip()) and not self.error


@dataclass(frozen=True)
class PdfPageText:
    page_number: int
    text: str


@dataclass(frozen=True)
class PdfStructuredExtraction:
    text: str
    pages: tuple[PdfPageText, ...]
    sections_markdown: str
    captions_markdown: str
    backend: str
    error: str
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.text.strip()) and not self.error

    @property
    def pages_checked(self) -> int:
        return len(self.pages)


def extract_pdf_text(path: str | Path, max_chars: int = 12000) -> PdfTextExtraction:
    pdf_path = Path(path)
    if not pdf_path.exists():
        return PdfTextExtraction("", 0, f"PDF file does not exist: {pdf_path}")
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError:
        return PdfTextExtraction("", 0, "pypdf is not installed; PDF text extraction is unavailable.")

    try:
        reader = PdfReader(str(pdf_path))
        chunks: list[str] = []
        pages_checked = 0
        for page in reader.pages:
            pages_checked += 1
            page_text = page.extract_text() or ""
            if page_text.strip():
                chunks.append(page_text.strip())
            if sum(len(chunk) for chunk in chunks) >= max_chars:
                break
        text = "\n\n".join(chunks).strip()
        if len(text) > max_chars:
            text = text[:max_chars].rstrip()
        return PdfTextExtraction(text, pages_checked, "")
    except Exception as exc:
        return PdfTextExtraction("", 0, f"{type(exc).__name__}: {exc}")


def extract_pdf_text_structured(path: str | Path, max_chars: int = 200000) -> PdfStructuredExtraction:
    pdf_path = Path(path)
    if not pdf_path.exists():
        return PdfStructuredExtraction("", (), "", "", "", f"PDF file does not exist: {pdf_path}")

    extraction = _extract_with_pymupdf(pdf_path)
    if extraction.error:
        fallback = _extract_pages_with_pypdf(pdf_path)
        if fallback.error:
            return PdfStructuredExtraction(
                "",
                (),
                "",
                "",
                "",
                f"{extraction.error}; pypdf fallback failed: {fallback.error}",
            )
        extraction = fallback

    text = extraction.text
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars].rstrip()
        pages = _truncate_pages(extraction.pages, max_chars)
    else:
        pages = extraction.pages
    sections = _build_sections_markdown(pages)
    captions = _build_captions_markdown(pages)
    return PdfStructuredExtraction(
        text=text,
        pages=pages,
        sections_markdown=sections,
        captions_markdown=captions,
        backend=extraction.backend,
        error="",
        truncated=truncated,
    )


def pages_to_markdown(pages: tuple[PdfPageText, ...]) -> str:
    if not pages:
        return "Not available."
    parts: list[str] = []
    for page in pages:
        parts.append(f"### Page {page.page_number}\n\n```text\n{page.text.strip() or 'No extractable text.'}\n```")
    return "\n\n".join(parts)


def _extract_with_pymupdf(pdf_path: Path) -> PdfStructuredExtraction:
    try:
        import fitz  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return PdfStructuredExtraction("", (), "", "", "pymupdf", "PyMuPDF is not installed.")

    try:
        doc = fitz.open(str(pdf_path))
        pages: list[PdfPageText] = []
        for page_index, page in enumerate(doc, start=1):
            page_text = _extract_pymupdf_page_text(page)
            pages.append(PdfPageText(page_index, page_text.strip()))
        text = "\n\n".join(page.text for page in pages if page.text).strip()
        return PdfStructuredExtraction(text, tuple(pages), "", "", "pymupdf", "")
    except Exception as exc:
        return PdfStructuredExtraction("", (), "", "", "pymupdf", f"PyMuPDF {type(exc).__name__}: {exc}")


def _extract_pymupdf_page_text(page) -> str:
    blocks = []
    page_width = float(page.rect.width or 1)
    for block in page.get_text("blocks") or []:
        if len(block) < 5:
            continue
        x0, y0, x1, y1, text = block[:5]
        text = str(text).strip()
        if not text:
            continue
        width = float(x1) - float(x0)
        midpoint = (float(x0) + float(x1)) / 2.0
        is_full_width = width >= page_width * 0.62
        column = 0 if midpoint < page_width / 2.0 else 1
        if is_full_width:
            order_key = (float(y0), -1, float(x0))
        else:
            order_key = (float(y0), column, float(x0))
        blocks.append((order_key, text))
    blocks.sort(key=lambda item: item[0])
    return "\n\n".join(text for _, text in blocks)


def _extract_pages_with_pypdf(pdf_path: Path) -> PdfStructuredExtraction:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError:
        return PdfStructuredExtraction("", (), "", "", "pypdf", "pypdf is not installed; PDF text extraction is unavailable.")

    try:
        reader = PdfReader(str(pdf_path))
        pages: list[PdfPageText] = []
        for page_index, page in enumerate(reader.pages, start=1):
            page_text = (page.extract_text() or "").strip()
            pages.append(PdfPageText(page_index, page_text))
        text = "\n\n".join(page.text for page in pages if page.text).strip()
        return PdfStructuredExtraction(text, tuple(pages), "", "", "pypdf", "")
    except Exception as exc:
        return PdfStructuredExtraction("", (), "", "", "pypdf", f"pypdf {type(exc).__name__}: {exc}")


def _truncate_pages(pages: tuple[PdfPageText, ...], max_chars: int) -> tuple[PdfPageText, ...]:
    kept: list[PdfPageText] = []
    used = 0
    for page in pages:
        if used >= max_chars:
            break
        remaining = max_chars - used
        text = page.text[:remaining].rstrip()
        if text:
            kept.append(PdfPageText(page.page_number, text))
            used += len(text)
    return tuple(kept)


_SECTION_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s+|[IVXLC]+\.\s+)?"
    r"(Abstract|Introduction|Background|Related Work|Method|Methods|Approach|System Design|Design|Implementation|"
    r"Evaluation|Experiments?|Experimental Setup|Datasets?|Baselines?|Metrics?|Results?|Discussion|Limitations?|"
    r"Artifact|Artifact Availability|Reproducibility|Conclusion|Appendix|References)\s*$",
    re.IGNORECASE,
)


def _build_sections_markdown(pages: tuple[PdfPageText, ...]) -> str:
    if not pages:
        return "Not available."
    sections: list[tuple[str, int, list[str]]] = []
    current_title = "Unsectioned Extracted Text"
    current_page = pages[0].page_number
    current_lines: list[str] = []
    for page in pages:
        for raw_line in page.text.splitlines():
            line = raw_line.strip()
            if not line:
                if current_lines and current_lines[-1] != "":
                    current_lines.append("")
                continue
            heading = _section_heading(line)
            if heading:
                if current_lines:
                    sections.append((current_title, current_page, current_lines))
                current_title = heading
                current_page = page.page_number
                current_lines = []
                continue
            current_lines.append(line)
    if current_lines:
        sections.append((current_title, current_page, current_lines))
    if not sections:
        return "Not available."
    parts = []
    for title, page_number, lines in sections:
        body = "\n".join(lines).strip()
        if body:
            parts.append(f"## {title}\n\n_Source page: {page_number}_\n\n{body}")
    return "\n\n".join(parts) if parts else "Not available."


def _section_heading(line: str) -> str:
    normalized = re.sub(r"\s+", " ", line).strip()
    if len(normalized) > 80:
        return ""
    match = _SECTION_RE.match(normalized)
    if not match:
        return ""
    return normalized


_CAPTION_START_RE = re.compile(r"^\s*(?:Fig\.?|Figure|Table)\s*[0-9IVXLC]+[a-zA-Z]?\s*[:.\-]", re.IGNORECASE)


def _build_captions_markdown(pages: tuple[PdfPageText, ...]) -> str:
    captions: list[str] = []
    for page in pages:
        lines = [line.strip() for line in page.text.splitlines()]
        for index, line in enumerate(lines):
            if not _CAPTION_START_RE.match(line):
                continue
            continuation = [line]
            for follow in lines[index + 1 : index + 4]:
                if not follow or _CAPTION_START_RE.match(follow) or _section_heading(follow):
                    break
                continuation.append(follow)
            captions.append(f"- Page {page.page_number}: {' '.join(continuation)}")
    return "\n".join(captions) if captions else "Not available."
