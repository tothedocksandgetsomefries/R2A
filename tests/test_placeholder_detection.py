"""Standalone tests for OpenClaw config path placeholder detection.

This module tests the placeholder detection logic without importing the full app.
"""

import pytest


def _is_placeholder_config_path(path: str) -> bool:
    """Check if config path looks like a placeholder example.

    Only rejects paths with explicit placeholder markers like <user> or example usernames.
    Does NOT reject real paths like /home/r2auser/.openclaw/openclaw.json.

    This function checks for explicit placeholder patterns that indicate
    the path is an example/template rather than a real user path.

    Important: WSL usernames can be short (x, a, u, dev, etc.) and should
    NOT be rejected. Only explicit placeholder markers should be rejected.
    """
    if not path:
        return False

    text = str(path).strip()
    lowered = text.lower()

    # 1. Explicit placeholder markers (clear indicators of example text)
    explicit_placeholders = [
        "<user>",
        "<username>",
        "<YOUR_USER>",
        "<your_username>",
        "<path>",
        "<config>",
        "{user}",
        "{username}",
    ]
    if any(placeholder.lower() in lowered for placeholder in explicit_placeholders):
        return True

    # 2. Windows-style placeholder path with angle brackets
    # Only match if it still has angle brackets
    if "c:\\users\\<" in lowered:
        return True

    # 3. POSIX-style placeholder path with angle brackets
    # Only match if it still has angle brackets
    if "/home/<" in lowered or "/users/<" in lowered:
        return True

    # 4. Common example usernames in documentation
    # These are typically used in examples and tutorials
    # Only reject if the path pattern suggests it's an example
    example_usernames = ["example", "username", "your_user", "your_username"]
    for username in example_usernames:
        patterns = [
            f"/home/{username}/",
            f"/users/{username}/",
            f"c:\\users\\{username}\\",
            f"c:/users/{username}/",
        ]
        if any(pattern in lowered for pattern in patterns):
            return True

    # 5. Paths containing quotes (suggests copy-paste from example text)
    if '"' in text or "'" in text:
        return True

    # 6. Not detected or empty markers
    if lowered in {"not detected", "", "none", "null"}:
        return True

    # Do NOT reject paths with short usernames like /home/r2auser/ or /home/a/
    # These are legitimate WSL usernames

    # Do NOT reject paths that are just .openclaw/openclaw.json
    # These might be relative paths that resolve correctly

    return False


class TestPlaceholderConfigPath:
    """Test placeholder config path detection."""

    def test_short_username_not_placeholder(self):
        """Short WSL usernames like /home/r2auser/ should NOT be rejected as placeholder."""
        # Real WSL paths with short usernames
        assert not _is_placeholder_config_path("/home/r2auser/.openclaw/openclaw.json")
        assert not _is_placeholder_config_path("/home/a/.openclaw/openclaw.json")
        assert not _is_placeholder_config_path("/home/u/.openclaw/openclaw.json")
        assert not _is_placeholder_config_path("/home/dev/.openclaw/openclaw.json")
        assert not _is_placeholder_config_path("/home/user1/.openclaw/openclaw.json")

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

    def test_empty_and_null_not_placeholder(self):
        """Empty or null-like paths should return False (handled by caller)."""
        # Empty string returns False (not a placeholder, but also not valid)
        # The caller should check for empty separately
        assert not _is_placeholder_config_path("")

        # But these textual markers ARE placeholders
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
        assert not _is_placeholder_config_path("\\\\wsl.localhost\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json")
        assert not _is_placeholder_config_path("\\\\wsl$\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json")

    def test_brace_placeholder(self):
        """Paths with curly brace placeholders should be rejected."""
        assert _is_placeholder_config_path("/home/{user}/.openclaw/openclaw.json")
        assert _is_placeholder_config_path("C:\\Users\\{username}\\.openclaw\\openclaw.json")

    def test_config_path_placeholder(self):
        """Paths with <config> placeholder should be rejected."""
        assert _is_placeholder_config_path("<config>")
        assert _is_placeholder_config_path("/path/to/<config>/openclaw.json")

    def test_real_posix_paths_not_placeholder(self):
        """Real POSIX paths should NOT be rejected as placeholder."""
        assert not _is_placeholder_config_path("/home/myuser/.openclaw/openclaw.json")
        assert not _is_placeholder_config_path("/home/admin/.openclaw/openclaw.json")
        assert not _is_placeholder_config_path("/home/test/.openclaw/openclaw.json")
        assert not _is_placeholder_config_path("/root/.openclaw/openclaw.json")

    def test_relative_path_not_placeholder(self):
        """Relative paths should NOT be rejected as placeholder."""
        assert not _is_placeholder_config_path(".openclaw/openclaw.json")
        assert not _is_placeholder_config_path("~/.openclaw/openclaw.json")
        assert not _is_placeholder_config_path("../.openclaw/openclaw.json")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
