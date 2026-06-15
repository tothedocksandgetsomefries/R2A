from __future__ import annotations

import json
from pathlib import Path

import pytest

from r2a.core.paths import artifact_dir, report_path
from r2a.core.state import make_initial_state
from r2a.tools.workflow_decision import (
    PAPER_STRUCTURED_KEYS,
    _source_blockers,
    _source_rows_successful,
    aggregate_terminal_decision,
)


def test_artifact_source_prevents_empty_repository_scaffold_blocker(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (artifact_dir(tmp_path) / "results").mkdir(parents=True)
    _write_source_artifact(tmp_path)

    assert _source_blockers(tmp_path) == []


def test_source_verification_status_verified_preserved_is_success() -> None:
    rows = [{"source_verification_status": "VERIFIED_PRESERVED"}]

    assert _source_rows_successful(rows) is True


def test_source_acquisition_available_prevents_empty_repository_scaffold_blocker(tmp_path: Path) -> None:
    source_root = _write_source_artifact(tmp_path)
    report_path(tmp_path, "source_acquisition").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "source_status": "available",
                "local_path": str(source_root),
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )

    assert _source_blockers(tmp_path) == []


def test_empty_scaffold_without_source_or_successful_rows_still_blocks(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (artifact_dir(tmp_path) / "results").mkdir(parents=True)

    blockers = _source_blockers(tmp_path)

    assert blockers
    assert blockers[0]["id"] == "empty_repository_scaffold"


@pytest.mark.parametrize("status", ["FAILED", "NOT_FOUND", "NEEDS_INPUT"])
def test_failed_source_statuses_are_not_success(status: str) -> None:
    rows = [{"source_verification_status": status}]

    assert _source_rows_successful(rows) is False


def test_latest_false_positive_source_layout_does_not_request_source(tmp_path: Path) -> None:
    paper = _write_paper_bundle(tmp_path)
    source_root = _write_scaffold_repo_with_artifact_source(tmp_path)
    report_path(tmp_path, "source_acquisition").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "source_status": "available",
                "source_type": "official",
                "repo_url": "https://github.com/spcl/fanns-benchmark",
                "local_path": str(source_root),
                "commit": "ca8f0c2a9bfc0122669fecb412eaa564718ab221",
                "branch": "main",
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )
    report_path(tmp_path, "source_inspection").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "inspection_status": "complete",
                "repo_root": str(source_root),
                "readme_files": ["README.md"],
                "environment_files": ["compute_ground_truth/Makefile", "exhaustive_search/Makefile"],
                "entrypoints": ["benchmark.py"],
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )

    state = make_initial_state(tmp_path, paper_path=paper, auto_iterate=False)
    state.update(
        {
            "paper_readiness": {"ready": True},
            "planner_readiness": {"ready": True},
            "engineer_readiness": {"ready": True},
            "engineer_status": "PASS",
            "manager_status": "PASS",
            "reviewer_executed": True,
            "reviewer_verdict": "NEEDS_FIX",
        }
    )

    decision = aggregate_terminal_decision(state)

    assert decision["typed_decision"] != "request_source"
    assert all(blocker.get("reason_code") != "EMPTY_REPOSITORY_SCAFFOLD" for blocker in decision.get("active_blockers", []))


def _write_scaffold_repo_with_artifact_source(repo: Path) -> Path:
    (repo / ".git").mkdir(exist_ok=True)
    (artifact_dir(repo) / "results").mkdir(parents=True, exist_ok=True)
    source_root = _write_source_artifact(repo)
    (artifact_dir(repo) / "results" / "source_verification.csv").write_text(
        "source_verification_status,artifact_url,source_path,branch,commit,readme_found,build_docs_found,experiment_scripts_found,data_scripts_found,notes\n"
        f"VERIFIED_PRESERVED,https://github.com/spcl/fanns-benchmark,{source_root},main,abc123,YES,YES,YES,YES,preserved source\n",
        encoding="utf-8",
    )
    return source_root


def _write_source_artifact(repo: Path) -> Path:
    source_root = artifact_dir(repo) / "artifacts" / "source"
    (source_root / "compute_ground_truth").mkdir(parents=True, exist_ok=True)
    (source_root / "exhaustive_search").mkdir(parents=True, exist_ok=True)
    (source_root / "README.md").write_text("# source\n", encoding="utf-8")
    (source_root / "benchmark.py").write_text("print('benchmark')\n", encoding="utf-8")
    (source_root / "compute_ground_truth" / "Makefile").write_text("all:\n\t@echo ok\n", encoding="utf-8")
    (source_root / "exhaustive_search" / "Makefile").write_text("all:\n\t@echo ok\n", encoding="utf-8")
    return source_root


def _write_paper_bundle(repo: Path) -> Path:
    paper = repo / "paper.txt"
    paper.write_text("paper text", encoding="utf-8")
    for key in PAPER_STRUCTURED_KEYS:
        path = report_path(repo, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"source_or_artifact_urls":[]}' if key == "paper_output" else f"# {key}\n\nok\n", encoding="utf-8")
    return paper
