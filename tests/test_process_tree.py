from __future__ import annotations

import subprocess
from pathlib import Path

from r2a.tools.process_tree import _command_uses_wsl, _kill_registered_wsl_group


def test_kill_registered_wsl_group_targets_only_recorded_pgid(tmp_path: Path, monkeypatch) -> None:
    pgid_file = tmp_path / "run.wsl.pgid"
    pgid_file.write_text("4567\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("r2a.tools.process_tree.subprocess.run", fake_run)

    _kill_registered_wsl_group({"R2A_WSL_PGID_FILE": str(pgid_file), "R2A_WSL_DISTRO": "Ubuntu"})

    assert commands == [["wsl", "-d", "Ubuntu", "--", "bash", "-lc", "kill -KILL -- -4567 2>/dev/null"]]


def test_kill_registered_wsl_group_ignores_missing_or_invalid_pgid(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr("r2a.tools.process_tree.subprocess.run", lambda command, **kwargs: calls.append(command))

    _kill_registered_wsl_group({"R2A_WSL_PGID_FILE": str(tmp_path / "missing"), "R2A_WSL_DISTRO": "Ubuntu"})

    invalid = tmp_path / "invalid.wsl.pgid"
    invalid.write_text("not-a-pgid\n", encoding="utf-8")
    _kill_registered_wsl_group({"R2A_WSL_PGID_FILE": str(invalid), "R2A_WSL_DISTRO": "Ubuntu"})

    assert calls == []


def test_command_uses_wsl_detects_windows_and_path_forms() -> None:
    assert _command_uses_wsl(["wsl", "-d", "Ubuntu"])
    assert _command_uses_wsl(["C:/Windows/System32/wsl.exe", "-d", "Ubuntu"])
    assert not _command_uses_wsl(["python", "-c", "print('ok')"])
