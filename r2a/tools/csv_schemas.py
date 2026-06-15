from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class CsvSchema:
    filename: str
    required_columns: tuple[str, ...]
    optional_columns: tuple[str, ...] = ()
    numeric_columns: tuple[str, ...] = ()
    purpose: str = ""
    legacy_aliases: Mapping[str, tuple[str, ...]] | None = None
    enum_constraints: Mapping[str, tuple[str, ...]] | None = None
    legacy_value_aliases: Mapping[str, Mapping[str, str]] | None = None

    @property
    def all_columns(self) -> tuple[str, ...]:
        return (*self.required_columns, *self.optional_columns)


CSV_SCHEMAS: dict[str, CsvSchema] = {
    "reproduction_status.csv": CsvSchema(
        "reproduction_status.csv",
        ("status", "reason", "evidence_source", "next_action"),
        optional_columns=("component",),
        purpose="Current reproduction status and next action.",
        legacy_aliases={
            "status": ("result", "outcome"),
            "reason": ("message", "details"),
            "evidence_source": ("source", "file", "test_scope", "scope"),
        },
    ),
    "project_tests.csv": CsvSchema(
        "project_tests.csv",
        ("status", "command", "exit_code", "duration_sec", "test_scope", "log_path", "notes"),
        numeric_columns=("exit_code", "duration_sec"),
        purpose="Target project test command evidence.",
        legacy_aliases={
            "status": ("result", "outcome"),
            "command": ("test_command", "cmd"),
            "test_scope": ("file", "scope", "evidence_source"),
            "exit_code": ("returncode", "return_code"),
            "log_path": ("log", "stdout_path"),
            "notes": ("note", "message", "details"),
        },
    ),
    "project_health.csv": CsvSchema(
        "project_health.csv",
        ("status", "command", "exit_code", "duration_sec", "test_scope", "log_path", "notes"),
        numeric_columns=("exit_code", "duration_sec"),
        purpose="Project health command evidence.",
        legacy_aliases={
            "status": ("result", "outcome"),
            "command": ("test_command", "cmd"),
            "test_scope": ("file", "scope", "evidence_source"),
            "exit_code": ("returncode", "return_code"),
            "log_path": ("log", "stdout_path"),
            "notes": ("note", "message", "details"),
        },
    ),
    "command_manifest.csv": CsvSchema(
        "command_manifest.csv",
        (
            "command_id",
            "command",
            "exit_code",
            "duration_sec",
            "log_path",
            "artifact_path",
            "artifact_hash",
            "input_provenance",
            "notes",
        ),
        optional_columns=(
            "cwd",
            "start_time",
            "end_time",
            "returncode",
            "stdout_path",
            "stderr_path",
            "observed_outputs",
            "declared_outputs",
            "network_used",
            "stage",
            "iteration",
        ),
        numeric_columns=("exit_code", "duration_sec"),
        purpose="Command provenance for measured artifacts.",
    ),
    "source_verification.csv": CsvSchema(
        "source_verification.csv",
        ("artifact_url", "branch", "commit", "readme_found", "build_docs_found", "notes"),
        optional_columns=(
            "status",
            "source_path",
            "tag",
            "access_status",
            "license",
            "experiment_scripts_found",
            "data_scripts_found",
            "evidence_source",
        ),
        purpose="Official source/artifact verification evidence.",
    ),
    "build_smoke.csv": CsvSchema(
        "build_smoke.csv",
        ("status", "command", "exit_code", "duration_sec", "component", "notes"),
        numeric_columns=("exit_code", "duration_sec"),
        purpose="Build or import smoke evidence.",
        legacy_aliases={
            "status": ("result", "outcome"),
            "command": ("test_command", "cmd"),
            "exit_code": ("returncode", "return_code"),
            "notes": ("note", "message", "details"),
        },
    ),
    "runtime_smoke.csv": CsvSchema(
        "runtime_smoke.csv",
        ("status", "command", "exit_code", "duration_sec", "component", "evidence_source", "notes"),
        numeric_columns=("exit_code", "duration_sec"),
        purpose="Runtime smoke evidence.",
        legacy_aliases={
            "status": ("result", "outcome"),
            "command": ("test_command", "cmd"),
            "exit_code": ("returncode", "return_code"),
            "evidence_source": ("source", "file", "test_scope", "scope"),
            "notes": ("note", "message", "details"),
        },
    ),
    "docker_build.csv": CsvSchema(
        "docker_build.csv",
        ("image_tag", "dockerfile", "context_dir", "command", "exit_code", "duration_sec", "log_path", "image_id", "status", "notes"),
        numeric_columns=("exit_code", "duration_sec"),
        purpose="Bounded Docker build evidence.",
    ),
    "docker_runtime_smoke.csv": CsvSchema(
        "docker_runtime_smoke.csv",
        ("image_tag", "command", "exit_code", "duration_sec", "component", "log_path", "status", "notes"),
        numeric_columns=("exit_code", "duration_sec"),
        purpose="Bounded Docker runtime smoke evidence.",
    ),
    "input_contract_verification.csv": CsvSchema(
        "input_contract_verification.csv",
        ("component", "status", "path_or_command", "evidence_source", "notes"),
        optional_columns=("dataset", "query", "ground_truth", "metric"),
        purpose="Dataset, query, ground-truth, metric, command, and input readiness evidence.",
    ),
    "dependency_setup.csv": CsvSchema(
        "dependency_setup.csv",
        ("package", "command", "status", "version", "evidence_source", "notes"),
        purpose="Dependency setup evidence.",
    ),
    "feature_localization.csv": CsvSchema(
        "feature_localization.csv",
        ("component", "status", "path", "symbol_or_command", "evidence_source", "notes"),
        purpose="Feature/source localization evidence.",
    ),
    "source_localization.csv": CsvSchema(
        "source_localization.csv",
        ("component", "evidence_source", "notes"),
        optional_columns=("found", "file_path", "path", "symbol_or_command", "status"),
        purpose="Source localization evidence.",
    ),
    "figure_table_verification.csv": CsvSchema(
        "figure_table_verification.csv",
        ("item", "status", "evidence_source", "notes", "next_action"),
        purpose="Paper figure/table verification evidence.",
    ),
    "reduced_metrics.csv": CsvSchema(
        "reduced_metrics.csv",
        ("command_id", "dataset", "method", "k", "notes"),
        optional_columns=(
            "ground_truth_source",
            "metric_definition",
            "input_provenance",
            "efs",
            "ef_search",
            "selectivity",
            "latency_ms",
            "recall",
            "qps",
            "query_count",
            "repetitions",
            "build_time",
            "index_size",
        ),
        numeric_columns=("k", "efs", "ef_search", "selectivity", "latency_ms", "recall", "qps", "query_count", "repetitions", "build_time", "index_size"),
        purpose="Measured official or paper-linked reduced-run metrics.",
    ),
    "paper_alignment.csv": CsvSchema(
        "paper_alignment.csv",
        (
            "paper_item",
            "setting_name",
            "paper_setting",
            "reduced_setting",
            "match_status",
            "evidence_source",
            "notes",
        ),
        purpose="Mapping from paper settings to the reduced run for L4 evidence.",
        legacy_aliases={"reduced_setting": ("verified_setting",), "match_status": ("status",)},
        enum_constraints={
            "match_status": (
                "MATCH",
                "PARTIAL_MATCH",
                "MISMATCH",
                "NOT_AVAILABLE",
                "NEEDS_HUMAN_VERIFICATION",
            )
        },
        legacy_value_aliases={
            "match_status": {
                "DIFFERENT": "MISMATCH",
                "GAP": "NOT_AVAILABLE",
                "NONE": "NOT_AVAILABLE",
                "NULL": "NOT_AVAILABLE",
                "": "NOT_AVAILABLE",
            }
        },
    ),
    "baseline_comparison.csv": CsvSchema(
        "baseline_comparison.csv",
        ("method", "baseline_method", "reduced_input_id", "metric", "environment", "budget_notes"),
        optional_columns=("command_id", "command", "exit_code", "duration_sec", "log_path", "artifact_hash", "input_provenance", "notes"),
        numeric_columns=("exit_code", "duration_sec"),
        purpose="Minimal same-input baseline comparison evidence for L5.",
    ),
    "reduced_demo_metrics.csv": CsvSchema(
        "reduced_demo_metrics.csv",
        (
            "dataset",
            "method",
            "k",
            "efs",
            "selectivity",
            "latency_ms",
            "recall",
            "query_count",
            "ground_truth_source",
            "input_level",
            "result_level",
            "notes",
        ),
        purpose="Explicitly demo-only reduced metrics that cannot claim L3/L4.",
    ),
}


def schema_for_csv(path_or_name: str | Path) -> CsvSchema | None:
    name = Path(str(path_or_name)).name.lower()
    return CSV_SCHEMAS.get(name)


def required_columns_for_csv(path_or_name: str | Path) -> tuple[str, ...]:
    schema = schema_for_csv(path_or_name)
    return schema.required_columns if schema else ()


def numeric_columns_for_csv(path_or_name: str | Path) -> tuple[str, ...]:
    schema = schema_for_csv(path_or_name)
    if schema:
        return schema.numeric_columns
    return ("qps",)


def enum_constraints_for_csv(path_or_name: str | Path) -> Mapping[str, tuple[str, ...]]:
    schema = schema_for_csv(path_or_name)
    if not schema:
        return {}
    return schema.enum_constraints or {}


def legacy_value_aliases_for_csv(path_or_name: str | Path) -> Mapping[str, Mapping[str, str]]:
    schema = schema_for_csv(path_or_name)
    if not schema:
        return {}
    return schema.legacy_value_aliases or {}


def allowed_values_for_csv(path_or_name: str | Path, column: str) -> tuple[str, ...]:
    return tuple(enum_constraints_for_csv(path_or_name).get(column, ()))


def csv_header(path_or_name: str | Path) -> str:
    schema = schema_for_csv(path_or_name)
    if not schema:
        return ""
    return ",".join(schema.required_columns)


def canonicalize_row(path_or_name: str | Path, row: Mapping[str, object]) -> dict[str, object]:
    schema = schema_for_csv(path_or_name)
    output = dict(row)
    if not schema:
        return output
    if schema.legacy_aliases:
        normalized = {_normalize_column(key): key for key in output}
        for canonical, aliases in schema.legacy_aliases.items():
            if _has_value(output.get(canonical)):
                continue
            for alias in aliases:
                actual = normalized.get(_normalize_column(alias))
                if actual and _has_value(output.get(actual)):
                    output[canonical] = output[actual]
                    break
    if schema.legacy_value_aliases:
        for column, aliases in schema.legacy_value_aliases.items():
            if not _has_value(output.get(column)):
                continue
            output[column] = canonicalize_cell_value(path_or_name, column, output[column])
    return output


def canonicalize_cell_value(path_or_name: str | Path, column: str, value: object) -> str:
    text = str(value or "").strip()
    aliases = legacy_value_aliases_for_csv(path_or_name).get(column, {})
    # Handle empty string with explicit alias if defined
    if not text and "" in aliases:
        return aliases[""]
    if not text:
        return ""
    return aliases.get(text.upper(), text.upper())


def legacy_alias_messages(path_or_name: str | Path, columns: list[str] | tuple[str, ...]) -> list[str]:
    schema = schema_for_csv(path_or_name)
    if not schema or not schema.legacy_aliases:
        return []
    normalized = {_normalize_column(column): column for column in columns}
    messages: list[str] = []
    for canonical, aliases in schema.legacy_aliases.items():
        if canonical in columns:
            continue
        for alias in aliases:
            if _normalize_column(alias) in normalized:
                messages.append(
                    f"Legacy column `{alias}` was mapped to `{canonical}` for compatibility; new artifacts must use `{canonical}`."
                )
                break
    return messages


def legacy_value_messages(path_or_name: str | Path, column: str, values: list[object] | tuple[object, ...]) -> list[str]:
    aliases = legacy_value_aliases_for_csv(path_or_name).get(column, {})
    if not aliases:
        return []
    seen: set[str] = set()
    messages: list[str] = []
    for value in values:
        raw = str(value or "").strip()
        key = raw.upper()
        if not key or key not in aliases or key in seen:
            continue
        seen.add(key)
        messages.append(
            f"Legacy value `{raw}` in `{column}` was mapped to `{aliases[key]}` for compatibility; new artifacts must use `{aliases[key]}`."
        )
    return messages


def _normalize_column(value: str) -> str:
    return str(value).strip().lower().replace(" ", "_").replace("-", "_")


def _has_value(value: object) -> bool:
    return value is not None and str(value).strip() != ""
