from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Any

from r2a.core.config import DEFAULT_CODEX_EXECUTABLE


@dataclass(frozen=True)
class CodexCliCheckResult:
    available: bool
    executable: str
    resolved_path: str | None
    version_output: str
    error: str
    hint: str

    @property
    def ok(self) -> bool:
        return self.available

    @property
    def attempted_executable(self) -> str:
        return self.resolved_path or self.executable

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ok"] = self.available
        data["attempted_executable"] = self.attempted_executable
        return data


def check_codex_cli(executable: str | None = None, timeout: int = 10) -> CodexCliCheckResult:
    """Return a structured preflight result for the local Codex CLI."""
    requested = _clean_executable(executable)
    resolved_path = _resolve_from_path(requested)
    if _is_default_command(requested) and not resolved_path:
        return CodexCliCheckResult(
            available=False,
            executable=requested,
            resolved_path=None,
            version_output="",
            error="Codex CLI was not found in PATH.",
            hint=(
                "R2A calls the local command-line Codex CLI from Streamlit or Python, not the Codex chat session. "
                "Install/configure a real CLI entry so PowerShell can run `codex --version`, or provide a runnable codex.cmd path."
            ),
        )

    candidate = resolved_path or requested
    try:
        completed = subprocess.run(
            [candidate, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return CodexCliCheckResult(
            available=False,
            executable=requested,
            resolved_path=resolved_path,
            version_output="",
            error=f"FileNotFoundError: {exc}",
            hint=(
                "The configured Codex executable could not be found. R2A needs a local CLI command that works in PowerShell, "
                "for example `codex --version` or `C:\\Users\\<user>\\AppData\\Roaming\\npm\\codex.cmd --version`."
            ),
        )
    except PermissionError as exc:
        return _permission_denied_result(requested, resolved_path, f"PermissionError: {exc}")
    except subprocess.TimeoutExpired:
        return CodexCliCheckResult(
            available=False,
            executable=requested,
            resolved_path=resolved_path,
            version_output="",
            error=f"Timed out after {timeout} seconds while running `{candidate} --version`.",
            hint="The configured Codex executable did not answer `--version` in time. Verify the CLI manually in PowerShell.",
        )
    except OSError as exc:
        error = f"{exc.__class__.__name__}: {exc}"
        if _looks_like_access_denied(error, exc):
            return _permission_denied_result(requested, resolved_path, error)
        return CodexCliCheckResult(
            available=False,
            executable=requested,
            resolved_path=resolved_path,
            version_output="",
            error=error,
            hint="R2A could not start the configured Codex executable. Verify that the path is a real CLI entry and can run `--version`.",
        )

    version_output = _combined_output(completed.stdout, completed.stderr)
    if completed.returncode == 0:
        return CodexCliCheckResult(
            available=True,
            executable=requested,
            resolved_path=resolved_path or candidate,
            version_output=version_output,
            error="",
            hint="Codex CLI is available to this Python/Streamlit process.",
        )

    error = f"`{candidate} --version` exited with code {completed.returncode}."
    if version_output:
        error = f"{error} {version_output}"
    if _looks_like_access_denied(version_output):
        return _permission_denied_result(requested, resolved_path or candidate, error, version_output=version_output)
    return CodexCliCheckResult(
        available=False,
        executable=requested,
        resolved_path=resolved_path or candidate,
        version_output=version_output,
        error=error,
        hint="The configured command exists but did not behave like a runnable Codex CLI. It must succeed when running `<path> --version`.",
    )


def resolve_codex_executable() -> str | None:
    check = check_codex_cli()
    return check.attempted_executable if check.available else None


def is_codex_available() -> bool:
    return check_codex_cli().available


def check_codex_executable(executable: str | None = None, timeout: int = 10) -> dict[str, Any]:
    """Backward-compatible dict wrapper used by older callers/tests."""
    return check_codex_cli(executable, timeout=timeout).to_dict()


def format_codex_cli_error(check: CodexCliCheckResult) -> str:
    parts = [
        f"Codex CLI is not available: {check.attempted_executable}",
        check.error,
        check.hint,
    ]
    return "\n\n".join(part for part in parts if part)


def _clean_executable(executable: str | None) -> str:
    cleaned = (executable or DEFAULT_CODEX_EXECUTABLE).strip().strip('"')
    return cleaned or DEFAULT_CODEX_EXECUTABLE


def _resolve_from_path(executable: str) -> str | None:
    if _has_path_separator(executable):
        return executable
    return shutil.which(executable)


def _has_path_separator(executable: str) -> bool:
    return any(separator in executable for separator in ("/", "\\"))


def _is_default_command(executable: str) -> bool:
    return executable.lower() == DEFAULT_CODEX_EXECUTABLE.lower()


def _combined_output(stdout: str | None, stderr: str | None) -> str:
    return "\n".join(part.strip() for part in (stdout or "", stderr or "") if part and part.strip())


def _looks_like_access_denied(text: str, exc: BaseException | None = None) -> bool:
    lowered = (text or "").lower()
    if "access is denied" in lowered or "winerror 5" in lowered:
        return True
    return getattr(exc, "winerror", None) == 5


def _permission_denied_result(
    executable: str,
    resolved_path: str | None,
    error: str,
    version_output: str = "",
) -> CodexCliCheckResult:
    candidate = resolved_path or executable
    windowsapps_note = ""
    if "windowsapps" in candidate.lower() or "openai.codex_" in candidate.lower():
        windowsapps_note = " This looks like a WindowsApps/OpenAI.Codex protected app-internal path."
    return CodexCliCheckResult(
        available=False,
        executable=executable,
        resolved_path=resolved_path,
        version_output=version_output,
        error=error,
        hint=(
            f"The configured path exists but cannot be executed by Python subprocess.{windowsapps_note} "
            "Do not use `C:\\Program Files\\WindowsApps\\OpenAI.Codex_...\\app\\resources\\codex.exe` as R2A's CLI path. "
            "Install/configure a real Codex CLI entry, or provide a runnable codex.cmd path that passes `<path> --version`."
        ),
    )
