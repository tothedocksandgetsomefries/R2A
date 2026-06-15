"""Tests for Planner no-placeholder and no-fake-setup rules."""

from pathlib import Path

import pytest


def test_planner_prompt_contains_no_tbd_rule() -> None:
    """Planner prompt must explicitly forbid TBD placeholder."""
    prompt_path = Path(__file__).parent.parent / "r2a" / "prompts" / "planner_v2.md"
    assert prompt_path.exists(), "Planner prompt file not found"

    content = prompt_path.read_text(encoding="utf-8")

    # Must contain explicit "No placeholder text in outputs" rule
    assert "No placeholder text in outputs" in content or "placeholder text" in content.lower(), \
        "Planner prompt must contain rule forbidding placeholder text"

    # Must mention TBD as forbidden
    assert "TBD" in content, \
        "Planner prompt must mention TBD as forbidden placeholder"

    # Must provide alternative statuses
    assert "BLOCKED_ENVIRONMENT" in content, \
        "Planner prompt must provide BLOCKED_ENVIRONMENT as alternative"
    assert "NOT_RUN_MISSING_COMPILER" in content or "SKIPPED_WITH_REASON" in content, \
        "Planner prompt must provide explicit status alternatives"


def test_planner_prompt_contains_no_todo_fixme_rule() -> None:
    """Planner prompt must forbid TODO and FIXME."""
    prompt_path = Path(__file__).parent.parent / "r2a" / "prompts" / "planner_v2.md"
    content = prompt_path.read_text(encoding="utf-8")

    # Must mention TODO as forbidden
    assert "TODO" in content, \
        "Planner prompt must mention TODO as forbidden"

    # Must mention FIXME as forbidden
    assert "FIXME" in content, \
        "Planner prompt must mention FIXME as forbidden"


def test_planner_prompt_provides_alternative_statuses() -> None:
    """Planner prompt must provide alternative statuses for placeholders."""
    prompt_path = Path(__file__).parent.parent / "r2a" / "prompts" / "planner_v2.md"
    content = prompt_path.read_text(encoding="utf-8")

    # Must provide at least some alternative statuses
    alternatives = [
        "BLOCKED_ENVIRONMENT",
        "NOT_RUN_MISSING_COMPILER",
        "NOT_RUN_MISSING_DEPENDENCY",
        "SKIPPED_WITH_REASON",
        "UNKNOWN_NOT_EXECUTED",
    ]

    found_alternatives = [alt for alt in alternatives if alt in content]
    assert len(found_alternatives) >= 2, \
        f"Planner prompt must provide at least 2 alternative statuses, found: {found_alternatives}"


def test_planner_prompt_contains_setup_py_rule() -> None:
    """Planner prompt must contain rule about not referencing non-existent setup.py."""
    prompt_path = Path(__file__).parent.parent / "r2a" / "prompts" / "planner_v2.md"
    content = prompt_path.read_text(encoding="utf-8")

    # Must mention setup.py rule
    assert "setup.py" in content, \
        "Planner prompt must mention setup.py rule"

    # Must say not to reference non-existent setup.py
    assert "not exist" in content.lower() or "does not exist" in content.lower(), \
        "Planner prompt must explain not to reference non-existent files"


def test_planner_prompt_does_not_contain_conditional_setup_py_examples() -> None:
    """Planner prompt must not teach conditional missing-script commands as examples."""
    prompt_path = Path(__file__).parent.parent / "r2a" / "prompts" / "planner_v2.md"
    content = prompt_path.read_text(encoding="utf-8")

    forbidden = [
        "If setup.py exists, run python setup.py test",
        "If setup.py exists with test command",
        "If tests exist, run pytest",
        "If benchmark.py exists, run",
    ]

    for phrase in forbidden:
        assert phrase not in content


def test_planner_prompt_requires_inventory_confirmed_commands() -> None:
    """Planner prompt should require deterministic commands from source inventory."""
    prompt_path = Path(__file__).parent.parent / "r2a" / "prompts" / "planner_v2.md"
    content = prompt_path.read_text(encoding="utf-8")

    assert "source inventory" in content
    assert "Do NOT write conditional command text" in content
    assert "project_tests=SKIPPED_WITH_REASON" in content


def test_planner_prompt_provides_correct_examples() -> None:
    """Planner prompt must provide correct examples showing how to replace TBD."""
    prompt_path = Path(__file__).parent.parent / "r2a" / "prompts" / "planner_v2.md"
    content = prompt_path.read_text(encoding="utf-8")

    # Should have example showing WRONG: TBD
    assert "WRONG" in content or "wrong" in content.lower() or "Example (WRONG)" in content, \
        "Planner prompt should show wrong example with TBD"

    # Should have example showing CORRECT: explicit status
    assert "CORRECT" in content or "correct" in content.lower() or "Example (CORRECT)" in content, \
        "Planner prompt should show correct example with explicit status"


def test_readiness_gate_placeholder_patterns() -> None:
    """Verify readiness gate has placeholder patterns defined."""
    from r2a.tools.readiness_gate import PLACEHOLDER_PATTERNS

    # Should have patterns for TBD, TODO, FIXME
    pattern_strs = [p.pattern for p in PLACEHOLDER_PATTERNS]

    has_tbd = any("TBD" in p for p in pattern_strs)
    has_todo = any("TODO" in p for p in pattern_strs)

    assert has_tbd, "Readiness gate must have TBD pattern"
    assert has_todo, "Readiness gate must have TODO pattern"


def test_readiness_gate_would_catch_tbd_in_task_spec(tmp_path: Path) -> None:
    """Verify readiness gate catches TBD in TASK_SPEC content."""
    from r2a.tools.readiness_gate import PLACEHOLDER_PATTERNS

    # Create a minimal TASK_SPEC with TBD
    task_spec = tmp_path / "TASK_SPEC.md"
    task_spec.write_text(
        "# TASK_SPEC\n\n"
        "## Tasks\n\n"
        "### task_001\n\n"
        "- Actions:\n"
        "  - echo 'reduced=TBD based on build success'\n",
        encoding="utf-8",
    )

    # Verify TBD pattern exists
    content = task_spec.read_text(encoding="utf-8")
    tbd_pattern = None
    for pattern in PLACEHOLDER_PATTERNS:
        if "TBD" in pattern.pattern:
            tbd_pattern = pattern
            break

    assert tbd_pattern, "Should have TBD pattern defined"
    assert tbd_pattern.search(content), "TBD pattern should match TASK_SPEC with TBD text"


def test_readiness_gate_accepts_explicit_status(tmp_path: Path) -> None:
    """Verify readiness gate accepts explicit status instead of TBD."""
    from r2a.tools.readiness_gate import PLACEHOLDER_PATTERNS

    # Create a TASK_SPEC with explicit status (no TBD)
    task_spec = tmp_path / "TASK_SPEC.md"
    task_spec.write_text(
        "# TASK_SPEC\n\n"
        "## Tasks\n\n"
        "### task_001\n\n"
        "- Actions:\n"
        "  - echo 'reduced=NOT_RUN_MISSING_COMPILER, status=BLOCKED_ENVIRONMENT, reason=C++ compiler unavailable'\n",
        encoding="utf-8",
    )

    # Verify TBD pattern does NOT match
    content = task_spec.read_text(encoding="utf-8")
    tbd_pattern = None
    for pattern in PLACEHOLDER_PATTERNS:
        if "TBD" in pattern.pattern:
            tbd_pattern = pattern
            break

    assert tbd_pattern, "Should have TBD pattern defined"
    assert not tbd_pattern.search(content), \
        "TBD pattern should NOT match TASK_SPEC with explicit status"
