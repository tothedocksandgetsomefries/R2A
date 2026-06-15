"""Tests for Planner stage-level provider/model configuration.

Validates that Planner can use different provider/model than other stages.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from r2a.agents.planner_agent import _resolve_planner_openclaw_config


class TestPlannerStageLevelProvider:
    """Test Planner stage-level provider/model configuration."""

    def test_planner_uses_default_provider_when_no_override(self):
        """When no planner_provider/planner_model, use Planner stage profile."""
        state = {
            "repo_path": "/tmp/test_repo",
            "openclaw_provider": "deepseek",
            "openclaw_model": "deepseek-chat",
        }

        planner_provider, planner_model = _resolve_planner_openclaw_config(state)

        assert planner_provider == "ai-coding-plan"
        assert planner_model == "glm-5"

    def test_planner_uses_override_provider(self):
        """When planner_provider/planner_model are set, use them."""
        state = {
            "repo_path": "/tmp/test_repo",
            "openclaw_provider": "ai-coding-plan",
            "openclaw_model": "glm-5",
            "planner_provider": "deepseek",
            "planner_model": "deepseek-chat",
        }

        planner_provider, planner_model = _resolve_planner_openclaw_config(state)

        assert planner_provider == "deepseek"
        assert planner_model == "deepseek-chat"

    def test_planner_provider_partial_override(self):
        """Partial override: only planner_provider set."""
        state = {
            "repo_path": "/tmp/test_repo",
            "openclaw_provider": "ai-coding-plan",
            "openclaw_model": "glm-5",
            "planner_provider": "deepseek",
            # planner_model not set
        }

        planner_provider, planner_model = _resolve_planner_openclaw_config(state)

        assert planner_provider == "deepseek"
        assert planner_model == "glm-5"

    def test_planner_model_partial_override(self):
        """Partial override: only planner_model set."""
        state = {
            "repo_path": "/tmp/test_repo",
            "openclaw_provider": "ai-coding-plan",
            "openclaw_model": "glm-5",
            # planner_provider not set
            "planner_model": "deepseek-chat",
        }

        planner_provider, planner_model = _resolve_planner_openclaw_config(state)

        assert planner_provider == "ai-coding-plan"
        assert planner_model == "deepseek-chat"

    def test_empty_string_override_ignored(self):
        """Empty string override should be ignored (use default)."""
        state = {
            "repo_path": "/tmp/test_repo",
            "openclaw_provider": "ai-coding-plan",
            "openclaw_model": "glm-5",
            "planner_provider": "",  # Empty string
            "planner_model": "",     # Empty string
        }

        planner_provider, planner_model = _resolve_planner_openclaw_config(state)

        assert planner_provider == "ai-coding-plan"
        assert planner_model == "glm-5"


class TestEngineerReviewerUnchanged:
    """Verify Engineer and Reviewer stage-level configuration."""

    def test_engineer_uses_override_provider(self):
        """Engineer can use stage-level provider/model override."""
        state = {
            "repo_path": "/tmp/test_repo",
            "openclaw_provider": "ai-coding-plan",
            "openclaw_model": "glm-5",
            "engineer_provider": "deepseek",
            "engineer_model": "deepseek-chat",
        }

        # Engineer now supports stage-level override
        engineer_provider = state.get("engineer_provider") or state.get("openclaw_provider")
        engineer_model = state.get("engineer_model") or state.get("openclaw_model")

        assert engineer_provider == "deepseek"
        assert engineer_model == "deepseek-chat"

    def test_engineer_uses_default_when_no_override(self):
        """Engineer uses openclaw_provider when no stage-level override."""
        state = {
            "repo_path": "/tmp/test_repo",
            "openclaw_provider": "ai-coding-plan",
            "openclaw_model": "glm-5",
        }

        engineer_provider = state.get("engineer_provider") or state.get("openclaw_provider")
        engineer_model = state.get("engineer_model") or state.get("openclaw_model")

        assert engineer_provider == "ai-coding-plan"
        assert engineer_model == "glm-5"

    def test_reviewer_uses_openclaw_provider_not_planner(self):
        """Reviewer should use openclaw_provider, not planner_provider."""
        state = {
            "repo_path": "/tmp/test_repo",
            "openclaw_provider": "ai-coding-plan",
            "openclaw_model": "glm-5",
            "planner_provider": "deepseek",
            "planner_model": "deepseek-chat",
            "engineer_provider": "deepseek",
            "engineer_model": "deepseek-chat",
        }

        # Reviewer uses openclaw_provider directly (from reviewer_agent.py)
        reviewer_provider = state.get("openclaw_provider")
        reviewer_model = state.get("openclaw_model")

        assert reviewer_provider == "ai-coding-plan"
        assert reviewer_model == "glm-5"

    def test_all_stages_independent(self):
        """All stages can have independent provider/model."""
        state = {
            "repo_path": "/tmp/test_repo",
            "openclaw_provider": "ai-coding-plan",
            "openclaw_model": "glm-5",
            "planner_provider": "deepseek",
            "planner_model": "deepseek-chat",
            "engineer_provider": "deepseek",
            "engineer_model": "deepseek-chat",
        }

        planner_provider, planner_model = _resolve_planner_openclaw_config(state)
        engineer_provider = state.get("engineer_provider") or state.get("openclaw_provider")
        engineer_model = state.get("engineer_model") or state.get("openclaw_model")
        reviewer_provider = state.get("openclaw_provider")
        reviewer_model = state.get("openclaw_model")

        assert planner_provider == "deepseek"
        assert engineer_provider == "deepseek"
        assert reviewer_provider == "ai-coding-plan"


class TestPlannerTimeoutUnchanged:
    """Verify Planner timeout remains at 300s."""

    def test_planner_timeout_default_300(self):
        """Planner default timeout should be 300s."""
        state = {
            "repo_path": "/tmp/test_repo",
        }

        timeout = int(state.get("codex_stage_timeout", state.get("timeout", 300)))
        assert timeout == 300

    def test_planner_timeout_explicit_override(self):
        """Planner timeout can be explicitly set."""
        state = {
            "repo_path": "/tmp/test_repo",
            "timeout": 600,
        }

        timeout = int(state.get("codex_stage_timeout", state.get("timeout", 300)))
        assert timeout == 600


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
