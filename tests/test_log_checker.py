from pathlib import Path

from r2a.tools.log_checker import check_logs


def test_log_checker_skips_manager_and_reviewer_logs(tmp_path: Path) -> None:
    logs_dir = tmp_path / ".r2a" / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "manager_stderr.log").write_text("ERROR old check report text\n", encoding="utf-8")
    (logs_dir / "reviewer_stderr.log").write_text("ERROR old review report text\n", encoding="utf-8")
    (logs_dir / "engineer_stderr.log").write_text("all good\n", encoding="utf-8")

    report = check_logs(tmp_path)

    assert report.passed
    assert str(logs_dir / "manager_stderr.log") not in report.checked_files
    assert str(logs_dir / "reviewer_stderr.log") not in report.checked_files


def test_log_checker_ignores_codex_quota_and_router_noise(tmp_path: Path) -> None:
    logs_dir = tmp_path / ".r2a" / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "planner_stderr.log").write_text(
        "ERROR: You've hit your usage limit. Upgrade to Pro.\n"
        "2026-05-17 ERROR codex_core::tools::router: error=Exit code: 1\n"
        "--ws-error-highlight <kind>\n",
        encoding="utf-8",
    )

    report = check_logs(tmp_path)

    assert report.passed
    assert report.issues == []
