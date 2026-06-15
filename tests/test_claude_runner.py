from pathlib import Path

from r2a.tools.claude_runner import _engineer_completion_observed, _ensure_engineer_done_from_terminal_results, check_claude_code_cli, run_claude_code_exec
from r2a.tools.codex_cli import CodexCliCheckResult
from r2a.tools.process_tree import ProcessResult


def _available_cli(path: str = "claude") -> CodexCliCheckResult:
    return CodexCliCheckResult(True, path, path, "2.1.128 (Claude Code)", "", "ok")


def _mock_engineer_guard_available(monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.claude_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.claude_runner.check_stage_allowed_modifications",
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


def test_run_claude_code_exec_uses_print_mode_and_writes_logs(tmp_path: Path, monkeypatch) -> None:
    captured = {}
    task_spec = tmp_path / ".r2a" / "TASK_SPEC.md"
    task_spec.parent.mkdir()
    task_spec.write_text(
        "# TASK_SPEC\n\n"
        "## Goal\n\nRun source verification.\n\n"
        "## Allowed Files\n\n- .r2a/results/source_verification.csv\n\n"
        "## Forbidden Files\n\n- .git/\n\n"
        "## Acceptance Criteria\n\n- CSV exists.\n\n"
        "## Stop Conditions\n\n- Stop on unsafe changes.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("r2a.tools.claude_runner.check_claude_code_cli", lambda path=None: _available_cli(path or "claude"))
    _mock_engineer_guard_available(monkeypatch)

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs.get("input_text")
        captured["completion_check"] = kwargs.get("completion_check")
        return ProcessResult(returncode=0, stdout="done\n", stderr="")

    monkeypatch.setattr("r2a.tools.claude_runner.run_command_with_timeout", fake_run)

    result = run_claude_code_exec(tmp_path, task_spec, claude_executable_path="claude")

    assert result.ok
    assert "--print" in captured["command"]
    assert "--permission-mode" in captured["command"]
    allowed_tools = captured["command"][captured["command"].index("--allowedTools") + 1]
    assert "Bash(python -m pip install *)" in allowed_tools
    assert "Bash(pip install *)" in allowed_tools
    assert "Bash(curl *)" in allowed_tools
    assert "Bash(wget *)" in allowed_tools
    assert "Bash(hf *)" in allowed_tools
    assert "Bash(cmake -S *)" in allowed_tools
    assert "Bash(cmake --build *)" in allowed_tools
    assert "Bash(make *)" in allowed_tools
    assert ".venv" in allowed_tools
    assert "TASK_SPEC.md content" in captured["input"]
    assert "Backend choice affects the execution model, not R2A evidence rules" in captured["input"]
    assert "r2a/prompts/R2A_PROTOCOL.md" in captured["input"]
    assert "TASK_SPEC.md, and `.r2a/EXPERIMENT_CONTRACT.md`" in captured["input"]
    assert "Do not inflate smoke tests, synthetic demos, or unofficial reimplementations into L3/L4 evidence" in captured["input"]
    assert "final response should be plain text only" in captured["input"]
    assert "Real experiment means running code and writing measured outputs" in captured["input"]
    assert "official-input acquisition iteration" in captured["input"]
    assert "dependency_setup.csv" in captured["input"]
    assert "reproduction_status.csv" in captured["input"]
    assert "ENGINEER_DONE.txt" in captured["input"]
    assert "must be newly written during this invocation" in captured["input"]
    assert "Do not write `ENGINEER_DONE.txt` as `PARTIAL` or `BLOCKED` immediately after the first configure/build failure" in captured["input"]
    assert "A single-file compile smoke is not enough" in captured["input"]
    assert "Do not write `.r2a/EXECUTION_REPORT.md` directly" in captured["input"]
    assert callable(captured["completion_check"])
    assert (tmp_path / ".r2a" / "logs" / "claude_stdout.log").exists()
    assert (tmp_path / ".r2a" / "logs" / "claude_stderr.log").exists()


def test_run_claude_code_exec_uses_router_code_subcommand(tmp_path: Path, monkeypatch) -> None:
    captured = {}
    task_spec = tmp_path / ".r2a" / "TASK_SPEC.md"
    task_spec.parent.mkdir()
    task_spec.write_text("# TASK_SPEC\n\n## Goal\n\nRun source verification.\n", encoding="utf-8")
    monkeypatch.setattr("r2a.tools.claude_runner.check_claude_code_cli", lambda path=None: _available_cli(path or "ccr"))
    _mock_engineer_guard_available(monkeypatch)

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs.get("input_text")
        captured["completion_check"] = kwargs.get("completion_check")
        return ProcessResult(returncode=0, stdout="done\n", stderr="")

    monkeypatch.setattr("r2a.tools.claude_runner.run_command_with_timeout", fake_run)

    result = run_claude_code_exec(tmp_path, task_spec, claude_executable_path="C:/Tools/ClaudeCode/ccr.cmd")

    assert result.ok
    assert captured["command"][0] == "C:/Tools/ClaudeCode/ccr.cmd"
    assert captured["command"][1] == "code"
    assert "-p" in captured["command"]
    assert "--print" not in captured["command"]
    assert captured["input"] == ""
    prompt_file = tmp_path / ".r2a" / "logs" / "claude_engineer_prompt.md"
    assert prompt_file.exists()
    assert "TASK_SPEC.md content" in prompt_file.read_text(encoding="utf-8")
    assert "r2a/prompts/R2A_PROTOCOL.md" in prompt_file.read_text(encoding="utf-8")
    assert "ENGINEER_DONE.txt" in prompt_file.read_text(encoding="utf-8")
    assert "Do not write EXECUTION_REPORT.md directly" in " ".join(captured["command"])
    assert callable(captured["completion_check"])


def test_run_claude_code_exec_exposes_tool_call_parse_failure(tmp_path: Path, monkeypatch) -> None:
    task_spec = tmp_path / ".r2a" / "TASK_SPEC.md"
    task_spec.parent.mkdir()
    task_spec.write_text("# TASK_SPEC\n\n## Goal\n\nRun source verification.\n", encoding="utf-8")
    monkeypatch.setattr("r2a.tools.claude_runner.check_claude_code_cli", lambda path=None: _available_cli(path or "claude"))
    _mock_engineer_guard_available(monkeypatch)

    calls = {"count": 0}

    def fake_run(command, **kwargs):
        calls["count"] += 1
        return ProcessResult(
            returncode=1,
            stdout="The model's tool call could not be parsed (retry also failed).",
            stderr="",
        )

    monkeypatch.setattr("r2a.tools.claude_runner.run_command_with_timeout", fake_run)

    result = run_claude_code_exec(tmp_path, task_spec, claude_executable_path="claude")

    assert not result.ok
    assert calls["count"] == 1
    assert result.backend_failure_category == "TOOL_CALL_PARSE_FAILURE"
    assert result.backend_failure_scope == "BACKEND_TRANSIENT_FAILURE"
    assert result.backend_suggested_action == "manual_retry_same_stage"
    assert result.safe_to_retry_likely is True
    assert result.side_effects_detected is False
    assert "Manual retry recommended" in result.manual_retry_message


def test_run_claude_code_exec_parse_failure_with_side_effects_requires_manual_inspection(tmp_path: Path, monkeypatch) -> None:
    task_spec = tmp_path / ".r2a" / "TASK_SPEC.md"
    task_spec.parent.mkdir()
    task_spec.write_text("# TASK_SPEC\n\n## Goal\n\nRun source verification.\n", encoding="utf-8")
    monkeypatch.setattr("r2a.tools.claude_runner.check_claude_code_cli", lambda path=None: _available_cli(path or "claude"))
    _mock_engineer_guard_available(monkeypatch)

    def fake_run(command, **kwargs):
        results = tmp_path / ".r2a" / "results"
        results.mkdir(parents=True, exist_ok=True)
        (results / "source_verification.csv").write_text("status,notes\nPARTIAL,written before backend failure\n", encoding="utf-8")
        return ProcessResult(
            returncode=1,
            stdout="The model's tool call could not be parsed (retry also failed).",
            stderr="",
        )

    monkeypatch.setattr("r2a.tools.claude_runner.run_command_with_timeout", fake_run)

    result = run_claude_code_exec(tmp_path, task_spec, claude_executable_path="claude")

    assert not result.ok
    assert result.backend_failure_category == "TOOL_CALL_PARSE_FAILURE"
    assert result.safe_to_retry_likely is False
    assert result.side_effects_detected is True
    assert "Manual inspection required before retry" in result.manual_retry_message


def test_run_claude_code_exec_exposes_authentication_failure(tmp_path: Path, monkeypatch) -> None:
    task_spec = tmp_path / ".r2a" / "TASK_SPEC.md"
    task_spec.parent.mkdir()
    task_spec.write_text("# TASK_SPEC\n\n## Goal\n\nRun source verification.\n", encoding="utf-8")
    monkeypatch.setattr("r2a.tools.claude_runner.check_claude_code_cli", lambda path=None: _available_cli(path or "claude"))
    _mock_engineer_guard_available(monkeypatch)

    def fake_run(command, **kwargs):
        return ProcessResult(returncode=1, stdout="Not logged in · Please run /login", stderr="")

    monkeypatch.setattr("r2a.tools.claude_runner.run_command_with_timeout", fake_run)

    result = run_claude_code_exec(tmp_path, task_spec, claude_executable_path="claude")

    assert not result.ok
    assert result.backend_failure_category == "AUTHENTICATION_FAILURE"
    assert result.backend_failure_scope == "BACKEND_AUTH_FAILURE"
    assert result.transient_backend_failure is False
    assert result.safe_to_retry_likely is False


def test_engineer_completion_requires_fresh_done_marker(tmp_path: Path) -> None:
    results_dir = tmp_path / ".r2a" / "results"
    results_dir.mkdir(parents=True)
    done = results_dir / "ENGINEER_DONE.txt"
    csv = results_dir / "reproduction_status.csv"
    done.write_text("PARTIAL\n", encoding="utf-8")
    csv.write_text("status,reason\nPARTIAL,old\n", encoding="utf-8")

    done_before = (done.stat().st_mtime_ns, done.stat().st_size)
    csv_before = {csv: (csv.stat().st_mtime_ns, csv.stat().st_size)}

    csv.write_text("status,reason\nPARTIAL,new csv only\n", encoding="utf-8")

    assert not _engineer_completion_observed(tmp_path, csv_before, done_before)

    done.write_text("PARTIAL\nfresh\n", encoding="utf-8")

    assert _engineer_completion_observed(tmp_path, csv_before, done_before)


def test_engineer_done_fallback_uses_terminal_result_csv(tmp_path: Path) -> None:
    results_dir = tmp_path / ".r2a" / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "build_smoke.csv").write_text(
        "status,command,exit_code,duration_sec,component,notes\n"
        "BLOCKED,cmake configure,NA,0,cmake_configure,No CMakeLists.txt found.\n",
        encoding="utf-8",
    )

    wrote = _ensure_engineer_done_from_terminal_results(tmp_path)

    assert wrote
    # BLOCKED is mapped to FAIL via LEGACY_STATUS_MAP
    assert (results_dir / "ENGINEER_DONE.txt").read_text(encoding="utf-8").strip() == "FAIL"
    assert "R2A Fallback Completion" in (results_dir / "ENGINEER_NOTES.md").read_text(encoding="utf-8")


def test_check_claude_code_cli_uses_router_version_command(monkeypatch) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return ProcessResult(returncode=0, stdout="claude-code-router version: 2.0.0\n", stderr="")

    monkeypatch.setattr("r2a.tools.claude_runner.shutil.which", lambda executable: "C:/Tools/ccr.cmd")
    monkeypatch.setattr("r2a.tools.claude_runner.subprocess.run", fake_run)

    result = check_claude_code_cli("ccr")

    assert result.available
    assert captured["command"] == ["C:/Tools/ccr.cmd", "version"]
