from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path

from r2a.core.paths import artifact_dir

INPUT_CONTRACT_PASS_WITHOUT_LOCAL_FILE = "INPUT_CONTRACT_PASS_WITHOUT_LOCAL_FILE"
INPUT_CONTRACT_PASS_WITH_EMPTY_LOCAL_FILE = "INPUT_CONTRACT_PASS_WITH_EMPTY_LOCAL_FILE"
INPUT_CONTRACT_PASS_WITHOUT_COMMAND_PROVENANCE = "INPUT_CONTRACT_PASS_WITHOUT_COMMAND_PROVENANCE"

_OFFICIAL_COMPONENT_MARKERS = (
    "database_vectors",
    "query_vectors",
    "ground_truth",
    "database_attributes",
    "query_attributes",
    "em_query_attributes",
    "r_query_attributes",
    "emis_query_attributes",
)
_OFFICIAL_FILE_MARKERS = (
    ".fvecs",
    ".ivecs",
    ".bvecs",
    "database_attributes.jsonl",
    "em_query_attributes.jsonl",
    "r_query_attributes.jsonl",
    "emis_query_attributes.jsonl",
)
_EXCLUDED_COMPONENTS = {
    "benchmark_cli",
    "metric_definition",
    "metric_definitions",
}
_PROVENANCE_VERBS = (
    "hf_hub_download",
    "snapshot_download",
    "load_dataset",
    "wget",
    "curl",
    "download",
    "copy",
    "cp ",
    "copy-item",
    "generated",
    "generate",
    "python",
    "stat",
    "ls ",
    "dir ",
    "du ",
    "wc ",
    "verify",
    "validated",
    "validate",
)


@dataclass(frozen=True)
class InputContractEvidenceIssue:
    code: str
    component: str
    status: str
    path_or_command: str
    row_number: int
    message: str
    resolved_path: str = ""

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)


def validate_official_input_pass_evidence(
    repo: Path,
    input_contract_csv: Path,
    command_manifest_csv: Path | None = None,
    logs_dir: Path | None = None,
) -> list[InputContractEvidenceIssue]:
    """Validate PASS rows for official local input files.

    The check is intentionally narrow: it only inspects rows that claim PASS for
    official local input files, resolves only the declared path, and looks only
    at the provided command manifest or direct log files.
    """

    repo = Path(repo)
    input_contract_csv = Path(input_contract_csv)
    if not input_contract_csv.exists():
        return []

    rows = _read_csv_rows(input_contract_csv)
    provenance_text = _collect_provenance_text(command_manifest_csv, logs_dir)
    issues: list[InputContractEvidenceIssue] = []

    for row_number, row in rows:
        status = _value(row, "status").upper()
        if status != "PASS":
            continue
        if not _is_official_local_input_row(row):
            continue

        component = _value(row, "component")
        declared_path = _declared_path(row)
        if not _is_clear_local_file_path(declared_path):
            issues.append(
                _issue(
                    INPUT_CONTRACT_PASS_WITHOUT_LOCAL_FILE,
                    row,
                    row_number,
                    "Official input PASS row does not declare a clear local file path.",
                )
            )
            continue

        resolved = _resolve_declared_path(repo, input_contract_csv, declared_path)
        if resolved is None or not resolved.exists() or not resolved.is_file():
            issues.append(
                _issue(
                    INPUT_CONTRACT_PASS_WITHOUT_LOCAL_FILE,
                    row,
                    row_number,
                    f"Official input PASS row declares missing local file: {declared_path}",
                    resolved,
                )
            )
            continue

        try:
            size_bytes = resolved.stat().st_size
        except OSError:
            size_bytes = 0
        if size_bytes <= 0:
            issues.append(
                _issue(
                    INPUT_CONTRACT_PASS_WITH_EMPTY_LOCAL_FILE,
                    row,
                    row_number,
                    f"Official input PASS row declares an empty local file: {declared_path}",
                    resolved,
                )
            )
            continue

        if not _has_command_provenance(declared_path, resolved, provenance_text):
            issues.append(
                _issue(
                    INPUT_CONTRACT_PASS_WITHOUT_COMMAND_PROVENANCE,
                    row,
                    row_number,
                    (
                        "Official input PASS row has a local non-empty file but no "
                        "download/copy/generate/verify command provenance."
                    ),
                    resolved,
                )
            )

    return issues


def _read_csv_rows(path: Path) -> list[tuple[int, dict[str, str]]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [
                (index, {str(key or ""): str(value or "") for key, value in row.items()})
                for index, row in enumerate(csv.DictReader(handle), start=2)
            ]
    except Exception:
        return []


def _is_official_local_input_row(row: dict[str, str]) -> bool:
    component = _value(row, "component").lower()
    declared_path = _declared_path(row).lower()
    notes = _value(row, "notes").lower()
    combined = " ".join((component, declared_path, notes))

    if component in _EXCLUDED_COMPONENTS:
        return False
    if _looks_like_command(declared_path):
        return False
    if _looks_like_remote_dataset_id(declared_path) and not any(marker in component for marker in _OFFICIAL_COMPONENT_MARKERS):
        return False
    if any(marker in component for marker in _OFFICIAL_COMPONENT_MARKERS):
        return True
    if any(marker in declared_path for marker in _OFFICIAL_FILE_MARKERS):
        return True
    return any(marker in combined for marker in _OFFICIAL_FILE_MARKERS)


def _declared_path(row: dict[str, str]) -> str:
    return _value(row, "path_or_command") or _value(row, "path") or _value(row, "file_path")


def _value(row: dict[str, str], key: str) -> str:
    for row_key, value in row.items():
        if row_key.lower() == key.lower():
            return str(value or "").strip()
    return ""


def _is_clear_local_file_path(value: str) -> bool:
    if not value:
        return False
    lowered = value.strip().lower()
    if lowered in {"n/a", "na", "none", "unknown", "not found", "missing"}:
        return False
    if _looks_like_remote_dataset_id(lowered) or _looks_like_command(lowered):
        return False
    if lowered.endswith(".md") or lowered in {"readme", "readme.md"}:
        return False
    return any(marker in lowered for marker in _OFFICIAL_FILE_MARKERS) or bool(Path(value).suffix) or "/" in value or "\\" in value


def _looks_like_remote_dataset_id(value: str) -> bool:
    text = value.strip()
    if not text or "\\" in text:
        return False
    if text.lower().startswith(("http://", "https://", "hf://")):
        return True
    if "/" not in text:
        return False
    suffix = Path(text).suffix.lower()
    return not suffix and len(text.split("/")) == 2


def _looks_like_command(value: str) -> bool:
    text = value.strip().lower()
    if not text:
        return False
    command_tokens = (" --", " -", "python ", "python3 ", "bash ", "sh ", "powershell ", "cmd ", "pytest ", "cmake ", "make ")
    if any(token in text for token in command_tokens):
        return True
    return text.endswith(" --help") or " --help " in text


def _resolve_declared_path(repo: Path, input_contract_csv: Path, declared_path: str) -> Path | None:
    path = Path(declared_path)
    candidates = [path] if path.is_absolute() else [
        repo / path,
        artifact_dir(repo) / path,
        input_contract_csv.parent / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else None


def _collect_provenance_text(command_manifest_csv: Path | None, logs_dir: Path | None) -> str:
    chunks: list[str] = []
    if command_manifest_csv:
        manifest = Path(command_manifest_csv)
        if manifest.exists() and manifest.is_file():
            try:
                chunks.append(manifest.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    if logs_dir:
        logs = Path(logs_dir)
        if logs.exists() and logs.is_dir():
            for path in sorted(logs.iterdir()):
                if path.is_file():
                    try:
                        chunks.append(path.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        pass
    return "\n".join(chunks).lower()


def _has_command_provenance(declared_path: str, resolved: Path, provenance_text: str) -> bool:
    if not provenance_text:
        return False
    declared_norm = declared_path.replace("\\", "/").lower()
    resolved_norm = str(resolved).replace("\\", "/").lower()
    basename = resolved.name.lower()
    mentions_file = basename in provenance_text or declared_norm in provenance_text or resolved_norm in provenance_text
    if not mentions_file:
        return False
    return any(verb in provenance_text for verb in _PROVENANCE_VERBS)


def _issue(
    code: str,
    row: dict[str, str],
    row_number: int,
    message: str,
    resolved_path: Path | None = None,
) -> InputContractEvidenceIssue:
    return InputContractEvidenceIssue(
        code=code,
        component=_value(row, "component"),
        status=_value(row, "status"),
        path_or_command=_declared_path(row),
        row_number=row_number,
        message=message,
        resolved_path=str(resolved_path or ""),
    )
