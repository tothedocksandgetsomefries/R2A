from __future__ import annotations

import argparse
from pathlib import Path
import os
import subprocess
import sys
import time
import webbrowser

from r2a.tools.web_runtime_registry import (
    check_registry,
    clear_web_registry,
    find_r2a_process_on_port,
    http_accessible,
    port_in_use,
    process_alive,
    read_web_registry,
    write_web_registry,
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    project_root = Path(__file__).resolve().parent
    app_path = project_root / "r2a_web" / "app.py"
    port = args.port

    # --stop: shut down existing R2A Web
    if args.stop:
        return _cmd_stop(app_path)

    # --restart: stop then start fresh
    if args.restart:
        stop_code = _cmd_stop(app_path)
        if stop_code != 0:
            return stop_code
        time.sleep(0.5)

    # Check registry validity (auto-cleans stale entries)
    registered = check_registry(app_path)

    if registered.get("valid"):
        # Healthy existing instance - reuse
        pid = registered["pid"]
        port = registered["port"]
        print(f"R2A Web is already running. Reusing existing UI:")
        print(f"http://127.0.0.1:{port}")
        _open_browser(port)
        return 0

    # Check port for an actual listener (even if registry was stale or missing)
    listener = find_r2a_process_on_port(port, app_path)
    if listener:
        # Found an actual R2A process on the port but registry was stale; rewrite it
        write_web_registry(pid=listener["pid"], port=port, app_path=app_path)
        print(f"R2A Web is already running (recovered registry). Reusing:")
        print(f"http://127.0.0.1:{port}")
        _open_browser(port)
        return 0

    # Port occupied by unknown process
    if port_in_use(port):
        # Try next port
        alt_port = port + 1
        alt_listener = find_r2a_process_on_port(alt_port, app_path)
        if alt_listener:
            write_web_registry(pid=alt_listener["pid"], port=alt_port, app_path=app_path)
            print(f"R2A Web already running on port {alt_port}. Reusing:")
            print(f"http://127.0.0.1:{alt_port}")
            _open_browser(alt_port)
            return 0
        if not port_in_use(alt_port):
            port = alt_port
            print(f"Port {args.port} is in use by another process. Using port {port} instead.")
        else:
            print(
                f"Port {args.port} is in use by a non-R2A process and port {alt_port} is also in use.",
                file=sys.stderr,
            )
            print("Please free a port manually and retry.", file=sys.stderr)
            return 3

    # Start new Streamlit
    command = [
        str(_streamlit_python(project_root)),
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(port),
        "--server.headless",
        "true",
    ]
    env = os.environ.copy()
    process = subprocess.Popen(command, cwd=project_root, env=env)
    listener = _wait_for_r2a_listener(port, app_path, process=process, timeout=15)
    if not listener and process.poll() not in (None, 0):
        return int(process.poll() or 1)
    registered_pid = int(listener.get("pid") or process.pid)
    write_web_registry(pid=registered_pid, port=port, app_path=app_path)

    _open_browser(port)
    print(f"R2A Web started at http://127.0.0.1:{port}")

    try:
        return _wait_for_web_process(process, registered_pid, port)
    finally:
        registered = check_registry(app_path)
        if registered.get("valid") and int(registered.get("pid", -1)) == registered_pid:
            clear_web_registry()


def _cmd_stop(app_path: Path) -> int:
    registered = check_registry(app_path)
    if not registered.get("valid"):
        # Still check the port for a running R2A process
        for port in range(8501, 8510):
            listener = find_r2a_process_on_port(port, app_path)
            if listener:
                _terminate_pid(listener["pid"], force=False)
                if not _wait_for_port_release(port, timeout=5):
                    _terminate_pid(listener["pid"], force=True)
                    if not _wait_for_port_release(port, timeout=5):
                        print(f"R2A Web (pid={listener['pid']}) did not release port {port}.", file=sys.stderr)
                        return 4
                clear_web_registry()
                print(f"R2A Web (pid={listener['pid']}) stopped.")
                return 0
        print("R2A Web is not running.")
        return 0
    pid = registered["pid"]
    port = registered.get("port", 8501)
    _terminate_pid(pid, force=False)
    if not _wait_for_port_release(port, timeout=5):
        _terminate_pid(pid, force=True)
        if not _wait_for_port_release(port, timeout=5):
            print(f"R2A Web (pid={pid}) did not release port {port}.", file=sys.stderr)
            return 4
    clear_web_registry()
    print(f"R2A Web (pid={pid}) stopped.")
    return 0


def _terminate_pid(pid: int, *, force: bool) -> None:
    if os.name == "nt":
        command = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            command.append("/F")
        subprocess.run(command, check=False, capture_output=True, text=True)
    else:
        os.kill(pid, 15)


def _wait_for_port_release(port: int, *, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not port_in_use(port):
            return True
        time.sleep(0.2)
    return not port_in_use(port)


def _wait_for_r2a_listener(
    port: int,
    app_path: Path,
    *,
    process: subprocess.Popen,
    timeout: float,
) -> dict[str, int]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        listener = find_r2a_process_on_port(port, app_path)
        if listener:
            return listener
        if process.poll() not in (None, 0) and not port_in_use(port):
            break
        time.sleep(0.2)
    return {}


def _wait_for_web_process(process: subprocess.Popen, registered_pid: int, port: int) -> int:
    while True:
        launcher_code = process.poll()
        if launcher_code is not None and registered_pid == process.pid:
            return int(launcher_code)
        if not process_alive(registered_pid) or not port_in_use(port):
            registered = read_web_registry()
            if int(registered.get("pid", -1) or -1) != int(registered_pid):
                return 0
            return int(launcher_code or 0)
        time.sleep(0.5)


def _streamlit_python(project_root: Path) -> Path:
    candidates = [
        Path(sys.executable),
        project_root / ".venv" / "Scripts" / "python.exe",
        project_root / ".venv" / "bin" / "python",
    ]
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen or not candidate.exists():
            continue
        seen.add(key)
        if _python_has_streamlit(candidate):
            return candidate
    return Path(sys.executable)


def _python_has_streamlit(python_executable: Path) -> bool:
    try:
        completed = subprocess.run(
            [str(python_executable), "-c", "import streamlit"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _open_browser(port: int) -> None:
    url = f"http://127.0.0.1:{port}"
    try:
        webbrowser.open(url)
    except Exception:
        pass  # Non-blocking; may fail in headless environments


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start or manage the R2A Streamlit Web UI.")
    parser.add_argument("--port", type=int, default=8501, help="Streamlit port (default: 8501)")
    parser.add_argument("--restart", action="store_true", help="Stop existing R2A Web and start a fresh instance")
    parser.add_argument("--stop", action="store_true", help="Stop the running R2A Web instance")
    # Compat shim
    parser.add_argument("--stop-existing", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
