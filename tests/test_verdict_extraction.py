"""Tests for verdict extraction with backtick and bold wrapping."""

import pytest
from r2a.agents.reviewer_agent import (
    _extract_verdict,
    _extract_labeled_verdict,
    _extract_standalone_verdict,
    _extract_standalone_bold_verdict,
    _strip_wrapping_backticks,
)


class TestBacktickStripping:
    """Tests for _strip_wrapping_backticks function."""

    def test_simple_backtick(self):
        """Simple backtick wrapping should be stripped."""
        assert _strip_wrapping_backticks("`PASS_WITH_LIMITATIONS`") == "PASS_WITH_LIMITATIONS"

    def test_backtick_with_spaces(self):
        """Backtick wrapping with spaces should be stripped."""
        assert _strip_wrapping_backticks("` PASS_WITH_LIMITATIONS `") == "PASS_WITH_LIMITATIONS"

    def test_no_backtick(self):
        """Text without backticks should be unchanged."""
        assert _strip_wrapping_backticks("PASS_WITH_LIMITATIONS") == "PASS_WITH_LIMITATIONS"

    def test_single_backtick(self):
        """Single backtick should not be stripped."""
        assert _strip_wrapping_backticks("`PASS") == "`PASS"

    def test_backtick_in_middle(self):
        """Backticks in the middle should not be stripped."""
        assert _strip_wrapping_backticks("PASS`VALUE") == "PASS`VALUE"


class TestExtractVerdictBackticks:
    """Tests for _extract_verdict with backtick-wrapped tokens."""

    def test_backtick_wrapped_verdict(self):
        """Verdict tokens wrapped in backticks should be recognized."""
        text = "**Verdict**: `PASS_WITH_LIMITATIONS`"
        assert _extract_verdict(text) == "PASS_WITH_LIMITATIONS"

    def test_backtick_only_verdict(self):
        """Bare backtick-wrapped verdict should be recognized in heading section."""
        # This format appears in a ## Verdict section
        text = """
## Verdict

`PASS_WITH_LIMITATIONS`
"""
        assert _extract_verdict(text) == "PASS_WITH_LIMITATIONS"

    def test_bold_and_backtick_verdict(self):
        """Both bold and backtick wrapping should be handled."""
        text = "**`PASS_WITH_LIMITATIONS`**"
        assert _extract_verdict(text) == "PASS_WITH_LIMITATIONS"

    def test_labeled_backtick_verdict(self):
        """Labeled verdict with backticks should be recognized."""
        text = "Verdict: `PASS_REDUCED_ALIGNED`"
        assert _extract_verdict(text) == "PASS_REDUCED_ALIGNED"

    def test_labeled_quoted_verdict(self):
        """Labeled verdict with quotes should be recognized."""
        text = 'Verdict: "PASS_WITH_LIMITATIONS"'
        assert _extract_verdict(text) == "PASS_WITH_LIMITATIONS"

    def test_chinese_labeled_backtick_verdict(self):
        """Chinese labeled verdict with backticks should be recognized."""
        text = "审查判定: `PASS_WITH_LIMITATIONS`"
        assert _extract_verdict(text) == "PASS_WITH_LIMITATIONS"

    def test_chinese_judgment_heading_verdict(self):
        text = "## 判决: PASS_REDUCED_ALIGNED"
        assert _extract_verdict(text) == "PASS_REDUCED_ALIGNED"

    def test_chinese_ruling_heading_verdict(self):
        text = "## 裁决: PASS_REDUCED_ALIGNED"
        assert _extract_verdict(text) == "PASS_REDUCED_ALIGNED"

    def test_heading_section_backtick_verdict(self):
        """Verdict in heading section with backticks should be recognized."""
        text = """
# REVIEW_REPORT

## Verdict

`PASS_WITH_LIMITATIONS`

## Conclusion
"""
        assert _extract_verdict(text) == "PASS_WITH_LIMITATIONS"


class TestExtractLabeledVerdictBackticks:
    """Tests for _extract_labeled_verdict with backtick-wrapped tokens."""

    def test_colon_backtick_format(self):
        """Format 'Label: `VERDICT`' should be recognized."""
        text = "Verdict: `PASS_WITH_LIMITATIONS`"
        assert _extract_labeled_verdict(text) == "PASS_WITH_LIMITATIONS"

    def test_bold_colon_backtick_format(self):
        """Format '**Label**: `VERDICT`' should be recognized."""
        text = "**Verdict**: `PASS_WITH_LIMITATIONS`"
        assert _extract_labeled_verdict(text) == "PASS_WITH_LIMITATIONS"

    def test_backtick_no_label(self):
        """Backtick-wrapped verdict without label should not be extracted by labeled parser."""
        text = "`PASS_WITH_LIMITATIONS`"
        # This should return empty because there's no label
        assert _extract_labeled_verdict(text) == ""


class TestExtractStandaloneVerdictBackticks:
    """Tests for _extract_standalone_verdict with backtick-wrapped tokens."""

    def test_standalone_backtick_verdict(self):
        """Standalone backtick-wrapped verdict should be recognized."""
        text = "`PASS_WITH_LIMITATIONS`"
        assert _extract_standalone_verdict(text) == "PASS_WITH_LIMITATIONS"

    def test_standalone_bold_backtick_verdict(self):
        """Standalone bold and backtick-wrapped verdict should be recognized."""
        text = "**`PASS_WITH_LIMITATIONS`**"
        assert _extract_standalone_verdict(text) == "PASS_WITH_LIMITATIONS"


class TestExtractStandaloneBoldVerdictBackticks:
    """Tests for _extract_standalone_bold_verdict with backtick-wrapped tokens."""

    def test_bold_backtick_verdict(self):
        """Bold backtick-wrapped verdict should be recognized."""
        text = "**`PASS_WITH_LIMITATIONS`**"
        assert _extract_standalone_bold_verdict(text) == "PASS_WITH_LIMITATIONS"

    def test_bold_only_verdict(self):
        """Bold-only verdict should still work."""
        text = "**PASS_WITH_LIMITATIONS**"
        assert _extract_standalone_bold_verdict(text) == "PASS_WITH_LIMITATIONS"


class TestRealWorldExamples:
    """Tests based on real REVIEW_REPORT.md examples."""

    def test_real_acorn_run_format(self):
        """Test the exact format from the ACORN run."""
        text = """
# REVIEW_REPORT.md - ACORN 论文复现评审报告

## 判定结果

**Verdict**: `PASS_WITH_LIMITATIONS`

**当前复现等级**: `L2_input_contract_ready`
"""
        assert _extract_verdict(text) == "PASS_WITH_LIMITATIONS"

    def test_multiline_verdict_section(self):
        """Test verdict in a multiline section."""
        text = """
## Verdict

`PASS_WITH_LIMITATIONS`

This is a detailed explanation of why the verdict was given.
"""
        assert _extract_verdict(text) == "PASS_WITH_LIMITATIONS"

    def test_multiple_backtick_values_only_verdict_recognized(self):
        """Multiple backtick values, only verdict should be recognized."""
        text = """
**Verdict**: `PASS_WITH_LIMITATIONS`

**Current Level**: `L2_input_contract_ready`
**Target Level**: `L4_reduced_paper_aligned`
"""
        assert _extract_verdict(text) == "PASS_WITH_LIMITATIONS"
