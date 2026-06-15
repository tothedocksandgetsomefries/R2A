from __future__ import annotations

VALID_VERDICTS = (
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
    "BORDERLINE",
    "NEEDS_FIX",
    "REJECT",
    "PASS_L4",
    "PASS_L3",
    "PASS_L2",
    "PASS_L1",
    "PASS",
)

PASS_LIKE_VERDICTS = frozenset(
    {
        "PASS_L4",
        "PASS_L3",
        "PASS_L2",
        "PASS_L1",
        "PASS",
        "INPUT_CONTRACT_READY",
        "PASS_SMOKE_ONLY",
        "PASS_WITH_LIMITATIONS",
        "PASS_DEMO_ONLY",
        "PASS_REDUCED_METHOD_ONLY",
        "PASS_REDUCED_ALIGNED",
        "PASS_REDUCED_COMPARISON",
    }
)

BLOCKING_VERDICTS = frozenset(
    {
        "REJECT",
        "NEEDS_FIX",
        "NEEDS_INPUT",
        "NEEDS_OFFICIAL_INPUT",
        "NEEDS_INPUT_OR_BUDGET",
        "BORDERLINE",
        "BLOCKED",
        "FAIL",
        "FAILED",
        "MANAGER_CLASSIFICATION_CONFLICT",
        "NEEDS_DETERMINISTIC_RECHECK",
        "HUMAN_REVIEW_REQUIRED",
    }
)


def normalize_verdict(verdict: object) -> str:
    return str(verdict or "").strip().upper()


def is_valid_verdict(verdict: object) -> bool:
    return normalize_verdict(verdict) in VALID_VERDICTS


def is_pass_like_verdict(verdict: object) -> bool:
    return normalize_verdict(verdict) in PASS_LIKE_VERDICTS
