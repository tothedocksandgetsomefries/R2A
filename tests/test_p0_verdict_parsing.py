"""Tests for P0-1: Reviewer verdict parsing.

Validates that _extract_verdict() supports multiple verdict formats:
1. Independent `## Verdict` section
2. Inline `**Verdict: PASS_REDUCED_ALIGNED**`
3. Plain `Verdict: PASS_REDUCED_ALIGNED`
4. Chinese `最终判定: PASS_REDUCED_ALIGNED`
5. Section `### 评审结论` with verdict
"""

import pytest
from r2a.agents.reviewer_agent import _extract_verdict


class TestVerdictParsing:
    """Test verdict extraction from various formats."""

    def test_independent_verdict_section(self):
        """Independent `## Verdict` section should be parsed."""
        text = """
# REVIEW_REPORT

## Verdict

PASS_REDUCED_ALIGNED

## Other Section

Some content.
"""
        assert _extract_verdict(text) == "PASS_REDUCED_ALIGNED"

    def test_inline_bold_verdict(self):
        """Inline `**Verdict: PASS_REDUCED_ALIGNED**` should be parsed."""
        text = """
# REVIEW_REPORT

### 评审结论

**Verdict: PASS_REDUCED_ALIGNED**

本次迭代成功完成 L4 验证。
"""
        assert _extract_verdict(text) == "PASS_REDUCED_ALIGNED"

    def test_plain_verdict_line(self):
        """Plain `Verdict: PASS_REDUCED_ALIGNED` should be parsed."""
        text = """
# REVIEW_REPORT

## Summary

Verdict: PASS_REDUCED_ALIGNED

Some other text.
"""
        assert _extract_verdict(text) == "PASS_REDUCED_ALIGNED"

    def test_chinese_verdict(self):
        """Chinese `最终判定: PASS_REDUCED_ALIGNED` should be parsed."""
        text = """
# REVIEW_REPORT

## 结论

最终判定: PASS_REDUCED_ALIGNED

工作流已达到目标等级。
"""
        assert _extract_verdict(text) == "PASS_REDUCED_ALIGNED"

    def test_verdict_in_conclusion_section(self):
        """Verdict in `### 评审结论` section should be parsed."""
        text = """
# REVIEW_REPORT

### 评审结论

本次迭代成功完成验证。Verdict: INPUT_CONTRACT_READY
"""
        assert _extract_verdict(text) == "INPUT_CONTRACT_READY"

    def test_priority_independent_section(self):
        """Independent `## Verdict` section has highest priority."""
        text = """
# REVIEW_REPORT

### 评审结论

**Verdict: PASS_REDUCED_ALIGNED**

## Verdict

NEEDS_FIX
"""
        # Independent section has priority
        assert _extract_verdict(text) == "NEEDS_FIX"

    def test_invalid_verdict_rejected(self):
        """Invalid verdict tokens should not be accepted."""
        text = """
# REVIEW_REPORT

## Verdict

INVALID_VERDICt
"""
        assert _extract_verdict(text) == ""

    def test_no_verdict_returns_empty(self):
        """No valid verdict should return empty string."""
        text = """
# REVIEW_REPORT

## Summary

This is a summary without verdict.
"""
        assert _extract_verdict(text) == ""

    def test_multiple_verdict_tokens(self):
        """Multiple verdict tokens should match the longest/first valid one."""
        text = """
# REVIEW_REPORT

## Verdict

PASS_REDUCED_ALIGNED
"""
        assert _extract_verdict(text) == "PASS_REDUCED_ALIGNED"

    def test_all_valid_verdicts(self):
        """All valid verdict tokens should be recognized."""
        valid_verdicts = [
            "PASS_REDUCED_COMPARISON",
            "PASS_REDUCED_ALIGNED",
            "PASS_REDUCED_METHOD_ONLY",
            "PASS_WITH_REVIEW_CONFLICT",
            "MANAGER_CLASSIFICATION_CONFLICT",
            "NEEDS_DETERMINISTIC_RECHECK",
            "HUMAN_REVIEW_REQUIRED",
            "INPUT_CONTRACT_READY",
            "PASS_SMOKE_ONLY",
            "PASS_DEMO_ONLY",
            "NEEDS_INPUT_OR_BUDGET",
            "NEEDS_OFFICIAL_INPUT",
            "PASS_WITH_LIMITATIONS",
            "NEEDS_FIX",
            "BORDERLINE",
            "REJECT",
            "PASS",
        ]
        for verdict in valid_verdicts:
            text = f"""
# REVIEW_REPORT

## Verdict

{verdict}
"""
            assert _extract_verdict(text) == verdict, f"Failed to parse verdict: {verdict}"

    def test_actual_review_report_format(self):
        """Test actual format from run_20260612_003125_23ef3d02."""
        text = """
# REVIEW_REPORT.md

## 迭代信息

- **迭代次数**: 9
- **最大迭代限制**: 10
- **工作流状态**: 已达成 L4_reduced_paper_aligned
- **输出语言**: 简体中文

---

## 1. 评审摘要

### 评审结论

**Verdict: PASS_REDUCED_ALIGNED**

本次迭代（第9次迭代）成功完成了 L4_reduced_paper_aligned 证据完整性验证和工作流最终状态文档化。
"""
        assert _extract_verdict(text) == "PASS_REDUCED_ALIGNED"

    @pytest.mark.parametrize(
        "body",
        [
            "## Verdict\nPASS_REDUCED_ALIGNED\n",
            "### Verdict: **PASS_REDUCED_ALIGNED**\n",
            "**Verdict**: PASS_REDUCED_ALIGNED\n",
            '**Verdict**: "PASS_REDUCED_ALIGNED"\n',
            "- **审查判定**: PASS_REDUCED_ALIGNED\n",
            "## 判决: PASS_REDUCED_ALIGNED\n",
            "## 裁决: PASS_REDUCED_ALIGNED\n",
            "**审查判定**: PASS_REDUCED_ALIGNED\n",
            "**裁决: PASS_REDUCED_ALIGNED**\n",
            "**裁决**: PASS_REDUCED_ALIGNED\n",
            "裁决：PASS_REDUCED_ALIGNED\n",
            "**判定**: PASS_REDUCED_ALIGNED\n",
            "判定：PASS_REDUCED_ALIGNED\n",
            "### 最终裁决\n**PASS_REDUCED_ALIGNED**\n",
            "**PASS_REDUCED_ALIGNED**\n",
        ],
    )
    def test_requested_p0_pass_reduced_aligned_formats(self, body):
        assert _extract_verdict(f"# REVIEW_REPORT\n\n{body}\n") == "PASS_REDUCED_ALIGNED"

    def test_plain_body_token_is_not_verdict(self):
        text = """
# REVIEW_REPORT

## Summary

Reviewer mentioned PASS_REDUCED_ALIGNED as an example of a possible verdict,
but did not make a final adjudication.
"""
        assert _extract_verdict(text) == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
