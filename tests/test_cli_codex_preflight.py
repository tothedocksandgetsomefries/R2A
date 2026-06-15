from __future__ import annotations

from typer.testing import CliRunner

from r2a.cli import app
from r2a.tools.codex_cli import CodexCliCheckResult


def test_workflow_template_shell_rules_does_not_check_codex(tmp_path, monkeypatch) -> None:
    class FakeGraph:
        def invoke(self, state):
            return {"final_report": "ok", "final_report_path": ""}

    monkeypatch.setattr("r2a.cli.build_workflow_graph", lambda: FakeGraph())
    monkeypatch.setattr("r2a.cli.check_codex_cli", lambda path: (_ for _ in ()).throw(AssertionError("codex should not be checked")))

    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "--repo",
            str(tmp_path),
            "--goal",
            "reproduce safely",
            "--auto-approve",
            "--paper-backend",
            "preprocess",
            "--planner-backend",
            "template",
            "--engineer-executor",
            "shell",
            "--manager-backend",
            "rules",
        ],
    )

    assert result.exit_code == 0
    assert "ok" in result.output


def test_workflow_passes_paper_path_and_output_language_to_state(tmp_path, monkeypatch) -> None:
    captured = {}

    class FakeGraph:
        def invoke(self, state):
            captured.update(state)
            return {"final_report": "ok", "final_report_path": ""}

    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr("r2a.cli.build_workflow_graph", lambda: FakeGraph())

    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "--repo",
            str(tmp_path),
            "--goal",
            "reproduce safely",
            "--paper-path",
            str(paper),
            "--output-language",
            "Chinese",
            "--auto-approve",
        ],
    )

    assert result.exit_code == 0
    assert captured["paper_path"] == str(paper)
    assert captured["language"] == "zh"
    assert captured["output_language"] == "Chinese"
    assert captured["guidance"] == "reproduce safely"
    assert captured["resolved_goal"] == "reproduce safely"


def test_workflow_passes_codex_stage_timeout_to_engineer_timeout(tmp_path, monkeypatch) -> None:
    captured = {}

    class FakeGraph:
        def invoke(self, state):
            captured.update(state)
            return {"final_report": "ok", "final_report_path": ""}

    monkeypatch.setattr("r2a.cli.build_workflow_graph", lambda: FakeGraph())
    monkeypatch.setattr("r2a.cli.check_codex_cli", lambda path: CodexCliCheckResult(True, path, path, "codex 1.0", "", "ok"))

    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "--repo",
            str(tmp_path),
            "--goal",
            "reproduce safely",
            "--auto-approve",
            "--engineer-executor",
            "codex",
            "--codex-stage-timeout",
            "123",
        ],
    )

    assert result.exit_code == 0
    assert captured["codex_stage_timeout"] == 123
    assert captured["timeout"] == 123


def test_workflow_codex_backend_checks_codex_and_exits_when_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "r2a.cli.check_codex_cli",
        lambda path: CodexCliCheckResult(False, path, path, "", "PermissionError: Access is denied", "WindowsApps protected path; use codex.cmd."),
    )

    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "--repo",
            str(tmp_path),
            "--goal",
            "reproduce safely",
            "--auto-approve",
            "--engineer-executor",
            "codex",
            "--codex-executable-path",
            "C:/Program Files/WindowsApps/OpenAI.Codex_123/app/resources/codex.exe",
        ],
    )

    assert result.exit_code == 1
    assert "Access is denied" in result.output
    assert "codex.cmd" in result.output


def test_workflow_paper_ai_reader_checks_codex_cli(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "r2a.cli.check_codex_cli",
        lambda path: CodexCliCheckResult(False, path, path, "", "PermissionError: Access is denied", "Use codex.cmd."),
    )

    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "--repo",
            str(tmp_path),
            "--goal",
            "read paper",
            "--auto-approve",
            "--paper-backend",
            "ai_reader",
            "--planner-backend",
            "template",
            "--engineer-executor",
            "shell",
            "--manager-backend",
            "rules",
        ],
    )

    assert result.exit_code == 1
    assert "Access is denied" in result.output


def test_workflow_paper_backend_codex_is_disabled_before_preflight(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.cli.check_codex_cli", lambda path: (_ for _ in ()).throw(AssertionError("paper backend should not preflight codex")))

    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "--repo",
            str(tmp_path),
            "--goal",
            "reproduce safely",
            "--auto-approve",
            "--paper-backend",
            "codex",
        ],
    )

    assert result.exit_code != 0
    assert "Paper Codex backend is disabled" in result.output


def test_workflow_claude_engineer_checks_claude_cli(tmp_path, monkeypatch) -> None:
    captured = {}

    class FakeGraph:
        def invoke(self, state):
            captured.update(state)
            return {"final_report": "ok", "final_report_path": ""}

    monkeypatch.setattr("r2a.cli.build_workflow_graph", lambda: FakeGraph())
    monkeypatch.setattr(
        "r2a.cli.check_claude_code_cli",
        lambda path: CodexCliCheckResult(True, path, path, "2.1.128 (Claude Code)", "", "ok"),
    )

    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "--repo",
            str(tmp_path),
            "--goal",
            "reproduce safely",
            "--auto-approve",
            "--engineer-executor",
            "claude",
            "--claude-executable-path",
            "C:/Tools/claude.cmd",
        ],
    )

    assert result.exit_code == 0
    assert captured["engineer_executor"] == "claude"
    assert captured["claude_executable_path"] == "C:/Tools/claude.cmd"


def test_workflow_claude_planner_checks_claude_cli(tmp_path, monkeypatch) -> None:
    captured = {}

    class FakeGraph:
        def invoke(self, state):
            captured.update(state)
            return {"final_report": "ok", "final_report_path": ""}

    monkeypatch.setattr("r2a.cli.build_workflow_graph", lambda: FakeGraph())
    monkeypatch.setattr(
        "r2a.cli.check_claude_code_cli",
        lambda path: CodexCliCheckResult(True, path, path, "2.1.128 (Claude Code)", "", "ok"),
    )
    monkeypatch.setattr("r2a.cli.check_codex_cli", lambda path: (_ for _ in ()).throw(AssertionError("codex should not be checked")))

    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "--repo",
            str(tmp_path),
            "--goal",
            "reproduce safely",
            "--auto-approve",
            "--paper-backend",
            "preprocess",
            "--planner-backend",
            "claude",
            "--engineer-executor",
            "shell",
            "--manager-backend",
            "rules",
            "--claude-executable-path",
            "C:/Tools/claude.cmd",
        ],
    )

    assert result.exit_code == 0
    assert captured["planner_backend"] == "claude"
    assert captured["claude_executable_path"] == "C:/Tools/claude.cmd"


def test_workflow_claude_reader_checks_claude_cli(tmp_path, monkeypatch) -> None:
    captured = {}

    class FakeGraph:
        def invoke(self, state):
            captured.update(state)
            return {"final_report": "ok", "final_report_path": ""}

    monkeypatch.setattr("r2a.cli.build_workflow_graph", lambda: FakeGraph())
    monkeypatch.setattr(
        "r2a.cli.check_claude_code_cli",
        lambda path: CodexCliCheckResult(True, path, path, "2.1.128 (Claude Code)", "", "ok"),
    )
    monkeypatch.setattr("r2a.cli.check_codex_cli", lambda path: (_ for _ in ()).throw(AssertionError("codex should not be checked")))

    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "--repo",
            str(tmp_path),
            "--goal",
            "read paper",
            "--auto-approve",
            "--paper-backend",
            "claude_reader",
            "--planner-backend",
            "template",
            "--engineer-executor",
            "shell",
            "--manager-backend",
            "rules",
            "--claude-executable-path",
            "C:/Tools/ccr.cmd",
        ],
    )

    assert result.exit_code == 0
    assert captured["paper_backend"] == "claude_reader"
    assert captured["claude_executable_path"] == "C:/Tools/ccr.cmd"


def test_workflow_reviewer_backend_claude_checks_claude_cli_and_state(tmp_path, monkeypatch) -> None:
    captured = {}

    class FakeGraph:
        def invoke(self, state):
            captured.update(state)
            return {"final_report": "ok", "final_report_path": ""}

    monkeypatch.setattr("r2a.cli.build_workflow_graph", lambda: FakeGraph())
    monkeypatch.setattr(
        "r2a.cli.check_claude_code_cli",
        lambda path: CodexCliCheckResult(True, path, path, "2.1.128 (Claude Code)", "", "ok"),
    )
    monkeypatch.setattr("r2a.cli.check_codex_cli", lambda path: (_ for _ in ()).throw(AssertionError("codex should not be checked")))

    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "--repo",
            str(tmp_path),
            "--goal",
            "review safely",
            "--auto-approve",
            "--paper-backend",
            "preprocess",
            "--planner-backend",
            "template",
            "--engineer-executor",
            "shell",
            "--manager-backend",
            "rules",
            "--reviewer-backend",
            "claude",
            "--claude-executable-path",
            "C:/Tools/ccr.cmd",
        ],
    )

    assert result.exit_code == 0
    assert captured["reviewer_backend"] == "claude"
    assert captured["claude_executable_path"] == "C:/Tools/ccr.cmd"
