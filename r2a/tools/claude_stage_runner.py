from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time
from typing import Any

from r2a.core.paths import artifact_dir, iteration_dir
from r2a.tools.backend_errors import BACKEND_TRANSIENT_FAILURE, TOOL_CALL_PARSE_FAILURE, classify_backend_error
from r2a.tools.claude_runner import DISALLOWED_CLAUDE_TOOLS, check_claude_code_cli, format_claude_code_cli_error
from r2a.tools.process_tree import run_command_with_timeout
from r2a.tools.stage_guard import check_stage_allowed_modifications, snapshot_stage_changes


CLAUDE_STAGE_ALLOWED_TOOLS = ",".join(
    [
        "Read",
        "Write",
        "Edit",
        "MultiEdit",
    ]
)


def run_claude_stage(
    repo_path: str | Path,
    stage: str,
    prompt: str,
    allowed_outputs: list[str],
    iteration: int | None = None,
    timeout: int = 10800,
    claude_executable_path: str | None = None,
    language: str = "en",
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    repo = Path(repo_path)
    effective_allowed_outputs = _allowed_outputs_with_logs(stage, allowed_outputs)
    baseline_changes = snapshot_stage_changes(repo)
    full_prompt = _build_stage_prompt(stage, prompt, effective_allowed_outputs, iteration, language)
    prompt_size_bytes = len(full_prompt.encode("utf-8"))
    cli_check = check_claude_code_cli(claude_executable_path)
    attempted_executable = cli_check.attempted_executable
    command, prompt_input_text = _build_claude_stage_command(attempted_executable, repo, stage, full_prompt)
    prompt_file = artifact_dir(repo) / "logs" / f"claude_{stage}_prompt.md"
    stdout = ""
    stderr = ""
    returncode = 0

    if not cli_check.available:
        stderr = format_claude_code_cli_error(cli_check)
        returncode = _check_failure_code(cli_check.error)
        guard = check_stage_allowed_modifications(repo, stage, effective_allowed_outputs, baseline_changes)
        stderr = _append_guard_message(stderr, guard)
        stdout_log, stderr_log = _write_stage_logs(repo, stage, stdout, stderr, attempted_executable, command, prompt_size_bytes)
        if iteration is not None:
            _archive_stage_logs(repo, stage, iteration, stdout_log, stderr_log)
        return _result(
            stage,
            returncode,
            stdout,
            stderr,
            stdout_log,
            stderr_log,
            effective_allowed_outputs,
            attempted_executable,
            command,
            False,
            cli_check.error,
            cli_check.hint,
            guard,
            prompt_size_bytes=prompt_size_bytes,
            prompt_file_path=str(prompt_file) if prompt_file.exists() else "",
        )

    attempts: list[dict[str, Any]] = []
    max_attempts = 2 if _auto_retry_allowed(stage, attempted_executable) else 1

    for attempt in range(1, max_attempts + 1):
        attempt_start_time = time.time()
        stdout = ""
        stderr = ""
        returncode = 0
        try:
            _write_stage_logs(repo, stage, "Claude stage started; waiting for subprocess output.\n", "", attempted_executable, command, prompt_size_bytes)
            completed = run_command_with_timeout(
                command,
                cwd=str(repo),
                input_text=prompt_input_text,
                timeout=timeout,
                env=env,
            )
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            returncode = int(completed.returncode)
            if completed.timed_out:
                stderr += f"\nTimeoutExpired: stage `{stage}` exceeded {timeout} seconds while invoking `{attempted_executable}`. The Claude process tree was terminated."
        except FileNotFoundError as exc:
            stderr = f"FileNotFoundError while invoking `{attempted_executable}`: {exc}"
            returncode = 127
        except PermissionError as exc:
            stderr = f"PermissionError while invoking `{attempted_executable}`: {exc}"
            returncode = 126

        guard = check_stage_allowed_modifications(repo, stage, effective_allowed_outputs, baseline_changes)
        stderr = _append_guard_message(stderr, guard)
        freshness = _validate_required_outputs(repo, effective_allowed_outputs, attempt_start_time)
        backend_error = classify_backend_error(stdout, stderr, backend="claude")
        stdout_log, stderr_log = _write_stage_logs(repo, stage, stdout, stderr, attempted_executable, command, prompt_size_bytes)
        attempt_stdout_log, attempt_stderr_log = _write_attempt_logs(
            repo,
            stage,
            attempt,
            stdout,
            stderr,
            attempted_executable,
            command,
            returncode,
            freshness,
            prompt_size_bytes,
            backend_error,
        )
        if iteration is not None:
            _archive_stage_logs(repo, stage, iteration, stdout_log, stderr_log, attempt_stdout_log, attempt_stderr_log)
        success = (
            returncode == 0
            and guard["ok"]
            and freshness["ok"]
            and not bool(backend_error.get("is_backend_failure", False))
        )
        attempts.append(
            {
                "attempt": attempt,
                "stdout": stdout,
                "stderr": stderr,
                "returncode": returncode,
                "guard": guard,
                "freshness": freshness,
                "success": success,
                "backend_error": backend_error,
                "stdout_log": stdout_log,
                "stderr_log": stderr_log,
                "attempt_stdout_log": attempt_stdout_log,
                "attempt_stderr_log": attempt_stderr_log,
            }
        )
        if success:
            break
        if attempt >= max_attempts or not _should_retry_attempt(attempts[-1]):
            break
        _archive_partial_outputs(repo, stage, attempt, effective_allowed_outputs)
        _cleanup_required_outputs(repo, effective_allowed_outputs)

    final_attempt = attempts[-1]
    stdout = final_attempt["stdout"]
    stderr = final_attempt["stderr"]
    returncode = final_attempt["returncode"]
    guard = final_attempt["guard"]
    freshness = final_attempt["freshness"]
    stdout_log = final_attempt["stdout_log"]
    stderr_log = final_attempt["stderr_log"]
    success = final_attempt["success"]
    error = stderr if returncode != 0 else ""
    if returncode == 0 and not guard["ok"]:
        if not guard.get("guard_available", True):
            error = f"Stage guard could not verify modifications: {guard['error']}"
        else:
            error = f"Stage guard rejected unexpected modifications: {guard['unexpected_modifications']}"
    if returncode == 0 and guard["ok"] and not freshness["ok"]:
        error = "Output freshness validation failed for required stage outputs."
    if returncode == 0 and guard["ok"] and freshness["ok"] and final_attempt.get("backend_error", {}).get("is_backend_failure"):
        error = str(final_attempt["backend_error"].get("user_message") or "Backend transient failure detected in stage output.")
    retry_metadata = _retry_metadata(attempts)
    return _result(
        stage,
        returncode,
        stdout,
        stderr,
        stdout_log,
        stderr_log,
        effective_allowed_outputs,
        attempted_executable,
        command,
        success,
        error,
        "",
        guard,
        freshness,
        retry_metadata,
        prompt_size_bytes=prompt_size_bytes,
        prompt_file_path=str(prompt_file) if prompt_file.exists() else "",
    )


def _build_claude_stage_command(attempted_executable: str, repo: Path, stage: str, full_prompt: str) -> tuple[list[str], str]:
    command = [attempted_executable]
    if _is_claude_code_router(attempted_executable):
        prompt_path = _write_prompt_file(repo, stage, full_prompt)
        command.append("code")
        prompt_arg = f"Read `{prompt_path}` and execute those {stage} stage instructions exactly."
        prompt_input_text = ""
    else:
        command.insert(1, "--print")
        prompt_arg = ""
        prompt_input_text = full_prompt
    command.extend(
        [
            "--permission-mode",
            "auto",
            "--add-dir",
            str(repo),
            "--allowedTools",
            CLAUDE_STAGE_ALLOWED_TOOLS,
            "--disallowedTools",
            DISALLOWED_CLAUDE_TOOLS,
            "--output-format",
            "text",
        ]
    )
    if _is_claude_code_router(attempted_executable):
        command.extend(["-p", prompt_arg])
    return command, prompt_input_text


def _allowed_outputs_with_logs(stage: str, allowed_outputs: list[str]) -> list[str]:
    outputs = list(allowed_outputs)
    for path in (
        f".r2a/logs/{stage}_stdout.log",
        f".r2a/logs/{stage}_stderr.log",
        f".r2a/logs/claude_{stage}_prompt.md",
        f".r2a/logs/claude_{stage}_attempt_*_stdout.log",
        f".r2a/logs/claude_{stage}_attempt_*_stderr.log",
        f".r2a/runs/iter_*/logs/claude_{stage}_attempt_*_stdout.log",
        f".r2a/runs/iter_*/logs/claude_{stage}_attempt_*_stderr.log",
    ):
        if path not in outputs:
            outputs.append(path)
    return outputs


def _build_stage_prompt(stage: str, prompt: str, allowed_outputs: list[str], iteration: int | None, language: str) -> str:
    allowed = "\n".join(f"- {item}" for item in allowed_outputs)
    iteration_text = "not applicable" if iteration is None else str(iteration)
    language_name = "Simplified Chinese" if language == "zh" else "English"
    return (
        f"R2A Claude stage: {stage}\n"
        f"Iteration: {iteration_text}\n\n"
        f"Output language: {language_name}\n"
        f"- Write every generated Markdown report, status summary, and final message in {language_name}.\n"
        "- If the output language is Simplified Chinese, all natural-language prose must be Simplified Chinese.\n"
        "- Keep literal file paths, command names, schema keys, and verdict labels unchanged.\n\n"
        "Allowed output files/directories:\n"
        f"{allowed}\n\n"
        "Hard safety constraints:\n"
        "- Do not use dangerous approval bypass options.\n"
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


def _write_prompt_file(repo_path: Path, stage: str, prompt: str) -> Path:
    logs_dir = artifact_dir(repo_path) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = logs_dir / f"claude_{stage}_prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    return prompt_path


def _write_stage_logs(
    repo: Path,
    stage: str,
    stdout: str,
    stderr: str,
    attempted_executable: str,
    command: list[str],
    prompt_size_bytes: int,
) -> tuple[Path, Path]:
    logs_dir = artifact_dir(repo) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = logs_dir / f"{stage}_stdout.log"
    stderr_log = logs_dir / f"{stage}_stderr.log"
    header = f"claude_executable_path: {attempted_executable}\nprompt_size_bytes: {prompt_size_bytes}\nallowed_tools: {CLAUDE_STAGE_ALLOWED_TOOLS}\ncommand: {command} [...prompt omitted...]\n\n"
    stdout_log.write_text(header + (stdout or ""), encoding="utf-8")
    stderr_log.write_text(header + (stderr or ""), encoding="utf-8")
    return stdout_log, stderr_log


def _write_attempt_logs(
    repo: Path,
    stage: str,
    attempt: int,
    stdout: str,
    stderr: str,
    attempted_executable: str,
    command: list[str],
    returncode: int,
    freshness: dict[str, Any],
    prompt_size_bytes: int,
    backend_error: dict[str, Any],
) -> tuple[Path, Path]:
    logs_dir = artifact_dir(repo) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = logs_dir / f"claude_{stage}_attempt_{attempt}_stdout.log"
    stderr_log = logs_dir / f"claude_{stage}_attempt_{attempt}_stderr.log"
    header = (
        f"claude_executable_path: {attempted_executable}\n"
        f"attempt: {attempt}\n"
        f"returncode: {returncode}\n"
        f"prompt_size_bytes: {prompt_size_bytes}\n"
        f"allowed_tools: {CLAUDE_STAGE_ALLOWED_TOOLS}\n"
        f"backend_failure_category: {backend_error.get('failure_category', '')}\n"
        f"backend_failure_detail: {backend_error.get('failure_detail', '')}\n"
        f"freshness_ok: {str(freshness.get('ok', False)).lower()}\n"
        f"stale_output_detected: {str(bool(freshness.get('stale_outputs'))).lower()}\n"
        f"empty_output_detected: {str(bool(freshness.get('empty_outputs'))).lower()}\n"
        f"command: {command} [...prompt omitted...]\n\n"
    )
    stdout_log.write_text(header + (stdout or ""), encoding="utf-8")
    stderr_log.write_text(header + (stderr or ""), encoding="utf-8")
    _append_stage_diagnostic(
        repo,
        stage,
        attempt,
        attempted_executable,
        command,
        returncode,
        stderr,
        stdout_log,
        stderr_log,
        backend_error,
    )
    return stdout_log, stderr_log


def _append_stage_diagnostic(
    repo: Path,
    stage: str,
    attempt: int,
    attempted_executable: str,
    command: list[str],
    returncode: int,
    stderr: str,
    stdout_log: Path,
    stderr_log: Path,
    backend_error: dict[str, Any],
) -> None:
    logs_dir = artifact_dir(repo) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    diagnostic = {
        "run_id": os.environ.get("R2A_RUN_ID", ""),
        "stage": stage,
        "gateway": "ccr" if _is_claude_code_router(attempted_executable) else "claude",
        "provider": _provider_label(attempted_executable),
        "model": "",
        "mode": "non_engineer_stable" if stage.lower() != "engineer" else "engineer",
        "streaming": True,
        "attempt": attempt,
        "max_attempts": 2 if _auto_retry_allowed(stage, attempted_executable) else 1,
        "returncode": returncode,
        "error_code": backend_error.get("failure_category", ""),
        "error_summary": backend_error.get("user_message", ""),
        "stderr_excerpt": _tail(stderr, max_lines=20),
        "stdout_log_path": str(stdout_log),
        "stderr_log_path": str(stderr_log),
        "request_id": "",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "command_label": _command_label(command),
    }
    with (logs_dir / "claude_stage_diagnostics.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(diagnostic, ensure_ascii=False) + "\n")


def _archive_stage_logs(
    repo: Path,
    stage: str,
    iteration: int,
    stdout_log: Path,
    stderr_log: Path,
    attempt_stdout_log: Path | None = None,
    attempt_stderr_log: Path | None = None,
) -> None:
    logs_dir = iteration_dir(repo, iteration) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(stdout_log, logs_dir / f"{stage}_stdout.log")
    shutil.copy2(stderr_log, logs_dir / f"{stage}_stderr.log")
    if attempt_stdout_log:
        shutil.copy2(attempt_stdout_log, logs_dir / attempt_stdout_log.name)
    if attempt_stderr_log:
        shutil.copy2(attempt_stderr_log, logs_dir / attempt_stderr_log.name)


def _result(
    stage: str,
    returncode: int,
    stdout: str,
    stderr: str,
    stdout_log: Path,
    stderr_log: Path,
    allowed_outputs: list[str],
    attempted_executable: str,
    command: list[str],
    success: bool,
    error: str,
    hint: str,
    guard: dict[str, Any],
    freshness: dict[str, Any] | None = None,
    retry_metadata: dict[str, Any] | None = None,
    prompt_size_bytes: int = 0,
    prompt_file_path: str = "",
) -> dict[str, Any]:
    backend_error = classify_backend_error(stdout, stderr, backend="claude")
    retry_metadata = retry_metadata or {}
    if retry_metadata.get("retry_attempted") and not retry_metadata.get("transient_backend_retry_success"):
        backend_error = {
            **backend_error,
            "is_backend_failure": True,
            "transient_backend_failure": True,
            "failure_category": backend_error.get("failure_category") or retry_metadata.get("first_failure_category", ""),
            "failure_scope": backend_error.get("failure_scope") or BACKEND_TRANSIENT_FAILURE,
            "suggested_action": "manual_retry_same_stage",
            "user_message": "Claude Code backend transient failure persisted after retry; this is not evidence that the paper is unreproducible.",
        }
    result = {
        "stage": stage,
        "returncode": returncode,
        "stdout_log_path": str(stdout_log),
        "stderr_log_path": str(stderr_log),
        "stdout_tail": _tail(stdout),
        "stdout_excerpt": _tail(stdout, max_lines=200),
        "stderr_tail": _tail(stderr),
        "stderr_excerpt": _tail(stderr, max_lines=200),
        "allowed_outputs": allowed_outputs,
        "allowed_tools": CLAUDE_STAGE_ALLOWED_TOOLS,
        "prompt_size_bytes": prompt_size_bytes,
        "prompt_file_path": prompt_file_path,
        "claude_executable_path": attempted_executable,
        "attempted_executable": attempted_executable,
        "resolved_executable": attempted_executable,
        "command": command,
        "success": success,
        "error": error,
        "hint": hint,
        "baseline_changed_files": guard.get("baseline_changed_files", []),
        "stage_changed_files": guard.get("stage_changed_files", []),
        "signature_changed_files": guard.get("signature_changed_files", []),
        "unexpected_modifications": guard.get("unexpected_modifications", []),
        "stage_guard_ok": guard.get("ok", False),
        "guard_available": guard.get("guard_available", True),
        "guard_backend": guard.get("guard_backend", ""),
        "stage_guard_error": guard.get("error", ""),
        "stage_guard_warning": guard.get("warning", ""),
        "failure_category": guard.get("failure_category", ""),
        "execution_status": guard.get("execution_status", ""),
        "backend_error": backend_error,
        "is_backend_failure": backend_error.get("is_backend_failure", False),
        "transient_backend_failure": backend_error.get("transient_backend_failure", False),
        "backend_failure_category": backend_error.get("failure_category", ""),
        "backend_failure_detail": backend_error.get("failure_detail", ""),
        "backend_failure_scope": backend_error.get("failure_scope", ""),
        "backend_suggested_action": backend_error.get("suggested_action", ""),
        "backend_user_message": backend_error.get("user_message", ""),
        "backend_warning": backend_error.get("backend_warning", ""),
        "output_freshness": freshness or {},
        "retry_output_freshness_failed": not bool((freshness or {"ok": True}).get("ok", True)),
        "stale_output_detected": bool((freshness or {}).get("stale_outputs")),
        "partial_outputs_may_exist": bool(retry_metadata.get("retry_attempted")) and not bool(retry_metadata.get("transient_backend_retry_success")),
    }
    result.update(retry_metadata)
    return result


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


def _auto_retry_allowed(stage: str, attempted_executable: str) -> bool:
    if stage.lower() == "engineer":
        return False
    return "claude" in Path(attempted_executable).stem.lower() or Path(attempted_executable).stem.lower() == "ccr"


def _should_retry_attempt(attempt: dict[str, Any]) -> bool:
    backend_error = attempt.get("backend_error", {})
    return (
        backend_error.get("failure_category") == TOOL_CALL_PARSE_FAILURE
        and backend_error.get("failure_scope") == BACKEND_TRANSIENT_FAILURE
        and not attempt.get("success", False)
    )


def _validate_required_outputs(repo: Path, allowed_outputs: list[str], attempt_start_time: float) -> dict[str, Any]:
    required = _required_output_paths(repo, allowed_outputs)
    missing: list[str] = []
    stale: list[str] = []
    empty: list[str] = []
    for path in required:
        if not path.exists():
            missing.append(str(path))
            continue
        try:
            stat = path.stat()
        except OSError:
            missing.append(str(path))
            continue
        if stat.st_size <= 0:
            empty.append(str(path))
        if stat.st_mtime + 0.001 < attempt_start_time:
            stale.append(str(path))
    return {
        "ok": not missing and not stale and not empty,
        "required_outputs": [str(path) for path in required],
        "missing_outputs": missing,
        "stale_outputs": stale,
        "empty_outputs": empty,
        "attempt_start_time": attempt_start_time,
    }


def _required_output_paths(repo: Path, allowed_outputs: list[str]) -> list[Path]:
    paths: list[Path] = []
    for item in allowed_outputs:
        normalized = item.replace("\\", "/")
        if "*" in normalized or normalized.endswith("/"):
            continue
        if normalized.startswith(".r2a/logs/"):
            continue
        path = repo / normalized
        if path.suffix:
            paths.append(path)
    return paths


def _cleanup_required_outputs(repo: Path, allowed_outputs: list[str]) -> None:
    for path in _required_output_paths(repo, allowed_outputs):
        try:
            if path.exists() and path.is_file():
                path.unlink()
        except OSError:
            pass


def _archive_partial_outputs(repo: Path, stage: str, attempt: int, allowed_outputs: list[str]) -> None:
    partial_dir = artifact_dir(repo) / "logs" / f"{stage}_attempt_{attempt}_partial_outputs"
    for path in _required_output_paths(repo, allowed_outputs):
        if not path.exists() or not path.is_file():
            continue
        try:
            target = partial_dir / path.relative_to(repo)
        except ValueError:
            target = partial_dir / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, target)
        except OSError:
            pass


def _retry_metadata(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "retry_attempted": len(attempts) > 1,
        "retry_count": max(0, len(attempts) - 1),
        "transient_backend_retry_success": False,
    }
    if not attempts:
        return metadata
    first_error = attempts[0].get("backend_error", {})
    metadata["first_failure_category"] = first_error.get("failure_category", "")
    for attempt in attempts:
        index = int(attempt["attempt"])
        metadata[f"attempt_{index}_stdout_log"] = str(attempt["attempt_stdout_log"])
        metadata[f"attempt_{index}_stderr_log"] = str(attempt["attempt_stderr_log"])
    if len(attempts) > 1 and attempts[-1].get("success"):
        metadata["transient_backend_retry_success"] = True
    if len(attempts) > 1 and not attempts[-1].get("freshness", {}).get("ok", True):
        metadata["retry_output_freshness_failed"] = True
    return metadata


def _check_failure_code(error: str) -> int:
    lowered = (error or "").lower()
    if "permissionerror" in lowered or "access is denied" in lowered or "winerror 5" in lowered:
        return 126
    if "timed out" in lowered:
        return 124
    return 127


def _is_claude_code_router(executable: str) -> bool:
    return Path(executable).stem.lower() == "ccr"


def _provider_label(executable: str) -> str:
    return "router" if _is_claude_code_router(executable) else "anthropic"


def _command_label(command: list[str]) -> str:
    if not command:
        return ""
    return " ".join(Path(str(part)).name if index == 0 else str(part) for index, part in enumerate(command[:4]))
