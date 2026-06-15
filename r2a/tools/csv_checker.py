from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from r2a.tools.csv_schemas import (
    canonicalize_cell_value,
    enum_constraints_for_csv,
    legacy_alias_messages,
    legacy_value_messages,
    numeric_columns_for_csv,
    required_columns_for_csv,
    schema_for_csv,
)
from r2a.tools.csv_sanitizer import sanitized_csv_frame
from r2a.tools.csv_writer import validate_csv_strict


@dataclass(frozen=True)
class CsvCheckIssue:
    file: str
    level: str
    message: str


@dataclass(frozen=True)
class CsvCheckReport:
    checked_files: list[str]
    issues: list[CsvCheckIssue]

    @property
    def passed(self) -> bool:
        return not any(issue.level == "error" for issue in self.issues)


def check_csv_file(
    path: str | Path,
    required_columns: tuple[str, ...] | None = None,
    numeric_columns: tuple[str, ...] | None = None,
    strict_schema: bool = False,
) -> list[CsvCheckIssue]:
    """Check CSV file with lenient schema handling.

    Args:
        path: CSV file path
        required_columns: Required columns (None = use schema)
        numeric_columns: Numeric columns (None = use schema)
        strict_schema: If True, missing columns are errors; if False, warnings

    Returns:
        List of CsvCheckIssue objects
    """
    csv_path = Path(path)
    schema = schema_for_csv(csv_path)
    schema_numeric: tuple[str, ...] = numeric_columns_for_csv(csv_path)
    if required_columns is None or numeric_columns is None:
        schema_required = required_columns_for_csv(csv_path)
        required_columns = schema_required if required_columns is None else required_columns
        numeric_columns = schema_numeric if numeric_columns is None else numeric_columns
    issues: list[CsvCheckIssue] = []
    if schema and schema.legacy_aliases:
        for message in legacy_alias_messages(csv_path, _first_csv_header(csv_path)):
            issues.append(CsvCheckIssue(str(csv_path), "warning", message))
    strict_issues = validate_csv_strict(csv_path)
    if strict_issues:
        # Malformed CSV is a warning, not fatal - we can still try to extract facts
        for issue in strict_issues:
            issues.append(CsvCheckIssue(str(csv_path), "warning", f"CSV parse issue (non-fatal): {issue}"))
    frame, sanitize_issues = sanitized_csv_frame(csv_path)
    issues.extend(CsvCheckIssue(issue.file, issue.level, issue.message) for issue in sanitize_issues)

    if schema and schema.legacy_aliases:
        for canonical, aliases in schema.legacy_aliases.items():
            if canonical in frame.columns:
                continue
            for alias in aliases:
                if alias in frame.columns:
                    frame[canonical] = frame[alias]
                    break

    for column, allowed_values in enum_constraints_for_csv(csv_path).items():
        if column not in frame.columns:
            continue
        raw_values = list(frame[column])
        for message in legacy_value_messages(csv_path, column, raw_values):
            issues.append(CsvCheckIssue(str(csv_path), "warning", message))
        normalized = frame[column].astype(str).map(lambda value: canonicalize_cell_value(csv_path, column, value))
        invalid = sorted({value for value in normalized if value and value not in set(allowed_values)})
        if invalid:
            issues.append(CsvCheckIssue(str(csv_path), "warning", f"Invalid {column} value(s) (non-fatal): {', '.join(invalid)}"))
        frame[column] = normalized

    for column in required_columns:
        if column not in frame.columns:
            # Missing columns are warnings, not errors - we can still extract partial facts
            issues.append(CsvCheckIssue(str(csv_path), "warning", f"Missing required column (non-fatal): {column}"))

    for column in numeric_columns:
        if column in frame.columns:
            series = frame[column]
            allowed_na_tokens = _allowed_numeric_na_tokens(csv_path) if column in schema_numeric else set()
            if allowed_na_tokens:
                normalized = series.astype(str).str.strip().str.upper()
                series = series[~normalized.isin(allowed_na_tokens)]
            values = pd.to_numeric(series, errors="coerce")
            if values.isna().any():
                issues.append(CsvCheckIssue(str(csv_path), "warning", f"Column should be numeric (non-fatal): {column}"))

    issues.extend(_semantic_csv_issues(csv_path, frame))
    issues.extend(_command_manifest_recommended_field_warnings(csv_path, frame))

    if frame.empty:
        issues.append(CsvCheckIssue(str(csv_path), "error", "CSV file has no valid data rows."))
    return issues


def _metadata_row_schema_warnings(path: Path) -> list[CsvCheckIssue]:
    if path.name.lower() != "paper_alignment.csv" or not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    issues: list[CsvCheckIssue] = []
    header = lines[0].strip().lower()
    expected = "paper_item,setting_name,paper_setting,reduced_setting,match_status,evidence_source,notes"
    if header != expected and not all(column in header for column in ("paper_item", "setting_name", "match_status")):
        issues.append(CsvCheckIssue(str(path), "warning", "paper_alignment.csv schema warning: first non-empty row is not the canonical CSV header."))
    try:
        import csv

        rows = list(csv.reader(lines))
    except Exception:
        return issues
    if not rows:
        return issues
    width = len(rows[0])
    metadata_markers = ("generated", "metadata", "notes:", "status:", "#")
    for row_number, row in enumerate(rows[1:], start=2):
        first = str(row[0]).strip().lower() if row else ""
        if len(row) != width or any(first.startswith(marker) for marker in metadata_markers):
            issues.append(CsvCheckIssue(str(path), "warning", f"paper_alignment.csv schema warning: non-CSV metadata or malformed row detected at line {row_number}."))
            break
    return issues


def _first_csv_header(path: Path) -> list[str]:
    try:
        import csv

        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.reader(handle):
                if any(str(value).strip() for value in row):
                    return [str(value).strip() for value in row]
    except Exception:
        return []
    return []


def check_csv_tree(
    root: str | Path,
    required_columns: tuple[str, ...] | None = None,
    numeric_columns: tuple[str, ...] | None = None,
) -> CsvCheckReport:
    root_path = Path(root)
    files = sorted(
        path
        for path in root_path.rglob("*.csv")
        if ".r2a" not in path.parts and ".git" not in path.parts
    )
    issues: list[CsvCheckIssue] = []
    for path in files:
        issues.extend(check_csv_file(path, required_columns, numeric_columns))
    issues.extend(_cross_csv_issues(files))
    return CsvCheckReport([str(path) for path in files], issues)


def check_csv_files(files: list[str | Path]) -> CsvCheckReport:
    paths = [Path(path) for path in files]
    issues: list[CsvCheckIssue] = []
    for path in paths:
        issues.extend(check_csv_file(path))
    issues.extend(_cross_csv_issues(paths))
    return CsvCheckReport([str(path) for path in paths], issues)


def _schema_for_csv(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return required_columns_for_csv(path), numeric_columns_for_csv(path)


def _allowed_numeric_na_tokens(path: Path) -> set[str]:
    operational_csvs = {
        "project_tests.csv",
        "project_health.csv",
        "command_manifest.csv",
        "build_smoke.csv",
        "runtime_smoke.csv",
        "docker_build.csv",
        "docker_runtime_smoke.csv",
    }
    if path.name.lower() in operational_csvs:
        return {"", "NA", "N/A"}
    return set()


def _semantic_csv_issues(path: Path, frame: pd.DataFrame) -> list[CsvCheckIssue]:
    if path.name.lower() == "input_contract_verification.csv":
        return _input_contract_semantic_issues(path, frame)
    if path.name.lower() != "paper_alignment.csv":
        return []
    required_non_empty = ("paper_item", "setting_name", "evidence_source")
    issues: list[CsvCheckIssue] = []
    if "match_status" in frame.columns:
        normalized = frame["match_status"].astype(str).str.strip()
        if not normalized.isin({"MATCH", "PARTIAL_MATCH"}).any():
            issues.append(CsvCheckIssue(str(path), "warning", "paper_alignment.csv has no MATCH or PARTIAL_MATCH rows; it cannot support achieved L4 by itself."))
    for column in required_non_empty:
        if column not in frame.columns:
            continue
        empty_rows = frame.index[frame[column].astype(str).str.strip() == ""].tolist()
        if empty_rows:
            rows = ", ".join(str(index + 2) for index in empty_rows[:5])
            issues.append(CsvCheckIssue(str(path), "warning", f"Required L4 alignment field is empty (non-fatal): {column} at row(s) {rows}"))
    return issues


def _command_manifest_recommended_field_warnings(path: Path, frame: pd.DataFrame) -> list[CsvCheckIssue]:
    if path.name.lower() != "command_manifest.csv" or frame.empty:
        return []
    recommended = (
        "cwd",
        "command",
        "start_time",
        "end_time",
        "returncode",
        "stdout_path",
        "stderr_path",
        "observed_outputs",
        "declared_outputs",
        "artifact_hash",
        "network_used",
        "stage",
        "iteration",
    )
    missing = [column for column in recommended if column not in frame.columns or not frame[column].astype(str).str.strip().any()]
    if not missing:
        return []
    return [
        CsvCheckIssue(
            str(path),
            "warning",
            "command_manifest.csv recommended field missing (warning only): " + ", ".join(missing),
        )
    ]


def _input_contract_semantic_issues(path: Path, frame: pd.DataFrame) -> list[CsvCheckIssue]:
    issues: list[CsvCheckIssue] = []
    if "status" in frame.columns:
        statuses = frame["status"].astype(str).str.strip().str.upper()
        invalid = {"EMPTY_PLACEHOLDER_INPUT", "FORMAT_INVALID", "SIZE_INCONSISTENT"}
        if statuses.isin(invalid).any():
            issues.append(
                CsvCheckIssue(
                    str(path),
                    "error",
                    "Official input integrity failure recorded in input_contract_verification.csv; this blocks official_reduced/L3.",
                )
            )
    notes_blob = " ".join(str(value) for value in frame.astype(str).to_numpy().flatten()).upper()
    if "SIZE_BYTES=0" in notes_blob or "EMPTY_PLACEHOLDER_INPUT" in notes_blob:
        issues.append(
            CsvCheckIssue(
                str(path),
                "error",
                "Empty placeholder input detected (size_bytes=0); official input is not ready.",
            )
        )
    return issues


def _cross_csv_issues(files: list[Path]) -> list[CsvCheckIssue]:
    by_name: dict[str, list[Path]] = {}
    for path in files:
        by_name.setdefault(path.name.lower(), []).append(path)
    reduced_paths = by_name.get("reduced_metrics.csv", [])
    manifest_paths = by_name.get("command_manifest.csv", [])
    if not reduced_paths:
        return []

    issues: list[CsvCheckIssue] = []
    manifest_ids: set[str] = set()
    for path in manifest_paths:
        try:
            frame, _ = sanitized_csv_frame(path)
        except Exception:
            continue
        if "command_id" in frame.columns:
            manifest_ids.update(str(value).strip() for value in frame["command_id"] if str(value).strip())

    for path in reduced_paths:
        try:
            frame, _ = sanitized_csv_frame(path)
        except Exception:
            continue
        if "command_id" not in frame.columns:
            continue
        for row_index, command_id in enumerate(frame["command_id"], start=2):
            value = str(command_id).strip()
            if not value:
                continue
            if value not in manifest_ids:
                issues.append(CsvCheckIssue(str(path), "error", f"command_id `{value}` is not present in command_manifest.csv at row {row_index}"))
    return issues
