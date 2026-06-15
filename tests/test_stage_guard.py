from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from r2a.tools.codex_cli import CodexCliCheckResult
from r2a.tools.codex_stage_runner import run_codex_stage
from r2a.tools.process_tree import ProcessResult
from r2a.tools.stage_guard import check_stage_allowed_modifications, snapshot_stage_changes


def test_stage_guard_allows_only_allowed_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.stage_guard.shutil.which", lambda name: "git.exe")
    monkeypatch.setattr(
        "r2a.tools.stage_guard.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=" M .r2a/PAPER_BRIEF.md\n", stderr=""),
    )

    result = check_stage_allowed_modifications(tmp_path, "paper", [".r2a/PAPER_BRIEF.md"])

    assert result["ok"] is True
    assert result["guard_available"] is True
    assert result["unexpected_modifications"] == []


def test_stage_guard_expands_untracked_artifact_directory(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    artifact_dir = tmp_path / ".r2a"
    artifact_dir.mkdir()
    (artifact_dir / "PAPER_CONTEXT.md").write_text("# PAPER_CONTEXT\n", encoding="utf-8")
    (artifact_dir / "PAPER_BRIEF.md").write_text("# PAPER_BRIEF\n", encoding="utf-8")

    result = check_stage_allowed_modifications(
        tmp_path,
        "paper",
        [".r2a/PAPER_CONTEXT.md", ".r2a/PAPER_BRIEF.md"],
    )

    assert result["ok"] is True
    assert ".r2a/PAPER_CONTEXT.md" in result["changed_files"]
    assert ".r2a/PAPER_BRIEF.md" in result["changed_files"]
    assert ".r2a/" not in result["changed_files"]
    assert result["unexpected_modifications"] == []


def test_paper_stage_allows_only_paper_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.stage_guard.shutil.which", lambda name: "git.exe")
    monkeypatch.setattr(
        "r2a.tools.stage_guard.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=" M .r2a/PAPER_BRIEF.md\n M src/foo.py\n", stderr=""),
    )

    result = check_stage_allowed_modifications(tmp_path, "paper", [".r2a/PAPER_BRIEF.md", ".r2a/PAPER_EVIDENCE.md"])

    assert ".r2a/PAPER_BRIEF.md" in result["changed_files"]
    assert "src/foo.py" in result["unexpected_modifications"]
    assert result["ok"] is False
    assert result["guard_available"] is True


def test_planner_stage_allows_task_spec(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.stage_guard.shutil.which", lambda name: "git.exe")
    monkeypatch.setattr(
        "r2a.tools.stage_guard.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=" M .r2a/TASK_SPEC.md\n M src/foo.py\n", stderr=""),
    )

    result = check_stage_allowed_modifications(tmp_path, "planner", [".r2a/TASK_SPEC.md"])

    assert "src/foo.py" in result["unexpected_modifications"]


def test_planner_stage_rejects_artifact_dataset_write(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.stage_guard.shutil.which", lambda name: "git.exe")
    monkeypatch.setattr(
        "r2a.tools.stage_guard.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=" M .r2a/TASK_SPEC.md\n M .r2a/artifacts/datasets/foo/query_vectors.fvecs\n",
            stderr="",
        ),
    )

    result = check_stage_allowed_modifications(
        tmp_path,
        "planner",
        [".r2a/TASK_SPEC.md", ".r2a/EXPERIMENT_CONTRACT.md", ".r2a/logs/planner_*"],
    )

    assert result["ok"] is False
    assert ".r2a/artifacts/datasets/foo/query_vectors.fvecs" in result["unexpected_modifications"]
    assert result["failure_category"] == "STAGE_BOUNDARY_VIOLATION"
    assert result["execution_status"] == "PLANNER_FORBIDDEN_WRITE"


def test_planner_stage_rejects_results_write(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.stage_guard.shutil.which", lambda name: "git.exe")
    monkeypatch.setattr(
        "r2a.tools.stage_guard.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=" M .r2a/results/reduced_metrics.csv\n",
            stderr="",
        ),
    )

    result = check_stage_allowed_modifications(tmp_path, "planner", [".r2a/TASK_SPEC.md", ".r2a/EXPERIMENT_CONTRACT.md"])

    assert result["ok"] is False
    assert ".r2a/results/reduced_metrics.csv" in result["unexpected_modifications"]
    assert result["execution_status"] == "PLANNER_FORBIDDEN_WRITE"


def test_reviewer_stage_allows_review_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.stage_guard.shutil.which", lambda name: "git.exe")
    monkeypatch.setattr(
        "r2a.tools.stage_guard.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=" M .r2a/REVIEW_REPORT.md\n M tests/test_x.py\n", stderr=""),
    )

    result = check_stage_allowed_modifications(tmp_path, "reviewer", [".r2a/REVIEW_REPORT.md"])

    assert "tests/test_x.py" in result["unexpected_modifications"]


def test_stage_guard_allows_claude_retry_attempt_logs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.stage_guard.shutil.which", lambda name: "git.exe")
    monkeypatch.setattr(
        "r2a.tools.stage_guard.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                " M .r2a/logs/claude_planner_attempt_1_stdout.log\n"
                " M .r2a/logs/claude_planner_attempt_1_stderr.log\n"
                " M .r2a/runs/iter_008/logs/claude_reviewer_attempt_2_stdout.log\n"
                " M .r2a/runs/iter_008/logs/claude_reviewer_attempt_2_stderr.log\n"
            ),
            stderr="",
        ),
    )

    result = check_stage_allowed_modifications(tmp_path, "planner", [".r2a/TASK_SPEC.md"])

    assert result["ok"] is True
    assert result["unexpected_modifications"] == []


def test_stage_guard_allows_stage_stdout_stderr_archive_logs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.stage_guard.shutil.which", lambda name: "git.exe")
    monkeypatch.setattr(
        "r2a.tools.stage_guard.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                " M .r2a/runs/iter_001/logs/planner_stdout.log\n"
                " M .r2a/runs/iter_001/logs/planner_stderr.log\n"
                " M .r2a/runs/iter_001/logs/paper_stdout.log\n"
                " M .r2a/runs/iter_001/logs/reviewer_stderr.log\n"
            ),
            stderr="",
        ),
    )

    result = check_stage_allowed_modifications(tmp_path, "planner", [".r2a/TASK_SPEC.md"])

    assert result["ok"] is True
    assert result["unexpected_modifications"] == []


def test_stage_guard_rejects_agent_runtime_directory_write(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.stage_guard.shutil.which", lambda name: "git.exe")
    monkeypatch.setattr(
        "r2a.tools.stage_guard.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=" M .r2a/runtime/runs/fake.json\n",
            stderr="",
        ),
    )

    result = check_stage_allowed_modifications(tmp_path, "planner", [".r2a/TASK_SPEC.md"])

    assert result["ok"] is False
    assert ".r2a/runtime/runs/fake.json" in result["unexpected_modifications"]
    assert result["execution_status"] == "PLANNER_FORBIDDEN_WRITE"


def test_stage_guard_ignores_baseline_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.stage_guard.shutil.which", lambda name: "git.exe")
    monkeypatch.setattr(
        "r2a.tools.stage_guard.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=" M src/preexisting.py\n M .r2a/TASK_SPEC.md\n", stderr=""),
    )

    result = check_stage_allowed_modifications(tmp_path, "planner", [".r2a/TASK_SPEC.md"], baseline={"src/preexisting.py"})

    assert "src/preexisting.py" in result["changed_files"]
    assert "src/preexisting.py" not in result["stage_changed_files"]
    assert result["unexpected_modifications"] == []
    assert result["ok"] is True


def test_stage_guard_rejects_modified_baseline_dirty_disallowed_file(tmp_path: Path, monkeypatch) -> None:
    baseline = {
        "guard_available": True,
        "error": "",
        "dirty_files": ["src/foo.py"],
        "changed_files": ["src/foo.py"],
        "dirty_file_signatures": {"src/foo.py": "old_hash"},
    }
    monkeypatch.setattr(
        "r2a.tools.stage_guard.snapshot_stage_changes",
        lambda repo_path: {
            "guard_available": True,
            "error": "",
            "dirty_files": ["src/foo.py"],
            "changed_files": ["src/foo.py"],
            "dirty_file_signatures": {"src/foo.py": "new_hash"},
        },
    )

    result = check_stage_allowed_modifications(tmp_path, "paper", [".r2a/PAPER_BRIEF.md"], baseline=baseline)

    assert result["ok"] is False
    assert result["baseline_changed_files"] == ["src/foo.py"]
    assert result["signature_changed_files"] == ["src/foo.py"]
    assert "src/foo.py" in result["unexpected_modifications"]


def test_stage_guard_allows_modified_baseline_dirty_allowed_file(tmp_path: Path, monkeypatch) -> None:
    baseline = {
        "guard_available": True,
        "error": "",
        "dirty_files": [".r2a/PAPER_BRIEF.md"],
        "changed_files": [".r2a/PAPER_BRIEF.md"],
        "dirty_file_signatures": {".r2a/PAPER_BRIEF.md": "old_hash"},
    }
    monkeypatch.setattr(
        "r2a.tools.stage_guard.snapshot_stage_changes",
        lambda repo_path: {
            "guard_available": True,
            "error": "",
            "dirty_files": [".r2a/PAPER_BRIEF.md"],
            "changed_files": [".r2a/PAPER_BRIEF.md"],
            "dirty_file_signatures": {".r2a/PAPER_BRIEF.md": "new_hash"},
        },
    )

    result = check_stage_allowed_modifications(tmp_path, "paper", [".r2a/PAPER_BRIEF.md"], baseline=baseline)

    assert result["ok"] is True
    assert result["signature_changed_files"] == [".r2a/PAPER_BRIEF.md"]
    assert result["unexpected_modifications"] == []


def test_stage_guard_rejects_new_disallowed_file_with_snapshot_baseline(tmp_path: Path, monkeypatch) -> None:
    baseline = {
        "guard_available": True,
        "error": "",
        "dirty_files": [".r2a/PAPER_BRIEF.md"],
        "changed_files": [".r2a/PAPER_BRIEF.md"],
        "dirty_file_signatures": {".r2a/PAPER_BRIEF.md": "same_hash"},
    }
    monkeypatch.setattr(
        "r2a.tools.stage_guard.snapshot_stage_changes",
        lambda repo_path: {
            "guard_available": True,
            "error": "",
            "dirty_files": [".r2a/PAPER_BRIEF.md", "src/new_file.py"],
            "changed_files": [".r2a/PAPER_BRIEF.md", "src/new_file.py"],
            "dirty_file_signatures": {".r2a/PAPER_BRIEF.md": "same_hash", "src/new_file.py": "new_hash"},
        },
    )

    result = check_stage_allowed_modifications(tmp_path, "paper", [".r2a/PAPER_BRIEF.md"], baseline=baseline)

    assert result["ok"] is False
    assert result["new_dirty_files"] == ["src/new_file.py"]
    assert "src/new_file.py" in result["unexpected_modifications"]


def test_stage_guard_uses_filesystem_fallback_when_git_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.stage_guard.shutil.which", lambda name: None)
    artifact = tmp_path / ".r2a" / "PAPER_BRIEF.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# PAPER_BRIEF\n", encoding="utf-8")

    result = check_stage_allowed_modifications(tmp_path, "paper", [".r2a/PAPER_BRIEF.md"])

    assert result["ok"] is True
    assert result["guard_available"] is True
    assert result["guard_backend"] == "filesystem"
    assert "git executable not found" in result["error"]
    assert "filesystem snapshot fallback" in result["warning"]


def test_stage_guard_uses_filesystem_fallback_when_git_status_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.stage_guard.shutil.which", lambda name: "git.exe")
    monkeypatch.setattr(
        "r2a.tools.stage_guard.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=128, stdout="", stderr="fatal: not a git repository"),
    )

    result = check_stage_allowed_modifications(tmp_path, "planner", [".r2a/TASK_SPEC.md"])

    assert result["ok"] is True
    assert result["guard_available"] is True
    assert result["guard_backend"] == "filesystem"
    assert "not a git repository" in result["error"]


def test_stage_guard_filesystem_fallback_rejects_disallowed_new_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("r2a.tools.stage_guard.shutil.which", lambda name: None)
    baseline = snapshot_stage_changes(tmp_path)
    source = tmp_path / "src" / "new_file.py"
    source.parent.mkdir()
    source.write_text("print('unexpected')\n", encoding="utf-8")

    result = check_stage_allowed_modifications(tmp_path, "planner", [".r2a/TASK_SPEC.md"], baseline=baseline)

    assert result["ok"] is False
    assert result["guard_available"] is True
    assert result["guard_backend"] == "filesystem"
    assert "src/new_file.py" in result["unexpected_modifications"]


def test_non_engineer_codex_stage_uses_filesystem_guard_when_git_unavailable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "r2a.tools.codex_stage_runner.check_codex_cli",
        lambda path=None: CodexCliCheckResult(True, path or "codex", path or "codex", "codex 1.0", "", "ok"),
    )
    monkeypatch.setattr(
        "r2a.tools.codex_stage_runner.run_command_with_timeout",
        lambda *args, **kwargs: ProcessResult(returncode=0, stdout="ok", stderr=""),
    )
    monkeypatch.setattr("r2a.tools.stage_guard.shutil.which", lambda name: None)

    result = run_codex_stage(tmp_path, "reviewer", "write review", [".r2a/REVIEW_REPORT.md"])

    assert result["success"] is True
    assert result["stage_guard_ok"] is True
    assert result["guard_available"] is True
    assert result["guard_backend"] == "filesystem"
    assert "git executable not found" in result["stage_guard_error"]
    assert "filesystem snapshot fallback" in result["stderr_tail"]
