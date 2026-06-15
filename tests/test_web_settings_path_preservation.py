"""Test web_settings.json saves original user path, not UNC read path.

This test verifies the fix for the config path read/runtime split issue.
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock


def test_save_stage_model_defaults_preserves_user_path(tmp_path: Path) -> None:
    """_save_stage_model_defaults should NOT overwrite user path with UNC read path."""
    # Import after path setup to avoid module cache issues
    import sys
    sys.path.insert(0, str(tmp_path.parent))

    from r2a_web.app import _save_stage_model_defaults

    user_config_path = "/home/r2auser/.openclaw/openclaw.json"
    unc_read_path = "\\\\wsl.localhost\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json"

    # Detection result with UNC read path (this is what Windows UI sees)
    detection_result = {
        "model_detection_source": "openclaw_wsl_config",
        "model_detection_read_path": unc_read_path,
        "model_detection_checked_paths": [user_config_path, unc_read_path],
        "model_detection_errors": [],
        "model_detection_warnings": [],
    }

    # Call the function
    result = _save_stage_model_defaults(
        {"planner": {"provider": "ai-coding-plan", "model": "glm-5"}},
        openclaw_executable_path="/home/r2auser/.nvm/versions/node/v22.22.2/bin/openclaw",
        openclaw_config_path=user_config_path,
        detection_result=detection_result,
    )

    # Should succeed
    assert result.get("success"), f"Expected success, got error: {result.get('error')}"

    # Load saved settings
    settings_path = Path.home() / ".r2a" / "web_settings.json"
    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))

        # CRITICAL: openclaw_config_path should be user path, NOT UNC read path
        assert settings.get("openclaw_config_path") == user_config_path, \
            f"Expected user path {user_config_path}, got {settings.get('openclaw_config_path')}"

        # Should NOT have UNC path
        assert settings.get("openclaw_config_path") != unc_read_path, \
            f"Settings incorrectly saved UNC read path {unc_read_path}"


def test_detect_model_profiles_returns_original_path_in_config_path(tmp_path: Path) -> None:
    """detect_openclaw_model_profiles should return original path in config_path field."""
    from r2a.tools.openclaw_stage_runner import detect_openclaw_model_profiles

    # Create a real config file
    config_path = tmp_path / "openclaw.json"
    config_path.write_text(
        json.dumps({
            "agents": {
                "defaults": {
                    "models": {
                        "ai-coding-plan/glm-5": {"alias": "GLM-5"}
                    }
                }
            }
        }),
        encoding="utf-8",
    )

    result = detect_openclaw_model_profiles(openclaw_config_path=str(config_path))

    # config_path should be original input
    assert result["config_path"] == str(config_path)
    # config_read_path is what was actually read (may be same or UNC)
    assert result["config_read_path"] != ""
    # models should be detected
    assert len(result["models"]) > 0


if __name__ == "__main__":
    import pytest
    import sys

    # Run this test file
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
