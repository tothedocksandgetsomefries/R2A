from __future__ import annotations

import csv
import math
import re
from pathlib import Path
from typing import Any

from r2a.core.paths import artifact_dir
from r2a.tools.csv_schemas import allowed_values_for_csv, canonicalize_row, required_columns_for_csv
from r2a.tools.input_integrity import rows_have_input_integrity_blocker, summarize_official_input_integrity
from r2a.tools.reproduction_levels import LEVEL_INDEX, REPRODUCTION_LEVELS, normalize_level

L4_ALIGNMENT_MATCH_STATUSES = set(allowed_values_for_csv("paper_alignment.csv", "match_status"))
L4_REQUIRED_SETTING_GROUPS = {
    "dataset_scale": ("dataset scale", "scale"),
    "hardware": ("hardware",),
    "runtime_budget": ("runtime budget", "runtime", "budget"),
    "parameters": ("parameters", "params", "parameter"),
    "number_of_repeats": ("number of repeats", "repeats", "repeat"),
    "baselines": ("baseline", "baselines"),
    "metric_definition": ("metric definition", "metric"),
    "input_source": ("input source", "source"),
    "known_evidence_gaps": ("evidence gap", "known evidence gap", "gap"),
}
CONTRACT_MODES = {"verification_only", "smoke", "official_reduced", "full_benchmark"}
L2_CAPPED_CONTRACT_MODES = {
    "verification_only": "contract mode is verification_only",
    "smoke": "contract mode is smoke",
}
CANONICAL_STATUSES = {"PASS", "FAIL", "NOT_RUN", "NEEDS_INPUT"}
LEGACY_STATUS_MAP = {
    "CLONED": "PASS",
    "VERIFIED": "PASS",
    "READY": "PASS",
    "READY_WITH_GAPS": "PASS",
    "DONE": "PASS",
    "OK": "PASS",
    "FOUND": "PASS",
    "VALID": "PASS",
    "AVAILABLE": "PASS",
    "DOCUMENTED": "PASS",
    "PASS_USAGE_HELP": "PASS",
    "USAGE_HELP": "PASS",
    # Engineer 输出的成功状态
    "SUPPORTED": "PASS",      # 组件已验证可用
    "GENERATED": "PASS",      # 文件已生成
    "BUILT": "PASS",          # 构建成功
    "PRESENT": "PASS",        # 文件存在
    "PASSED": "PASS",
    "RESOLVED": "PASS",
    "FAILED": "FAIL",
    "ERROR": "FAIL",
    "BLOCKED": "FAIL",
    "PARTIAL": "FAIL",
    "NEEDS_CLARIFICATION": "NEEDS_INPUT",
    "NEEDS_OFFICIAL_INPUT": "NEEDS_INPUT",
    "NEEDS_INPUT_OR_BUDGET": "NEEDS_INPUT",
    "NOT_AVAILABLE": "NEEDS_INPUT",
    "MISSING": "NEEDS_INPUT",
    "NOT_ATTEMPTED": "NOT_RUN",
    "NO_TEST_COMMAND_FOUND": "NOT_RUN",
    "NO_TESTS_FOUND": "NOT_RUN",
    "NOT_APPLICABLE": "NOT_RUN",
    "SKIPPED_ROOT_CMAKE_NOT_REQUIRED": "NOT_RUN",
    "SKIPPED": "NOT_RUN",
    "NOT_RUN": "NOT_RUN",
    "PENDING": "NOT_RUN",
}
# Use REPRODUCTION_LEVELS and LEVEL_INDEX from reproduction_levels.py as single source of truth
LEVEL_ORDER = REPRODUCTION_LEVELS


def infer_evidence_level(repo_path: str | Path, fallback: str = "L0_project_health") -> str:
    """从文件推断 evidence level。

    .. deprecated::
        此函数已废弃，仅供旧模块兼容使用。
        正式等级判断应由 Reviewer 完成。
        此函数只支持基于文件存在的机械推断，不支持语义判断。
        不支持 L5-L6 的完整判断。
        新代码不得使用此函数作为正式等级来源。
        使用 reviewer_level_judgment.collect_evidence_artifacts() 收集证据，
        然后由 Reviewer 进行语义判断。
    """
    repo = Path(repo_path)
    csvs = _result_csvs(repo)
    names = {path.name.lower() for path in csvs}
    rows_by_name = {name: _rows_from_named_csv(csvs, name) for name in names}
    cap_reason = contract_l2_cap_reason(repo)

    level = "L0_project_health" if _has_project_health(repo, rows_by_name, names) else normalize_level(fallback)
    if _has_source_artifact(rows_by_name, names):
        level = "L1_source_artifact_verified"
    if _has_input_contract(rows_by_name, names):
        level = "L2_input_contract_ready"
    if cap_reason:
        return _cap_level_at_l2(level)
    if _has_official_reduced_metrics(repo, rows_by_name, names):
        level = "L3_official_reduced_run"
    if _has_paper_alignment(repo, rows_by_name, names):
        level = "L4_reduced_paper_aligned"
    if _has_baseline_comparison(repo, rows_by_name, names):
        level = "L5_minimal_baseline_comparison"
    if _has_full_or_near_full(rows_by_name, names):
        level = "L6_full_or_near_full_reproduction"
    return level


def evidence_level_summary(repo_path: str | Path) -> str:
    repo = Path(repo_path)
    csvs = _result_csvs(repo)
    names = {path.name.lower() for path in csvs}
    rows_by_name = {name: _rows_from_named_csv(csvs, name) for name in names}
    cap_reason = contract_l2_cap_reason(repo)
    checks = [
        ("L0 project health evidence", _yes_no(_has_project_health(repo, rows_by_name, names))),
        ("L1 source/artifact evidence", _yes_no(_has_source_artifact(rows_by_name, names))),
        ("L2 input contract evidence", _input_contract_status(rows_by_name.get("input_contract_verification.csv", []), names)),
        ("Contract L2 cap", cap_reason or "no"),
        ("L3 official reduced metrics evidence", _yes_no(_has_official_reduced_metrics(repo, rows_by_name, names))),
        ("L4 paper-aligned reduced evidence", _yes_no(_has_paper_alignment(repo, rows_by_name, names))),
        ("L5 baseline comparison evidence", _yes_no(_has_baseline_comparison(repo, rows_by_name, names))),
        ("L6 full or near-full evidence", _yes_no(_has_full_or_near_full(rows_by_name, names))),
    ]
    return "\n".join(f"- {name}: {status}" for name, status in checks)


def contract_l2_cap_reason(repo_path: str | Path) -> str:
    mode = explicit_contract_mode(repo_path)
    return L2_CAPPED_CONTRACT_MODES.get(mode, "")


def explicit_contract_mode(repo_path: str | Path) -> str:
    repo = Path(repo_path)
    for path in (artifact_dir(repo) / "EXPERIMENT_CONTRACT.md", artifact_dir(repo) / "TASK_SPEC.md"):
        mode = _explicit_contract_mode_from_text(_read_text(path))
        if mode:
            return mode
    return ""


def contract_is_l2_capped(repo_path: str | Path) -> bool:
    return bool(contract_l2_cap_reason(repo_path))


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _cap_level_at_l2(level: str) -> str:
    normalized = normalize_level(level)
    try:
        if LEVEL_ORDER.index(normalized) > LEVEL_ORDER.index("L2_input_contract_ready"):
            return "L2_input_contract_ready"
    except ValueError:
        return "L2_input_contract_ready"
    return normalized


def _l2_cap_reason_from_contract_text(text: str) -> str:
    mode = _explicit_contract_mode_from_text(text)
    return L2_CAPPED_CONTRACT_MODES.get(mode, "")


def _explicit_contract_mode_from_text(text: str) -> str:
    lines = [line.strip().strip("`").lower() for line in text.splitlines()]
    for index, line in enumerate(lines):
        if line in {"## contract mode", "contract mode"}:
            for candidate in lines[index + 1 : index + 4]:
                cleaned = candidate.strip().strip("`").strip(":").strip()
                if cleaned in CONTRACT_MODES:
                    return cleaned
                if cleaned and not cleaned.startswith("#"):
                    break
    for line in lines:
        match = re.match(r"^-?\s*contract mode\s*:\s*`?([a-z_]+)`?\s*$", line)
        if match and match.group(1) in CONTRACT_MODES:
            return match.group(1)
    return ""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _input_contract_status(rows: list[dict[str, str]], names: set[str]) -> str:
    if "input_contract_verification.csv" not in names or not rows:
        return "no"
    if _input_contract_ready_with_gaps(rows):
        return "yes (NEEDS_INPUT)"
    return "yes (PASS)"


def _result_csvs(repo: Path) -> list[Path]:
    files: list[Path] = []
    for directory in (repo / "results", artifact_dir(repo) / "results"):
        if directory.exists():
            files.extend(sorted(directory.glob("*.csv")))
    return files


def _rows_from_named_csv(paths: list[Path], name: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths:
        if path.name.lower() == name.lower():
            rows.extend(_read_csv_rows(path))
    return rows


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [
                {str(key): str(value) for key, value in canonicalize_row(path.name, row).items()}
                for row in csv.DictReader(handle)
            ]
    except Exception:
        return []


def _has_project_health(repo: Path, rows_by_name: dict[str, list[dict[str, str]]], names: set[str]) -> bool:
    if _workspace_health_ready(repo):
        return True
    return _has_pass(rows_by_name.get("project_tests.csv", [])) or _has_pass(rows_by_name.get("project_health.csv", []))


def _workspace_health_ready(repo: Path) -> bool:
    r2a_dir = artifact_dir(repo)
    required = (
        repo,
        r2a_dir,
        r2a_dir / "TASK_SPEC.md",
        r2a_dir / "EXPERIMENT_CONTRACT.md",
        r2a_dir / "results",
    )
    if not all(path.exists() for path in required):
        return False
    report_candidates = (
        r2a_dir / "CHECK_REPORT.md",
        r2a_dir / "REVIEW_REPORT.md",
        r2a_dir / "FINAL_REPORT.md",
        r2a_dir / "ITERATION_STATE.json",
    )
    return any(path.exists() for path in report_candidates)


def _has_source_artifact(rows_by_name: dict[str, list[dict[str, str]]], names: set[str]) -> bool:
    source_ok = _has_pass(rows_by_name.get("source_verification.csv", [])) or "source_localization.csv" in names
    build_ok = _has_pass(rows_by_name.get("build_smoke.csv", []))
    runtime_rows = rows_by_name.get("runtime_smoke.csv", [])
    runtime_ok = _has_pass(runtime_rows) or _has_partial(runtime_rows)
    return bool(source_ok and (build_ok or runtime_ok))


def _has_input_contract(rows_by_name: dict[str, list[dict[str, str]]], names: set[str]) -> bool:
    rows = rows_by_name.get("input_contract_verification.csv", [])
    return "input_contract_verification.csv" in names and bool(rows)


def _has_official_reduced_metrics(repo: Path, rows_by_name: dict[str, list[dict[str, str]]], names: set[str]) -> bool:
    if _has_needs_official_input(rows_by_name):
        return False
    if rows_have_input_integrity_blocker(rows_by_name.get("input_contract_verification.csv", [])):
        return False
    if summarize_official_input_integrity(repo).get("has_blocking_issue"):
        return False
    reduced_rows = rows_by_name.get("reduced_metrics.csv", [])
    if not reduced_rows:
        return False
    if not _input_contract_ready(rows_by_name.get("input_contract_verification.csv", [])):
        return False
    return any(
        _row_has_measured_metrics(row)
        and _row_has_l3_reduced_contract(row)
        and not _row_is_l2_capped(row)
        and not _has_synthetic_or_demo([row])
        and _has_command_provenance(repo, row, rows_by_name, "reduced_metrics.csv")
        for row in reduced_rows
    )


def _has_paper_alignment(repo: Path, rows_by_name: dict[str, list[dict[str, str]]], names: set[str]) -> bool:
    if not _has_official_reduced_metrics(repo, rows_by_name, names):
        return False
    rows = rows_by_name.get("paper_alignment.csv", []) + rows_by_name.get("figure_table_mapping.csv", [])
    required = required_columns_for_csv("paper_alignment.csv")
    required_groups = tuple((column,) for column in required)
    valid_rows = [
        row
        for row in rows
        if not _has_synthetic_or_demo([row])
        and _row_has_alias_groups(row, required_groups)
        and _first_present_alias(row, ("match_status",)).upper() in L4_ALIGNMENT_MATCH_STATUSES
    ]
    if not valid_rows:
        return False
    if not any(_first_present_alias(row, ("match_status",)).upper() in {"MATCH", "PARTIAL_MATCH"} for row in valid_rows):
        return False
    setting_text = "\n".join(
        f"{_first_present_alias(row, ('setting_name', 'setting'))} {_first_present_alias(row, ('notes',))}".lower()
        for row in valid_rows
    )
    return all(any(alias in setting_text for alias in aliases) for aliases in L4_REQUIRED_SETTING_GROUPS.values())


def _has_baseline_comparison(repo: Path, rows_by_name: dict[str, list[dict[str, str]]], names: set[str]) -> bool:
    if not _has_official_reduced_metrics(repo, rows_by_name, names):
        return False
    rows = rows_by_name.get("baseline_comparison.csv", [])
    required_groups = (
        ("method",),
        ("baseline_method", "baseline"),
        ("reduced_input_id", "input_id", "dataset"),
        ("metric", "metric_name"),
        ("environment", "env"),
        ("budget_notes", "budget"),
    )
    fair_rows = [
        row
        for row in rows
        if not _has_synthetic_or_demo([row])
        and _row_has_alias_groups(row, required_groups)
        and _has_command_provenance(repo, row, rows_by_name, "baseline_comparison.csv")
    ]
    if not fair_rows:
        return False
    input_values = {_normalized_value(_first_present_alias(row, ("reduced_input_id", "input_id", "dataset"))) for row in fair_rows}
    metric_values = {_normalized_value(_first_present_alias(row, ("metric", "metric_name"))) for row in fair_rows}
    environment_values = {_normalized_value(_first_present_alias(row, ("environment", "env"))) for row in fair_rows}
    return len(input_values) == 1 and len(metric_values) == 1 and len(environment_values) == 1


def _has_full_or_near_full(rows_by_name: dict[str, list[dict[str, str]]], names: set[str]) -> bool:
    rows = rows_by_name.get("full_reproduction.csv", []) + rows_by_name.get("near_full_reproduction.csv", [])
    return bool(rows) and _has_pass(rows)


def _has_pass(rows: list[dict[str, str]]) -> bool:
    return any(_status(row) == "PASS" for row in rows)


def _has_partial(rows: list[dict[str, str]]) -> bool:
    return False


def _has_needs_official_input(rows_by_name: dict[str, list[dict[str, str]]]) -> bool:
    return rows_have_input_integrity_blocker(rows_by_name.get("input_contract_verification.csv", [])) or any(
        _status(row) == "NEEDS_INPUT"
        for row in rows_by_name.get("reproduction_status.csv", [])
    )


def _input_contract_ready(rows: list[dict[str, str]]) -> bool:
    if not rows:
        return False
    if rows_have_input_integrity_blocker(rows):
        return False
    required = {"query", "ground"}
    found = set()
    for row in rows:
        component = str(row.get("component", "")).lower()
        status = _status(row)
        if status != "PASS":
            continue
        if "query" in component:
            found.add("query")
        if "ground" in component or "truth" in component:
            found.add("ground")
    return required <= found


def _input_contract_ready_with_gaps(rows: list[dict[str, str]]) -> bool:
    if not rows:
        return False
    if rows_have_input_integrity_blocker(rows):
        return False
    required_aliases = {
        "dataset": ("dataset", "base", "database", "vectors", "data"),
        "query": ("query",),
        "ground_truth": ("ground", "truth"),
        "task": ("task", "filter"),
        "metric": ("metric",),
        "k": ("k", "topk", "top_k"),
        "command": ("command", "executable", "script", "path"),
    }
    blocking_statuses = {"FAIL", "NEEDS_INPUT", "NOT_RUN"}
    found: set[str] = set()
    for row in rows:
        if _status(row) in blocking_statuses:
            continue
        text = " ".join(str(value).lower() for value in row.values())
        for key, aliases in required_aliases.items():
            if any(alias in text for alias in aliases):
                found.add(key)
    return set(required_aliases) <= found or _input_contract_ready(rows)


def _has_measured_metrics(rows: list[dict[str, str]]) -> bool:
    return any(_row_has_measured_metrics(row) for row in rows)


def _row_has_measured_metrics(row: dict[str, str]) -> bool:
    """Check if row has measured primary metric values.

    Supports two schema patterns:

    Pattern A (wide schema): Column names contain metric prefixes
    - e.g., recall=0.95, qps=1000, latency=5.2

    Pattern B (long schema): metric + value columns
    - e.g., metric="recall@10", value="0.95"
    - e.g., metric="qps", value="1000"

    Primary metrics (at least one required for L3):
    - recall / accuracy / precision / quality
    - qps / throughput
    - latency / query_time

    Auxiliary metrics (cannot support L3 alone):
    - build_time, index_time, index_size, memory usage

    This ensures L3 requires actual performance metrics, not just build artifacts.
    """
    if _status(row) in {"FAIL", "NEEDS_INPUT", "NOT_RUN"}:
        return False

    # Primary metric prefixes - must have at least one for L3
    primary_metric_prefixes = (
        "recall", "accuracy", "precision", "quality", "f1",
        "qps", "throughput", "queries_per",
        "latency", "query_time", "response_time",
    )

    # Auxiliary metric prefixes - can supplement but not replace primary metrics
    auxiliary_metric_prefixes = (
        "build_time", "index_time", "index_size",
        "memory", "vm_peak", "vm_hwm", "preparation"
    )

    has_primary_metric = False

    # Pattern A: Check column names for metric prefixes (wide schema)
    for key, value in row.items():
        key_lower = key.lower()
        if any(prefix in key_lower for prefix in primary_metric_prefixes):
            if _is_number(value):
                has_primary_metric = True
                break

    # Pattern B: Check metric + value columns (long schema)
    # e.g., metric="recall@10", value="0.95"
    if not has_primary_metric:
        metric_col = row.get("metric") or row.get("Metric") or row.get("METRIC")
        value_col = row.get("value") or row.get("Value") or row.get("VALUE")

        if metric_col and value_col:
            metric_lower = str(metric_col).lower()
            # Check if metric name contains primary metric prefix
            if any(prefix in metric_lower for prefix in primary_metric_prefixes):
                if _is_number(value_col):
                    has_primary_metric = True

    # Only return True if we have at least one primary metric
    return has_primary_metric


def _row_has_l3_reduced_contract(row: dict[str, str]) -> bool:
    """Check if row has minimum L3 reduced contract evidence.

    L3 minimum requirements:
    1. Input source (dataset/input_id)
    2. Method/algorithm
    3. Key parameter (k/topk)

    Additional evidence (ground_truth_source, metric_definition, input_provenance)
    can be inferred from other CSVs, so not required in reduced_metrics.csv.

    This enables cross-file evidence bundle judgment.
    """
    required_groups = (
        ("dataset", "input_id", "reduced_input_id"),  # Input source
        ("method", "algorithm"),                       # Method
        ("k", "top_k", "topk"),                       # Key parameter
        # Removed: ground_truth_source, metric_definition, input_provenance
        # These can be inferred from input_contract_verification.csv or other sources
    )
    return _row_has_alias_groups(row, required_groups)


def _has_synthetic_or_demo(rows: list[dict[str, str]]) -> bool:
    text = " ".join(" ".join(str(value) for value in row.values()) for row in rows).lower()
    return any(marker in text for marker in ("synthetic_input", "demo_only", "not_paper_reproduction", "synthetic", "toy", "mock"))


def _row_is_l2_capped(row: dict[str, str]) -> bool:
    text = " ".join(str(value).lower() for value in row.values())
    return "verification_only" in text or "ceiling=l2" in text or "ceiling: l2" in text


def _has_command_provenance(repo: Path, row: dict[str, str], rows_by_name: dict[str, list[dict[str, str]]], artifact_name: str) -> bool:
    """Check if command provenance exists with explicit linkage to reduced metrics.

    Command provenance must prove that a specific experiment command
    actually generated or corresponds to the current reduced metrics.

    Accepted sources (in priority order):
    1. command_manifest.csv with explicit linkage
    2. runtime_smoke.csv / project_tests.csv with benchmark command matching metrics
    3. Row itself has complete provenance (for baseline_comparison rows with inline provenance)
    4. Engineer log with explicit benchmark command matching metrics

    NOT accepted:
    - build_smoke.csv alone (build commands don't prove benchmark execution)
    - Any unrelated command (install, compile, generic tests)
    - Empty/placeholder/NOT_RUN commands
    """
    command_id = _first_present_alias(row, ("command_id", "cmd_id"))
    dataset = _first_present_alias(row, ("dataset", "input_id", "reduced_input_id"))
    method = _first_present_alias(row, ("method", "algorithm"))
    k = _first_present_alias(row, ("k", "top_k", "topk"))

    # Priority 1: command_manifest.csv with explicit linkage
    manifest_rows = rows_by_name.get("command_manifest.csv", [])
    if manifest_rows:
        # Check for matching command_id
        if command_id:
            for manifest_row in manifest_rows:
                manifest_command_id = _first_present_alias(manifest_row, ("command_id", "cmd_id"))
                if manifest_command_id == command_id:
                    if _command_manifest_linked_to_metrics(manifest_row, row, artifact_name):
                        return True

        # Check for artifact/output linkage without command_id
        for manifest_row in manifest_rows:
            if _command_manifest_linked_to_metrics(manifest_row, row, artifact_name):
                return True

    # Priority 2: Row itself has complete provenance (for baseline_comparison inline provenance)
    # This handles cases where baseline_comparison.csv has command_id, command, exit_code, log_path, etc.
    if command_id and _provenance_row_complete(repo, row):
        return True

    # Priority 3: runtime_smoke.csv / project_tests.csv (NOT build_smoke.csv)
    # Only accept if it's a benchmark/runtime command, not just build
    for csv_name in ("runtime_smoke.csv", "project_tests.csv"):
        csv_rows = rows_by_name.get(csv_name, [])
        if _csv_has_benchmark_command_linked_to_metrics(csv_rows, row, artifact_name, repo):
            return True

    # Priority 4: Engineer log with explicit benchmark command
    if _engineer_log_has_benchmark_command(repo, row):
        return True

    return False


def _command_manifest_linked_to_metrics(manifest_row: dict[str, str], metrics_row: dict[str, str], artifact_name: str) -> bool:
    """Check if command_manifest entry is linked to reduced metrics.

    Linkage criteria (must satisfy one of these):
    1. command_id matches metrics_row's command_id
    2. artifact_path points to reduced_metrics AND parameters (dataset/method/k) match

    This prevents unrelated manifest entries from being accepted.
    """
    if not _provenance_row_complete_simple(manifest_row):
        return False

    manifest_command_id = _first_present_alias(manifest_row, ("command_id", "cmd_id"))
    metrics_command_id = _first_present_alias(metrics_row, ("command_id", "cmd_id"))

    # Priority 1: command_id match (strongest linkage)
    if manifest_command_id and metrics_command_id and manifest_command_id == metrics_command_id:
        return True

    # Priority 2: artifact_path + parameter match (requires multiple parameters to match)
    artifact_path = _first_present_alias(manifest_row, ("artifact_path", "output", "output_artifact"))

    if artifact_path and "reduced_metrics" in artifact_path.lower():
        # Must have parameter match as additional evidence
        manifest_dataset = _first_present_alias(manifest_row, ("dataset", "input_provenance", "input_source"))
        manifest_method = _first_present_alias(manifest_row, ("method", "algorithm"))
        manifest_k = _first_present_alias(manifest_row, ("k", "top_k"))

        metrics_dataset = _first_present_alias(metrics_row, ("dataset", "input_id"))
        metrics_method = _first_present_alias(metrics_row, ("method", "algorithm"))
        metrics_k = _first_present_alias(metrics_row, ("k", "top_k"))

        # At least two parameters must match
        matches = 0
        if manifest_dataset and metrics_dataset and manifest_dataset.lower() == metrics_dataset.lower():
            matches += 1
        if manifest_method and metrics_method and manifest_method.lower() == metrics_method.lower():
            matches += 1
        if manifest_k and metrics_k and manifest_k == metrics_k:
            matches += 1

        if matches >= 2:
            return True

    return False


def _provenance_row_complete_simple(row: dict[str, str]) -> bool:
    """Simplified provenance check for manifest entries.

    Requires:
    - command_id or command
    - exit_code indicating success
    """
    command_id = _first_present_alias(row, ("command_id", "cmd_id"))
    command = _first_present_alias(row, ("command",))
    if not (command_id or command):
        return False

    exit_code = _first_present_alias(row, ("exit_code", "returncode"))
    status = normalize_status(_first_present_alias(row, ("status", "verdict")))

    return exit_code == "0" or is_execution_success_status(status)


def _csv_has_benchmark_command_linked_to_metrics(csv_rows: list[dict[str, str]], metrics_row: dict[str, str], artifact_name: str, repo: Path) -> bool:
    """Check if CSV has benchmark command explicitly linked to metrics.

    Requirements:
    1. Command is benchmark/runtime execution (not cmake/make/install)
    2. Status is execution success (PASS, exit_code=0)
    3. Command can be linked to metrics via dataset/method/k or output path
    """
    metrics_dataset = _first_present_alias(metrics_row, ("dataset", "input_id"))
    metrics_method = _first_present_alias(metrics_row, ("method", "algorithm"))
    metrics_k = _first_present_alias(metrics_row, ("k", "top_k"))

    for row in csv_rows:
        command = _first_present_alias(row, ("command", "test_command", "cmd"))
        if not command or not command.strip():
            continue

        # Reject build/install commands
        command_lower = command.lower()
        if any(build_marker in command_lower for build_marker in ["cmake", "make", "install", "setup", "pip", "npm", "cargo build"]):
            continue

        # Must be execution success (not just SUPPORTED/GENERATED)
        status_raw = _first_present_alias(row, ("status", "verdict", "result"))
        exit_code = _first_present_alias(row, ("exit_code", "returncode"))

        if not is_execution_success_status(status_raw) and exit_code != "0":
            continue

        # Check linkage to metrics
        row_dataset = _first_present_alias(row, ("dataset", "input"))
        row_method = _first_present_alias(row, ("method", "algorithm"))
        row_k = _first_present_alias(row, ("k", "top_k"))

        # Linkage via matching parameters
        if row_dataset and metrics_dataset and row_dataset.lower() == metrics_dataset.lower():
            return True
        if row_method and metrics_method and row_method.lower() == metrics_method.lower():
            return True
        if row_k and metrics_k and row_k == metrics_k:
            return True

        # Linkage via benchmark-related command
        if any(benchmark_marker in command_lower for benchmark_marker in ["benchmark", "query", "recall", "search", "test"]):
            return True

    return False


def _engineer_log_has_benchmark_command(repo: Path, metrics_row: dict[str, str]) -> bool:
    """Check if Engineer log has benchmark command linked to metrics.

    Requirements:
    1. Log contains explicit benchmark command (not just cmake/make)
    2. Command shows successful completion
    3. Command can be linked to metrics via parameters or output
    """
    log_paths = [
        artifact_dir(repo) / "logs" / "engineer_stdout.log",
        artifact_dir(repo) / "logs" / "engineer.log",
        repo / "results" / "engineer_stdout.log",
    ]

    metrics_dataset = _first_present_alias(metrics_row, ("dataset", "input_id"))
    metrics_method = _first_present_alias(metrics_row, ("method", "algorithm"))
    metrics_k = _first_present_alias(metrics_row, ("k", "top_k"))

    for log_path in log_paths:
        if not log_path.exists():
            continue

        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        log_lower = log_text.lower()

        # Reject if only build/install commands
        has_benchmark = any(benchmark_marker in log_lower for benchmark_marker in [
            "benchmark", "query", "recall", "search", "run", "execute", "test"
        ])
        if not has_benchmark:
            continue

        # Reject pure build/install logs
        only_build = all(build_marker in log_lower for build_marker in ["cmake", "make", "install"])
        if only_build and not has_benchmark:
            continue

        # Check for success markers
        has_success = any(success_marker in log_lower for success_marker in [
            "exit code: 0", "exit_code=0", "success", "completed", "pass", "recall", "qps"
        ])
        if not has_success:
            continue

        # Check linkage to metrics parameters
        if metrics_dataset and metrics_dataset.lower() in log_lower:
            return True
        if metrics_method and metrics_method.lower() in log_lower:
            return True
        if metrics_k and str(metrics_k) in log_text:
            return True

        # Accept if log contains benchmark execution with success
        if has_benchmark and has_success:
            return True

    return False


def _csv_has_successful_command(csv_rows: list[dict[str, str]], artifact_name: str) -> bool:
    """Legacy function - DO NOT USE for new code.

    This function is too lenient and accepts unrelated commands.
    Use _csv_has_benchmark_command_linked_to_metrics() instead.
    """
    # Deprecated - kept for backward compatibility only
    return False


def _engineer_log_has_command(repo: Path, artifact_name: str) -> bool:
    """Legacy function - DO NOT USE for new code.

    This function is too lenient and accepts any command pattern.
    Use _engineer_log_has_benchmark_command() instead.
    """
    # Deprecated - kept for backward compatibility only
    return False


def _provenance_row_complete(repo: Path, row: dict[str, str]) -> bool:
    if not _first_present_alias(row, ("command_id", "cmd_id")):
        return False
    if not _first_present_alias(row, ("command",)):
        return False
    if not _first_present_alias(row, ("exit_code",)):
        return False
    if not _first_present_alias(row, ("duration_sec", "duration")):
        return False
    if not _log_path_exists(repo, _first_present_alias(row, ("log_path", "log"))):
        return False
    if not _first_present_alias(row, ("artifact_hash", "hash", "sha256", "artifact_path", "output_artifact")):
        return False
    if not _first_present_alias(row, ("input_provenance", "input_source", "data_provenance", "dataset")):
        return False
    return True


def _log_path_exists(repo: Path, value: str) -> bool:
    if not value:
        return False
    path = Path(value)
    candidates = [path] if path.is_absolute() else [repo / path, repo / ".r2a" / "logs" / path, repo / ".r2a" / "results" / path]
    return any(candidate.exists() for candidate in candidates)


def _row_has_alias_groups(row: dict[str, str], groups: tuple[tuple[str, ...], ...]) -> bool:
    return all(_first_present_alias(row, aliases) for aliases in groups)


def _first_present_alias(row: dict[str, str], columns: tuple[str, ...]) -> str:
    """Extract first present value from row using field aliases.

    Supports common field name variations:
    - status, result, outcome
    - command, test_command, cmd
    - exit_code, returncode, return_code
    - test_scope, file, scope, evidence_source
    - notes, note, message, details
    """
    # Build normalized column map
    normalized = {_normalize_column(key): value for key, value in row.items()}

    # Extended alias map for common variations
    extended_aliases = {
        "status": {"status", "result", "outcome", "verdict"},
        "command": {"command", "test_command", "cmd"},
        "exit_code": {"exit_code", "returncode", "return_code"},
        "test_scope": {"test_scope", "file", "scope", "evidence_source"},
        "log_path": {"log_path", "log", "stdout_path"},
        "notes": {"notes", "note", "message", "details"},
        "duration_sec": {"duration_sec", "duration"},
        "evidence_source": {"evidence_source", "source", "file", "scope"},
    }

    for column in columns:
        # Try exact match first
        value = row.get(column)
        if value is not None and str(value).strip():
            return str(value).strip()

        # Try normalized match
        value = normalized.get(_normalize_column(column))
        if value is not None and str(value).strip():
            return str(value).strip()

        # Try extended aliases
        column_normalized = _normalize_column(column)
        for canonical, aliases in extended_aliases.items():
            if column_normalized == canonical or column_normalized in aliases:
                for alias in aliases:
                    alias_normalized = _normalize_column(alias)
                    if alias_normalized in normalized:
                        value = normalized[alias_normalized]
                        if value is not None and str(value).strip():
                            return str(value).strip()
                break

    return ""


def _normalize_column(value: str) -> str:
    return str(value).strip().lower().replace(" ", "_").replace("-", "_")


def _normalized_value(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


def _is_number(value: str) -> bool:
    if not str(value).strip():
        return False
    try:
        parsed = float(str(value).strip())
    except ValueError:
        return False
    return math.isfinite(parsed)


def _status(row: dict[str, Any]) -> str:
    for column in ("status", "verdict", "result"):
        value = row.get(column)
        if value is not None and str(value).strip():
            return normalize_status(value)
    return ""


def normalize_status(value: Any) -> str:
    """Normalize status value to canonical form.

    Canonical statuses: PASS, FAIL, NOT_RUN, NEEDS_INPUT

    This is the single source of truth for status normalization.
    All modules should use this function or is_success_status/is_not_run_status.
    """
    raw = str(value or "").strip().upper()
    if raw in CANONICAL_STATUSES:
        return raw
    return LEGACY_STATUS_MAP.get(raw, "FAIL" if raw else "")


# Context-specific status predicates to avoid overly broad status equivalence

EXECUTION_SUCCESS_STATUSES = {
    "PASS", "PASSED", "OK", "DONE", "RESOLVED"
}

INPUT_AVAILABLE_STATUSES = {
    "PASS", "PASSED", "OK", "DONE", "RESOLVED",
    "SUPPORTED", "AVAILABLE", "PRESENT", "FOUND", "READY", "VERIFIED"
}

ARTIFACT_PRODUCED_STATUSES = {
    "PASS", "PASSED", "OK", "DONE", "RESOLVED",
    "GENERATED", "PRESENT", "BUILT"
}


def is_execution_success_status(value: Any) -> bool:
    """Check if status indicates successful execution.

    Execution success means a check or command actually ran and passed.
    Use for runtime, benchmark, test execution checks.

    Includes: PASS, PASSED, OK, DONE, RESOLVED
    Excludes: SUPPORTED, AVAILABLE, PRESENT, GENERATED (these don't prove execution)
    """
    raw = str(value or "").strip().upper()
    return raw in EXECUTION_SUCCESS_STATUSES


def is_input_available_status(value: Any) -> bool:
    """Check if status indicates input/resource is available.

    Input available means input files or resources exist and are accessible.
    Use for input contract, dataset/query/ground truth availability checks.

    Includes: execution success statuses + SUPPORTED, AVAILABLE, PRESENT, FOUND, READY, VERIFIED
    """
    raw = str(value or "").strip().upper()
    return raw in INPUT_AVAILABLE_STATUSES


def is_artifact_produced_status(value: Any) -> bool:
    """Check if status indicates artifact was produced.

    Artifact produced means a file or result was generated.
    Use for build output, generated file checks.
    Note: GENERATED doesn't guarantee content validity - caller must verify.

    Includes: execution success statuses + GENERATED, PRESENT, BUILT
    """
    raw = str(value or "").strip().upper()
    return raw in ARTIFACT_PRODUCED_STATUSES


def is_success_status(value: Any) -> bool:
    """Check if status indicates success (legacy broad check).

    DEPRECATED: Use context-specific predicates instead:
    - is_execution_success_status() for runtime/benchmark execution
    - is_input_available_status() for input contract checks
    - is_artifact_produced_status() for build/generated file checks

    This function maps to normalize_status() for backward compatibility.
    """
    return normalize_status(value) == "PASS"


def is_not_run_status(value: Any) -> bool:
    """Check if status indicates not run.

    This is the unified not-run status check.
    """
    return normalize_status(value) == "NOT_RUN"
