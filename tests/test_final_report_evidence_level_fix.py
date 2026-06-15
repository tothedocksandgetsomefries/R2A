"""Regression test for NameError: evidence_level not defined in write_final_report."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from r2a.core.state import make_initial_state
from r2a.tools.iteration import write_final_report


def test_write_final_report_uses_current_level_for_progress_cards(tmp_path: Path) -> None:
    """Ensure write_final_report uses current_level, not undefined evidence_level.

    Regression test for:
    NameError: name 'evidence_level' is not defined
    at r2a/tools/iteration.py:510

    The bug was that write_final_report() defined 'current_level' but
    called _progress_cards(repo, evidence_level, language) with an
    undefined variable 'evidence_level'.
    """
    # Setup minimal state without evidence_ladder
    paper = tmp_path / "paper.txt"
    paper.write_text("test paper", encoding="utf-8")

    r2a = tmp_path / ".r2a"
    r2a.mkdir(parents=True, exist_ok=True)
    results = r2a / "results"
    results.mkdir(parents=True, exist_ok=True)
    latest = r2a / "latest"
    latest.mkdir(parents=True, exist_ok=True)

    # Create minimal evidence files
    (results / "source_verification.csv").write_text(
        "status,artifact_url,source_path,branch,commit,notes\n"
        "PASS,https://example.test,.,main,abc123,ok\n",
        encoding="utf-8",
    )
    (results / "build_smoke.csv").write_text(
        "status,command,exit_code,duration_sec,component,notes\n"
        "PASS,python main.py,0,0.1,main,ok\n",
        encoding="utf-8",
    )

    # Create CHECK_REPORT
    (r2a / "CHECK_REPORT.md").write_text(
        "# CHECK_REPORT\n\n## Status\n\nPASS\n",
        encoding="utf-8",
    )

    # Create RUN_MANIFEST
    (latest / "RUN_MANIFEST.json").write_text(
        json.dumps({
            "run_id": "test-run",
            "status": "COMPLETED",
            "stages": {},
        }),
        encoding="utf-8",
    )

    # State with L4 level (no evidence_ladder)
    state = {
        "repo_path": str(tmp_path),
        "paper_path": str(paper),
        "iteration": 1,
        "current_reproduction_level": "L4_reduced_paper_aligned",
        "current_level_iteration": 1,
        "level_source": "ai_backend",
        "level_reasoning": "Test L4 achieved",
        "reviewer_executed": True,
        "reviewer_level_valid": True,
        "decision_status": {
            "typed_decision": "stop_success",
            "reason_code": "TARGET_EVIDENCE_REACHED",
        },
        "iteration_history": [],
        "target_reproduction_level": "L4_reduced_paper_aligned",
        "loop_status": "completed",
        "language": "en",
    }

    # This should NOT raise NameError
    try:
        result = write_final_report(state)
        assert result.exists()

        # Verify report content
        text = result.read_text(encoding="utf-8")
        assert "# FINAL_REPORT" in text

        # Should contain L4 level
        assert "L4" in text

        # Should NOT contain KeyError for L0_project_health
        assert "KeyError" not in text
        assert "'L0_project_health'" not in text

        # Should have progress_cards generated (evidence of _progress_cards call)
        assert "Progress Cards" in text or "阶段进度" in text

    except NameError as e:
        pytest.fail(f"NameError raised (bug not fixed): {e}")


def test_write_final_report_handles_empty_level_without_error(tmp_path: Path) -> None:
    """Ensure write_final_report handles empty/unset current_reproduction_level."""
    paper = tmp_path / "paper.txt"
    paper.write_text("test paper", encoding="utf-8")

    r2a = tmp_path / ".r2a"
    r2a.mkdir(parents=True, exist_ok=True)
    results = r2a / "results"
    results.mkdir(parents=True, exist_ok=True)
    latest = r2a / "latest"
    latest.mkdir(parents=True, exist_ok=True)

    (results / "source_verification.csv").write_text(
        "status,artifact_url,source_path,branch,commit,notes\n"
        "PASS,https://example.test,.,main,abc123,ok\n",
        encoding="utf-8",
    )
    (r2a / "CHECK_REPORT.md").write_text(
        "# CHECK_REPORT\n\n## Status\n\nPASS\n",
        encoding="utf-8",
    )
    (latest / "RUN_MANIFEST.json").write_text(
        json.dumps({
            "run_id": "test-run",
            "status": "COMPLETED",
            "stages": {},
        }),
        encoding="utf-8",
    )

    # State without level (Reviewer not executed)
    state = {
        "repo_path": str(tmp_path),
        "paper_path": str(paper),
        "iteration": 1,
        "current_reproduction_level": "",  # Empty
        "current_level_iteration": 0,
        "reviewer_executed": False,
        "decision_status": {},
        "iteration_history": [],
        "loop_status": "completed",
        "language": "en",
    }

    # Should handle empty level gracefully
    try:
        result = write_final_report(state)
        assert result.exists()
        text = result.read_text(encoding="utf-8")
        assert "# FINAL_REPORT" in text
    except NameError as e:
        pytest.fail(f"NameError raised for empty level: {e}")


def test_progress_cards_function_receives_correct_level(tmp_path: Path) -> None:
    """Verify _progress_cards receives the correct level string."""
    from r2a.tools.iteration import _progress_cards

    # Test with L4 level
    result_l4 = _progress_cards(tmp_path, "L4_reduced_paper_aligned", "en")
    assert "Paper alignment" in result_l4 or "L4" in result_l4

    # Test with L3 level
    result_l3 = _progress_cards(tmp_path, "L3_official_reduced_run", "en")
    assert "Official reduced run" in result_l3 or "L3" in result_l3

    # Test with empty string (no level)
    result_empty = _progress_cards(tmp_path, "", "en")
    # Should not raise error, should handle gracefully
    assert isinstance(result_empty, str)
