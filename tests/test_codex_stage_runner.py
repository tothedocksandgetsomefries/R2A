from __future__ import annotations

from pathlib import Path

from r2a.tools.codex_cli import CodexCliCheckResult
from r2a.tools.codex_stage_runner import run_codex_stage
from r2a.tools.process_tree import ProcessResult


def _available_cli(path: str = "codex") -> CodexCliCheckResult:
    return CodexCliCheckResult(True, path, path, "codex 1.0", "", "ok")


def _ok_guard(stage: str, allowed: list[str]) -> dict:
    return {
        "stage": stage,
        "allowed_patterns": allowed,
        "changed_files": [],
        "stage_changed_files": [],
        "unexpected_modifications": [],
        "ok": True,
        "guard_available": True,
        "error": "",
        "warning": "",
    }


def test_codex_stage_writes_stage_logs(tmp_path: Path, monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr("r2a.tools.codex_stage_runner.check_codex_cli", lambda path=None: _available_cli(path or "codex"))

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs.get("input_text")
        return ProcessResult(returncode=0, stdout="ok\n", stderr="warn\n")

    monkeypatch.setattr("r2a.tools.codex_stage_runner.run_command_with_timeout", fake_run)
    monkeypatch.setattr("r2a.tools.codex_stage_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.codex_stage_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: _ok_guard(stage, allowed),
    )

    result = run_codex_stage(tmp_path, "paper", "write paper reports", [".r2a/PAPER_BRIEF.md"])

    stdout_log = tmp_path / ".r2a" / "logs" / "paper_stdout.log"
    stderr_log = tmp_path / ".r2a" / "logs" / "paper_stderr.log"
    assert stdout_log.exists()
    assert stderr_log.exists()
    stdout_text = stdout_log.read_text(encoding="utf-8")
    stderr_text = stderr_log.read_text(encoding="utf-8")
    assert "codex_executable_path: codex" in stdout_text
    assert stdout_text.endswith("ok\n")
    assert "codex_executable_path: codex" in stderr_text
    assert stderr_text.endswith("warn\n")
    assert result["stage"] == "paper"
    assert result["returncode"] == 0
    assert result["stdout_log_path"] == str(stdout_log)
    assert result["stderr_log_path"] == str(stderr_log)
    assert result["stdout_tail"] == "ok"
    assert result["stderr_tail"] == "warn"
    assert ".r2a/PAPER_BRIEF.md" in result["allowed_outputs"]
    assert ".r2a/logs/paper_stdout.log" in result["allowed_outputs"]
    assert ".r2a/logs/paper_stderr.log" in result["allowed_outputs"]
    assert result["success"] is True
    assert "--skip-git-repo-check" in captured["command"]
    assert captured["command"][-1] == "-"
    assert "write paper reports" in captured["input"]
    assert "Backend choice affects the execution model, not R2A evidence rules" in captured["input"]
    assert "r2a/prompts/R2A_PROTOCOL.md" in captured["input"]
    assert "Do not bypass `.r2a/TASK_SPEC.md`, `.r2a/EXPERIMENT_CONTRACT.md`" in captured["input"]
    assert "Do not inflate smoke tests, synthetic demos, or unofficial reimplementations into L3/L4 evidence" in captured["input"]


def test_codex_stage_handles_file_not_found(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "r2a.tools.codex_stage_runner.check_codex_cli",
        lambda path=None: CodexCliCheckResult(
            False,
            "codex",
            None,
            "",
            "FileNotFoundError: codex",
            "Install/configure a real Codex CLI.",
        ),
    )

    result = run_codex_stage(tmp_path, "planner", "write task", [".r2a/TASK_SPEC.md"])

    stderr_log = tmp_path / ".r2a" / "logs" / "planner_stderr.log"
    assert stderr_log.exists()
    assert result["returncode"] == 127
    assert result["success"] is False
    assert "FileNotFoundError" in result["stderr_tail"]
    assert result["attempted_executable"] == "codex"
    assert "Install/configure" in result["hint"]


def test_codex_stage_archives_iteration_logs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.codex_stage_runner.check_codex_cli", lambda path=None: _available_cli(path or "codex"))
    monkeypatch.setattr(
        "r2a.tools.codex_stage_runner.run_command_with_timeout",
        lambda *args, **kwargs: ProcessResult(returncode=0, stdout="review\n", stderr=""),
    )
    monkeypatch.setattr("r2a.tools.codex_stage_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.codex_stage_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: _ok_guard(stage, allowed),
    )

    run_codex_stage(tmp_path, "reviewer", "write review", [".r2a/REVIEW_REPORT.md"], iteration=1)

    assert (tmp_path / ".r2a" / "runs" / "iter_001" / "logs" / "reviewer_stdout.log").exists()
    assert (tmp_path / ".r2a" / "runs" / "iter_001" / "logs" / "reviewer_stderr.log").exists()


def test_codex_stage_fails_when_stage_guard_rejects_modifications(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.codex_stage_runner.check_codex_cli", lambda path=None: _available_cli(path or "codex"))
    monkeypatch.setattr(
        "r2a.tools.codex_stage_runner.run_command_with_timeout",
        lambda *args, **kwargs: ProcessResult(returncode=0, stdout="ok\n", stderr=""),
    )
    monkeypatch.setattr("r2a.tools.codex_stage_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.codex_stage_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: {
            "stage": stage,
            "allowed_patterns": allowed,
            "changed_files": ["src/side_effect.py"],
            "stage_changed_files": ["src/side_effect.py"],
            "unexpected_modifications": ["src/side_effect.py"],
            "ok": False,
        },
    )

    result = run_codex_stage(tmp_path, "planner", "write task", [".r2a/TASK_SPEC.md"])

    assert result["success"] is False
    assert result["stage_guard_ok"] is False
    assert "src/side_effect.py" in result["error"]


def test_codex_stage_timeout_reports_process_tree_termination(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.codex_stage_runner.check_codex_cli", lambda path=None: _available_cli(path or "codex"))
    monkeypatch.setattr(
        "r2a.tools.codex_stage_runner.run_command_with_timeout",
        lambda *args, **kwargs: ProcessResult(returncode=124, stdout="", stderr="", timed_out=True),
    )
    monkeypatch.setattr("r2a.tools.codex_stage_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.codex_stage_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: _ok_guard(stage, allowed),
    )

    result = run_codex_stage(tmp_path, "paper", "write paper reports", [".r2a/PAPER_BRIEF.md"], timeout=1)

    assert result["returncode"] == 124
    assert result["success"] is False
    assert "process tree was terminated" in result["stderr_tail"]
