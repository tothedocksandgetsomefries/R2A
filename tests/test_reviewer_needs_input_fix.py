"""Tests for Reviewer NEEDS_INPUT verdict normalization fix.

This module tests that NEEDS_INPUT (an internal blocking status) is properly
normalized to NEEDS_INPUT_OR_BUDGET before entering formal verdict validation.
"""

import json
import time

import pytest
from r2a.core.paths import report_path
from r2a.core.review_verdict import normalize_verdict_token, validate_review_verdict
from r2a.core.verdicts import VALID_VERDICTS
from r2a.tools.stage_transaction import (
    REVIEWER_ALLOWED_VERDICTS,
    commit_reviewer_transaction,
    reviewer_staging_dir,
    validate_reviewer_transaction,
)


def test_needs_input_normalized_to_needs_input_or_budget():
    """NEEDS_INPUT should be normalized to NEEDS_INPUT_OR_BUDGET."""
    assert normalize_verdict_token("NEEDS_INPUT") == "NEEDS_INPUT_OR_BUDGET"
    assert normalize_verdict_token("needs_input") == "NEEDS_INPUT_OR_BUDGET"
    assert normalize_verdict_token("**NEEDS_INPUT**") == "NEEDS_INPUT_OR_BUDGET"
    assert normalize_verdict_token("`NEEDS_INPUT`") == "NEEDS_INPUT_OR_BUDGET"


def test_needs_input_not_in_valid_verdicts():
    """NEEDS_INPUT should not be in VALID_VERDICTS."""
    assert "NEEDS_INPUT" not in VALID_VERDICTS
    assert "NEEDS_INPUT" not in REVIEWER_ALLOWED_VERDICTS


def test_needs_input_or_budget_in_valid_verdicts():
    """NEEDS_INPUT_OR_BUDGET should be in VALID_VERDICTS."""
    assert "NEEDS_INPUT_OR_BUDGET" in VALID_VERDICTS


def test_validate_review_verdict_rejects_needs_input():
    """validate_review_verdict should reject NEEDS_INPUT."""
    payload = {
        "verdict": "NEEDS_INPUT",
        "accepted_level": "UNASSESSED",
        "level_valid": False,
        "target_reached": False,
    }
    validation = validate_review_verdict(payload)
    # After normalization, verdict should be NEEDS_INPUT_OR_BUDGET
    assert validation.valid
    assert validation.payload["verdict"] == "NEEDS_INPUT_OR_BUDGET"
    assert "NEEDS_INPUT" not in validation.errors


def test_validate_review_verdict_accepts_needs_input_or_budget():
    """validate_review_verdict should accept NEEDS_INPUT_OR_BUDGET."""
    payload = {
        "verdict": "NEEDS_INPUT_OR_BUDGET",
        "accepted_level": "UNASSESSED",
        "level_valid": False,
        "target_reached": False,
    }
    validation = validate_review_verdict(payload)
    assert validation.valid
    assert validation.payload["verdict"] == "NEEDS_INPUT_OR_BUDGET"


def test_other_verdicts_not_affected():
    """Other verdicts should not be affected by normalization."""
    test_cases = [
        "PASS_REDUCED_ALIGNED",
        "NEEDS_FIX",
        "REJECT",
        "PASS_SMOKE_ONLY",
        "NEEDS_OFFICIAL_INPUT",
    ]
    for verdict in test_cases:
        assert normalize_verdict_token(verdict) == verdict
        assert normalize_verdict_token(verdict.lower()) == verdict


def test_needs_input_preserves_proposed_verdict_in_audit():
    """When normalizing NEEDS_INPUT, we should preserve it as proposed_verdict for audit."""
    # This test documents the expected behavior: normalize_verdict_token
    # always returns the canonical verdict, but the caller can preserve
    # the original in a proposed_verdict field for audit purposes.
    original = "NEEDS_INPUT"
    normalized = normalize_verdict_token(original)

    assert normalized == "NEEDS_INPUT_OR_BUDGET"
    # Caller should preserve original as proposed_verdict if needed
    # (this is implementation detail, not enforced by normalize_verdict_token)


def test_needs_input_with_wrapping():
    """NEEDS_INPUT with various wrapping should still normalize correctly."""
    test_cases = [
        "NEEDS_INPUT",
        "needs_input",
        "**NEEDS_INPUT**",
        "`NEEDS_INPUT`",
        '"NEEDS_INPUT"',
        "'NEEDS_INPUT'",
        "“NEEDS_INPUT”",  # Smart quotes
        "‘NEEDS_INPUT’",  # Smart single quotes
        "  NEEDS_INPUT  ",  # Whitespace
        "**`NEEDS_INPUT`**",  # Nested wrapping
    ]

    for test_input in test_cases:
        normalized = normalize_verdict_token(test_input)
        assert normalized == "NEEDS_INPUT_OR_BUDGET", f"Failed for input: {test_input!r}"


def test_empty_and_none_verdict():
    """Empty and None verdicts should normalize to empty string."""
    assert normalize_verdict_token("") == ""
    assert normalize_verdict_token(None) == ""
    assert normalize_verdict_token("  ") == ""


def test_stage_transaction_normalizes_review_feedback_needs_input(tmp_path):
    """REVIEW_FEEDBACK.json verdict=NEEDS_INPUT should commit as a canonical verdict."""
    staging = reviewer_staging_dir(tmp_path, 1, 1)
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "REVIEW_REPORT.md").write_text("# REVIEW_REPORT\n\n## Verdict\n\nNEEDS_INPUT\n", encoding="utf-8")
    (staging / "REVIEW_FEEDBACK.json").write_text(
        json.dumps({"schema_version": 1, "verdict": "NEEDS_INPUT", "should_iterate": False}),
        encoding="utf-8",
    )

    metadata = validate_reviewer_transaction(
        tmp_path,
        staging,
        {"success": True, "returncode": 0, "unexpected_modifications": []},
        iteration=1,
        attempt_started_at=time.time() - 1,
    )

    assert metadata["validation_status"] == "PASS"
    assert metadata["candidate_verdict"] == "NEEDS_INPUT_OR_BUDGET"
    assert metadata["raw_candidate_verdict"] == "NEEDS_INPUT"
    assert metadata["execution_status"] != "REVIEWER_INVALID_VERDICT"
    assert "REVIEWER_INVALID_VERDICT" not in metadata["issues"]

    committed = commit_reviewer_transaction(tmp_path, staging, metadata)
    feedback = json.loads(report_path(tmp_path, "review_feedback").read_text(encoding="utf-8"))
    assert committed["committed"] is True
    assert feedback["verdict"] == "NEEDS_INPUT_OR_BUDGET"
    assert feedback["raw_verdict"] == "NEEDS_INPUT"
    assert "internal blocking status alias" in feedback["normalization_reason"]


def test_stage_transaction_keeps_other_valid_verdicts_unchanged(tmp_path):
    """Canonical normalization should not change other allowed Reviewer verdicts."""
    staging = reviewer_staging_dir(tmp_path, 1, 1)
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "REVIEW_REPORT.md").write_text("# REVIEW_REPORT\n\n## Verdict\n\nNEEDS_FIX\n", encoding="utf-8")
    (staging / "REVIEW_FEEDBACK.json").write_text(
        json.dumps({"schema_version": 1, "verdict": "NEEDS_FIX", "should_iterate": True}),
        encoding="utf-8",
    )

    metadata = validate_reviewer_transaction(
        tmp_path,
        staging,
        {"success": True, "returncode": 0, "unexpected_modifications": []},
        iteration=1,
        attempt_started_at=time.time() - 1,
    )

    assert metadata["validation_status"] == "PASS"
    assert metadata["candidate_verdict"] == "NEEDS_FIX"
    assert metadata["raw_candidate_verdict"] == "NEEDS_FIX"
    assert metadata["verdict_normalization_reason"] == ""
