import os
from pathlib import Path
import subprocess

from r2a.tools.wsl import DEFAULT_WSL_DISTRO, _default_wsl_cache_dir, check_wsl, windows_to_wsl_path, wsl_bash_command, wsl_cache_exports


def test_windows_to_wsl_path_converts_drive_paths() -> None:
    assert windows_to_wsl_path("C:\\R2A_WORKSPACES_SAMPLE\\run_001\\repo") == "/mnt/c/R2A_WORKSPACES_SAMPLE/run_001/repo"
    assert windows_to_wsl_path("C:/Users/example") == "/mnt/c/Users/example"


def test_windows_to_wsl_path_leaves_unix_paths_unchanged() -> None:
    assert windows_to_wsl_path("/tmp/repo") == "/tmp/repo"


def test_default_wsl_cache_dir_uses_env_override(monkeypatch) -> None:
    monkeypatch.setenv("R2A_WSL_CACHE_DIR", "C:/R2A_CACHE_SAMPLE")

    assert _default_wsl_cache_dir().replace("\\", "/") == "C:/R2A_CACHE_SAMPLE"


def test_default_wsl_cache_dir_is_not_personal_e_drive(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("R2A_WSL_CACHE_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))

    default = Path(_default_wsl_cache_dir())

    assert str(default).replace("\\", "/") != "C:/R2A_CACHE_SAMPLE"
    if os.name == "nt":
        assert default == tmp_path / "LocalAppData" / "R2A" / "cache"
    else:
        assert default == Path.home() / ".cache" / "r2a"


def test_wsl_cache_exports_moves_common_caches_to_windows_drive() -> None:
    exports = wsl_cache_exports("C:/R2A_CACHE_SAMPLE")

    assert "R2A_CACHE_DIR=/mnt/c/R2A_CACHE_SAMPLE" in exports
    assert "XDG_CACHE_HOME=/mnt/c/R2A_CACHE_SAMPLE/xdg" in exports
    assert "PIP_CACHE_DIR=/mnt/c/R2A_CACHE_SAMPLE/pip" in exports
    assert "HF_HOME=/mnt/c/R2A_CACHE_SAMPLE/huggingface" in exports
    assert "TORCH_HOME=/mnt/c/R2A_CACHE_SAMPLE/torch" in exports


def test_wsl_bash_command_wraps_cwd_and_command() -> None:
    command = wsl_bash_command(["python3", "-m", "pytest", "tests"], cwd="C:/R2A_WORKSPACES_SAMPLE/run_001/repo")

    assert command[:4] == ["wsl", "-d", DEFAULT_WSL_DISTRO, "--"]
    assert "cd /mnt/c/R2A_WORKSPACES_SAMPLE/run_001/repo" in command[-1]
    assert "python3 -m pytest tests" in command[-1]


def test_wsl_bash_command_removes_stale_pgid_file(tmp_path, monkeypatch) -> None:
    stale = tmp_path / "run-test.wsl.pgid"
    stale.write_text("1234\n", encoding="utf-8")
    monkeypatch.setenv("R2A_RUN_ID", "run-test")
    monkeypatch.setenv("R2A_RUNTIME_DIR", str(tmp_path))

    command = wsl_bash_command(["true"], cwd="C:/R2A_SAMPLE")

    assert not stale.exists()
    assert "run-test.wsl.pgid" in command[-1]


def test_check_wsl_allows_stdout_warning_after_ok(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout="ok\nwsl: localhost proxy warning\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = check_wsl("Ubuntu")

    assert result.available is True
