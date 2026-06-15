"""Tests for OpenClaw config path conversion (read vs runtime).

Windows UI uses UNC paths for reading WSL configs.
WSL runtime must receive POSIX paths.
These tests verify the separation and defensive conversion.
"""

import os
import pytest

from r2a.tools.openclaw_stage_runner import (
    _is_wsl_unc_path,
    _wsl_unc_to_posix_path,
    _looks_like_wsl_posix_path,
    _openclaw_config_read_candidates,
    resolve_openclaw_config,
    detect_openclaw_model_profiles,
)


class TestWslPathHelpers:
    """Test WSL UNC <-> POSIX path conversion helpers."""

    def test_is_wsl_unc_path_detects_unc(self) -> None:
        """UNC paths should be detected."""
        assert _is_wsl_unc_path("\\\\wsl.localhost\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json")
        assert _is_wsl_unc_path("\\\\wsl$\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json")
        assert _is_wsl_unc_path("//wsl.localhost/Ubuntu/home/r2auser/.openclaw/openclaw.json")

    def test_is_wsl_unc_path_rejects_posix(self) -> None:
        """POSIX paths should not be detected as UNC."""
        assert not _is_wsl_unc_path("/home/r2auser/.openclaw/openclaw.json")
        assert not _is_wsl_unc_path("C:\\Users\\test\\.openclaw\\openclaw.json")
        assert not _is_wsl_unc_path("openclaw.json")

    def test_wsl_unc_to_posix_path_converts_unc(self) -> None:
        """UNC paths should convert to POSIX."""
        result = _wsl_unc_to_posix_path("\\\\wsl.localhost\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json")
        assert result == "/home/r2auser/.openclaw/openclaw.json"

        result = _wsl_unc_to_posix_path("\\\\wsl$\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json")
        assert result == "/home/r2auser/.openclaw/openclaw.json"

    def test_wsl_unc_to_posix_path_preserves_posix(self) -> None:
        """POSIX paths should be unchanged."""
        posix_path = "/home/r2auser/.openclaw/openclaw.json"
        result = _wsl_unc_to_posix_path(posix_path)
        assert result == posix_path

    def test_wsl_unc_to_posix_path_handles_empty(self) -> None:
        """Empty paths should return empty."""
        assert _wsl_unc_to_posix_path("") == ""
        assert _wsl_unc_to_posix_path(None) == ""


class TestConfigReadCandidates:
    """Test config read path candidate generation."""

    def test_posix_path_generates_unc_candidates_on_windows(self, monkeypatch) -> None:
        """On Windows, POSIX config path should generate UNC candidates."""
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setenv("R2A_WSL_DISTRO", "Ubuntu")

        posix_path = "/home/r2auser/.openclaw/openclaw.json"
        candidates = _openclaw_config_read_candidates(posix_path)

        assert posix_path in candidates
        assert "\\\\wsl.localhost\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json" in candidates
        assert "\\\\wsl$\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json" in candidates

    def test_posix_path_no_unc_on_linux(self, monkeypatch) -> None:
        """On Linux, POSIX config path should not generate UNC."""
        monkeypatch.setattr(os, "name", "posix")

        posix_path = "/home/r2auser/.openclaw/openclaw.json"
        candidates = _openclaw_config_read_candidates(posix_path)

        assert posix_path in candidates
        # Should not have UNC candidates on Linux
        assert len(candidates) == 1


class TestResolveOpenclawConfig:
    """Test config resolution preserves original paths."""

    def test_resolve_preserves_posix_path(self) -> None:
        """resolve_openclaw_config should preserve POSIX path unchanged."""
        posix_path = "/home/r2auser/.openclaw/openclaw.json"
        config = resolve_openclaw_config(openclaw_config_path=posix_path)

        assert config["openclaw_config_path"] == posix_path

    def test_resolve_preserves_windows_path(self) -> None:
        """resolve_openclaw_config should preserve Windows path unchanged."""
        windows_path = "C:\\Users\\test\\.openclaw\\openclaw.json"
        config = resolve_openclaw_config(openclaw_config_path=windows_path)

        assert config["openclaw_config_path"] == windows_path

    def test_resolve_preserves_unc_path(self) -> None:
        """resolve_openclaw_config should preserve UNC path unchanged."""
        unc_path = "\\\\wsl.localhost\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json"
        config = resolve_openclaw_config(openclaw_config_path=unc_path)

        # Resolution preserves the input; conversion happens at preflight/runtime
        assert config["openclaw_config_path"] == unc_path


class TestDetectOpenclawModelProfiles:
    """Test model detection read path vs config path separation."""

    def test_detect_returns_config_path_original(self, tmp_path) -> None:
        """detect should return original config_path, not converted read_path."""
        # Create a valid config file at POSIX path location
        config_path = tmp_path / "openclaw.json"
        config_path.write_text(
            '{"agents": {"defaults": {"models": {"test/model": {}}}}}',
            encoding="utf-8",
        )

        result = detect_openclaw_model_profiles(openclaw_config_path=str(config_path))

        # config_path should be the original input
        assert result["config_path"] == str(config_path)
        # config_read_path should be the path actually read (may be same or UNC)
        assert result["config_read_path"] == str(config_path)
        # checked_paths should include the original
        assert str(config_path) in result["checked_paths"]

    def test_detect_on_windows_generates_unc_checked_paths(self, monkeypatch, tmp_path) -> None:
        """On Windows, detection should try UNC candidates for POSIX path."""
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setenv("R2A_WSL_DISTRO", "Ubuntu")

        posix_path = "/home/r2auser/.openclaw/openclaw.json"
        result = detect_openclaw_model_profiles(openclaw_config_path=posix_path)

        # Original POSIX path preserved
        assert result["config_path"] == posix_path
        # Should have tried UNC candidates
        checked = result["checked_paths"]
        assert any("\\\\wsl.localhost\\Ubuntu" in p for p in checked)


class TestRuntimePathConversion:
    """Test that UNC paths are converted to POSIX before WSL runtime."""

    def test_preflight_converts_unc_to_posix(self) -> None:
        """Preflight should convert UNC config_path to POSIX before WSL call.

        This is tested indirectly - the preflight function applies
        _wsl_unc_to_posix_path internally before passing to WSL.
        """
        # This test verifies the conversion logic
        unc_path = "\\\\wsl.localhost\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json"
        expected_runtime = "/home/r2auser/.openclaw/openclaw.json"

        result = _wsl_unc_to_posix_path(unc_path)
        assert result == expected_runtime


class TestPathSeparation:
    """Test separation of user path, read path, and runtime path."""

    def test_user_path_never_modified(self) -> None:
        """User-provided path should never be modified in settings."""
        user_path = "/home/r2auser/.openclaw/openclaw.json"

        # This is what user typed in UI
        # It should be saved exactly as-is
        config = resolve_openclaw_config(openclaw_config_path=user_path)

        assert config["openclaw_config_path"] == user_path

    def test_read_path_for_windows_ui_only(self, monkeypatch) -> None:
        """Read path (UNC) is only for Windows UI reading, not for runtime."""
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setenv("R2A_WSL_DISTRO", "Ubuntu")

        user_path = "/home/r2auser/.openclaw/openclaw.json"
        candidates = _openclaw_config_read_candidates(user_path)

        # First candidate is original user path
        assert candidates[0] == user_path

        # UNC candidates are for reading only
        unc_candidate = "\\\\wsl.localhost\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json"
        assert unc_candidate in candidates

        # Runtime should use converted back to POSIX
        runtime_path = _wsl_unc_to_posix_path(unc_candidate)
        assert runtime_path == user_path