from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
from typing import Any

from r2a.core.runtime_paths import runtime_root


def web_registry_path() -> Path:
    return runtime_root() / "web" / "web_server.json"


def read_web_registry() -> dict[str, Any]:
    path = web_registry_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_web_registry(*, pid: int, port: int, app_path: str | Path) -> Path:
    path = web_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"pid": int(pid), "port": int(port), "app_path": str(Path(app_path).resolve())}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def clear_web_registry() -> None:
    path = web_registry_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, int(port))) == 0


def process_alive(pid: int) -> bool:
    if int(pid) <= 0:
        return False
    if os.name == "nt":
        cmd = ["powershell", "-NoProfile", "-Command", f"if (Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue) {{ 'alive' }}"]
        try:
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace")
        except Exception:
            return False
        return "alive" in completed.stdout
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def process_command_line(pid: int) -> str:
    if os.name == "nt":
        cmd = ["powershell", "-NoProfile", "-Command", f"(Get-CimInstance Win32_Process -Filter \"ProcessId = {int(pid)}\").CommandLine"]
        try:
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace")
        except Exception:
            return ""
        return completed.stdout.strip()
    try:
        return Path(f"/proc/{int(pid)}/cmdline").read_text(encoding="utf-8", errors="replace").replace("\x00", " ")
    except OSError:
        return ""


def is_r2a_streamlit_process(pid: int, app_path: str | Path) -> bool:
    command_line = process_command_line(pid).lower()
    if not command_line:
        return False
    normalized_app = str(Path(app_path).resolve()).lower().replace("\\", "/")
    normalized_cmd = command_line.replace("\\", "/")
    return "streamlit" in normalized_cmd and normalized_app in normalized_cmd


def http_accessible(port: int, host: str = "127.0.0.1") -> bool:
    """Check if the Streamlit server responds on the given port by attempting a TCP connection."""
    return port_in_use(port, host)

def existing_server_status(app_path: str | Path) -> dict[str, Any]:
    """Legacy compatibility wrapper. Use check_registry() for new code."""
    data = read_web_registry()
    if not data:
        return {"exists": False, "registry_path": str(web_registry_path())}
    pid = int(data.get("pid", -1) or -1)
    port = int(data.get("port", 0) or 0)
    if not process_alive(pid):
        return {"exists": False, "registry_path": str(web_registry_path())}
    return {
        **data,
        "pid": pid,
        "port": port,
        "exists": True,
        "alive": True,
        "verified_r2a_web": is_r2a_streamlit_process(pid, app_path),
        "listener_pid": pid,
        "port_in_use": port_in_use(port),
        "registry_path": str(web_registry_path()),
        "started_at": "",
        "build_version": "local",
    }




def find_r2a_process_on_port(port: int, app_path: str | Path) -> dict[str, Any]:
    """Find a running R2A Streamlit process on the given port.
    Returns {pid, port, r2a_web: bool} or empty dict if none found.
    """
    if not port_in_use(port):
        return {}
    if os.name == "nt":
        cmd = [
            "powershell", "-NoProfile", "-Command",
            f"Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess",
        ]
        try:
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace")
        except Exception:
            return {}
        for line in completed.stdout.splitlines():
            raw = line.strip()
            if raw.isdigit():
                pid = int(raw)
                if process_alive(pid) and is_r2a_streamlit_process(pid, app_path):
                    return {"pid": pid, "port": int(port), "r2a_web": True}
        return {}
    cmd = ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-t"]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace")
    except Exception:
        return {}
    for raw in completed.stdout.splitlines():
        raw = raw.strip()
        if raw.isdigit():
            pid = int(raw)
            if process_alive(pid) and is_r2a_streamlit_process(pid, app_path):
                return {"pid": pid, "port": int(port), "r2a_web": True}
    return {}


def check_registry(app_path: str | Path) -> dict[str, Any]:
    """Read registry and verify its validity. Auto-clean stale entries.
    Returns a status dict with keys: valid, pid, port, r2a_web.
    """
    data = read_web_registry()
    if not data:
        return {"valid": False}
    pid = int(data.get("pid", -1) or -1)
    port = int(data.get("port", 0) or 0)
    if pid <= 0 or not port:
        clear_web_registry()
        return {"valid": False}
    # PID dead -> stale
    if not process_alive(pid):
        clear_web_registry()
        return {"valid": False}
    # PID alive but not R2A -> stale
    if not is_r2a_streamlit_process(pid, app_path):
        clear_web_registry()
        return {"valid": False}
    return {"valid": True, "pid": pid, "port": port, "r2a_web": True}
