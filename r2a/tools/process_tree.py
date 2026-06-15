from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Callable

from r2a.tools.process_manager import cancel_requested, register_windows_process, register_wsl_pgid, update_run_heartbeat


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def run_command_with_timeout(
    command: list[str],
    *,
    cwd: str | Path,
    input_text: str,
    timeout: int,
    env: dict[str, str] | None = None,
    completion_check: Callable[[], bool] | None = None,
    completion_grace_seconds: int = 15,
    activity_check: Callable[[], object] | None = None,
    idle_timeout_seconds: int | None = None,
) -> ProcessResult:
    """Run a command and kill its whole process tree on timeout."""
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    start_new_session = os.name != "nt"
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=creationflags,
        start_new_session=start_new_session,
    )
    register_windows_process(process.pid, command=command, env=env)
    _wait_register_wsl_pgid_file(env)
    cleanup_wsl_group_on_exit = _command_uses_wsl(command)
    try:
        if completion_check is not None:
            return _communicate_until_exit_or_completion(
                process,
                input_text=input_text,
                timeout=timeout,
                completion_check=completion_check,
                completion_grace_seconds=completion_grace_seconds,
                activity_check=activity_check,
                idle_timeout_seconds=idle_timeout_seconds,
                runtime_env=env,
                cleanup_wsl_group_on_exit=cleanup_wsl_group_on_exit,
            )
        return _communicate_until_exit_or_completion(
            process,
            input_text=input_text,
            timeout=timeout,
            completion_check=lambda: False,
            completion_grace_seconds=completion_grace_seconds,
            activity_check=activity_check,
            idle_timeout_seconds=idle_timeout_seconds,
            runtime_env=env,
            cleanup_wsl_group_on_exit=cleanup_wsl_group_on_exit,
        )
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process.pid, env)
        stdout, stderr = process.communicate()
        combined_stdout = _coerce_text(exc.stdout) + (stdout or "")
        combined_stderr = _coerce_text(exc.stderr) + (stderr or "")
        return ProcessResult(124, combined_stdout, combined_stderr, timed_out=True)


def _communicate_until_exit_or_completion(
    process: subprocess.Popen,
    *,
    input_text: str,
    timeout: int,
    completion_check: Callable[[], bool],
    completion_grace_seconds: int,
    activity_check: Callable[[], object] | None,
    idle_timeout_seconds: int | None,
    runtime_env: dict[str, str] | None,
    cleanup_wsl_group_on_exit: bool,
) -> ProcessResult:
    deadline = time.monotonic() + timeout
    completion_seen_at: float | None = None
    last_activity_at = time.monotonic()
    last_heartbeat_at = 0.0
    last_activity_signature = None
    if activity_check is not None:
        try:
            last_activity_signature = activity_check()
        except Exception:
            last_activity_signature = None
    if process.stdin is not None:
        try:
            process.stdin.write(input_text or "")
            process.stdin.close()
        finally:
            process.stdin = None

    while True:
        if process.poll() is not None:
            _register_wsl_pgid_file(runtime_env)
            if cleanup_wsl_group_on_exit:
                _kill_registered_wsl_group(runtime_env)
            stdout, stderr = process.communicate()
            return ProcessResult(int(process.returncode), stdout or "", stderr or "", timed_out=False)

        now = time.monotonic()
        _register_wsl_pgid_file(runtime_env)
        if now - last_heartbeat_at >= 2:
            _heartbeat(runtime_env)
            last_heartbeat_at = now
        if cancel_requested(runtime_env):
            _terminate_process_tree(process.pid, runtime_env)
            stdout, stderr = process.communicate()
            note = "\nCancelled: process tree was terminated because the R2A run requested cancellation."
            return ProcessResult(130, stdout or "", (stderr or "") + note, timed_out=False)
        if now >= deadline:
            _terminate_process_tree(process.pid, runtime_env)
            stdout, stderr = process.communicate()
            return ProcessResult(124, stdout or "", stderr or "", timed_out=True)
        if activity_check is not None and idle_timeout_seconds:
            try:
                activity_signature = activity_check()
            except Exception:
                activity_signature = last_activity_signature
            if activity_signature != last_activity_signature:
                last_activity_signature = activity_signature
                last_activity_at = now
            elif now - last_activity_at >= idle_timeout_seconds:
                _terminate_process_tree(process.pid, runtime_env)
                stdout, stderr = process.communicate()
                note = f"\nIdleTimeout: process tree was terminated after {idle_timeout_seconds} seconds without observable workspace activity."
                return ProcessResult(124, stdout or "", (stderr or "") + note, timed_out=True)

        try:
            completed = completion_check()
        except Exception:
            completed = False
        if completed:
            if completion_seen_at is None:
                completion_seen_at = now
            elif now - completion_seen_at >= completion_grace_seconds:
                _terminate_process_tree(process.pid, runtime_env)
                stdout, stderr = process.communicate()
                note = "\nProcess tree was terminated after Engineer completion artifacts were observed."
                return ProcessResult(0, stdout or "", (stderr or "") + note, timed_out=False)
        else:
            completion_seen_at = None

        time.sleep(1)


def _terminate_process_tree(pid: int, runtime_env: dict[str, str] | None = None) -> None:
    _register_wsl_pgid_file(runtime_env)
    _kill_registered_wsl_group(runtime_env)
    _kill_process_tree(pid)


def _kill_process_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


def _kill_registered_wsl_group(env: dict[str, str] | None = None) -> None:
    source = env or os.environ
    path = source.get("R2A_WSL_PGID_FILE", "")
    if not path:
        return
    try:
        pgid = int(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    if pgid <= 0:
        return
    distro = source.get("R2A_WSL_DISTRO", "Ubuntu")
    subprocess.run(
        ["wsl", "-d", distro, "--", "bash", "-lc", f"kill -KILL -- -{pgid} 2>/dev/null"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _command_uses_wsl(command: list[str]) -> bool:
    if not command:
        return False
    executable = Path(str(command[0])).name.lower()
    return executable in {"wsl", "wsl.exe"}


def _coerce_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _register_wsl_pgid_file(env: dict[str, str] | None) -> None:
    source = env or os.environ
    path = source.get("R2A_WSL_PGID_FILE", "")
    if not path:
        return
    pgid_path = Path(path)
    if not pgid_path.exists():
        return
    try:
        pgid = int(pgid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    register_wsl_pgid(pgid, distro=source.get("R2A_WSL_DISTRO", "Ubuntu"), env=env)


def _heartbeat(env: dict[str, str] | None) -> None:
    source = env or os.environ
    run_id = source.get("R2A_RUN_ID", "")
    repo_path = source.get("R2A_REPO_PATH", "")
    if not run_id or not repo_path:
        return
    update_run_heartbeat(repo_path, run_id, stage_status="running")


def _wait_register_wsl_pgid_file(env: dict[str, str] | None) -> None:
    source = env or os.environ
    if not source.get("R2A_WSL_PGID_FILE", ""):
        return
    for _ in range(20):
        _register_wsl_pgid_file(env)
        path = source.get("R2A_WSL_PGID_FILE", "")
        if path and Path(path).exists():
            return
        time.sleep(0.1)
