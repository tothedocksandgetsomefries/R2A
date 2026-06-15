from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Create a minimal project structure for testing run_web.py."""
    root = tmp_path / "R2A"
    web_dir = root / "r2a_web"
    web_dir.mkdir(parents=True)
    (web_dir / "__init__.py").write_text("")
    (web_dir / "app.py").write_text("import streamlit; print('app')")
    (root / "r2a" / "tools").mkdir(parents=True)
    (root / "r2a" / "tools" / "__init__.py").write_text("")
    # Write a minimal web_runtime_registry so imports work during test
    reg_code = '''
from __future__ import annotations
import os
from pathlib import Path
import json

def web_registry_path():
    return Path(os.environ.get("R2A_RUNTIME_ROOT", str(Path.home() / ".r2a" / "runtime"))) / "web" / "web_server.json"

def read_web_registry():
    p = web_registry_path()
    if not p.exists(): return {}
    try:
        import json
        return json.loads(p.read_text())
    except: return {}

def write_web_registry(*, pid, port, app_path):
    p = web_registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"pid": pid, "port": port, "app_path": str(app_path)}))
    return p

def clear_web_registry():
    try: web_registry_path().unlink()
    except: pass

def check_registry(app_path):
    data = read_web_registry()
    if not data: return {"valid": False}
    return {"valid": True, "pid": data["pid"], "port": data["port"], "r2a_web": True}

def find_r2a_process_on_port(port, app_path):
    return {}

def port_in_use(port, host="127.0.0.1"):
    return False

def http_accessible(port, host="127.0.0.1"):
    return False

def process_alive(pid):
    return False
'''
    (root / "r2a" / "tools" / "web_runtime_registry.py").write_text(reg_code)

    # Write a minimal feature_flags
    feat_dir = root / "r2a" / "core"
    feat_dir.mkdir(parents=True)
    (feat_dir / "__init__.py").write_text("")
    (feat_dir / "feature_flags.py").write_text("from __future__ import annotations\ndef minimal_workflow_mode(): return True\ndef minimal_workflow_defaults(): return {'planner_backend': 'ccr_text', 'engineer_executor': 'mock'}")

    # Write minimal process_manager
    pm_dir = root / "r2a" / "tools"
    (pm_dir / "process_manager.py").write_text("from __future__ import annotations\ndef resolve_codex_executable(): return None\n")

    # Write a minimal __init__ for r2a
    (root / "r2a" / "__init__.py").write_text("")

    # Copy run_web.py
    run_web_src = Path(__file__).resolve().parents[1] / "run_web.py"
    if run_web_src.exists():
        (root / "run_web.py").write_text(run_web_src.read_text(encoding="utf-8"), encoding="utf-8")
    return root


def test_run_web_parse_defaults(project_root: Path) -> None:
    """Default args: port=8501, no restart, no stop."""
    import sys
    sys.path.insert(0, str(project_root))
    try:
        import run_web
        args = run_web._parse_args([])
        assert args.port == 8501
        assert args.restart is False
        assert args.stop is False
    finally:
        sys.path.pop(0)


def test_run_web_parse_restart(project_root: Path) -> None:
    import sys
    sys.path.insert(0, str(project_root))
    try:
        import run_web
        args = run_web._parse_args(["--restart"])
        assert args.restart is True
    finally:
        sys.path.pop(0)


def test_run_web_parse_stop(project_root: Path) -> None:
    import sys
    sys.path.insert(0, str(project_root))
    try:
        import run_web
        args = run_web._parse_args(["--stop"])
        assert args.stop is True
    finally:
        sys.path.pop(0)


def test_run_web_stop_with_no_instance(project_root: Path) -> None:
    """--stop when nothing running exits 0 with message."""
    import sys
    sys.path.insert(0, str(project_root))
    try:
        import run_web
        # Monkey-patch check_registry to return no valid instance
        run_web.check_registry = lambda ap: {"valid": False}
        run_web.find_r2a_process_on_port = lambda p, ap: {}
        rc = run_web._cmd_stop(project_root / "r2a_web" / "app.py")
        assert rc == 0
    finally:
        sys.path.pop(0)


def test_run_web_main_exits_0_when_already_running(project_root: Path, monkeypatch) -> None:
    """When a healthy instance exists, main() should exit 0 and open browser."""
    import sys
    sys.path.insert(0, str(project_root))
    try:
        import run_web
        monkeypatch.setattr(run_web, "_open_browser", lambda port: None)
        monkeypatch.setattr(run_web, "check_registry", lambda ap: {"valid": True, "pid": 12345, "port": 8501, "r2a_web": True})
        monkeypatch.setattr(run_web, "port_in_use", lambda port: True)
        rc = run_web.main([])
        assert rc == 0
    finally:
        sys.path.pop(0)


def test_run_web_reuses_listener_with_stale_registry(project_root: Path, monkeypatch) -> None:
    """Listener found on port but registry is stale: rewrite registry and exit 0."""
    import sys
    sys.path.insert(0, str(project_root))
    try:
        import run_web
        monkeypatch.setattr(run_web, "_open_browser", lambda port: None)
        monkeypatch.setattr(run_web, "check_registry", lambda ap: {"valid": False})
        monkeypatch.setattr(run_web, "find_r2a_process_on_port", lambda port, ap: {"pid": 12345, "port": 8501, "r2a_web": True})
        monkeypatch.setattr(run_web, "port_in_use", lambda port: True)
        monkeypatch.setattr(run_web, "write_web_registry", lambda pid, port, app_path: None)
        rc = run_web.main([])
        assert rc == 0
    finally:
        sys.path.pop(0)


def test_run_web_avoids_unknown_port(project_root: Path, monkeypatch) -> None:
    """Port occupied by non-R2A process: should try next port."""
    import sys
    sys.path.insert(0, str(project_root))
    try:
        import run_web
        monkeypatch.setattr(
            run_web.subprocess,
            "Popen",
            lambda cmd, cwd, env: type("Proc", (), {"pid": 55555, "poll": lambda self: 0})(),
        )
        monkeypatch.setattr(run_web, "_open_browser", lambda port: None)
        monkeypatch.setattr(run_web, "check_registry", lambda ap: {"valid": False})
        monkeypatch.setattr(run_web, "find_r2a_process_on_port", lambda port, ap: {})
        monkeypatch.setattr(run_web, "port_in_use", lambda port: port == 8501)  # 8501 taken, 8502 free
        monkeypatch.setattr(run_web, "_streamlit_python", lambda root: Path(sys.executable))
        monkeypatch.setattr(run_web, "write_web_registry", lambda pid, port, app_path: None)
        monkeypatch.setattr(run_web, "clear_web_registry", lambda: None)
        rc = run_web.main([])
        assert rc == 0
        # Should have started on 8502
    finally:
        sys.path.pop(0)


def test_run_web_registers_actual_listener_pid(project_root: Path, monkeypatch) -> None:
    import sys
    sys.path.insert(0, str(project_root))
    try:
        import run_web

        class Proc:
            pid = 11111

            def poll(self):
                return 0

        registered = []
        monkeypatch.setattr(run_web.subprocess, "Popen", lambda cmd, cwd, env: Proc())
        monkeypatch.setattr(run_web, "_open_browser", lambda port: None)
        monkeypatch.setattr(run_web, "check_registry", lambda ap: {"valid": False})
        monkeypatch.setattr(run_web, "find_r2a_process_on_port", lambda port, ap: {})
        monkeypatch.setattr(run_web, "port_in_use", lambda port: False)
        monkeypatch.setattr(run_web, "_streamlit_python", lambda root: Path(sys.executable))
        monkeypatch.setattr(run_web, "_wait_for_r2a_listener", lambda port, app_path, process, timeout: {"pid": 22222, "port": port, "r2a_web": True})
        monkeypatch.setattr(run_web, "_wait_for_web_process", lambda process, registered_pid, port: 0)
        monkeypatch.setattr(run_web, "write_web_registry", lambda pid, port, app_path: registered.append((pid, port)))
        monkeypatch.setattr(run_web, "clear_web_registry", lambda: None)

        rc = run_web.main([])

        assert rc == 0
        assert registered == [(22222, 8501)]
    finally:
        sys.path.pop(0)


def test_streamlit_python_prefers_repo_venv_when_current_lacks_streamlit(project_root: Path, monkeypatch) -> None:
    import sys
    sys.path.insert(0, str(project_root))
    try:
        import run_web

        venv_python = project_root / ".venv" / "Scripts" / "python.exe"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("")
        monkeypatch.setattr(run_web.sys, "executable", str(project_root / "system_python.exe"))
        (project_root / "system_python.exe").write_text("")
        monkeypatch.setattr(run_web, "_python_has_streamlit", lambda executable: executable == venv_python)

        assert run_web._streamlit_python(project_root) == venv_python
    finally:
        sys.path.pop(0)


def test_run_web_does_not_register_failed_streamlit_start(project_root: Path, monkeypatch) -> None:
    import sys
    sys.path.insert(0, str(project_root))
    try:
        import run_web

        class Proc:
            pid = 11111

            def poll(self):
                return 1

        registered = []
        monkeypatch.setattr(run_web.subprocess, "Popen", lambda cmd, cwd, env: Proc())
        monkeypatch.setattr(run_web, "_streamlit_python", lambda root: Path(sys.executable))
        monkeypatch.setattr(run_web, "_open_browser", lambda port: None)
        monkeypatch.setattr(run_web, "check_registry", lambda ap: {"valid": False})
        monkeypatch.setattr(run_web, "find_r2a_process_on_port", lambda port, ap: {})
        monkeypatch.setattr(run_web, "port_in_use", lambda port: False)
        monkeypatch.setattr(run_web, "write_web_registry", lambda pid, port, app_path: registered.append((pid, port)))

        rc = run_web.main([])

        assert rc == 1
        assert registered == []
    finally:
        sys.path.pop(0)


def test_run_web_starts_streamlit_headless_to_avoid_double_browser(project_root: Path, monkeypatch) -> None:
    import sys
    sys.path.insert(0, str(project_root))
    try:
        import run_web

        commands = []

        class Proc:
            pid = 11111

            def poll(self):
                return 0

        def fake_popen(cmd, cwd, env):
            commands.append(cmd)
            return Proc()

        monkeypatch.setattr(run_web.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(run_web, "_streamlit_python", lambda root: Path(sys.executable))
        monkeypatch.setattr(run_web, "_open_browser", lambda port: None)
        monkeypatch.setattr(run_web, "check_registry", lambda ap: {"valid": False})
        monkeypatch.setattr(run_web, "find_r2a_process_on_port", lambda port, ap: {})
        monkeypatch.setattr(run_web, "port_in_use", lambda port: False)
        monkeypatch.setattr(run_web, "_wait_for_r2a_listener", lambda port, app_path, process, timeout: {"pid": 22222, "port": port, "r2a_web": True})
        monkeypatch.setattr(run_web, "_wait_for_web_process", lambda process, registered_pid, port: 0)
        monkeypatch.setattr(run_web, "write_web_registry", lambda pid, port, app_path: None)
        monkeypatch.setattr(run_web, "clear_web_registry", lambda: None)

        rc = run_web.main([])

        assert rc == 0
        assert "--server.headless" in commands[0]
        assert commands[0][commands[0].index("--server.headless") + 1] == "true"
    finally:
        sys.path.pop(0)


def test_run_web_returns_zero_when_registered_listener_was_stopped(project_root: Path, monkeypatch) -> None:
    import sys
    sys.path.insert(0, str(project_root))
    try:
        import run_web

        class Proc:
            pid = 11111

            def poll(self):
                return 1

        monkeypatch.setattr(run_web, "process_alive", lambda pid: False)
        monkeypatch.setattr(run_web, "port_in_use", lambda port: False)
        monkeypatch.setattr(run_web, "read_web_registry", lambda: {})

        rc = run_web._wait_for_web_process(Proc(), 22222, 8501)

        assert rc == 0
    finally:
        sys.path.pop(0)


def test_run_web_preserves_nonzero_when_listener_died_without_registry_stop(project_root: Path, monkeypatch) -> None:
    import sys
    sys.path.insert(0, str(project_root))
    try:
        import run_web

        class Proc:
            pid = 11111

            def poll(self):
                return 1

        monkeypatch.setattr(run_web, "process_alive", lambda pid: False)
        monkeypatch.setattr(run_web, "port_in_use", lambda port: False)
        monkeypatch.setattr(run_web, "read_web_registry", lambda: {"pid": 22222, "port": 8501})

        rc = run_web._wait_for_web_process(Proc(), 22222, 8501)

        assert rc == 1
    finally:
        sys.path.pop(0)


def test_run_web_restart_does_not_start_when_stop_fails(project_root: Path, monkeypatch) -> None:
    import sys
    sys.path.insert(0, str(project_root))
    try:
        import run_web

        started = []
        monkeypatch.setattr(run_web, "_cmd_stop", lambda app_path: 4)
        monkeypatch.setattr(run_web.subprocess, "Popen", lambda *args, **kwargs: started.append(args))

        rc = run_web.main(["--restart"])

        assert rc == 4
        assert started == []
    finally:
        sys.path.pop(0)


def test_run_web_stop_tries_graceful_before_force(project_root: Path, monkeypatch) -> None:
    import sys
    sys.path.insert(0, str(project_root))
    try:
        import run_web

        calls = []
        monkeypatch.setattr(run_web, "check_registry", lambda ap: {"valid": True, "pid": 12345, "port": 8501, "r2a_web": True})

        def fake_terminate(pid: int, *, force: bool) -> None:
            calls.append((pid, force))

        monkeypatch.setattr(run_web, "_terminate_pid", fake_terminate)
        monkeypatch.setattr(run_web, "_wait_for_port_release", lambda port, timeout: True)
        monkeypatch.setattr(run_web, "clear_web_registry", lambda: None)

        rc = run_web._cmd_stop(project_root / "r2a_web" / "app.py")

        assert rc == 0
        assert calls == [(12345, False)]
    finally:
        sys.path.pop(0)
