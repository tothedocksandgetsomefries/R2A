from pathlib import Path

from r2a.tools.docker_policy import validate_docker_command


def test_docker_policy_allows_preflight_commands(tmp_path: Path) -> None:
    for command in (
        ["docker", "--version"],
        ["docker", "version"],
        ["docker", "info"],
        ["docker", "images"],
        ["docker", "ps"],
        ["docker", "image", "inspect", "nvidia/cuda:11.0.3-devel-ubuntu20.04"],
    ):
        assert validate_docker_command(command, tmp_path).allowed


def test_docker_policy_allows_safe_build_inside_repo(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")

    result = validate_docker_command(
        ["docker", "build", "-t", "r2a-test-safe", "-f", str(dockerfile), str(tmp_path)],
        tmp_path,
    )

    assert result.allowed


def test_docker_policy_allows_fanns_build_inside_artifacts(tmp_path: Path) -> None:
    artifact = tmp_path / ".r2a" / "artifacts" / "fanns-benchmark"
    artifact.mkdir(parents=True)
    dockerfile = artifact / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")

    result = validate_docker_command(
        ["docker", "build", "-t", "fanns-benchmark:latest", "-f", str(dockerfile), str(artifact)],
        tmp_path,
    )

    assert result.allowed


def test_docker_policy_blocks_dangerous_commands(tmp_path: Path) -> None:
    forbidden = (
        ["docker", "system", "prune"],
        ["docker", "volume", "rm", "data"],
        ["docker", "rmi", "ubuntu"],
        ["docker", "login"],
        ["docker", "push", "r2a-test-safe"],
        ["docker", "builder", "prune"],
        ["docker", "buildx", "prune"],
    )

    for command in forbidden:
        assert not validate_docker_command(command, tmp_path).allowed


def test_docker_policy_blocks_privileged_and_root_mount(tmp_path: Path) -> None:
    assert not validate_docker_command(["docker", "run", "--rm", "--privileged", "alpine"], tmp_path).allowed
    assert not validate_docker_command(["docker", "run", "--rm", "-v", "/:/host", "alpine"], tmp_path).allowed
    assert not validate_docker_command(["docker", "run", "--rm", "-v", "C:\\:/host", "alpine"], tmp_path).allowed


def test_docker_policy_requires_rm_for_run(tmp_path: Path) -> None:
    assert not validate_docker_command(["docker", "run", "alpine", "true"], tmp_path).allowed


def test_docker_policy_allows_workspace_mount(tmp_path: Path) -> None:
    result = validate_docker_command(
        ["docker", "run", "--rm", "-v", f"{tmp_path}:/workspace", "alpine", "true"],
        tmp_path,
    )

    assert result.allowed
