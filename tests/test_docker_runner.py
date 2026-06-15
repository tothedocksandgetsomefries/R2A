from pathlib import Path
import subprocess

from r2a.tools import docker_runner


def test_docker_runner_writes_build_and_manifest_csv(tmp_path: Path, monkeypatch) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")

    def fake_run(command, **kwargs):
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, "sha256:testimage\n", "")
        return subprocess.CompletedProcess(command, 0, "built\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    code = docker_runner.main([
        "--repo", str(tmp_path),
        "--timeout", "10",
        "build",
        "--tag", "r2a-test-safe",
        "--dockerfile", str(dockerfile),
        "--context", str(tmp_path),
    ])

    assert code == 0
    build_csv = (tmp_path / ".r2a" / "results" / "docker_build.csv").read_text(encoding="utf-8")
    manifest_csv = (tmp_path / ".r2a" / "results" / "command_manifest.csv").read_text(encoding="utf-8")
    assert "command,exit_code,duration_sec,log_path" in build_csv
    assert "r2a-test-safe" in build_csv
    assert "docker_build" in manifest_csv
    assert "sha256:testimage" in manifest_csv


def test_docker_runner_blocks_missing_run_image_without_pull(tmp_path: Path, monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "No such image\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    code = docker_runner.main([
        "--repo", str(tmp_path),
        "--timeout", "10",
        "run-smoke",
        "--image", "r2a-missing-image",
    ])

    assert code == 126
    status_csv = (tmp_path / ".r2a" / "results" / "reproduction_status.csv").read_text(encoding="utf-8")
    smoke_csv = (tmp_path / ".r2a" / "results" / "docker_runtime_smoke.csv").read_text(encoding="utf-8")
    assert "NEEDS_INPUT_OR_BUDGET" in status_csv
    assert "NEEDS_INPUT_OR_BUDGET" in smoke_csv
