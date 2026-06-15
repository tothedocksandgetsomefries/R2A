from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_planner_prompt_scopes_l3_l4_continuation_work() -> None:
    text = (ROOT / "r2a" / "prompts" / "planner_v2.md").read_text(encoding="utf-8")

    assert "L3/L4 continuation guidance" in text
    assert "L4_reduced_paper_aligned evidence" in text
    assert "plan only the smallest closure task" in text
    assert "does not expand L4 reduced scope into full reproduction" in text
    assert "does not authorize full-scale benchmarks" in text


def test_reviewer_prompt_scopes_l3_l4_next_iteration_guidance() -> None:
    text = (ROOT / "r2a" / "prompts" / "reviewer_codex.md").read_text(encoding="utf-8")

    assert "If L4_reduced_paper_aligned evidence already exists and iteration budget remains" in text
    assert "recommend only narrow closure work" in text
    assert "Do not recommend broad source acquisition" in text
    assert "must not expand L4 reduced scope into full reproduction" in text
    assert "new source discovery" in text
