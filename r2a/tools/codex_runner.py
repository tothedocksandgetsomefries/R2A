from __future__ import annotations

import csv
from dataclasses import dataclass
import json
from pathlib import Path

from r2a.core.config import DEFAULT_CODEX_EXECUTABLE
from r2a.core.paths import artifact_dir, report_path
from r2a.core.user_hints import format_user_hints_markdown, normalize_user_hints
from r2a.tools.codex_cli import check_codex_cli, format_codex_cli_error
from r2a.tools.process_tree import run_command_with_timeout
from r2a.tools.prompt_loader import render_prompt
from r2a.tools.stage_guard import check_stage_allowed_modifications, snapshot_stage_changes

ENGINEER_PROMPT_CONTEXT_BUDGET_CHARS = 100_000
ENGINEER_ARTIFACT_CONTEXT_MAX_CHARS = 24_000
ENGINEER_TEXT_TAIL_CHARS = 4_000
ENGINEER_CSV_SAMPLE_ROWS = 5
ENGINEER_CSV_SCAN_ROW_LIMIT = 10_000


def _get_engineer_source_root(repo: Path) -> Path | None:
    """Get the source artifact root from SOURCE_ACQUISITION.json for Engineer execution."""
    acquisition_path = artifact_dir(repo) / "SOURCE_ACQUISITION.json"
    if not acquisition_path.exists():
        return None
    try:
        data = json.loads(acquisition_path.read_text(encoding="utf-8"))
        local_path = str(data.get("local_path", "") or "").strip()
        if local_path:
            source_root = Path(local_path)
            if source_root.exists() and source_root.is_dir():
                return source_root.resolve()
    except (OSError, json.JSONDecodeError):
        pass
    # Fallback to artifacts/source
    artifacts_source = repo / ".r2a" / "artifacts" / "source"
    if artifacts_source.exists() and artifacts_source.is_dir():
        return artifacts_source.resolve()
    return None


@dataclass(frozen=True)
class CodexRunResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    stdout_log_path: str
    stderr_log_path: str
    stdout_tail: str
    stderr_tail: str
    attempted_executable: str = DEFAULT_CODEX_EXECUTABLE
    skipped: bool = False
    error: str = ""
    hint: str = ""
    backend_error: dict[str, object] | None = None
    is_backend_failure: bool = False
    transient_backend_failure: bool = False
    backend_failure_category: str = ""
    backend_failure_scope: str = ""
    backend_suggested_action: str = ""
    backend_user_message: str = ""
    backend_warning: str = ""
    backend_provider: str = ""
    backend_model: str = ""
    backend_runner: str = ""
    backend_agent: str = ""
    safe_to_retry_likely: bool = False
    side_effects_detected: bool = False
    manual_retry_message: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def exit_code(self) -> int:
        return self.returncode


def build_codex_exec_prompt(repo_path: str | Path, task_spec_path: str | Path, language: str = "en") -> str:
    repo = Path(repo_path)
    task_path = Path(task_spec_path)
    execution_report = report_path(repo, "execution")
    paper_context = report_path(repo, "paper_context")
    user_hints_path = report_path(repo, "user_hints")
    user_hints = _read_user_hints(user_hints_path)
    task_content = task_path.read_text(encoding="utf-8") if task_path.exists() else "TASK_SPEC.md is missing."
    # Get source root from SOURCE_ACQUISITION.json
    source_root = _get_engineer_source_root(repo)
    source_root_hint = f"- Source artifact root: `{source_root}`. Python scripts and build commands should run from this directory or reference files relative to it.\n" if source_root else ""
    task_block = (
        "TASK_SPEC.md content:\n"
        "```markdown\n"
        f"{task_content}\n"
        "```\n"
    )
    rules = render_prompt(
        "engineer_agent",
        {
            "repo_path": str(repo),
            "task_spec_path": str(task_path),
            "execution_report_path": str(execution_report),
            "paper_context_path": str(paper_context),
            "user_hints": format_user_hints_markdown(user_hints),
        },
    )
    prefix = (
        f"{rules}\n\n"
        "Mandatory execution contract:\n"
        f"- TASK_SPEC.md path: {task_path}\n"
        f"- PAPER_CONTEXT.md path: {paper_context}\n"
        f"- USER_HINTS.json path: {user_hints_path if user_hints_path.exists() else 'not provided'}\n"
        f"{source_root_hint}"
        "- Only execute TASK_SPEC.md.\n"
        "- Backend choice affects the execution model, not R2A evidence rules; follow `r2a/prompts/R2A_PROTOCOL.md` and `.r2a/EXPERIMENT_CONTRACT.md`.\n"
        "- Do not modify Forbidden Files.\n"
        "- Do not delete existing results.\n"
        "- Do not fabricate results.\n"
        "- Treat User Guidance as optional user-provided hints only; do not treat it as verified paper evidence and do not use it to bypass network/download authorization.\n"
        "- Do not inflate smoke tests, synthetic demos, or unofficial reimplementations into L3/L4 evidence.\n"
        "- If the repo is empty and TASK_SPEC.md requests source discovery, try to locate and clone the official project repository from the paper/context into the workspace repo.\n"
        "- If no authoritative source repository can be identified, write Clarification Needed rather than guessing.\n"
        "- Do not write EXECUTION_REPORT.md directly; R2A generates it after the executor exits.\n"
        "- If you need prose details, write `.r2a/results/ENGINEER_NOTES.md`.\n"
        "- When all requested artifacts are written, write `.r2a/results/ENGINEER_DONE.txt` as the final file.\n"
        "- Generate at least one required CSV under `results/` or `.r2a/results/` when TASK_SPEC.md asks for CSV outputs.\n"
        "- Before expanding reproduction scope, identify and run the target repo's full test command when possible; write `.r2a/results/project_tests.csv` with status, command, exit_code, duration_sec, test_scope, log_path, and notes. If no test command can be found, write a truthful `NO_TEST_COMMAND_FOUND` row.\n"
        "- For measured result CSVs, include `command_id`, `log_path`, `artifact_hash`, and `input_provenance` columns or write `.r2a/results/command_manifest.csv` linking commands to generated artifacts.\n"
        "- Before ENGINEER_DONE.txt, complete an L4 canonical artifact closure checklist for `.r2a/results/reduced_metrics.csv`, `.r2a/results/command_manifest.csv`, `.r2a/results/paper_alignment.csv`, and `.r2a/results/L4_ALIGNMENT_SUMMARY.md`: present/missing, path, row count or summary, required columns/provenance, and explicit missing reason. Do not substitute `reduced_experiment.csv` for `reduced_metrics.csv`; do not fabricate provenance; do not claim L4 closure unless all required canonical artifacts are present.\n"
        "- If execution is blocked, write `.r2a/results/reproduction_status.csv` with headers `status,reason,evidence_source,next_action`.\n"
        "- For Docker tasks, prefer `python -m r2a.tools.docker_runner --repo <repo> --timeout <seconds> ...` so R2A validates safe tags, workspace paths, mounts, timeouts, logs, and CSV provenance before invoking Docker.\n"
        "- Allowed Docker scope: `docker --version`, `docker version`, `docker info`, `docker images`, `docker image inspect <image>`, `docker ps`, bounded `docker build -t r2a-*|fanns-benchmark:* -f <Dockerfile> <context>`, and bounded `docker run --rm ...` smoke commands.\n"
        "- Forbidden Docker actions: prune/rm/rmi/login/push, unapproved large pulls, `--privileged`, root/home/system mounts, and any unbounded long-running build or run.\n"
        "- If failure occurs, write the failure reason clearly.\n"
        "- If the task is unclear, write Clarification Needed.\n"
        "- Follow `.r2a/EXPERIMENT_CONTRACT.md` download budget. Official paper-linked data may be downloaded only within the configured budget, normally 20GB; prefer the smallest viable subset and write NEEDS_INPUT_OR_BUDGET if the budget or license/access is unclear.\n"
        f"- Write reports and logs in {_language_name(language)} where practical.\n\n"
        + ("- All natural-language prose in EXECUTION_REPORT.md must be Simplified Chinese.\n\n" if language == "zh" else "")
    )
    available_for_artifacts = max(
        2_000,
        min(
            ENGINEER_ARTIFACT_CONTEXT_MAX_CHARS,
            ENGINEER_PROMPT_CONTEXT_BUDGET_CHARS - len(prefix) - len(task_block) - 1_000,
        ),
    )
    artifact_context = build_engineer_artifact_context(repo, max_chars=available_for_artifacts)
    prompt = f"{prefix}{artifact_context}\n\n{task_block}"
    if len(prompt) > ENGINEER_PROMPT_CONTEXT_BUDGET_CHARS:
        notice = (
            "\n\n[ENGINEER_CONTEXT_BUDGET_TRUNCATED]\n"
            f"truncated=true; original_chars={len(prompt)}; "
            f"max_chars={ENGINEER_PROMPT_CONTEXT_BUDGET_CHARS}. "
            "Use the file paths above as authoritative provenance.\n"
        )
        prompt = prompt[: ENGINEER_PROMPT_CONTEXT_BUDGET_CHARS - len(notice)] + notice
    return prompt


def build_engineer_artifact_context(
    repo_path: str | Path,
    *,
    max_chars: int = ENGINEER_ARTIFACT_CONTEXT_MAX_CHARS,
    text_tail_chars: int = ENGINEER_TEXT_TAIL_CHARS,
    csv_sample_rows: int = ENGINEER_CSV_SAMPLE_ROWS,
) -> str:
    repo = Path(repo_path)
    results_dirs = [artifact_dir(repo) / "results", repo / "results"]
    log_dirs = [artifact_dir(repo) / "logs"]
    lines = [
        "Engineer context budget:",
        f"- max_prompt_chars: {ENGINEER_PROMPT_CONTEXT_BUDGET_CHARS}",
        f"- artifact_context_max_chars: {max_chars}",
        f"- text_tail_chars: {text_tail_chars}",
        "- Long logs/text artifacts are represented by tail excerpts only.",
        "- CSV artifacts are represented by path, size, row count, columns, sample rows, and numeric min/max/count where practical.",
        "- Do not paste full large logs or historical invocation transcripts into the model; open exact paths only when a focused check is needed.",
        "",
        "## Engineer Artifact Context",
        "",
    ]
    for path in _engineer_context_files(results_dirs, log_dirs):
        summary = _summarize_engineer_context_file(
            path,
            repo=repo,
            text_tail_chars=text_tail_chars,
            csv_sample_rows=csv_sample_rows,
        )
        if summary:
            lines.extend(summary)
            lines.append("")
        current = "\n".join(lines)
        if len(current) > max_chars:
            lines.append(
                f"[artifact_context_truncated] truncated=true; max_chars={max_chars}; more files exist under `.r2a/results` and `.r2a/logs`."
            )
            break
    return _trim_to_chars("\n".join(lines).rstrip() + "\n", max_chars)


def _engineer_context_files(results_dirs: list[Path], log_dirs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for directory in results_dirs:
        if not directory.exists():
            continue
        files.extend(path for path in directory.iterdir() if path.is_file())
    for directory in log_dirs:
        if not directory.exists():
            continue
        for pattern in ("*stderr.log", "*stdout.log", "*.log", "*.txt"):
            files.extend(path for path in directory.glob(pattern) if path.is_file())
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in sorted(files, key=_path_mtime, reverse=True):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique[:40]


def _summarize_engineer_context_file(
    path: Path,
    *,
    repo: Path,
    text_tail_chars: int,
    csv_sample_rows: int,
) -> list[str]:
    try:
        size = path.stat().st_size
    except OSError:
        return []
    rel = _relative_context_path(path, repo)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _summarize_csv_file(path, rel=rel, size=size, sample_rows=csv_sample_rows)
    if suffix in {".log", ".txt", ".md", ".json"}:
        text, truncated = _read_text_tail(path, text_tail_chars)
        label = "tail" if truncated else "content"
        return [
            f"### {rel}",
            f"- kind: text; size_bytes: {size}; truncated={str(truncated).lower()}; original_path: `{path}`",
            f"```text {label}",
            text,
            "```",
        ]
    return [
        f"### {rel}",
        f"- kind: binary_or_unhandled; size_bytes: {size}; truncated=true; original_path: `{path}`",
    ]


def _summarize_csv_file(path: Path, *, rel: str, size: int, sample_rows: int) -> list[str]:
    columns: list[str] = []
    samples: list[dict[str, str]] = []
    numeric: dict[str, dict[str, float | int]] = {}
    row_count = 0
    truncated_scan = False
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = [str(item) for item in (reader.fieldnames or [])]
            for row in reader:
                row_count += 1
                if len(samples) < sample_rows:
                    samples.append({str(key): str(value) for key, value in row.items()})
                _update_numeric_stats(numeric, row)
                if row_count >= ENGINEER_CSV_SCAN_ROW_LIMIT:
                    truncated_scan = True
                    break
    except (OSError, csv.Error, UnicodeError) as exc:
        return [
            f"### {rel}",
            f"- kind: csv; size_bytes: {size}; truncated=true; original_path: `{path}`",
            f"- parse_error: {type(exc).__name__}: {exc}",
        ]
    return [
        f"### {rel}",
        f"- kind: csv; size_bytes: {size}; truncated=true; original_path: `{path}`",
        f"- row_count: {'>=' if truncated_scan else ''}{row_count}",
        f"- columns: {json.dumps(columns, ensure_ascii=False)}",
        f"- sample_rows: {json.dumps(samples, ensure_ascii=False)}",
        f"- numeric_summary: {json.dumps(_final_numeric_stats(numeric), ensure_ascii=False)}",
    ]


def _update_numeric_stats(stats: dict[str, dict[str, float | int]], row: dict[str, object]) -> None:
    for key, value in row.items():
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            continue
        bucket = stats.setdefault(str(key), {"count": 0, "min": number, "max": number})
        bucket["count"] = int(bucket["count"]) + 1
        bucket["min"] = min(float(bucket["min"]), number)
        bucket["max"] = max(float(bucket["max"]), number)


def _final_numeric_stats(stats: dict[str, dict[str, float | int]]) -> dict[str, dict[str, float | int]]:
    return {key: value for key, value in stats.items() if int(value.get("count", 0)) > 0}


def _read_text_tail(path: Path, max_chars: int) -> tuple[str, bool]:
    try:
        size = path.stat().st_size
        if size <= max_chars:
            return path.read_text(encoding="utf-8", errors="replace"), False
        read_bytes = max(max_chars * 4, 4096)
        with path.open("rb") as handle:
            handle.seek(max(0, size - read_bytes))
            data = handle.read()
        text = data.decode("utf-8", errors="replace")
        return _tail_to_chars(text, max_chars), True
    except OSError as exc:
        return f"[unreadable: {type(exc).__name__}: {exc}]", True


def _tail_to_chars(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    notice = f"[truncated=true; original_tail_chars={len(text)}; shown_tail_chars={max_chars}]\n"
    return notice + text[-max(0, max_chars - len(notice)) :]


def _trim_to_chars(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    notice = f"\n[truncated=true; original_chars={len(text)}; max_chars={max_chars}]\n"
    return text[: max(0, max_chars - len(notice))] + notice


def _relative_context_path(path: Path, repo: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _path_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _read_user_hints(path: Path) -> dict:
    if not path.exists():
        return normalize_user_hints({})
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return normalize_user_hints({})
    return normalize_user_hints(data)


def build_codex_exec_command(task_prompt: str) -> list[str]:
    return ["codex", "exec", task_prompt]


def run_codex_exec(
    repo_path: str | Path,
    task_spec_path: str | Path,
    timeout: int = 10800,
    language: str = "en",
    iteration: int = 1,
    auto_iterate: bool = False,
    codex_executable_path: str | None = None,
    env: dict[str, str] | None = None,
) -> CodexRunResult:
    repo = Path(repo_path)
    task_path = Path(task_spec_path)
    baseline_changes = snapshot_stage_changes(repo)
    cli_check = check_codex_cli(codex_executable_path)
    attempted_executable = cli_check.attempted_executable
    task_prompt = build_codex_exec_prompt(repo, task_path, language=language)
    task_prompt += (
        f"\nCurrent iteration: {iteration}\n"
        f"Auto iterate: {auto_iterate}\n"
        + ("This iteration may consume Codex quota.\n" if auto_iterate else "")
    )
    command = [
        attempted_executable,
        "exec",
        "--sandbox",
        "workspace-write",
        "--skip-git-repo-check",
        "--cd",
        str(repo),
        "-",
    ]
    if not cli_check.available:
        stderr = format_codex_cli_error(cli_check)
        stderr = _append_engineer_guard_warning(repo, baseline_changes, stderr)
        stdout_log, stderr_log = _write_codex_logs(repo, "", stderr, attempted_executable, command)
        return CodexRunResult(
            command=command,
            returncode=_check_failure_code(cli_check.error),
            stdout="",
            stderr=stderr,
            stdout_log_path=str(stdout_log),
            stderr_log_path=str(stderr_log),
            stdout_tail="",
            stderr_tail=_tail(stderr),
            attempted_executable=attempted_executable,
            skipped=True,
            error=cli_check.error,
            hint=cli_check.hint,
        )
    try:
        _write_codex_logs(
            repo,
            "Codex executor started; waiting for subprocess output.\n",
            "",
            attempted_executable,
            command,
        )
        completed = run_command_with_timeout(
            command,
            cwd=str(repo),
            input_text=task_prompt,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as exc:
        stderr = f"FileNotFoundError while invoking `{attempted_executable}`: {exc}"
        stderr = _append_engineer_guard_warning(repo, baseline_changes, stderr)
        stdout_log, stderr_log = _write_codex_logs(repo, "", stderr, attempted_executable, command)
        return CodexRunResult(
            command=command,
            returncode=127,
            stdout="",
            stderr=stderr,
            stdout_log_path=str(stdout_log),
            stderr_log_path=str(stderr_log),
            stdout_tail="",
            stderr_tail=_tail(stderr),
            attempted_executable=attempted_executable,
            skipped=True,
            error=str(exc),
        )
    except PermissionError as exc:
        stderr = f"PermissionError while invoking `{attempted_executable}`: {exc}"
        stderr = _append_engineer_guard_warning(repo, baseline_changes, stderr)
        stdout_log, stderr_log = _write_codex_logs(repo, "", stderr, attempted_executable, command)
        return CodexRunResult(
            command=command,
            returncode=126,
            stdout="",
            stderr=stderr,
            stdout_log_path=str(stdout_log),
            stderr_log_path=str(stderr_log),
            stdout_tail="",
            stderr_tail=_tail(stderr),
            attempted_executable=attempted_executable,
            skipped=True,
            error=str(exc),
            hint="The configured Codex executable could not be started because the OS denied access.",
        )

    if completed.timed_out:
        stderr = (completed.stderr or "") + f"\nTimeoutExpired: Codex executor exceeded {timeout} seconds while invoking `{attempted_executable}`. The Codex process tree was terminated."
        stderr = _append_engineer_guard_warning(repo, baseline_changes, stderr)
        stdout_log, stderr_log = _write_codex_logs(repo, completed.stdout, stderr, attempted_executable, command)
        return CodexRunResult(
            command=command,
            returncode=124,
            stdout=completed.stdout,
            stderr=stderr,
            stdout_log_path=str(stdout_log),
            stderr_log_path=str(stderr_log),
            stdout_tail=_tail(completed.stdout),
            stderr_tail=_tail(stderr),
            attempted_executable=attempted_executable,
            skipped=True,
            error=f"Timed out after {timeout} seconds; process tree terminated",
        )
    stderr = _append_engineer_guard_warning(repo, baseline_changes, completed.stderr)
    stdout_log, stderr_log = _write_codex_logs(repo, completed.stdout, stderr, attempted_executable, command)
    return CodexRunResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=stderr,
        stdout_log_path=str(stdout_log),
        stderr_log_path=str(stderr_log),
        stdout_tail=_tail(completed.stdout),
        stderr_tail=_tail(stderr),
        attempted_executable=attempted_executable,
        skipped=False,
        error="" if completed.returncode == 0 else stderr,
    )


def mock_codex_exec(repo_path: str | Path, task_spec_path: str | Path, language: str = "en") -> CodexRunResult:
    stdout = f"安全演示执行器已完成：{Path(repo_path)}。" if language == "zh" else f"Mock executor completed for {Path(repo_path)}."
    stderr = ""
    stdout_log, stderr_log = _write_codex_logs(Path(repo_path), stdout, stderr, "mock-codex", ["mock-codex", "exec", str(task_spec_path)])
    return CodexRunResult(
        command=["mock-codex", "exec", str(task_spec_path)],
        returncode=0,
        stdout=stdout,
        stderr=stderr,
        stdout_log_path=str(stdout_log),
        stderr_log_path=str(stderr_log),
        stdout_tail=_tail(stdout),
        stderr_tail="",
        attempted_executable="mock-codex",
        skipped=True,
    )


def _write_codex_logs(repo_path: Path, stdout: str, stderr: str, attempted_executable: str, command: list[str]) -> tuple[Path, Path]:
    logs_dir = artifact_dir(repo_path) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = logs_dir / "codex_stdout.log"
    stderr_log = logs_dir / "codex_stderr.log"
    header = f"codex_executable_path: {attempted_executable}\ncommand: {command[:-1]} [...prompt omitted...]\n\n"
    stdout_log.write_text(header + (stdout or ""), encoding="utf-8")
    stderr_log.write_text(header + (stderr or ""), encoding="utf-8")
    return stdout_log, stderr_log


def _append_engineer_guard_warning(repo: Path, baseline_changes: set[str], stderr: str) -> str:
    guard = check_stage_allowed_modifications(repo, "engineer", ["*"], baseline_changes)
    if guard.get("guard_available", True):
        return stderr or ""
    message = f"Stage Guard: {guard.get('warning', 'Stage guard could not verify modifications')}: {guard.get('error', '')}"
    return f"{(stderr or '').rstrip()}\n\n{message}\n" if stderr else f"{message}\n"


def _tail(text: str, max_lines: int = 80) -> str:
    lines = (text or "").splitlines()
    return "\n".join(lines[-max_lines:])


def _language_name(language: str) -> str:
    return "Chinese" if language == "zh" else "English"


def _check_failure_code(error: str) -> int:
    lowered = (error or "").lower()
    if "permissionerror" in lowered or "access is denied" in lowered or "winerror 5" in lowered:
        return 126
    if "timed out" in lowered:
        return 124
    return 127
