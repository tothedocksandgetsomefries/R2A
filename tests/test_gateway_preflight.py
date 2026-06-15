from __future__ import annotations

import json
import subprocess
from pathlib import Path

from r2a.core.model_capabilities import check_stage_policy_compatibility
from r2a.tools.gateway_preflight import check_gateway_preflight


def test_gateway_preflight_reports_ccr_not_running(tmp_path, monkeypatch) -> None:
    config_dir = tmp_path / ".claude-code-router"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"Providers": [{"name": "provider-a"}], "Router": {"default": "provider-a,model-a"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("r2a.tools.gateway_preflight.shutil.which", lambda name: "C:/Tools/ccr.cmd")

    def fake_run(command, **kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "claude-code-router version: 2.0.0", "")
        if command[-1] == "status":
            return subprocess.CompletedProcess(command, 0, "Status: Not Running", "")
        raise AssertionError(command)

    monkeypatch.setattr("r2a.tools.gateway_preflight.subprocess.run", fake_run)

    result = check_gateway_preflight("ccr", stages=["planner"], auto_start=False)

    assert result["ok"] is False
    assert "GATEWAY_NOT_RUNNING" in result["errors"]
    assert result["provider"] == "provider-a"
    assert result["model"] == "model-a"


def test_gateway_preflight_can_auto_start_ccr(tmp_path, monkeypatch) -> None:
    (tmp_path / ".claude-code-router").mkdir()
    (tmp_path / ".claude-code-router" / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("r2a.tools.gateway_preflight.shutil.which", lambda name: "C:/Tools/ccr.cmd")
    calls = {"status": 0}

    def fake_run(command, **kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "claude-code-router version: 2.0.0", "")
        if command[-1] == "start":
            return subprocess.CompletedProcess(command, 0, "started", "")
        if command[-1] == "status":
            calls["status"] += 1
            text = "Status: Not Running" if calls["status"] == 1 else "Status: Running\nProcess ID: 123\nPort: 3456"
            return subprocess.CompletedProcess(command, 0, text, "")
        raise AssertionError(command)

    monkeypatch.setattr("r2a.tools.gateway_preflight.subprocess.run", fake_run)

    result = check_gateway_preflight("ccr", auto_start=True, startup_timeout_seconds=1)

    assert result["ok"] is True
    assert result["gateway_running"] is True
    assert result["pid"] == "123"
    assert result["port"] == "3456"


def test_stage_policy_is_provider_agnostic() -> None:
    result = check_stage_policy_compatibility("planner")

    assert result["ok"] is True
    assert result["policy"]["allow_bash"] is False
    assert result["capability_profile"]["tool_calls"] is True
