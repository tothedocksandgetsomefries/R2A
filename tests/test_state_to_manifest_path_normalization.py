"""Test that openclaw_config_from_state normalizes UNC to POSIX.

This ensures RUN_MANIFEST records POSIX path even when state contains UNC.
"""

import pytest


def test_openclaw_config_from_state_converts_unc_to_posix() -> None:
    """openclaw_config_from_state should normalize UNC path to POSIX."""
    from r2a.tools.openclaw_stage_runner import openclaw_config_from_state

    # State with UNC path (as Windows UI might save)
    state = {
        "openclaw_executable_path": "/home/r2auser/.nvm/versions/node/v22.22.2/bin/openclaw",
        "openclaw_config_path": "\\\\wsl.localhost\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json",
    }

    config = openclaw_config_from_state(state)

    # Should return normalized POSIX path
    assert config["openclaw_config_path"] == "/home/r2auser/.openclaw/openclaw.json"
    # Should NOT preserve UNC path
    assert config["openclaw_config_path"] != "\\\\wsl.localhost\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json"


def test_openclaw_config_from_state_preserves_posix() -> None:
    """openclaw_config_from_state should keep POSIX path unchanged."""
    from r2a.tools.openclaw_stage_runner import openclaw_config_from_state

    posix_path = "/home/r2auser/.openclaw/openclaw.json"
    state = {
        "openclaw_config_path": posix_path,
    }

    config = openclaw_config_from_state(state)

    assert config["openclaw_config_path"] == posix_path


def test_openclaw_config_from_state_handles_empty() -> None:
    """openclaw_config_from_state should handle empty/missing path."""
    from r2a.tools.openclaw_stage_runner import openclaw_config_from_state

    # Missing key
    config = openclaw_config_from_state({})
    assert config["openclaw_config_path"] == ""  # Default

    # Empty string
    config = openclaw_config_from_state({"openclaw_config_path": ""})
    assert config["openclaw_config_path"] == ""


def test_run_manifest_will_record_posix_not_unc() -> None:
    """Verify RUN_MANIFEST will record POSIX path via openclaw_config_from_state."""
    from r2a.tools.openclaw_stage_runner import openclaw_config_from_state
    from r2a.core.run_manifest import _openclaw_config

    # State with UNC path
    state = {
        "openclaw_executable_path": "/home/r2auser/.nvm/versions/node/v22.22.2/bin/openclaw",
        "openclaw_config_path": "\\\\wsl.localhost\\Ubuntu\\home\\r2auser\\.openclaw\\openclaw.json",
    }

    # This is what RUN_MANIFEST records
    manifest_openclaw = _openclaw_config(state)

    # Should contain POSIX path, not UNC
    assert manifest_openclaw["openclaw_config_path"] == "/home/r2auser/.openclaw/openclaw.json"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
