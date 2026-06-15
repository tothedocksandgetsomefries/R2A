from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex
import subprocess
import os


DEFAULT_WSL_DISTRO = "Ubuntu"
R2A_WSL_CACHE_DIR_ENV = "R2A_WSL_CACHE_DIR"


def _default_wsl_cache_dir() -> str:
    configured = os.environ.get(R2A_WSL_CACHE_DIR_ENV, "").strip()
    if configured:
        return configured
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if os.name == "nt" and local_app_data:
        return str(Path(local_app_data).expanduser() / "R2A" / "cache")
    if os.name == "nt":
        return str(Path.home() / "AppData" / "Local" / "R2A" / "cache")
    return str(Path.home() / ".cache" / "r2a")


DEFAULT_WSL_CACHE_DIR = _default_wsl_cache_dir()


@dataclass(frozen=True)
class WslCheckResult:
    available: bool
    distro: str
    error: str = ""
    hint: str = ""


def check_wsl(distro: str = DEFAULT_WSL_DISTRO, timeout: int = 10) -> WslCheckResult:
    try:
        completed = subprocess.run(
            ["wsl", "-d", distro, "--", "bash", "-lc", "printf ok"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return WslCheckResult(False, distro, f"wsl executable not found: {exc}", "Install WSL and an Ubuntu distribution, or select Windows local execution.")
    except subprocess.TimeoutExpired:
        return WslCheckResult(False, distro, f"Timed out after {timeout} seconds while checking WSL distro `{distro}`.", "Start the distro once from PowerShell and retry.")
    if completed.returncode == 0 and (completed.stdout or "").startswith("ok"):
        return WslCheckResult(True, distro)
    message = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip())
    return WslCheckResult(False, distro, message or f"`wsl -d {distro}` failed.", "Check `wsl -l -v` and make sure the selected distro name matches exactly.")


def windows_to_wsl_path(path: str | Path) -> str:
    text = str(path).replace("\\", "/")
    match = re.match(r"^([A-Za-z]):/(.*)$", text)
    if not match:
        return text
    drive = match.group(1).lower()
    rest = match.group(2)
    return f"/mnt/{drive}/{rest}"


def wsl_cache_exports(cache_dir: str = DEFAULT_WSL_CACHE_DIR) -> str:
    base = windows_to_wsl_path(cache_dir.rstrip("/\\"))
    env = {
        "R2A_CACHE_DIR": base,
        "XDG_CACHE_HOME": f"{base}/xdg",
        "PIP_CACHE_DIR": f"{base}/pip",
        "HF_HOME": f"{base}/huggingface",
        "TRANSFORMERS_CACHE": f"{base}/huggingface",
        "TORCH_HOME": f"{base}/torch",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    }
    mkdirs = " ".join(shlex.quote(value) for key, value in env.items() if key not in {"PIP_DISABLE_PIP_VERSION_CHECK"})
    exports = " ".join(f"export {key}={shlex.quote(value)};" for key, value in env.items())
    return f"mkdir -p {mkdirs}; {exports}"


def wsl_bash_command(command: list[str], *, cwd: str | Path, distro: str = DEFAULT_WSL_DISTRO, cache_dir: str = DEFAULT_WSL_CACHE_DIR) -> list[str]:
    wsl_cwd = windows_to_wsl_path(cwd)
    converted = [windows_to_wsl_path(part) if _looks_like_windows_path(part) else str(part) for part in command]
    command_text = " ".join(shlex.quote(part) for part in converted)
    pgid_file = _wsl_pgid_file()
    inner_command = f"cd {shlex.quote(wsl_cwd)} && {command_text}"
    if pgid_file:
        pgid_target = shlex.quote(pgid_file)
        inner = (
            f"({inner_command}) & "
            "child=$!; "
            "for _ in 1 2 3 4 5 6 7 8 9 10; do "
            "pgid=$(ps -o pgid= -p \"$child\" 2>/dev/null | tr -d ' '); "
            "if [ -z \"$pgid\" ]; then "
            "descendant=$(pgrep -P \"$child\" 2>/dev/null | head -n 1); "
            "if [ -n \"$descendant\" ]; then pgid=$(ps -o pgid= -p \"$descendant\" 2>/dev/null | tr -d ' '); fi; "
            "fi; "
            f"if [ -n \"$pgid\" ]; then echo \"$pgid\" > {pgid_target}; break; fi; "
            "sleep 0.1; "
            "done; "
            "wait \"$child\"; exit $?"
        )
    else:
        inner = inner_command
    script = f"{wsl_cache_exports(cache_dir)} setsid --wait bash -lc {shlex.quote(inner)}"
    return ["wsl", "-d", distro, "--", "bash", "-lc", script]


def _looks_like_windows_path(value: object) -> bool:
    text = str(value)
    return bool(re.match(r"^[A-Za-z]:[\\/]", text))


def _wsl_pgid_file() -> str:
    run_id = os.environ.get("R2A_RUN_ID", "")
    runtime_dir = os.environ.get("R2A_RUNTIME_DIR", "")
    if not run_id or not runtime_dir:
        return ""
    pgid_path = Path(runtime_dir) / f"{run_id}.wsl.pgid"
    try:
        pgid_path.unlink()
    except OSError:
        pass
    os.environ["R2A_WSL_PGID_FILE"] = str(pgid_path)
    return windows_to_wsl_path(pgid_path)
