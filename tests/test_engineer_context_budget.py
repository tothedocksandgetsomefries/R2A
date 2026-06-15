from __future__ import annotations

from pathlib import Path

from r2a.core.paths import artifact_dir, report_path
from r2a.tools.codex_runner import (
    ENGINEER_PROMPT_CONTEXT_BUDGET_CHARS,
    build_codex_exec_prompt,
    build_engineer_artifact_context,
)


def test_engineer_context_tails_large_logs_and_preserves_path(tmp_path: Path) -> None:
    results = artifact_dir(tmp_path) / "results"
    results.mkdir(parents=True)
    log_path = results / "large.log"
    log_path.write_text("STARTMARK\n" + ("x" * 8000) + "\nENDMARK\n", encoding="utf-8")

    context = build_engineer_artifact_context(tmp_path, max_chars=9000, text_tail_chars=2000)

    assert ".r2a/results/large.log" in context
    assert f"original_path: `{log_path}`" in context
    assert "truncated=true" in context
    assert "ENDMARK" in context
    assert "STARTMARK" not in context


def test_engineer_context_summarizes_csv_instead_of_embedding_full_file(tmp_path: Path) -> None:
    results = artifact_dir(tmp_path) / "results"
    results.mkdir(parents=True)
    csv_path = results / "metrics.csv"
    rows = ["metric,value,notes"]
    rows.extend(f"recall,{idx},row-{idx:04d}" for idx in range(50))
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    context = build_engineer_artifact_context(tmp_path, max_chars=9000, csv_sample_rows=3)

    assert ".r2a/results/metrics.csv" in context
    assert "columns" in context
    assert "row_count: 50" in context
    assert "sample_rows" in context
    assert "numeric_summary" in context
    assert "row-0000" in context
    assert "row-0049" not in context


def test_engineer_context_keeps_small_text_file_complete(tmp_path: Path) -> None:
    results = artifact_dir(tmp_path) / "results"
    results.mkdir(parents=True)
    note = results / "small.txt"
    note.write_text("small file complete\nsecond line\n", encoding="utf-8")

    context = build_engineer_artifact_context(tmp_path, max_chars=9000, text_tail_chars=2000)

    assert "truncated=false" in context
    assert "small file complete\nsecond line" in context


def test_engineer_prompt_stays_below_budget_with_large_artifacts(tmp_path: Path) -> None:
    report_path(tmp_path, "task").parent.mkdir(parents=True)
    task_spec = report_path(tmp_path, "task")
    task_spec.write_text(
        "# TASK_SPEC\n\n"
        "## Goal\n\nRun bounded task.\n\n"
        "## Allowed Files\n\n- .r2a/results/output.csv\n\n"
        "## Forbidden Files\n\n- .git/\n\n"
        "## Acceptance Criteria\n\n- output.csv exists.\n\n"
        "## Stop Conditions\n\n- Stop when output.csv exists.\n",
        encoding="utf-8",
    )
    results = artifact_dir(tmp_path) / "results"
    results.mkdir(parents=True)
    (results / "huge.log").write_text("a" * 200_000 + "TAIL", encoding="utf-8")

    prompt = build_codex_exec_prompt(tmp_path, task_spec)

    assert len(prompt) <= ENGINEER_PROMPT_CONTEXT_BUDGET_CHARS
    assert "Engineer context budget:" in prompt
    assert "huge.log" in prompt
    assert "truncated=true" in prompt
    assert "TASK_SPEC.md content" in prompt
