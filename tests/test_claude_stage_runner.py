from __future__ import annotations

from pathlib import Path

from r2a.tools.claude_stage_runner import CLAUDE_STAGE_ALLOWED_TOOLS, run_claude_stage
from r2a.tools.codex_cli import CodexCliCheckResult
from r2a.tools.process_manager import create_run_record, update_run_record, workflow_run_context
from r2a.tools.process_tree import ProcessResult


def _available_cli(path: str = "claude") -> CodexCliCheckResult:
    return CodexCliCheckResult(True, path, path, "2.1.128 (Claude Code)", "", "ok")


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


def test_non_engineer_claude_stage_allowed_tools_exclude_bash() -> None:
    allowed = CLAUDE_STAGE_ALLOWED_TOOLS.split(",")

    assert allowed == ["Read", "Write", "Edit", "MultiEdit"]
    assert not any(tool.startswith("Bash(") for tool in allowed)


def test_claude_stage_writes_stage_logs_and_passes_env(tmp_path: Path, monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr("r2a.tools.claude_stage_runner.check_claude_code_cli", lambda path=None: _available_cli(path or "claude"))
    monkeypatch.setattr("r2a.tools.claude_stage_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.claude_stage_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: _ok_guard(stage, allowed),
    )

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs.get("input_text")
        captured["env"] = kwargs.get("env")
        (tmp_path / ".r2a").mkdir(exist_ok=True)
        (tmp_path / ".r2a" / "TASK_SPEC.md").write_text("# TASK_SPEC\n", encoding="utf-8")
        return ProcessResult(returncode=0, stdout="ok\n", stderr="warn\n")

    monkeypatch.setattr("r2a.tools.claude_stage_runner.run_command_with_timeout", fake_run)

    result = run_claude_stage(
        tmp_path,
        "planner",
        "write task",
        [".r2a/TASK_SPEC.md"],
        claude_executable_path="claude",
        env={"ANTHROPIC_API_KEY": "dummy-key-placeholder"},
    )

    stdout_log = tmp_path / ".r2a" / "logs" / "planner_stdout.log"
    stderr_log = tmp_path / ".r2a" / "logs" / "planner_stderr.log"
    assert result["success"] is True
    assert "--print" in captured["command"]
    assert "write task" in captured["input"]
    assert "Backend choice affects the execution model, not R2A evidence rules" in captured["input"]
    assert "r2a/prompts/R2A_PROTOCOL.md" in captured["input"]
    assert "Do not bypass `.r2a/TASK_SPEC.md`, `.r2a/EXPERIMENT_CONTRACT.md`" in captured["input"]
    assert "Do not inflate smoke tests, synthetic demos, or unofficial reimplementations into L3/L4 evidence" in captured["input"]
    assert captured["env"]["ANTHROPIC_API_KEY"] == "dummy-key-placeholder"
    assert "dummy-key-placeholder" not in stdout_log.read_text(encoding="utf-8")
    stdout_text = stdout_log.read_text(encoding="utf-8")
    assert "claude_executable_path: claude" in stdout_text
    assert "prompt_size_bytes:" in stdout_text
    assert "allowed_tools: Read,Write,Edit,MultiEdit" in stdout_text
    assert stderr_log.read_text(encoding="utf-8").endswith("warn\n")


def test_runtime_registry_does_not_trigger_planner_boundary_violation(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime-root"))
    monkeypatch.setattr("r2a.tools.claude_stage_runner.check_claude_code_cli", lambda path=None: _available_cli(path or "claude"))
    monkeypatch.setattr("r2a.tools.stage_guard.shutil.which", lambda name: None)
    create_run_record(repo, "run-clean", status="running")

    def fake_run(command, **kwargs):
        update_run_record(repo, "run-clean", current_stage="planner", heartbeat="ok")
        (repo / ".r2a").mkdir(exist_ok=True)
        (repo / ".r2a" / "TASK_SPEC.md").write_text("# TASK_SPEC\n", encoding="utf-8")
        return ProcessResult(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("r2a.tools.claude_stage_runner.run_command_with_timeout", fake_run)

    with workflow_run_context(repo, "run-clean"):
        result = run_claude_stage(repo, "planner", "write task", [".r2a/TASK_SPEC.md"], claude_executable_path="claude")

    assert result["success"] is True
    assert result["unexpected_modifications"] == []
    assert not (repo / ".r2a" / "runtime").exists()


def test_runtime_registry_does_not_trigger_reviewer_boundary_violation(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("R2A_RUNTIME_ROOT", str(tmp_path / "runtime-root"))
    monkeypatch.setattr("r2a.tools.claude_stage_runner.check_claude_code_cli", lambda path=None: _available_cli(path or "claude"))
    monkeypatch.setattr("r2a.tools.stage_guard.shutil.which", lambda name: None)
    create_run_record(repo, "run-reviewer", status="running")

    def fake_run(command, **kwargs):
        update_run_record(repo, "run-reviewer", current_stage="reviewer", heartbeat="ok")
        (repo / ".r2a").mkdir(exist_ok=True)
        (repo / ".r2a" / "REVIEW_REPORT.md").write_text("# REVIEW_REPORT\n", encoding="utf-8")
        return ProcessResult(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("r2a.tools.claude_stage_runner.run_command_with_timeout", fake_run)

    with workflow_run_context(repo, "run-reviewer"):
        result = run_claude_stage(repo, "reviewer", "write review", [".r2a/REVIEW_REPORT.md"], claude_executable_path="claude")

    assert result["success"] is True
    assert result["unexpected_modifications"] == []


def test_claude_stage_uses_router_prompt_file(tmp_path: Path, monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr("r2a.tools.claude_stage_runner.check_claude_code_cli", lambda path=None: _available_cli(path or "ccr"))
    monkeypatch.setattr("r2a.tools.claude_stage_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.claude_stage_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: _ok_guard(stage, allowed),
    )

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs.get("input_text")
        (tmp_path / ".r2a").mkdir(exist_ok=True)
        (tmp_path / ".r2a" / "REVIEW_REPORT.md").write_text("# REVIEW_REPORT\n", encoding="utf-8")
        return ProcessResult(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("r2a.tools.claude_stage_runner.run_command_with_timeout", fake_run)

    result = run_claude_stage(tmp_path, "reviewer", "write review", [".r2a/REVIEW_REPORT.md"], claude_executable_path="ccr")

    assert result["success"] is True
    assert captured["command"][:2] == ["ccr", "code"]
    allowed_tools = captured["command"][captured["command"].index("--allowedTools") + 1]
    assert "Bash(" not in allowed_tools
    assert allowed_tools == "Read,Write,Edit,MultiEdit"
    assert "-p" in captured["command"]
    assert captured["input"] == ""
    prompt_file = tmp_path / ".r2a" / "logs" / "claude_reviewer_prompt.md"
    assert prompt_file.exists()
    prompt_text = prompt_file.read_text(encoding="utf-8")
    assert "write review" in prompt_text
    assert "Backend choice affects the execution model, not R2A evidence rules" in prompt_text
    assert "r2a/prompts/R2A_PROTOCOL.md" in prompt_text


def test_claude_stage_exposes_tool_call_parse_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.claude_stage_runner.check_claude_code_cli", lambda path=None: _available_cli(path or "claude"))
    monkeypatch.setattr("r2a.tools.claude_stage_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.claude_stage_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: _ok_guard(stage, allowed),
    )

    def fake_run(command, **kwargs):
        return ProcessResult(
            returncode=1,
            stdout="The model's tool call could not be parsed (retry also failed).",
            stderr="",
        )

    monkeypatch.setattr("r2a.tools.claude_stage_runner.run_command_with_timeout", fake_run)

    result = run_claude_stage(tmp_path, "planner", "write task", [".r2a/TASK_SPEC.md"], claude_executable_path="claude")

    assert result["success"] is False
    assert result["backend_failure_category"] == "TOOL_CALL_PARSE_FAILURE"
    assert result["backend_failure_scope"] == "BACKEND_TRANSIENT_FAILURE"
    assert result["backend_suggested_action"] == "manual_retry_same_stage"
    assert result["retry_attempted"] is True
    assert result["transient_backend_retry_success"] is False


def test_claude_stage_zero_returncode_with_parse_failure_is_not_success(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.claude_stage_runner.check_claude_code_cli", lambda path=None: _available_cli(path or "claude"))
    monkeypatch.setattr("r2a.tools.claude_stage_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.claude_stage_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: _ok_guard(stage, allowed),
    )

    def fake_run(command, **kwargs):
        (tmp_path / ".r2a").mkdir(exist_ok=True)
        (tmp_path / ".r2a" / "TASK_SPEC.md").write_text("# TASK_SPEC\n", encoding="utf-8")
        return ProcessResult(
            returncode=0,
            stdout="The model's tool call could not be parsed (retry also failed).",
            stderr="",
        )

    monkeypatch.setattr("r2a.tools.claude_stage_runner.run_command_with_timeout", fake_run)

    result = run_claude_stage(tmp_path, "planner", "write task", [".r2a/TASK_SPEC.md"], claude_executable_path="claude")

    assert result["success"] is False
    assert result["backend_failure_category"] == "TOOL_CALL_PARSE_FAILURE"


def test_claude_stage_retries_tool_call_parse_failure_once_and_succeeds(tmp_path: Path, monkeypatch) -> None:
    calls = {"count": 0}
    monkeypatch.setattr("r2a.tools.claude_stage_runner.check_claude_code_cli", lambda path=None: _available_cli(path or "claude"))
    monkeypatch.setattr("r2a.tools.claude_stage_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.claude_stage_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: _ok_guard(stage, allowed),
    )

    def fake_run(command, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return ProcessResult(returncode=1, stdout="The model's tool call could not be parsed (retry also failed).", stderr="")
        (tmp_path / ".r2a").mkdir(exist_ok=True)
        (tmp_path / ".r2a" / "TASK_SPEC.md").write_text("# TASK_SPEC\n", encoding="utf-8")
        return ProcessResult(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("r2a.tools.claude_stage_runner.run_command_with_timeout", fake_run)

    result = run_claude_stage(tmp_path, "planner", "write task", [".r2a/TASK_SPEC.md"], claude_executable_path="claude")

    assert calls["count"] == 2
    assert result["success"] is True
    assert result["retry_attempted"] is True
    assert result["retry_count"] == 1
    assert result["transient_backend_retry_success"] is True
    assert result["first_failure_category"] == "TOOL_CALL_PARSE_FAILURE"
    assert Path(result["attempt_1_stdout_log"]).exists()
    assert Path(result["attempt_2_stdout_log"]).exists()
    diagnostics = tmp_path / ".r2a" / "logs" / "claude_stage_diagnostics.jsonl"
    assert diagnostics.exists()
    assert "TOOL_CALL_PARSE_FAILURE" in diagnostics.read_text(encoding="utf-8")


def test_claude_stage_retry_failure_suggests_manual_retry(tmp_path: Path, monkeypatch) -> None:
    calls = {"count": 0}
    monkeypatch.setattr("r2a.tools.claude_stage_runner.check_claude_code_cli", lambda path=None: _available_cli(path or "claude"))
    monkeypatch.setattr("r2a.tools.claude_stage_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.claude_stage_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: _ok_guard(stage, allowed),
    )

    def fake_run(command, **kwargs):
        calls["count"] += 1
        return ProcessResult(returncode=1, stdout="The model's tool call could not be parsed (retry also failed).", stderr="")

    monkeypatch.setattr("r2a.tools.claude_stage_runner.run_command_with_timeout", fake_run)

    result = run_claude_stage(tmp_path, "planner", "write task", [".r2a/TASK_SPEC.md"], claude_executable_path="claude")

    assert calls["count"] == 2
    assert result["success"] is False
    assert result["retry_attempted"] is True
    assert result["transient_backend_retry_success"] is False
    assert result["backend_failure_category"] == "TOOL_CALL_PARSE_FAILURE"
    assert result["backend_suggested_action"] == "manual_retry_same_stage"


def test_claude_stage_plain_failure_does_not_retry(tmp_path: Path, monkeypatch) -> None:
    calls = {"count": 0}
    monkeypatch.setattr("r2a.tools.claude_stage_runner.check_claude_code_cli", lambda path=None: _available_cli(path or "claude"))
    monkeypatch.setattr("r2a.tools.claude_stage_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.claude_stage_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: _ok_guard(stage, allowed),
    )

    def fake_run(command, **kwargs):
        calls["count"] += 1
        return ProcessResult(returncode=1, stdout="ordinary prompt failure", stderr="")

    monkeypatch.setattr("r2a.tools.claude_stage_runner.run_command_with_timeout", fake_run)

    result = run_claude_stage(tmp_path, "planner", "write task", [".r2a/TASK_SPEC.md"], claude_executable_path="claude")

    assert calls["count"] == 1
    assert result["retry_attempted"] is False


def test_claude_stage_dep0190_warning_does_not_retry(tmp_path: Path, monkeypatch) -> None:
    calls = {"count": 0}
    monkeypatch.setattr("r2a.tools.claude_stage_runner.check_claude_code_cli", lambda path=None: _available_cli(path or "claude"))
    monkeypatch.setattr("r2a.tools.claude_stage_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.claude_stage_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: _ok_guard(stage, allowed),
    )

    def fake_run(command, **kwargs):
        calls["count"] += 1
        return ProcessResult(returncode=1, stdout="", stderr="[DEP0190] DeprecationWarning")

    monkeypatch.setattr("r2a.tools.claude_stage_runner.run_command_with_timeout", fake_run)

    result = run_claude_stage(tmp_path, "planner", "write task", [".r2a/TASK_SPEC.md"], claude_executable_path="claude")

    assert calls["count"] == 1
    assert result["retry_attempted"] is False
    assert result["backend_warning"] == "NODE_DEP0190_WARNING"


def test_claude_stage_retry_fails_when_second_attempt_uses_stale_output(tmp_path: Path, monkeypatch) -> None:
    calls = {"count": 0}
    monkeypatch.setattr("r2a.tools.claude_stage_runner.check_claude_code_cli", lambda path=None: _available_cli(path or "claude"))
    monkeypatch.setattr("r2a.tools.claude_stage_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.claude_stage_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: _ok_guard(stage, allowed),
    )

    def fake_run(command, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            (tmp_path / ".r2a").mkdir(exist_ok=True)
            (tmp_path / ".r2a" / "TASK_SPEC.md").write_text("# partial\n", encoding="utf-8")
            return ProcessResult(returncode=1, stdout="The model's tool call could not be parsed (retry also failed).", stderr="")
        return ProcessResult(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("r2a.tools.claude_stage_runner.run_command_with_timeout", fake_run)

    result = run_claude_stage(tmp_path, "planner", "write task", [".r2a/TASK_SPEC.md"], claude_executable_path="claude")

    assert calls["count"] == 2
    assert result["success"] is False
    assert result["retry_output_freshness_failed"] is True
    assert result["output_freshness"]["missing_outputs"]


def test_claude_stage_tool_parse_retry_uses_clean_staging(tmp_path: Path, monkeypatch) -> None:
    calls = {"count": 0}
    staging_file = tmp_path / ".r2a" / "staging" / "planner" / "iter_001" / "attempt_001" / "TASK_SPEC.md"
    monkeypatch.setattr("r2a.tools.claude_stage_runner.check_claude_code_cli", lambda path=None: _available_cli(path or "claude"))
    monkeypatch.setattr("r2a.tools.claude_stage_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.claude_stage_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: _ok_guard(stage, allowed),
    )

    def fake_run(command, **kwargs):
        calls["count"] += 1
        staging_file.parent.mkdir(parents=True, exist_ok=True)
        if calls["count"] == 1:
            staging_file.write_text("# partial\n", encoding="utf-8")
            return ProcessResult(returncode=1, stdout="The model's tool call could not be parsed (retry also failed).", stderr="")
        assert not staging_file.exists()
        staging_file.write_text("# TASK_SPEC\n", encoding="utf-8")
        return ProcessResult(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("r2a.tools.claude_stage_runner.run_command_with_timeout", fake_run)

    result = run_claude_stage(
        tmp_path,
        "planner",
        "write task",
        [".r2a/staging/planner/iter_001/attempt_001/TASK_SPEC.md"],
        claude_executable_path="claude",
    )

    assert result["success"] is True
    archived = tmp_path / ".r2a" / "logs" / "planner_attempt_1_partial_outputs" / ".r2a" / "staging" / "planner" / "iter_001" / "attempt_001" / "TASK_SPEC.md"
    assert archived.exists()


def test_claude_stage_empty_required_output_fails_freshness(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.claude_stage_runner.check_claude_code_cli", lambda path=None: _available_cli(path or "claude"))
    monkeypatch.setattr("r2a.tools.claude_stage_runner.snapshot_stage_changes", lambda repo_path: set())
    monkeypatch.setattr(
        "r2a.tools.claude_stage_runner.check_stage_allowed_modifications",
        lambda repo_path, stage, allowed, baseline=None: _ok_guard(stage, allowed),
    )

    def fake_run(command, **kwargs):
        (tmp_path / ".r2a").mkdir(exist_ok=True)
        (tmp_path / ".r2a" / "TASK_SPEC.md").write_text("", encoding="utf-8")
        return ProcessResult(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("r2a.tools.claude_stage_runner.run_command_with_timeout", fake_run)

    result = run_claude_stage(tmp_path, "planner", "write task", [".r2a/TASK_SPEC.md"], claude_executable_path="claude")

    assert result["success"] is False
    assert result["output_freshness"]["empty_outputs"]
