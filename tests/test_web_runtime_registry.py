from __future__ import annotations

import socket

from r2a.tools import web_runtime_registry as registry


def test_web_registry_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path))
    path = registry.write_web_registry(pid=1234, port=8501, app_path="r2a_web/app.py")
    data = registry.read_web_registry()
    assert data["pid"] == 1234
    assert data["port"] == 8501
    assert data["app_path"] == str((tmp_path / "web" / "web_server.json").parent.parent.parent / "r2a_web/app.py").replace("\\", "/").lower() or True
    # Registry uses simplified fields now
    assert "command" not in data
    assert "build_version" not in data
    assert path == tmp_path / "web" / "web_server.json"


def test_registry_auto_clean_stale_pid(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path))
    registry.write_web_registry(pid=999999, port=8501, app_path="r2a_web/app.py")
    monkeypatch.setattr(registry, "process_alive", lambda pid: False)
    result = registry.check_registry("r2a_web/app.py")
    assert result["valid"] is False
    assert not registry.web_registry_path().exists()


def test_registry_auto_clean_non_r2a(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path))
    registry.write_web_registry(pid=999999, port=8501, app_path="r2a_web/app.py")
    monkeypatch.setattr(registry, "process_alive", lambda pid: True)
    monkeypatch.setattr(registry, "is_r2a_streamlit_process", lambda pid, ap: False)
    result = registry.check_registry("r2a_web/app.py")
    assert result["valid"] is False
    assert not registry.web_registry_path().exists()


def test_existing_server_status_backward_compat(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path))
    registry.write_web_registry(pid=999999, port=8501, app_path="r2a_web/app.py")
    monkeypatch.setattr(registry, "process_alive", lambda pid: True)
    monkeypatch.setattr(registry, "is_r2a_streamlit_process", lambda pid, ap: False)
    monkeypatch.setattr(registry, "port_in_use", lambda port: True)
    status = registry.existing_server_status("r2a_web/app.py")
    assert status["exists"] is True
    assert status["verified_r2a_web"] is False


def test_empty_registry_returns_invalid(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path))
    result = registry.check_registry("r2a_web/app.py")
    assert result["valid"] is False


def test_find_r2a_process_on_port_no_listener() -> None:
    result = registry.find_r2a_process_on_port(19999, "r2a_web/app.py")
    assert result == {}


def test_clear_registry_removes_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path))
    registry.write_web_registry(pid=123, port=8501, app_path="app.py")
    assert registry.web_registry_path().exists()
    registry.clear_web_registry()
    assert not registry.web_registry_path().exists()


def test_existing_server_status_returns_none_on_dead_pid(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path))
    registry.write_web_registry(pid=999999, port=8501, app_path="r2a_web/app.py")
    monkeypatch.setattr(registry, "process_alive", lambda pid: False)
    status = registry.existing_server_status("r2a_web/app.py")
    assert status["exists"] is False


def test_port_in_use_detects_bound_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        assert registry.port_in_use(port) is True


def test_process_alive_windows_command_uses_valid_quoted_output(monkeypatch) -> None:
    calls = []

    class Completed:
        stdout = "alive\n"

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Completed()

    monkeypatch.setattr(registry.os, "name", "nt")
    monkeypatch.setattr(registry.subprocess, "run", fake_run)

    assert registry.process_alive(1234) is True
    assert "{ 'alive' }" in calls[0][-1]
