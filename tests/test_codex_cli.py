from __future__ import annotations

import subprocess
from types import SimpleNamespace

from r2a.tools.codex_cli import check_codex_cli


def test_check_codex_cli_default_uses_path_and_version(monkeypatch) -> None:
    calls = {}

    def fake_which(command: str) -> str | None:
        calls["which"] = command
        return "C:/Users/example/AppData/Roaming/npm/codex.cmd"

    def fake_run(command, **kwargs):
        calls["run"] = command
        return SimpleNamespace(returncode=0, stdout="codex 1.2.3\n", stderr="")

    monkeypatch.setattr("r2a.tools.codex_cli.shutil.which", fake_which)
    monkeypatch.setattr("r2a.tools.codex_cli.subprocess.run", fake_run)

    result = check_codex_cli()

    assert calls["which"] == "codex"
    assert calls["run"] == ["C:/Users/example/AppData/Roaming/npm/codex.cmd", "--version"]
    assert result.available is True
    assert result.executable == "codex"
    assert result.resolved_path == "C:/Users/example/AppData/Roaming/npm/codex.cmd"
    assert result.version_output == "codex 1.2.3"
    assert result.error == ""
    assert "available" in result.hint.lower()


def test_check_codex_cli_missing_from_path(monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.codex_cli.shutil.which", lambda command: None)

    result = check_codex_cli()

    assert result.available is False
    assert result.executable == "codex"
    assert result.resolved_path is None
    assert "PATH" in result.error
    assert "codex --version" in result.hint


def test_check_codex_cli_file_not_found(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        raise FileNotFoundError("missing codex")

    monkeypatch.setattr("r2a.tools.codex_cli.subprocess.run", fake_run)

    result = check_codex_cli("D:/missing/codex.exe")

    assert result.available is False
    assert result.executable == "D:/missing/codex.exe"
    assert result.resolved_path == "D:/missing/codex.exe"
    assert "FileNotFoundError" in result.error
    assert "--version" in result.hint


def test_check_codex_cli_permission_error_mentions_windowsapps(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        raise PermissionError("Access is denied")

    monkeypatch.setattr("r2a.tools.codex_cli.subprocess.run", fake_run)

    result = check_codex_cli("C:/Program Files/WindowsApps/OpenAI.Codex_123/app/resources/codex.exe")

    assert result.available is False
    assert "PermissionError" in result.error
    assert "WindowsApps" in result.hint
    assert "codex.cmd" in result.hint


def test_check_codex_cli_access_denied_output_gets_permission_hint(monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.codex_cli.subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="Access is denied"))

    result = check_codex_cli("C:/Program Files/WindowsApps/OpenAI.Codex_123/app/resources/codex.exe")

    assert result.available is False
    assert "Access is denied" in result.error
    assert "WindowsApps" in result.hint


def test_check_codex_cli_timeout(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 10)

    monkeypatch.setattr("r2a.tools.codex_cli.subprocess.run", fake_run)

    result = check_codex_cli("C:/Tools/codex.cmd")

    assert result.available is False
    assert "Timed out" in result.error
    assert result.version_output == ""
    assert result.hint
