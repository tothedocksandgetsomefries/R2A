from pathlib import Path

from r2a.core.state import make_initial_state
from r2a.tools.codex_cli import CodexCliCheckResult
from r2a.tools.codex_stage_runner import run_codex_stage
from r2a.tools.process_tree import ProcessResult


def test_run_codex_stage_uses_explicit_executable_path(tmp_path: Path, monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(
        "r2a.tools.codex_stage_runner.check_codex_cli",
        lambda path=None: CodexCliCheckResult(True, path or "codex", path or "codex", "codex 1.0", "", "ok"),
    )

    def fake_run(command, **kwargs):
        captured["command"] = command
        return ProcessResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("r2a.tools.codex_stage_runner.run_command_with_timeout", fake_run)

    result = run_codex_stage(
        tmp_path,
        "paper",
        "write paper",
        [".r2a/PAPER_BRIEF.md"],
        codex_executable_path="C:/Tools/codex.exe",
    )

    assert captured["command"][0] == "C:/Tools/codex.exe"
    assert captured["command"][1] == "exec"
    assert result["attempted_executable"] == "C:/Tools/codex.exe"
    assert result["codex_executable_path"] == "C:/Tools/codex.exe"


def test_run_codex_stage_returns_attempted_executable_on_file_not_found(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "r2a.tools.codex_stage_runner.check_codex_cli",
        lambda path=None: CodexCliCheckResult(
            False,
            path or "codex",
            None,
            "",
            "FileNotFoundError: missing",
            "Install/configure a real Codex CLI.",
        ),
    )

    result = run_codex_stage(
        tmp_path,
        "planner",
        "write task",
        [".r2a/TASK_SPEC.md"],
        codex_executable_path="D:/missing/codex.exe",
    )

    stderr_log = tmp_path / ".r2a" / "logs" / "planner_stderr.log"
    assert result["returncode"] == 127
    assert result["success"] is False
    assert result["attempted_executable"] == "D:/missing/codex.exe"
    assert "D:/missing/codex.exe" in stderr_log.read_text(encoding="utf-8")


def test_run_codex_stage_handles_permission_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "r2a.tools.codex_stage_runner.check_codex_cli",
        lambda path=None: CodexCliCheckResult(
            False,
            path or "codex",
            path,
            "",
            "PermissionError: Access is denied",
            "WindowsApps protected path cannot be used; use codex.cmd.",
        ),
    )

    result = run_codex_stage(
        tmp_path,
        "paper",
        "write paper",
        [".r2a/PAPER_BRIEF.md"],
        codex_executable_path="C:/Program Files/WindowsApps/codex.exe",
    )

    assert result["returncode"] == 126
    assert result["attempted_executable"] == "C:/Program Files/WindowsApps/codex.exe"
    assert "PermissionError" in result["stderr_tail"]
    assert "codex.cmd" in result["hint"]


def test_initial_state_saves_codex_executable_path(tmp_path: Path) -> None:
    state = make_initial_state(tmp_path, codex_executable_path="C:/Tools/codex.exe")

    assert state["codex_executable_path"] == "C:/Tools/codex.exe"
