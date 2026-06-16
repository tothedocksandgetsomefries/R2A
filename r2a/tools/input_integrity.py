from __future__ import annotations

import csv
import json
import struct
from pathlib import Path
from typing import Any, Iterable

from r2a.core.paths import artifact_dir

ANN_FORMATS = {".fvecs", ".ivecs", ".bvecs"}
INVALID_INPUT_STATUSES = {
    "MISSING",
    "BLOCKED",
    "NEEDS_INPUT",
    "NEEDS_OFFICIAL_INPUT",
    "NEEDS_INPUT_OR_BUDGET",
    "NOT_AVAILABLE",
    "EMPTY_PLACEHOLDER_INPUT",
    "FORMAT_INVALID",
    "SIZE_INCONSISTENT",
}
SEVERE_INPUT_STATUSES = {
    "EMPTY_PLACEHOLDER_INPUT",
    "FORMAT_INVALID",
    "SIZE_INCONSISTENT",
}
TRUTHY_MARKERS = {"1", "true", "yes", "y", "required", "target_required", "core", "p0", "must"}
FALSEY_MARKERS = {"", "0", "false", "no", "n", "optional", "not_required", "none", "na", "n/a"}
REQUIRED_SCOPE_MARKERS = {
    "required",
    "target_required",
    "required_for_target",
    "core",
    "target",
    "reduced",
    "official_reduced",
    "l3",
    "l4",
    "p0",
    "p1",
}
OPTIONAL_SCOPE_MARKERS = {
    "optional",
    "out_of_scope",
    "out of scope",
    "extra",
    "extra_dataset",
    "comparison",
    "optional_comparison",
    "full",
    "full_benchmark",
    "near_full",
    "l5",
    "l6",
    "p2",
    "p3",
}
OPTIONAL_DATASET_MARKERS = {
    "tripclick",
    "laion",
    "laion_1m",
    "laion-1m",
    "paper dataset 2m",
    "paper reference dataset",
    "paper_level_dataset",
    "paper level dataset",
    "figure reference dataset",
    "table reference dataset",
    "internal corpus",
    "not publicly available",
    "may not be publicly available",
    "2m 200d",
    "full scale",
    "full_scale",
    "full benchmark",
    "optional comparison",
    "extra dataset",
    "out of scope",
    "out_of_scope",
    "lcps benchmark",
    "hcps benchmark",
}
GENERATED_SOURCE_PATH_MARKERS = {
    "build",
    "build2",
    "cmake_build",
    "cmake-build",
    "tmp",
    "temp",
    "cache",
    "demos",
}
INPUT_ROLE_MARKERS = {
    "dataset": ("dataset", "database", "base", "vectors", "data"),
    "query": ("query",),
    "ground_truth": ("ground_truth", "ground truth", "groundtruth", "truth", "gt"),
}


def validate_input_file(path: str | Path, kind: str | None = None) -> dict[str, Any]:
    input_path = Path(path)
    suffix = (kind or input_path.suffix.lstrip(".")).lower().strip(".")
    fmt = f".{suffix}" if suffix else input_path.suffix.lower()
    result: dict[str, Any] = {
        "path": str(input_path),
        "exists": input_path.exists(),
        "size_bytes": 0,
        "format": fmt.lstrip(".") if fmt else "",
        "is_nonempty": False,
        "is_parseable": False,
        "record_count_estimate": None,
        "dimension": None,
        "integrity_status": "MISSING",
        "max_evidence_level_allowed": "L2_input_contract_ready",
        "notes": "",
    }
    if not input_path.exists():
        result["notes"] = "input file is missing"
        return result
    try:
        size = input_path.stat().st_size
    except OSError as exc:
        result["integrity_status"] = "FORMAT_INVALID"
        result["notes"] = f"cannot stat input file: {exc}"
        return result
    result["size_bytes"] = size
    result["is_nonempty"] = size > 0
    if size == 0:
        result["integrity_status"] = "EMPTY_PLACEHOLDER_INPUT"
        result["notes"] = "size_bytes=0; empty placeholder input cannot support official_reduced"
        return result

    if fmt in ANN_FORMATS:
        return _validate_ann_vector_file(input_path, fmt, result)
    if fmt == ".json":
        return _validate_json_file(input_path, result)
    if fmt == ".jsonl":
        return _validate_jsonl_file(input_path, result)
    if fmt == ".csv":
        return _validate_csv_file(input_path, result)

    result["integrity_status"] = "UNKNOWN_FORMAT_WARNING"
    result["notes"] = "unknown input format; do not use as required official input without additional verification"
    return result


def validate_input_contract(paths: Iterable[str | Path | dict[str, Any]]) -> dict[str, Any]:
    validations = []
    for item in paths:
        if isinstance(item, dict):
            path = item.get("path") or item.get("path_or_command") or item.get("file") or ""
            kind = item.get("kind") or item.get("format")
        else:
            path = item
            kind = None
        if not path:
            continue
        validations.append(validate_input_file(path, str(kind) if kind else None))
    invalid = [
        item
        for item in validations
        if item["integrity_status"] != "OK"
    ]
    return {
        "input_contract_integrity_status": "OK" if validations and not invalid else "NEEDS_OFFICIAL_INPUT",
        "all_required_inputs_ok": bool(validations) and not invalid,
        "missing_or_invalid_inputs": invalid,
        "validations": validations,
        "max_evidence_level_allowed": "L3_official_reduced_run" if validations and not invalid else "L2_input_contract_ready",
        "recommended_status": "OK" if validations and not invalid else "NEEDS_OFFICIAL_INPUT",
    }


def summarize_official_input_integrity(repo_path: str | Path) -> dict[str, Any]:
    repo = Path(repo_path)
    rows = _input_contract_rows(repo)
    row_blockers = _row_integrity_blockers(rows)
    row_warnings = _row_integrity_warnings(rows)
    candidate_files = _candidate_input_files(repo)
    file_validations = [validate_input_file(path) for path in candidate_files]
    file_blockers = [
        item
        for item in file_validations
        if item["integrity_status"] in SEVERE_INPUT_STATUSES
        and _is_target_required_candidate_path(Path(item["path"]), rows)
    ]
    valid_roles = _valid_input_roles(rows, file_validations)
    missing_roles = sorted({"dataset", "query", "ground_truth"} - valid_roles)
    role_blockers = _missing_role_blockers(missing_roles, [*row_blockers, *file_blockers])
    blockers = [*row_blockers, *file_blockers, *role_blockers]
    return {
        "input_contract_integrity_status": "NEEDS_OFFICIAL_INPUT" if blockers else "OK",
        "has_blocking_issue": bool(blockers),
        "all_required_inputs_ok": not blockers and not missing_roles and bool(rows or file_validations),
        "missing_or_invalid_inputs": blockers,
        "warnings": row_warnings,
        "diagnostics": {
            "optional_missing_or_invalid_inputs": row_warnings,
            "candidate_file_count": len(file_validations),
        },
        "valid_input_roles": sorted(valid_roles),
        "missing_required_roles": missing_roles,
        "max_evidence_level_allowed": "L2_input_contract_ready" if blockers else "L3_official_reduced_run",
        "recommended_status": "NEEDS_OFFICIAL_INPUT" if blockers else "OK",
        "summary_lines": _summary_lines(blockers, missing_roles, row_warnings),
    }


def input_integrity_blocks_l3(repo_path: str | Path) -> bool:
    return bool(summarize_official_input_integrity(repo_path)["has_blocking_issue"])


def rows_have_input_integrity_blocker(rows: list[dict[str, str]]) -> bool:
    return bool(_row_integrity_blockers(rows))


def _validate_ann_vector_file(path: Path, fmt: str, result: dict[str, Any]) -> dict[str, Any]:
    """Validate ANN vector file (.fvecs, .ivecs, .bvecs) by reading all records.

    Supports both fixed-length and variable-length files.
    Variable-length is valid for ground truth files where k varies per query.
    """
    bytes_per_value = 1 if fmt == ".bvecs" else 4

    # Early size check
    if result["size_bytes"] < 4:
        result["integrity_status"] = "SIZE_INCONSISTENT"
        result["notes"] = "file too small to contain one ANN vector record header"
        return result

    # Parse all records
    parse_result = _parse_ann_vector_records(path, bytes_per_value)

    if parse_result["error"]:
        result["integrity_status"] = parse_result["error_status"]
        result["notes"] = parse_result["error"]
        return result

    # Success
    result["dimension"] = parse_result["first_dimension"]
    result["record_count_estimate"] = parse_result["record_count"]
    result["is_parseable"] = True
    result["integrity_status"] = "OK"
    result["max_evidence_level_allowed"] = "L3_official_reduced_run"

    # Add variable-length metadata
    result["min_dimension"] = parse_result["min_dimension"]
    result["max_dimension"] = parse_result["max_dimension"]
    result["is_variable_length"] = parse_result["is_variable_length"]
    result["unique_dimension_count"] = parse_result["unique_dimension_count"]

    if parse_result["is_variable_length"]:
        result["notes"] = f"Variable-length {fmt} file: {parse_result['record_count']} records, dimension range [{parse_result['min_dimension']}, {parse_result['max_dimension']}]"
    else:
        result["notes"] = f"Fixed-length {fmt} file: {parse_result['record_count']} records, dimension={parse_result['first_dimension']}"

    return result


def _parse_ann_vector_records(path: Path, bytes_per_value: int) -> dict[str, Any]:
    """Parse all records in an ANN vector file.

    Returns a dict with:
    - record_count: number of records
    - first_dimension: dimension of first record
    - min_dimension: minimum dimension across all records
    - max_dimension: maximum dimension across all records
    - unique_dimension_count: number of unique dimensions
    - is_variable_length: True if dimensions vary
    - error: error message if parsing failed, None otherwise
    - error_status: SIZE_INCONSISTENT or FORMAT_INVALID if error
    """
    result = {
        "record_count": 0,
        "first_dimension": None,
        "min_dimension": None,
        "max_dimension": None,
        "unique_dimensions": set(),
        "unique_dimension_count": 0,
        "is_variable_length": False,
        "error": None,
        "error_status": None,
    }

    try:
        with path.open("rb") as handle:
            dims = []

            while True:
                # Read dimension header (4 bytes, little-endian int32)
                dim_bytes = handle.read(4)

                if not dim_bytes:
                    # Clean EOF
                    break

                if len(dim_bytes) < 4:
                    result["error"] = f"incomplete dimension header (only {len(dim_bytes)} bytes) at record {result['record_count'] + 1}"
                    result["error_status"] = "SIZE_INCONSISTENT"
                    return result

                (dimension,) = struct.unpack("<i", dim_bytes)

                # Validate dimension
                if dimension <= 0:
                    result["error"] = f"invalid dimension={dimension} (must be > 0) at record {result['record_count'] + 1}"
                    result["error_status"] = "FORMAT_INVALID"
                    return result

                if dimension >= 1_000_000:
                    result["error"] = f"dimension={dimension} exceeds maximum (1,000,000) at record {result['record_count'] + 1}"
                    result["error_status"] = "FORMAT_INVALID"
                    return result

                # Read data
                data_size = dimension * bytes_per_value
                data_bytes = handle.read(data_size)

                if len(data_bytes) < data_size:
                    result["error"] = f"incomplete data for dimension={dimension} at record {result['record_count'] + 1}: expected {data_size} bytes, got {len(data_bytes)}"
                    result["error_status"] = "SIZE_INCONSISTENT"
                    return result

                dims.append(dimension)
                result["record_count"] += 1

                if result["record_count"] == 1:
                    result["first_dimension"] = dimension

            # Check for trailing data
            trailing = handle.read(1)
            if trailing:
                result["error"] = f"trailing data after {result['record_count']} complete records"
                result["error_status"] = "SIZE_INCONSISTENT"
                return result

            # Compute dimension statistics
            if dims:
                result["min_dimension"] = min(dims)
                result["max_dimension"] = max(dims)
                result["unique_dimensions"] = set(dims)
                result["unique_dimension_count"] = len(set(dims))
                result["is_variable_length"] = len(set(dims)) > 1

            return result

    except Exception as exc:
        result["error"] = f"exception while parsing: {exc}"
        result["error_status"] = "FORMAT_INVALID"
        return result


def _validate_json_file(path: Path, result: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        result["integrity_status"] = "FORMAT_INVALID"
        result["notes"] = f"invalid JSON: {exc}"
        return result
    if data == [] or data == {}:
        result["integrity_status"] = "EMPTY_PLACEHOLDER_INPUT"
        result["notes"] = "JSON input is empty"
        return result
    result["is_parseable"] = True
    result["record_count_estimate"] = len(data) if isinstance(data, (list, dict)) else None
    result["integrity_status"] = "OK"
    result["max_evidence_level_allowed"] = "L3_official_reduced_run"
    result["notes"] = "JSON input is parseable and non-empty"
    return result


def _validate_jsonl_file(path: Path, result: dict[str, Any]) -> dict[str, Any]:
    count = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                json.loads(line)
                count += 1
    except Exception as exc:
        result["integrity_status"] = "FORMAT_INVALID"
        result["notes"] = f"invalid JSONL: {exc}"
        return result
    if count == 0:
        result["integrity_status"] = "EMPTY_PLACEHOLDER_INPUT"
        result["notes"] = "JSONL input has zero non-empty rows"
        return result
    result["is_parseable"] = True
    result["record_count_estimate"] = count
    result["integrity_status"] = "OK"
    result["max_evidence_level_allowed"] = "L3_official_reduced_run"
    result["notes"] = "JSONL input is parseable and non-empty"
    return result


def _validate_csv_file(path: Path, result: dict[str, Any]) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = [row for row in csv.reader(handle)]
    except Exception as exc:
        result["integrity_status"] = "FORMAT_INVALID"
        result["notes"] = f"invalid CSV: {exc}"
        return result
    nonempty = [row for row in rows if any(str(cell).strip() for cell in row)]
    if not nonempty:
        result["integrity_status"] = "EMPTY_PLACEHOLDER_INPUT"
        result["notes"] = "CSV input has no non-empty rows"
        return result
    result["is_parseable"] = True
    result["record_count_estimate"] = max(len(nonempty) - 1, 0)
    result["integrity_status"] = "OK"
    result["max_evidence_level_allowed"] = "L3_official_reduced_run"
    result["notes"] = "CSV input is parseable and non-empty"
    return result


def _input_contract_rows(repo: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for directory in (artifact_dir(repo) / "results", repo / "results"):
        path = directory / "input_contract_verification.csv"
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                rows.extend(dict(row) for row in csv.DictReader(handle))
        except Exception:
            continue
    return rows


def _row_integrity_blockers(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        finding
        for finding in _row_integrity_findings(rows)
        if finding.get("severity") == "blocker"
    ]


def _row_integrity_warnings(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        finding
        for finding in _row_integrity_findings(rows)
        if finding.get("severity") == "warning"
    ]


def _row_integrity_findings(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for row in rows:
        text = " ".join(str(value) for value in row.values())
        status = str(row.get("status", "")).strip().upper()
        component = str(row.get("component", "")).strip()
        if not _is_required_input_component(component):
            continue
        if _row_has_integrity_problem(row):
            target_required = _row_is_target_required(row)
            severity = "blocker" if target_required else "warning"
            blockers.append(
                {
                    "path": row.get("path_or_command", ""),
                    "component": component,
                    "status": status or "UNKNOWN",
                    "integrity_status": status if status in INVALID_INPUT_STATUSES else "FORMAT_INVALID",
                    "notes": row.get("notes", ""),
                    "max_evidence_level_allowed": "L2_input_contract_ready",
                    "severity": severity,
                    "target_required": target_required,
                }
            )
    return blockers


def _row_has_integrity_problem(row: dict[str, str]) -> bool:
    text = " ".join(str(value) for value in row.values()).upper()
    status = str(row.get("status", "")).strip().upper()
    return (
        status in INVALID_INPUT_STATUSES
        or "EMPTY_PLACEHOLDER_INPUT" in text
        or "SIZE_BYTES=0" in text
        or "INTEGRITY_STATUS=FORMAT_INVALID" in text
        or "INTEGRITY_STATUS=SIZE_INCONSISTENT" in text
    )


def _candidate_input_files(repo: Path) -> list[Path]:
    roots = [artifact_dir(repo) / "artifacts", artifact_dir(repo) / "experiments", repo / "results"]
    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for suffix in ("*.fvecs", "*.ivecs", "*.bvecs", "*.json", "*.jsonl", "*.csv"):
            candidates.extend(path for path in root.rglob(suffix) if _looks_required_input_path(path))
    return sorted(set(candidates))


def _valid_input_roles(rows: list[dict[str, str]], validations: list[dict[str, Any]]) -> set[str]:
    """Extract valid input roles from rows and validations.

    Uses context-specific status check for input availability.
    SUPPORTED/AVAILABLE/PRESENT are acceptable for input contract.
    """
    from r2a.tools.evidence_levels import is_input_available_status

    roles: set[str] = set()
    for row in rows:
        status = str(row.get("status", "")).strip().upper()
        if _row_has_integrity_problem(row):
            continue
        # Use context-specific status check for input availability
        if is_input_available_status(status):
            roles.update(_roles_for_text(" ".join(str(value) for value in row.values())))
    for item in validations:
        if item["integrity_status"] == "OK":
            roles.update(_roles_for_text(item["path"]))
    return roles


def _is_required_input_component(component: str) -> bool:
    return bool(_roles_for_text(component))


def _looks_required_input_path(path: Path) -> bool:
    return bool(_roles_for_text(str(path)))


def _is_target_required_candidate_path(path: Path, rows: list[dict[str, str]]) -> bool:
    normalized_path = _normalize_text(str(path))
    for row in rows:
        if not _row_is_target_required(row):
            continue
        raw_path = str(row.get("path_or_command") or row.get("path") or row.get("file") or "").strip()
        if not raw_path:
            continue
        normalized_row_path = _normalize_text(raw_path)
        row_name = _normalize_text(Path(raw_path).name)
        if normalized_row_path and (normalized_row_path in normalized_path or normalized_path.endswith(normalized_row_path)):
            return True
        if row_name and row_name in normalized_path:
            return True

    parts = {_normalize_text(part) for part in path.parts}
    if "source" in parts and parts & GENERATED_SOURCE_PATH_MARKERS:
        return False
    if parts & {"datasets", "dataset", "data", "official"}:
        return _looks_required_input_path(path)
    return False


def _row_is_target_required(row: dict[str, str]) -> bool:
    fields = {str(key).strip().lower(): str(value).strip() for key, value in row.items()}
    for key in ("target_required", "required"):
        if key in fields:
            marker = _normalize_text(fields[key])
            if marker in TRUTHY_MARKERS:
                return True
            if marker in FALSEY_MARKERS:
                return False
    if "required_for_target" in fields:
        marker = _normalize_text(fields["required_for_target"])
        if marker in FALSEY_MARKERS:
            return False
        if marker:
            return True
    for key in ("priority", "scope"):
        marker = _normalize_text(fields.get(key, ""))
        if any(item in marker for item in REQUIRED_SCOPE_MARKERS):
            return True
        if any(item in marker for item in OPTIONAL_SCOPE_MARKERS):
            return False

    text = _normalize_text(" ".join(str(value) for value in row.values()))
    if any(marker in text for marker in OPTIONAL_DATASET_MARKERS):
        return False
    component = str(row.get("component", "")).strip()
    return _is_required_input_component(component)


def _missing_role_blockers(missing_roles: list[str], existing_blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    existing_roles: set[str] = set()
    for item in existing_blockers:
        existing_roles.update(_roles_for_text(f"{item.get('component', '')} {item.get('path', '')}"))
    for role in missing_roles:
        if role in existing_roles:
            continue
        blockers.append(
            {
                "path": "",
                "component": role,
                "status": "MISSING",
                "integrity_status": "MISSING",
                "notes": f"required input role is missing: {role}",
                "max_evidence_level_allowed": "L2_input_contract_ready",
                "severity": "blocker",
                "target_required": True,
            }
        )
    return blockers


def _roles_for_text(text: str) -> set[str]:
    lowered = _normalize_text(text)
    return {
        role
        for role, markers in INPUT_ROLE_MARKERS.items()
        if any(marker in lowered for marker in markers)
    }


def _normalize_text(text: str) -> str:
    return str(text or "").lower().replace("-", "_").replace("\\", "/")


def _summary_lines(
    blockers: list[dict[str, Any]],
    missing_roles: list[str],
    warnings: list[dict[str, Any]] | None = None,
) -> list[str]:
    lines: list[str] = []
    for item in blockers[:8]:
        status = item.get("integrity_status") or item.get("status") or "UNKNOWN"
        path = item.get("path") or item.get("component") or "unknown input"
        notes = item.get("notes") or ""
        lines.append(f"{status}: {path}; {notes}")
    if len(blockers) > 8:
        lines.append(f"{len(blockers) - 8} additional input integrity blocker(s) omitted.")
    if missing_roles:
        lines.append(f"Missing required input role(s): {', '.join(missing_roles)}.")
    warning_count = len(warnings or [])
    if warning_count:
        lines.append(f"{warning_count} optional/out-of-scope input warning(s) did not block L3/L4.")
    if not lines:
        lines.append("Official input integrity blockers: none detected.")
    return lines
