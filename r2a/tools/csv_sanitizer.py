from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import pandas as pd

from r2a.tools.csv_schemas import canonicalize_row, numeric_columns_for_csv, required_columns_for_csv
from r2a.tools.csv_schemas import legacy_value_messages


@dataclass(frozen=True)
class CsvSanitizeIssue:
    file: str
    level: str
    message: str


@dataclass(frozen=True)
class CsvSanitizeResult:
    rows: list[dict[str, str]]
    issues: list[CsvSanitizeIssue]

    @property
    def has_error(self) -> bool:
        return any(issue.level == "error" for issue in self.issues)


def sanitized_csv_rows(path: str | Path) -> CsvSanitizeResult:
    csv_path = Path(path)
    issues: list[CsvSanitizeIssue] = []
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            raw_rows = list(csv.reader(handle))
    except Exception as exc:
        return CsvSanitizeResult(
            [],
            [CsvSanitizeIssue(str(csv_path), "error", f"CSV file unreadable: {type(exc).__name__}: {exc}")],
        )

    if not raw_rows:
        return CsvSanitizeResult([], [CsvSanitizeIssue(str(csv_path), "error", "CSV file has no header row.")])

    header_index = next((index for index, row in enumerate(raw_rows) if _row_has_content(row)), -1)
    if header_index < 0:
        return CsvSanitizeResult([], [CsvSanitizeIssue(str(csv_path), "error", "CSV file has no non-empty rows.")])
    if header_index:
        issues.append(CsvSanitizeIssue(str(csv_path), "warning", f"Skipped {header_index} empty row(s) before header."))

    header = [str(column).strip() for column in raw_rows[header_index]]
    if not any(header):
        return CsvSanitizeResult([], [CsvSanitizeIssue(str(csv_path), "error", "CSV header row is empty.")])

    rows: list[dict[str, str]] = []
    width = len(header)
    required = set(required_columns_for_csv(csv_path))
    numeric_columns = set(numeric_columns_for_csv(csv_path))
    seen_numeric_warning: set[str] = set()

    for line_number, raw_row in enumerate(raw_rows[header_index + 1 :], start=header_index + 2):
        row = [str(value) for value in raw_row]
        if not _row_has_content(row):
            continue
        padded = [*row, *[""] * max(0, width - len(row))]
        if _is_duplicate_header_row(header, padded[:width]):
            issues.append(CsvSanitizeIssue(str(csv_path), "warning", f"Skipped duplicate header row at line {line_number}."))
            continue
        if _is_metadata_or_explanatory_row(csv_path.name, header, row, required):
            issues.append(CsvSanitizeIssue(str(csv_path), "warning", f"Skipped metadata or explanatory row at line {line_number}."))
            continue
        if len(row) != width:
            issues.append(
                CsvSanitizeIssue(
                    str(csv_path),
                    "warning",
                    f"Skipped malformed row at line {line_number}: expected {width} field(s), saw {len(row)}.",
                )
            )
            continue

        mapped = {header[index]: row[index].strip() for index in range(width)}
        for column in tuple(mapped):
            for message in legacy_value_messages(csv_path.name, column, [mapped[column]]):
                issues.append(CsvSanitizeIssue(str(csv_path), "warning", message))
        mapped = {str(key): str(value) for key, value in canonicalize_row(csv_path.name, mapped).items()}
        bad_numeric = [
            column
            for column in numeric_columns
            if column in mapped and not _numeric_cell_ok(mapped.get(column, ""))
        ]
        if bad_numeric:
            for column in bad_numeric:
                if column not in seen_numeric_warning:
                    issues.append(
                        CsvSanitizeIssue(
                            str(csv_path),
                            "warning",
                            f"Column should be numeric (non-fatal); skipped bad row/value: {column}",
                        )
                    )
                    seen_numeric_warning.add(column)
            continue
        rows.append(mapped)

    if required and not required.intersection(header):
        issues.append(
            CsvSanitizeIssue(
                str(csv_path),
                "error",
                "CSV core columns are completely missing: " + ", ".join(sorted(required)),
            )
        )
    if not rows:
        issues.append(CsvSanitizeIssue(str(csv_path), "error", "CSV file has no valid data rows after sanitization."))
    return CsvSanitizeResult(rows, issues)


def sanitized_csv_frame(path: str | Path) -> tuple[pd.DataFrame, list[CsvSanitizeIssue]]:
    result = sanitized_csv_rows(path)
    return pd.DataFrame(result.rows), result.issues


def _row_has_content(row: list[str]) -> bool:
    return any(str(value).strip() for value in row)


def _is_duplicate_header_row(header: list[str], row: list[str]) -> bool:
    normalized_header = [_normalize_cell(column) for column in header]
    normalized_row = [_normalize_cell(value) for value in row[: len(header)]]
    return normalized_header == normalized_row


def _is_metadata_or_explanatory_row(
    filename: str,
    header: list[str],
    row: list[str],
    required_columns: set[str],
) -> bool:
    values = [str(value).strip() for value in row]
    first = values[0].lower() if values else ""
    non_empty = [value for value in values if value]
    if first.startswith(("#", "//")):
        return True
    metadata_markers = (
        "generated",
        "metadata",
        "notes:",
        "note:",
        "status:",
        "summary:",
        "explanation:",
        "this file",
        "csv schema",
    )
    if any(first.startswith(marker) for marker in metadata_markers):
        return True
    if filename.lower() in {"reduced_metrics.csv", "paper_alignment.csv", "input_contract_verification.csv"}:
        normalized_header = {_normalize_cell(column) for column in header}
        normalized_values = {_normalize_cell(value) for value in values if value}
        if len(non_empty) <= 2 and not normalized_values.intersection(normalized_header):
            return True
        if required_columns and not any(str(value).strip() for value in values[: len(header)]):
            return True
    return False


def _numeric_cell_ok(value: object) -> bool:
    text = str(value or "").strip()
    if not text or text.upper() in {"NA", "N/A", "NOT_MEASURED", "NOT_AVAILABLE"}:
        return True
    try:
        float(text)
        return True
    except ValueError:
        return False


def _normalize_cell(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
