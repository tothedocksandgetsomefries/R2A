from pathlib import Path

from r2a.tools.codex_cli import CodexCliCheckResult
from r2a.tools.codex_runner import run_codex_exec
from r2a.tools.process_tree import ProcessResult


def _available_cli(path: str = "codex") -> CodexCliCheckResult:
    return CodexCliCheckResult(True, path, path, "codex 1.0", "", "ok")


def _mock_engineer_guard_available(monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.codex_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.codex_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: {
            "stage": stage,
            "allowed_patterns": allowed,
            "changed_files": [],
            "stage_changed_files": [],
            "unexpected_modifications": [],
            "ok": True,
            "guard_available": True,
            "error": "",
            "warning": "",
        },
    )


def test_run_codex_exec_writes_stdout_and_stderr_logs(tmp_path: Path, monkeypatch) -> None:
    captured = {}
    task_spec = tmp_path / ".r2a" / "TASK_SPEC.md"
    task_spec.parent.mkdir()
    task_spec.write_text("# TASK_SPEC\n\n## Goal\n\nRun a reduced test.\n", encoding="utf-8")
    monkeypatch.setattr("r2a.tools.codex_runner.check_codex_cli", lambda path=None: _available_cli(path or "codex"))
    _mock_engineer_guard_available(monkeypatch)

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs.get("input_text")
        return ProcessResult(returncode=0, stdout="line1\nline2\n", stderr="warn\n")

    monkeypatch.setattr("r2a.tools.codex_runner.run_command_with_timeout", fake_run)

    result = run_codex_exec(tmp_path, task_spec, codex_executable_path="codex")

    stdout_log = tmp_path / ".r2a" / "logs" / "codex_stdout.log"
    stderr_log = tmp_path / ".r2a" / "logs" / "codex_stderr.log"
    assert stdout_log.exists()
    assert stderr_log.exists()
    assert stdout_log.read_text(encoding="utf-8").endswith("line1\nline2\n")
    assert stderr_log.read_text(encoding="utf-8").endswith("warn\n")
    assert result.stdout_log_path == str(stdout_log)
    assert result.stderr_log_path == str(stderr_log)
    assert result.returncode == 0
    assert result.stdout_tail == "line1\nline2"
    assert result.stderr_tail == "warn"
    assert "--skip-git-repo-check" in captured["command"]
    assert captured["command"][-1] == "-"
    assert "TASK_SPEC.md content" in captured["input"]
    assert "Backend choice affects the execution model, not R2A evidence rules" in captured["input"]
    assert "r2a/prompts/R2A_PROTOCOL.md" in captured["input"]
    assert "Do not inflate smoke tests, synthetic demos, or unofficial reimplementations into L3/L4 evidence" in captured["input"]
    assert "`command_id`, `log_path`, `artifact_hash`, and `input_provenance`" in captured["input"]


def test_run_codex_exec_writes_stderr_log_on_file_not_found(tmp_path: Path, monkeypatch) -> None:
    task_spec = tmp_path / ".r2a" / "TASK_SPEC.md"
    task_spec.parent.mkdir()
    task_spec.write_text("# TASK_SPEC\n", encoding="utf-8")

    monkeypatch.setattr(
        "r2a.tools.codex_runner.check_codex_cli",
        lambda path=None: CodexCliCheckResult(
            False,
            path or "codex",
            None,
            "",
            "FileNotFoundError: codex",
            "Install/configure a real Codex CLI.",
        ),
    )
    _mock_engineer_guard_available(monkeypatch)

    result = run_codex_exec(tmp_path, task_spec, codex_executable_path="codex")

    stderr_log = tmp_path / ".r2a" / "logs" / "codex_stderr.log"
    stdout_log = tmp_path / ".r2a" / "logs" / "codex_stdout.log"
    assert stdout_log.exists()
    assert stderr_log.exists()
    stderr = stderr_log.read_text(encoding="utf-8")
    assert "FileNotFoundError" in stderr
    assert result.returncode == 127
    assert result.stderr_log_path == str(stderr_log)
    assert "FileNotFoundError" in result.stderr_tail
    assert "Install/configure" in result.hint


def test_run_codex_exec_timeout_reports_process_tree_termination(tmp_path: Path, monkeypatch) -> None:
    task_spec = tmp_path / ".r2a" / "TASK_SPEC.md"
    task_spec.parent.mkdir()
    task_spec.write_text("# TASK_SPEC\n", encoding="utf-8")
    monkeypatch.setattr("r2a.tools.codex_runner.check_codex_cli", lambda path=None: _available_cli(path or "codex"))
    _mock_engineer_guard_available(monkeypatch)
    monkeypatch.setattr(
        "r2a.tools.codex_runner.run_command_with_timeout",
        lambda *args, **kwargs: ProcessResult(returncode=124, stdout="", stderr="", timed_out=True),
    )

    result = run_codex_exec(tmp_path, task_spec, timeout=1, codex_executable_path="codex")

    assert result.returncode == 124
    assert result.skipped is True
    assert "process tree was terminated" in result.stderr_tail


def test_run_codex_exec_records_guard_unavailable_without_blocking(tmp_path: Path, monkeypatch) -> None:
    task_spec = tmp_path / ".r2a" / "TASK_SPEC.md"
    task_spec.parent.mkdir()
    task_spec.write_text("# TASK_SPEC\n", encoding="utf-8")
    monkeypatch.setattr("r2a.tools.codex_runner.check_codex_cli", lambda path=None: _available_cli(path or "codex"))
    monkeypatch.setattr("r2a.tools.codex_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.codex_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: {
            "stage": stage,
            "allowed_patterns": allowed,
            "changed_files": [],
            "stage_changed_files": [],
            "unexpected_modifications": [],
            "ok": False,
            "guard_available": False,
            "error": "git executable not found",
            "warning": "Stage guard could not verify modifications",
        },
    )
    monkeypatch.setattr(
        "r2a.tools.codex_runner.run_command_with_timeout",
        lambda *args, **kwargs: ProcessResult(returncode=0, stdout="ok\n", stderr=""),
    )

    result = run_codex_exec(tmp_path, task_spec, codex_executable_path="codex")

    assert result.returncode == 0
    assert result.ok is True
    assert "Stage guard could not verify modifications" in result.stderr_tail
    assert "git executable not found" in (tmp_path / ".r2a" / "logs" / "codex_stderr.log").read_text(encoding="utf-8")
