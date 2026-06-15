from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

from r2a.core.config import DEFAULT_CODEX_EXECUTABLE
from r2a.core.paths import artifact_dir, iteration_dir
from r2a.tools.codex_cli import check_codex_cli, format_codex_cli_error
from r2a.tools.process_tree import run_command_with_timeout
from r2a.tools.stage_guard import check_stage_allowed_modifications, snapshot_stage_changes


def run_codex_stage(
    repo_path: str | Path,
    stage: str,
    prompt: str,
    allowed_outputs: list[str],
    iteration: int | None = None,
    timeout: int = 10800,
    codex_executable_path: str | None = None,
    language: str = "en",
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    repo = Path(repo_path)
    effective_allowed_outputs = _allowed_outputs_with_logs(stage, allowed_outputs)
    baseline_changes = snapshot_stage_changes(repo)
    full_prompt = _build_stage_prompt(stage, prompt, effective_allowed_outputs, iteration, language)
    cli_check = check_codex_cli(codex_executable_path)
    attempted_executable = cli_check.attempted_executable
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
    stdout = ""
    stderr = ""
    returncode = 0

    if not cli_check.available:
        stderr = format_codex_cli_error(cli_check)
        returncode = _check_failure_code(cli_check.error)
        guard = check_stage_allowed_modifications(repo, stage, effective_allowed_outputs, baseline_changes)
        stderr = _append_guard_message(stderr, guard)
        stdout_log, stderr_log = _write_stage_logs(repo, stage, stdout, stderr, attempted_executable, command)
        if iteration is not None:
            _archive_stage_logs(repo, stage, iteration, stdout_log, stderr_log)
        return {
            "stage": stage,
            "returncode": returncode,
            "stdout_log_path": str(stdout_log),
            "stderr_log_path": str(stderr_log),
            "stdout_tail": "",
            "stderr_tail": _tail(stderr),
            "allowed_outputs": effective_allowed_outputs,
            "codex_executable_path": attempted_executable,
            "attempted_executable": attempted_executable,
            "resolved_executable": cli_check.resolved_path,
            "command": command,
            "success": False,
            "error": cli_check.error,
            "hint": cli_check.hint,
            "baseline_changed_files": guard.get("baseline_changed_files", []),
            "stage_changed_files": guard.get("stage_changed_files", []),
            "signature_changed_files": guard.get("signature_changed_files", []),
            "unexpected_modifications": guard["unexpected_modifications"],
            "stage_guard_ok": guard["ok"],
            "guard_available": guard["guard_available"],
            "guard_backend": guard.get("guard_backend", ""),
            "stage_guard_error": guard["error"],
            "stage_guard_warning": guard["warning"],
            "failure_category": guard.get("failure_category", ""),
            "execution_status": guard.get("execution_status", ""),
        }

    try:
        _write_stage_logs(
            repo,
            stage,
            "Codex stage started; waiting for subprocess output.\n",
            "",
            attempted_executable,
            command,
        )
        completed = run_command_with_timeout(
            command,
            cwd=str(repo),
            input_text=full_prompt,
            timeout=timeout,
            env=env,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        returncode = int(completed.returncode)
        if completed.timed_out:
            stderr += f"\nTimeoutExpired: stage `{stage}` exceeded {timeout} seconds while invoking `{attempted_executable}`. The Codex process tree was terminated."
    except FileNotFoundError as exc:
        stderr = f"FileNotFoundError while invoking `{attempted_executable}`: {exc}"
        returncode = 127
    except PermissionError as exc:
        stderr = f"PermissionError while invoking `{attempted_executable}`: {exc}"
        returncode = 126

    guard = check_stage_allowed_modifications(repo, stage, effective_allowed_outputs, baseline_changes)
    stderr = _append_guard_message(stderr, guard)
    stdout_log, stderr_log = _write_stage_logs(repo, stage, stdout, stderr, attempted_executable, command)
    if iteration is not None:
        _archive_stage_logs(repo, stage, iteration, stdout_log, stderr_log)
    success = returncode == 0 and guard["ok"]
    error = stderr if returncode != 0 else ""
    if returncode == 0 and not guard["ok"]:
        if not guard.get("guard_available", True):
            error = f"Stage guard could not verify modifications: {guard['error']}"
        else:
            error = f"Stage guard rejected unexpected modifications: {guard['unexpected_modifications']}"
    return {
        "stage": stage,
        "returncode": returncode,
        "stdout_log_path": str(stdout_log),
        "stderr_log_path": str(stderr_log),
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
        "allowed_outputs": effective_allowed_outputs,
        "codex_executable_path": attempted_executable,
        "attempted_executable": attempted_executable,
        "resolved_executable": cli_check.resolved_path,
        "command": command,
        "success": success,
        "error": error,
        "hint": "",
        "baseline_changed_files": guard.get("baseline_changed_files", []),
        "stage_changed_files": guard.get("stage_changed_files", []),
        "signature_changed_files": guard.get("signature_changed_files", []),
        "unexpected_modifications": guard["unexpected_modifications"],
        "stage_guard_ok": guard["ok"],
        "guard_available": guard.get("guard_available", True),
        "guard_backend": guard.get("guard_backend", ""),
        "stage_guard_error": guard.get("error", ""),
        "stage_guard_warning": guard.get("warning", ""),
        "failure_category": guard.get("failure_category", ""),
        "execution_status": guard.get("execution_status", ""),
    }


def _allowed_outputs_with_logs(stage: str, allowed_outputs: list[str]) -> list[str]:
    outputs = list(allowed_outputs)
    for path in (f".r2a/logs/{stage}_stdout.log", f".r2a/logs/{stage}_stderr.log"):
        if path not in outputs:
            outputs.append(path)
    return outputs


def _build_stage_prompt(stage: str, prompt: str, allowed_outputs: list[str], iteration: int | None, language: str) -> str:
    allowed = "\n".join(f"- {item}" for item in allowed_outputs)
    iteration_text = "not applicable" if iteration is None else str(iteration)
    language_name = "Simplified Chinese" if language == "zh" else "English"
    return (
        f"R2A Codex stage: {stage}\n"
        f"Iteration: {iteration_text}\n\n"
        f"Output language: {language_name}\n"
        f"- Write every generated Markdown report, status summary, and final message in {language_name}.\n"
        "- If the output language is Simplified Chinese, all natural-language prose must be Simplified Chinese.\n"
        "- Keep literal file paths, command names, schema keys, and verdict labels unchanged.\n\n"
        "Allowed output files/directories:\n"
        f"{allowed}\n\n"
        "Hard safety constraints:\n"
        "- Do not use yolo, bypass sandbox, or dangerous approval bypass options.\n"
        "- Do not modify files outside the allowed output list for this stage.\n"
        "- If you cannot complete the stage safely, write the limitation in the allowed report and stop.\n"
        "- Do not fabricate paper facts, experiment results, or rule-check outcomes.\n\n"
        "Shared R2A evidence policy:\n"
        "- Backend choice affects the execution model, not R2A evidence rules.\n"
        "- Codex, Claude Code, and any other model backend must obey the stage prompt and `r2a/prompts/R2A_PROTOCOL.md`.\n"
        "- Do not bypass `.r2a/TASK_SPEC.md`, `.r2a/EXPERIMENT_CONTRACT.md`, `CHECK_REPORT.md`, `EXECUTION_REPORT.md`, or `REVIEW_FEEDBACK.json` when the stage prompt requires them.\n"
        "- Do not inflate smoke tests, synthetic demos, or unofficial reimplementations into L3/L4 evidence.\n\n"
        "Stage instructions:\n"
        f"{prompt}\n"
    )


def _write_stage_logs(repo: Path, stage: str, stdout: str, stderr: str, attempted_executable: str, command: list[str]) -> tuple[Path, Path]:
    logs_dir = artifact_dir(repo) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = logs_dir / f"{stage}_stdout.log"
    stderr_log = logs_dir / f"{stage}_stderr.log"
    header = f"codex_executable_path: {attempted_executable}\ncommand: {command[:-1]} [...prompt omitted...]\n\n"
    stdout_log.write_text(header + (stdout or ""), encoding="utf-8")
    stderr_log.write_text(header + (stderr or ""), encoding="utf-8")
    return stdout_log, stderr_log


def _archive_stage_logs(repo: Path, stage: str, iteration: int, stdout_log: Path, stderr_log: Path) -> None:
    logs_dir = iteration_dir(repo, iteration) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(stdout_log, logs_dir / f"{stage}_stdout.log")
    shutil.copy2(stderr_log, logs_dir / f"{stage}_stderr.log")


def _tail(text: str, max_lines: int = 80) -> str:
    lines = (text or "").splitlines()
    return "\n".join(lines[-max_lines:])


def _append_guard_message(stderr: str, guard: dict[str, Any]) -> str:
    guard.setdefault("guard_available", True)
    guard.setdefault("error", "")
    guard.setdefault("warning", "")
    guard.setdefault("unexpected_modifications", [])
    messages: list[str] = []
    if guard.get("warning"):
        messages.append(str(guard["warning"]))
    if guard.get("error"):
        messages.append(str(guard["error"]))
    if guard.get("unexpected_modifications"):
        messages.append(f"Unexpected modifications: {guard['unexpected_modifications']}")
    if guard.get("failure_category"):
        messages.append(f"failure_category: {guard['failure_category']}")
    if guard.get("execution_status"):
        messages.append(f"execution_status: {guard['execution_status']}")
    if not messages:
        return stderr
    guard_text = "\n".join(messages)
    return f"{stderr.rstrip()}\n\nStage Guard:\n{guard_text}\n" if stderr else f"Stage Guard:\n{guard_text}\n"


def _check_failure_code(error: str) -> int:
    lowered = (error or "").lower()
    if "permissionerror" in lowered or "access is denied" in lowered or "winerror 5" in lowered:
        return 126
    if "timed out" in lowered:
        return 124
    return 127
