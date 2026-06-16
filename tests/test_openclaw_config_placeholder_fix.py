"""Tests for OpenClaw config path placeholder detection."""

import pytest
from r2a_web.app import _is_placeholder_config_path, _save_stage_model_defaults


@pytest.fixture(autouse=True)
def isolate_web_settings(tmp_path, monkeypatch):
    import r2a_web.app as app

    monkeypatch.setattr(app, "WEB_SETTINGS_PATH", tmp_path / "web_settings.json")


class TestPlaceholderConfigPath:
    """Test placeholder config path detection."""

    def test_short_username_not_placeholder(self):
        """Short real WSL usernames like /home/x/ should NOT be rejected as placeholder."""
        # Real WSL paths with short usernames
        assert not _is_placeholder_config_path("/home/x/.openclaw/openclaw.json")
        assert not _is_placeholder_config_path("/home/a/.openclaw/openclaw.json")
        assert not _is_placeholder_config_path("/home/u/.openclaw/openclaw.json")
        assert not _is_placeholder_config_path("/home/dev/.openclaw/openclaw.json")
        assert not _is_placeholder_config_path("/home/user1/.openclaw/openclaw.json")
        assert _is_placeholder_config_path("/home/r2auser/.openclaw/openclaw.json")

    def test_example_username_is_placeholder(self):
        """Example usernames like /home/user/ should be rejected as placeholder."""
        # Example usernames commonly used in documentation
        assert _is_placeholder_config_path("/home/example/.openclaw/openclaw.json")
        assert _is_placeholder_config_path("/home/username/.openclaw/openclaw.json")
        assert _is_placeholder_config_path("/home/your_user/.openclaw/openclaw.json")
        assert _is_placeholder_config_path("/home/your_username/.openclaw/openclaw.json")

    def test_angle_bracket_placeholder(self):
        """Paths with angle brackets should be rejected as placeholder."""
        assert _is_placeholder_config_path("C:\\Users\\<user>\\.openclaw\\openclaw.json")
        assert _is_placeholder_config_path("/home/<user>/.openclaw/openclaw.json")
        assert _is_placeholder_config_path("/home/<username>/.openclaw/openclaw.json")
        assert _is_placeholder_config_path("C:\\Users\\<YOUR_USER>\\.openclaw\\openclaw.json")

    def test_quoted_path_is_placeholder(self):
        """Paths containing quotes should be rejected as placeholder."""
        assert _is_placeholder_config_path('"/home/user/.openclaw/openclaw.json"')
        assert _is_placeholder_config_path("'/home/user/.openclaw/openclaw.json'")
        assert _is_placeholder_config_path("C:\\Users\\user\\.openclaw\\openclaw.json\"")

    def test_empty_path_is_placeholder(self):
        """Empty or null-like paths should be rejected as placeholder."""
        assert _is_placeholder_config_path("")
        assert _is_placeholder_config_path("not detected")
        assert _is_placeholder_config_path("none")
        assert _is_placeholder_config_path("null")

    def test_real_windows_path_not_placeholder(self):
        """Real Windows paths should NOT be rejected as placeholder."""
        assert not _is_placeholder_config_path("C:\\Users\\John\\.openclaw\\openclaw.json")
        assert not _is_placeholder_config_path("D:\\Tools\\.openclaw\\openclaw.json")
        assert not _is_placeholder_config_path("C:\\R2A_SAMPLE\\.openclaw\\openclaw.json")

    def test_wsl_unc_path_not_placeholder(self):
        """WSL UNC paths should NOT be rejected as placeholder."""
        assert not _is_placeholder_config_path("\\\\wsl.localhost\\Ubuntu\\home\\x\\.openclaw\\openclaw.json")
        assert not _is_placeholder_config_path("\\\\wsl$\\Ubuntu\\home\\x\\.openclaw\\openclaw.json")


class TestSaveStageModelDefaults:
    """Test saving OpenClaw stage model defaults with detection-aware validation."""

    def test_detection_success_allows_save(self, tmp_path):
        """If detection succeeded, the path should be allowed even if it looks like a placeholder."""
        selection = {
            "planner": {
                "provider": "ai-coding-plan",
                "model": "glm-5",
                "profile": "default",
            }
        }

        # Simulate successful detection
        detection_result = {
            "model_detection_source": "openclaw_wsl_config",
            "model_detection_read_path": "\\\\wsl.localhost\\Ubuntu\\home\\x\\.openclaw\\openclaw.json",
            "model_options": [
                {
                    "provider": "ai-coding-plan",
                    "model": "glm-5",
                    "profile": "default",
                }
            ],
            "model_detection_errors": [],
        }

        result = _save_stage_model_defaults(
            selection,
            openclaw_executable_path="/home/x/.nvm/versions/node/v22.22.2/bin/openclaw",
            openclaw_config_path="/home/x/.openclaw/openclaw.json",
            detection_result=detection_result,
        )

        assert result.get("success"), f"Expected success but got error: {result.get('error')}"
        assert not result.get("error")

    def test_detection_failure_with_real_path_saves_with_warning(self, tmp_path):
        """If detection failed but path looks real, save it and show a warning."""
        selection = {
            "planner": {
                "provider": "ai-coding-plan",
                "model": "glm-5",
                "profile": "default",
            }
        }

        # Simulate failed detection with errors
        detection_result = {
            "model_detection_source": "not_detected",
            "model_detection_read_path": "",
            "model_options": [],
            "model_detection_errors": ["Config file not found"],
        }

        result = _save_stage_model_defaults(
            selection,
            openclaw_executable_path="/home/x/.nvm/versions/node/v22.22.2/bin/openclaw",
            openclaw_config_path="/home/x/.openclaw/openclaw.json",
            detection_result=detection_result,
        )

        assert result.get("success") is True
        assert result.get("warning")
        assert "Config file not found" in result["warning"]

    def test_detection_failure_with_placeholder_rejected(self, tmp_path):
        """If detection failed and path looks like placeholder, show placeholder error."""
        selection = {
            "planner": {
                "provider": "ai-coding-plan",
                "model": "glm-5",
                "profile": "default",
            }
        }

        # Simulate failed detection
        detection_result = {
            "model_detection_source": "not_detected",
            "model_detection_read_path": "",
            "model_options": [],
            "model_detection_errors": [],
        }

        result = _save_stage_model_defaults(
            selection,
            openclaw_executable_path="openclaw",
            openclaw_config_path="/home/<user>/.openclaw/openclaw.json",
            detection_result=detection_result,
        )

        assert not result.get("success")
        assert result.get("error")
        assert "placeholder" in result["error"].lower()

    def test_no_detection_result_saves_real_path_with_warning(self, tmp_path):
        """If no detection result is provided for a real path, save it with a warning."""
        selection = {
            "planner": {
                "provider": "ai-coding-plan",
                "model": "glm-5",
                "profile": "default",
            }
        }

        result = _save_stage_model_defaults(
            selection,
            openclaw_executable_path="openclaw",
            openclaw_config_path="/home/x/.openclaw/openclaw.json",
            detection_result=None,
        )

        assert result.get("success") is True
        assert result.get("warning")
        assert "refresh models" in result["warning"].lower()

    def test_not_detected_selection_not_saved(self, tmp_path):
        """Selection with 'Not detected' should not be saved."""
        selection = {
            "planner": {
                "provider": "not",
                "model": "detected",
            }
        }

        detection_result = {
            "model_detection_source": "openclaw_config",
            "model_detection_read_path": "/home/x/.openclaw/openclaw.json",
            "model_options": [],
            "model_detection_errors": [],
        }

        result = _save_stage_model_defaults(
            selection,
            openclaw_executable_path="openclaw",
            openclaw_config_path="/home/x/.openclaw/openclaw.json",
            detection_result=detection_result,
        )

        # Should succeed but with warning about no valid selection
        assert result.get("warning")
        assert "No valid stage model selection" in result["warning"]


class TestDetectionAndSaveConsistency:
    """Test that detection and save use consistent logic."""

    def test_wsl_path_detection_and_save_consistency(self, tmp_path):
        """WSL paths that work in detection should also work in save."""
        # This is the exact scenario from the bug report
        wsl_config_path = "/home/x/.openclaw/openclaw.json"
        wsl_executable_path = "/home/x/.nvm/versions/node/v22.22.2/bin/openclaw"

        selection = {
            "planner": {
                "provider": "ai-coding-plan",
                "model": "glm-5",
                "profile": "default",
            }
        }

        # Simulate successful detection (like Refresh Models button)
        detection_result = {
            "model_detection_source": "openclaw_wsl_config",
            "model_detection_read_path": "\\\\wsl.localhost\\Ubuntu\\home\\x\\.openclaw\\openclaw.json",
            "model_options": [
                {
                    "provider": "ai-coding-plan",
                    "model": "glm-5",
                    "profile": "default",
                    "display_name": "ai-coding-plan/glm-5 (default)",
                }
            ],
            "model_detection_errors": [],
            "model_detection_warnings": [],
        }

        # Try to save (like save button)
        result = _save_stage_model_defaults(
            selection,
            openclaw_executable_path=wsl_executable_path,
            openclaw_config_path=wsl_config_path,
            detection_result=detection_result,
        )

        # Should succeed without placeholder error
        assert result.get("success"), f"Save failed with error: {result.get('error')}"
        assert not result.get("error") or "placeholder" not in result.get("error", "").lower()
