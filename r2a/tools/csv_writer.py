from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Mapping


CSV_PARSE_ERROR = "CSV_PARSE_ERROR"


def write_csv_rows(path: str | Path, headers: Iterable[str], rows: Iterable[Mapping[str, object]]) -> None:
    """Write R2A CSV rows with deterministic quoting and UTF-8 encoding."""
    csv_path = Path(path)
    fieldnames = [str(header) for header in headers]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if value is None else str(value) for key, value in row.items()})


def validate_csv_strict(path: str | Path, expected_headers: Iterable[str] | None = None) -> list[str]:
    """Return parse/schema issues that catch malformed rows before pandas sees them."""
    csv_path = Path(path)
    expected = [str(header) for header in expected_headers or ()]
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            rows = list(reader)
    except Exception as exc:
        return [f"{CSV_PARSE_ERROR}: Unable to read CSV: {exc}"]

    if not rows:
        return [f"{CSV_PARSE_ERROR}: CSV file is empty."]

    header = rows[0]
    issues: list[str] = []
    if expected:
        missing = [column for column in expected if column not in header]
        if missing:
            issues.extend(f"Missing required column: {column}" for column in missing)

    width = len(header)
    for line_number, row in enumerate(rows[1:], start=2):
        if len(row) != width:
            issues.append(
                f"{CSV_PARSE_ERROR}: Expected {width} field(s) from header, saw {len(row)} at line {line_number}."
            )
            break
    return issues
