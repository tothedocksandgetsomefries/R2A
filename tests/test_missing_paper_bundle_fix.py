"""Test for the specific MISSING_PAPER_BUNDLE fix.

This test ensures that the scenario from run_20260608_133027_7eb2eb06
is properly handled: having Markdown artifacts but no PAPER_OUTPUT.json
should NOT block Planner.
"""
from __future__ import annotations

from pathlib import Path

from r2a.core.paths import report_path
from r2a.core.state import make_initial_state
from r2a.tools.readiness_gate import check_paper_readiness
from r2a.tools.workflow_decision import aggregate_terminal_decision
from r2a.workflow.router import route_after_paper


def test_run_20260608_scenario_no_paper_output_json(tmp_path: Path) -> None:
    """Regression test for run_20260608_133027_7eb2eb06.

    Scenario: Paper Agent (openclaw_reader) generated all Markdown artifacts
    but no PAPER_OUTPUT.json. This should NOT block Planner.

    Files that existed in the real run:
    - PAPER_ANALYSIS_CN.md
    - PAPER_BRIEF.md
    - PAPER_CAPTIONS.md
    - PAPER_CONTEXT.md
    - PAPER_EVIDENCE.md
    - PAPER_FIGURES_TABLES.md
    - PAPER_PAGES.md
    - PAPER_PARSE_QUALITY.md
    - PAPER_REPRODUCTION_CARD.md
    - PAPER_SECTIONS.md
    - PAPER_TEXT.md

    Missing:
    - PAPER_OUTPUT.json (this was the issue)
    """
    # Write all Markdown artifacts that existed in the real run
    markdown_artifacts = [
        "paper_analysis",  # PAPER_ANALYSIS_CN.md
        "paper",           # PAPER_BRIEF.md
        "paper_captions",
        "paper_context",
        "paper_evidence",
        "paper_figures_tables",
        "paper_pages",
        "paper_parse_quality",
        "paper_reproduction_card",
        "paper_sections",
        "paper_text",
    ]

    for key in markdown_artifacts:
        path = report_path(tmp_path, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {key}\n\nContent for {key}\n", encoding="utf-8")

    # Do NOT write PAPER_OUTPUT.json
    # This is the key difference from the old behavior

    # Create a paper file
    paper_file = tmp_path / "paper.pdf"
    paper_file.write_text("PDF content", encoding="utf-8")

    state = make_initial_state(tmp_path, paper_path=str(paper_file))

    # Check readiness
    readiness = check_paper_readiness(state)

    # Should be ready despite missing PAPER_OUTPUT.json
    assert readiness["ready"] is True, f"Expected ready=True, got {readiness}"
    assert readiness["reason_code"] == "PAPER_INPUTS_AVAILABLE"
    assert "PAPER_OUTPUT.json" in " ".join(readiness.get("warnings", []))

    # Check decision
    decision = aggregate_terminal_decision({**state, "paper_readiness": readiness})

    # Should NOT be request_paper
    assert decision["typed_decision"] != "request_paper", \
        f"Should not be request_paper, got {decision['typed_decision']}"

    # The paper bundle itself should not block, but current routing only sends
    # continue_iteration decisions to Planner.
    route = route_after_paper({**state, "paper_readiness": readiness})
    assert route == "final", f"Current route should be final when decision is not continue_iteration, got {route}"


def test_only_paper_output_json_missing_all_other_artifacts_present(tmp_path: Path) -> None:
    """Test that ONLY missing PAPER_OUTPUT.json doesn't block."""
    # Write ALL PAPER_STRUCTURED_KEYS artifacts EXCEPT paper_output
    all_keys_except_output = [
        "paper_context",
        "paper",
        "paper_evidence",
        "paper_reproduction_card",
        "paper_parse_quality",
        "paper_analysis",
        "paper_text",
        "paper_pages",
        "paper_sections",
        "paper_captions",
        "paper_figures_tables",
    ]

    for key in all_keys_except_output:
        path = report_path(tmp_path, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {key}\n\nContent for {key}\n", encoding="utf-8")

    # Do NOT write paper_output

    paper_file = tmp_path / "paper.pdf"
    paper_file.write_text("PDF content", encoding="utf-8")

    state = make_initial_state(tmp_path, paper_path=str(paper_file))

    readiness = check_paper_readiness(state)
    decision = aggregate_terminal_decision({**state, "paper_readiness": readiness})

    # Should be ready
    assert readiness["ready"] is True
    assert readiness["reason_code"] == "PAPER_INPUTS_AVAILABLE"
    assert decision["typed_decision"] != "request_paper"
    assert route_after_paper({**state, "paper_readiness": readiness}) == "final"


def test_no_paper_output_and_minimal_artifacts_still_sufficient(tmp_path: Path) -> None:
    """Test that even with minimal artifacts, missing PAPER_OUTPUT.json doesn't block."""
    # Write only 2 core artifacts (minimum for "usable")
    for key in ("paper_context", "paper"):
        path = report_path(tmp_path, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {key}\n\nContent for {key}\n", encoding="utf-8")

    paper_file = tmp_path / "paper.pdf"
    paper_file.write_text("PDF content", encoding="utf-8")

    state = make_initial_state(tmp_path, paper_path=str(paper_file))

    readiness = check_paper_readiness(state)

    # Should be ready with just 2 artifacts
    assert readiness["ready"] is True
    assert readiness["artifact_count"] >= 2


def test_absolutely_no_paper_input_still_blocks(tmp_path: Path) -> None:
    """Test that having NO paper input at all still blocks."""
    # No paper_path, no artifacts
    state = make_initial_state(tmp_path, auto_iterate=True)

    readiness = check_paper_readiness(state)
    decision = aggregate_terminal_decision({**state, "paper_readiness": readiness})

    # Should block
    assert readiness["ready"] is False
    assert readiness["reason_code"] == "MISSING_PAPER"
    assert decision["typed_decision"] == "request_paper"
