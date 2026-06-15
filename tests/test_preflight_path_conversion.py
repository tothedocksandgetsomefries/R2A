"""Unit test for UNC -> POSIX path conversion in preflight.

This test verifies that preflight_openclaw_stage converts UNC paths to POSIX
before passing to WSL runtime, without requiring langgraph or other heavy deps.
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock, call
import pytest


def test_preflight_converts_unc_to_posix_for_wsl(tmp_path: Path, monkeypatch) -> None:
    """preflight_openclaw_stage should convert UNC config_path to POSIX before WSL call."""
    from r2a.tools.openclaw_stage_runner import preflight_openclaw_stage, _wsl_unc_to_posix_path

    # Setup mock WSL command
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps({
        "ok": True,
        "stage": "planner",
        "agent": "default",
        "provider": "ai-coding-plan",
        "model": "glm-5",
        "runner": "embedded",
        "executable": "/home/r2auser/.nvm/versions/node/v22.22.2/bin/openclaw",
        "config_path": "/home/r2auser/.openclaw/openclaw.json",  # Will be POSIX
        "failure_category": "",
        "errors": [],
        "warnings": [],
        "available_agents": [],
        "available_providers": ["ai-coding-plan"],
        "available_models": ["ai-coding-plan/glm-5"],
    })
    mock_result.stderr = ""
    mock_result.timed_out = False

    with patch("r2a.tools.openclaw_stage_runner.run_command_with_timeout") as mock_run:
        mock_run.return_value = mock_result

        # Call preflight with UNC path (as Windows UI might pass)
        unc_config_path = "\\\\wsl.localhost\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json"
        expected_runtime_path = "/home/r2auser/.openclaw/openclaw.json"

        result = preflight_openclaw_stage(
            "planner",
            executable="/home/r2auser/.nvm/versions/node/v22.22.2/bin/openclaw",
            provider="ai-coding-plan",
            model="glm-5",
            runner="embedded",
            agent="",
            wsl_distro="Ubuntu",
            openclaw_config_path=unc_config_path,
            repo_path=tmp_path,
        )

        # Verify WSL command was called with POSIX path, not UNC
        assert mock_run.called
        call_args = mock_run.call_args[0][0]  # First positional arg (command list)

        # The config_path argument (second to last before stage args)
        # Command format: ["wsl", "-d", "Ubuntu", "--", "python3", "-c", code, executable, config_path, ...]
        # Find the config_path in the command
        assert expected_runtime_path in call_args, \
            f"Expected POSIX path {expected_runtime_path} in WSL command, got: {call_args}"

        # UNC path should NOT be in the command
        assert unc_config_path not in call_args, \
            f"UNC path {unc_config_path} should not be passed to WSL runtime"

        # Result should have both paths recorded
        assert result.get("config_path") == unc_config_path  # Original
        assert result.get("config_path_runtime") == expected_runtime_path  # For WSL


def test_preflight_preserves_posix_path(tmp_path: Path) -> None:
    """preflight_openclaw_stage should keep POSIX path as-is (no conversion needed)."""
    from r2a.tools.openclaw_stage_runner import preflight_openclaw_stage

    posix_config_path = "/home/r2auser/.openclaw/openclaw.json"

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps({
        "ok": True,
        "stage": "planner",
        "config_path": posix_config_path,
        "errors": [],
    })
    mock_result.stderr = ""
    mock_result.timed_out = False

    with patch("r2a.tools.openclaw_stage_runner.run_command_with_timeout") as mock_run:
        mock_run.return_value = mock_result

        result = preflight_openclaw_stage(
            "planner",
            executable="/home/r2auser/.nvm/versions/node/v22.22.2/bin/openclaw",
            provider="ai-coding-plan",
            model="glm-5",
            runner="embedded",
            agent="",
            wsl_distro="Ubuntu",
            openclaw_config_path=posix_config_path,
            repo_path=tmp_path,
        )

        # Verify POSIX path is passed through unchanged
        call_args = mock_run.call_args[0][0]
        assert posix_config_path in call_args

        # Both original and runtime should be same
        assert result.get("config_path") == posix_config_path
        assert result.get("config_path_runtime") == posix_config_path


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
