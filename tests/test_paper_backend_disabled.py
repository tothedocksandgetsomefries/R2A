from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from r2a.agents.paper_agent import run_paper_agent
from r2a.cli import app
from r2a.core.paths import report_path
from r2a.core.state import make_initial_state


def test_paper_backend_codex_is_disabled_in_agent(tmp_path: Path) -> None:
    state = make_initial_state(tmp_path, paper_backend="codex")

    with pytest.raises(ValueError, match="Paper Codex backend is disabled"):
        run_paper_agent(state)


def test_workflow_cli_rejects_paper_backend_codex(tmp_path: Path) -> None:
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


def test_paper_backend_ai_reader_uses_local_fallback_without_stage_runner(tmp_path: Path, monkeypatch) -> None:
    paper = tmp_path / "source paper.pdf"
    paper.write_bytes(b"%PDF-1.4")

    def fake_run(repo_path, stage, prompt, allowed_outputs, **kwargs):
        raise AssertionError("Paper ai_reader must not call the Codex stage runner")

    monkeypatch.setattr("r2a.tools.codex_stage_runner.run_codex_stage", fake_run)

    result = run_paper_agent(make_initial_state(tmp_path, paper_path=paper, paper_backend="ai_reader", codex_stage_timeout=300, timeout=300))

    assert result["paper_backend"] == "local_preprocess_fallback"
    assert result["paper_quality"] == "LOW_CONFIDENCE"
    assert result["fallback_used"] is True
    assert Path(result["paper_context_path"]).exists()
    assert report_path(tmp_path, "paper_output").exists()


def test_paper_backend_openclaw_reader_falls_back_to_local_structured_outputs(tmp_path: Path, monkeypatch) -> None:
    paper = tmp_path / "source paper.txt"
    paper.write_text("Title\n\nAbstract\n\nWe evaluate recall on an official small dataset.\n", encoding="utf-8")

    def fake_run(*args, **kwargs):
        return {"success": False, "error": "provider unavailable", "unexpected_modifications": []}

    monkeypatch.setattr("r2a.tools.openclaw_stage_runner.run_openclaw_stage", fake_run)

    result = run_paper_agent(make_initial_state(tmp_path, paper_path=paper, paper_backend="openclaw_reader"))

    assert result["paper_backend"] == "local_preprocess_fallback"
    assert result["paper_backend_requested"] == "openclaw_reader"
    assert result["paper_quality"] == "LOW_CONFIDENCE"
    assert result["fallback_used"] is True
    assert result["paper_openclaw_reader_failed"] is True
    assert Path(result["paper_context_path"]).exists()
    assert report_path(tmp_path, "paper_output").exists()


def test_paper_backend_claude_reader_uses_claude_stage_runner(tmp_path: Path, monkeypatch) -> None:
    """Test that paper_backend=claude_reader now invokes the Claude stage runner."""
    paper = tmp_path / "source paper.pdf"
    paper.write_bytes(b"%PDF-1.4")

    call_log = []

    def fake_run(repo_path, stage, prompt, allowed_outputs, **kwargs):
        call_log.append({
            "repo_path": repo_path,
            "stage": stage,
            "prompt": prompt,
            "allowed_outputs": allowed_outputs,
            "kwargs": kwargs,
        })
        # Return a successful result with expected outputs
        from r2a.core.paths import report_path
        repo = Path(repo_path)
        brief_path = report_path(repo, "paper")
        evidence_path = report_path(repo, "paper_evidence")
        context_path = report_path(repo, "paper_context")

        # Create minimal required outputs
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text("# PAPER_BRIEF\n\n## Topic\n\nTest paper\n", encoding="utf-8")
        evidence_path.write_text("# PAPER_EVIDENCE\n\n## Evidence\n\nTest evidence\n", encoding="utf-8")
        context_path.write_text("# PAPER_CONTEXT\n\n## Context\n\nTest context\n", encoding="utf-8")

        return {
            "success": True,
            "returncode": 0,
            "error": "",
            "unexpected_modifications": [],
            "stage": stage,
        }

    monkeypatch.setattr("r2a.tools.claude_stage_runner.run_claude_stage", fake_run)

    result = run_paper_agent(
        make_initial_state(
            tmp_path,
            paper_path=paper,
            paper_backend="claude_reader",
            claude_executable_path="C:/Tools/ccr.cmd",
            stage_api_keys={"paper": "paper-dummy-key"},
            stage_api_key_env_vars={"paper": "ANTHROPIC_API_KEY"},
        )
    )

    # Verify Claude stage runner was called
    assert len(call_log) == 1
    assert call_log[0]["stage"] == "paper"
    assert ".r2a/PAPER_BRIEF.md" in call_log[0]["allowed_outputs"]
    assert ".r2a/PAPER_EVIDENCE.md" in call_log[0]["allowed_outputs"]

    # Verify result indicates successful Claude reader run
    assert result["paper_backend"] == "claude_reader"
    assert result["fallback_used"] is False
    assert Path(result["paper_brief_path"]).exists()
    assert Path(result["paper_evidence_path"]).exists()


def test_paper_backend_openclaw_reader_uses_openclaw_stage_runner(tmp_path: Path, monkeypatch) -> None:
    paper = tmp_path / "source paper.pdf"
    paper.write_bytes(b"%PDF-1.4")
    call_log = []

    def fake_run(repo_path, stage, input_path, allowed_outputs, **kwargs):
        call_log.append(
            {
                "repo_path": repo_path,
                "stage": stage,
                "input_path": Path(input_path),
                "input_text": Path(input_path).read_text(encoding="utf-8"),
                "allowed_outputs": allowed_outputs,
                "kwargs": kwargs,
            }
        )
        from r2a.core.paths import report_path

        repo = Path(repo_path)
        outputs = {
            "paper_context": "# PAPER_CONTEXT\n\n## Context\n\nTest context\n",
            "paper": "# PAPER_BRIEF\n\n## Topic\n\nTest paper\n",
            "paper_evidence": "# PAPER_EVIDENCE\n\n## Evidence\n\nTest evidence\n",
            "paper_reproduction_card": "# PAPER_REPRODUCTION_CARD\n\n## Bibliographic Info\n\nTest\n",
            "paper_figures_tables": "# PAPER_FIGURES_TABLES\n\nNo figures visible.\n",
            "paper_parse_quality": "# PAPER_PARSE_QUALITY\n\nLOW_CONFIDENCE\n",
            "paper_analysis": "# PAPER_ANALYSIS_CN\n\n中文分析。\n",
        }
        for key, text in outputs.items():
            path = report_path(repo, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return {
            "success": True,
            "returncode": 0,
            "error": "",
            "unexpected_modifications": [],
            "stage": stage,
            "stdout_json": True,
            "provider": "deepseek",
            "model": "deepseek-chat",
            "runner": "embedded",
            "fallbackUsed": False,
        }

    monkeypatch.setattr("r2a.agents.paper_agent.openclaw_stage_runner.run_openclaw_stage", fake_run)

    result = run_paper_agent(make_initial_state(tmp_path, paper_path=paper, paper_backend="openclaw_reader"))

    assert len(call_log) == 1
    assert call_log[0]["stage"] == "paper"
    assert call_log[0]["input_path"].name == "OPENCLAW_INPUT.md"
    assert "R2A Paper OpenClaw Stage" in call_log[0]["input_text"]
    assert ".r2a/PAPER_BRIEF.md" in call_log[0]["allowed_outputs"]
    assert ".r2a/PAPER_ANALYSIS_CN.md" in call_log[0]["allowed_outputs"]
    assert result["paper_backend"] == "openclaw_reader"
    assert result["fallback_used"] is False
    assert result["paper_quality"] == "LOW_CONFIDENCE"
    assert Path(result["paper_brief_path"]).exists()
    assert Path(result["paper_analysis_path"]).exists()
