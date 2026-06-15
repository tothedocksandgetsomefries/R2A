"""Tests for reviewer level judgment module."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from r2a.core.reviewer_level_judgment import (
    REPRODUCTION_LEVELS,
    LEVEL_INDEX,
    LEVEL_LABELS,
    LEVEL_SEMANTICS,
    VALID_LEVELS,
    normalize_level,
    is_valid_level,
    level_index,
    level_reached,
    validate_reviewer_output,
    parse_reviewer_json_output,
    build_reviewer_output,
    build_level_semantics_prompt,
)


class TestLevelDefinitions:
    """Test level definitions."""

    def test_levels_count(self):
        """Should have 7 levels (L0-L6)."""
        assert len(REPRODUCTION_LEVELS) == 7

    def test_level_names(self):
        """Should have correct level names."""
        assert REPRODUCTION_LEVELS[0] == "L0_project_health"
        assert REPRODUCTION_LEVELS[1] == "L1_source_artifact_verified"
        assert REPRODUCTION_LEVELS[2] == "L2_input_contract_ready"
        assert REPRODUCTION_LEVELS[3] == "L3_official_reduced_run"
        assert REPRODUCTION_LEVELS[4] == "L4_reduced_paper_aligned"
        assert REPRODUCTION_LEVELS[5] == "L5_minimal_baseline_comparison"
        assert REPRODUCTION_LEVELS[6] == "L6_full_or_near_full_reproduction"

    def test_level_index(self):
        """Level index should be correct."""
        assert LEVEL_INDEX["L0_project_health"] == 0
        assert LEVEL_INDEX["L6_full_or_near_full_reproduction"] == 6

    def test_all_levels_valid(self):
        """All defined levels should be valid."""
        for level in REPRODUCTION_LEVELS:
            assert is_valid_level(level)

    def test_level_labels_exist(self):
        """All levels should have labels."""
        for level in REPRODUCTION_LEVELS:
            assert level in LEVEL_LABELS

    def test_level_semantics_exist(self):
        """All levels should have semantics."""
        for level in REPRODUCTION_LEVELS:
            assert level in LEVEL_SEMANTICS


class TestNormalizeLevel:
    """Test level normalization."""

    def test_valid_level(self):
        """Valid level should be normalized."""
        assert normalize_level("L3_official_reduced_run") == "L3_official_reduced_run"

    def test_invalid_level(self):
        """Invalid level should return default."""
        assert normalize_level("invalid") == "L0_project_health"

    def test_empty_level(self):
        """Empty level should return default."""
        assert normalize_level("") == "L0_project_health"
        assert normalize_level(None) == "L0_project_health"

    def test_custom_default(self):
        """Should use custom default."""
        assert normalize_level("invalid", default="L4_reduced_paper_aligned") == "L4_reduced_paper_aligned"

    def test_whitespace(self):
        """Should handle whitespace."""
        assert normalize_level("  L2_input_contract_ready  ") == "L2_input_contract_ready"


class TestIsValidLevel:
    """Test level validation."""

    def test_valid_levels(self):
        """Valid levels should return True."""
        for level in REPRODUCTION_LEVELS:
            assert is_valid_level(level)

    def test_invalid_levels(self):
        """Invalid levels should return False."""
        assert not is_valid_level("L7_invalid")
        assert not is_valid_level("invalid")
        assert not is_valid_level("")
        assert not is_valid_level(None)


class TestLevelComparison:
    """Test level comparison functions."""

    def test_level_index(self):
        """Level index should be correct."""
        assert level_index("L0_project_health") == 0
        assert level_index("L4_reduced_paper_aligned") == 4
        assert level_index("L6_full_or_near_full_reproduction") == 6

    def test_level_reached(self):
        """Level reached should work correctly."""
        assert level_reached("L4_reduced_paper_aligned", "L2_input_contract_ready")
        assert level_reached("L6_full_or_near_full_reproduction", "L4_reduced_paper_aligned")
        assert not level_reached("L2_input_contract_ready", "L4_reduced_paper_aligned")
        assert level_reached("L3_official_reduced_run", "L3_official_reduced_run")


class TestValidateReviewerOutput:
    """Test reviewer output validation."""

    def test_valid_output(self):
        """Valid output should pass."""
        output = {
            "current_reproduction_level": "L3_official_reduced_run",
            "level_reasoning": "Achieved L3 with metrics.",
        }
        result = validate_reviewer_output(output)
        assert result["valid"]
        assert result["level"] == "L3_official_reduced_run"
        assert result["reasoning"] == "Achieved L3 with metrics."
        assert result["errors"] == []

    def test_missing_level(self):
        """Missing level should fail."""
        output = {
            "level_reasoning": "Achieved L3 with metrics.",
        }
        result = validate_reviewer_output(output)
        assert not result["valid"]
        assert "current_reproduction_level" in " ".join(result["errors"])

    def test_missing_reasoning(self):
        """Missing reasoning should fail."""
        output = {
            "current_reproduction_level": "L3_official_reduced_run",
        }
        result = validate_reviewer_output(output)
        assert not result["valid"]
        assert "level_reasoning" in " ".join(result["errors"])

    def test_invalid_level(self):
        """Invalid level should fail."""
        output = {
            "current_reproduction_level": "L7_invalid",
            "level_reasoning": "Invalid level.",
        }
        result = validate_reviewer_output(output)
        assert not result["valid"]
        assert "Invalid level" in " ".join(result["errors"])

    def test_empty_reasoning(self):
        """Empty reasoning should fail."""
        output = {
            "current_reproduction_level": "L3_official_reduced_run",
            "level_reasoning": "",
        }
        result = validate_reviewer_output(output)
        assert not result["valid"]


class TestParseReviewerJsonOutput:
    """Test reviewer JSON output parsing."""

    def test_parse_valid_json(self):
        """Should parse valid JSON."""
        text = json.dumps({
            "current_reproduction_level": "L3_official_reduced_run",
            "level_reasoning": "Achieved L3.",
        })
        result = parse_reviewer_json_output(text)
        assert result["parsed"]
        assert result["output"]["current_reproduction_level"] == "L3_official_reduced_run"
        assert result["validation"]["valid"]

    def test_parse_json_in_markdown_block(self):
        """Should extract JSON from markdown code block."""
        text = """```json
{
    "current_reproduction_level": "L4_reduced_paper_aligned",
    "level_reasoning": "Achieved L4."
}
```"""
        result = parse_reviewer_json_output(text)
        assert result["parsed"]
        assert result["output"]["current_reproduction_level"] == "L4_reduced_paper_aligned"

    def test_parse_embedded_json(self):
        """Should extract JSON from text."""
        text = "Here is the result:\n{\"current_reproduction_level\": \"L2_input_contract_ready\", \"level_reasoning\": \"L2 achieved.\"}\nEnd."
        result = parse_reviewer_json_output(text)
        assert result["parsed"]
        assert result["output"]["current_reproduction_level"] == "L2_input_contract_ready"

    def test_parse_invalid_json(self):
        """Should handle invalid JSON."""
        result = parse_reviewer_json_output("not json")
        assert not result["parsed"]
        assert result["error"]

    def test_parse_empty(self):
        """Should handle empty input."""
        result = parse_reviewer_json_output("")
        assert not result["parsed"]


class TestBuildReviewerOutput:
    """Test building reviewer output."""

    def test_minimal_output(self):
        """Should build minimal output."""
        output = build_reviewer_output(
            level="L3_official_reduced_run",
            reasoning="Achieved L3.",
        )
        assert output["current_reproduction_level"] == "L3_official_reduced_run"
        assert output["level_reasoning"] == "Achieved L3."

    def test_full_output(self):
        """Should build full output."""
        output = build_reviewer_output(
            level="L4_reduced_paper_aligned",
            reasoning="Achieved L4.",
            supporting_artifacts=["file1.csv", "file2.md"],
            remaining_gaps=["L5 not achieved"],
            next_iteration_guidance="Run baseline comparison",
            review_summary="Good progress",
            verdict="PASS_REDUCED_ALIGNED",
        )
        assert output["current_reproduction_level"] == "L4_reduced_paper_aligned"
        assert output["supporting_artifacts"] == ["file1.csv", "file2.md"]
        assert output["remaining_gaps"] == ["L5 not achieved"]
        assert output["verdict"] == "PASS_REDUCED_ALIGNED"


class TestBuildLevelSemanticsPrompt:
    """Test building level semantics prompt."""

    def test_contains_l0_l6(self):
        """Prompt should mention L0-L6."""
        prompt = build_level_semantics_prompt()
        assert "L0" in prompt
        assert "L6" in prompt
        assert "full or near-full reproduction" in prompt.lower()

    def test_contains_judge_instructions(self):
        """Prompt should contain judge instructions."""
        prompt = build_level_semantics_prompt()
        assert "judge" in prompt.lower()
        assert "reasoning" in prompt.lower()

    def test_contains_no_cap_notice(self):
        """Prompt should mention no cap at L4."""
        prompt = build_level_semantics_prompt()
        assert "Do not cap" in prompt or "no cap" in prompt.lower()
