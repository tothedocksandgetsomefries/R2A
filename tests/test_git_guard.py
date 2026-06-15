from pathlib import Path
import subprocess

from r2a.tools.git_guard import inspect_repo


def test_git_guard_reports_non_git_repo(tmp_path: Path) -> None:
    report = inspect_repo(tmp_path)

    assert not report.is_git_repo
    assert report.clean
    assert report.warnings


def test_git_guard_reports_dirty_git_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    (tmp_path / "file.txt").write_text("dirty\n", encoding="utf-8")

    report = inspect_repo(tmp_path)

    assert report.is_git_repo
    assert not report.clean
    assert report.changed_files


def test_git_guard_ignores_r2a_runtime_artifacts(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    results_dir = tmp_path / ".r2a" / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "runtime_smoke.csv").write_text("status\nPASS\n", encoding="utf-8")

    report = inspect_repo(tmp_path)

    assert report.is_git_repo
    assert report.clean
    assert report.changed_files == []
    assert report.warnings == []
