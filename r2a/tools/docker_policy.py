from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex

from r2a.core.paths import artifact_dir


SAFE_TAG_PATTERN = re.compile(r"^(r2a-[a-z0-9_.-]+|fanns-benchmark(?::[a-z0-9_.-]+)?)$", re.IGNORECASE)
SYSTEM_PATHS = {"/", "/home", "/root", "/usr", "/etc", "/var", "/mnt", "c:/", "c:\\", "e:/", "e:\\"}


@dataclass(frozen=True)
class DockerPolicyResult:
    allowed: bool
    reason: str = ""


def validate_docker_command(command: str | list[str], repo_path: str | Path, *, cache_dir: str | Path | None = None) -> DockerPolicyResult:
    parts = _coerce_command(command)
    if not parts or parts[0] != "docker":
        return DockerPolicyResult(False, "Command must start with docker.")
    if _contains_dangerous_text(parts):
        return DockerPolicyResult(False, "Docker command contains a forbidden high-risk option or subcommand.")
    if len(parts) < 2:
        return DockerPolicyResult(False, "Docker subcommand is required.")

    subcommand = parts[1]
    if subcommand in {"--version", "version", "info", "images", "ps"}:
        return DockerPolicyResult(True)
    if parts[:3] == ["docker", "image", "inspect"] and len(parts) >= 4:
        return DockerPolicyResult(True)
    if subcommand == "build":
        return _validate_build(parts, repo_path)
    if subcommand == "run":
        return _validate_run(parts, repo_path, cache_dir=cache_dir)
    return DockerPolicyResult(False, f"Docker subcommand is not allowed: {subcommand}")


def _validate_build(parts: list[str], repo_path: str | Path) -> DockerPolicyResult:
    tag = _option_value(parts, "-t") or _option_value(parts, "--tag")
    dockerfile = _option_value(parts, "-f") or _option_value(parts, "--file")
    context = parts[-1] if parts else ""
    if not tag or not SAFE_TAG_PATTERN.match(tag):
        return DockerPolicyResult(False, "Docker build tag must be safe, such as r2a-* or fanns-benchmark:*.")
    if not dockerfile:
        return DockerPolicyResult(False, "Docker build must specify -f <Dockerfile>.")
    if not _path_allowed(dockerfile, repo_path):
        return DockerPolicyResult(False, "Dockerfile must be inside the repo or .r2a/artifacts.")
    if not _path_allowed(context, repo_path):
        return DockerPolicyResult(False, "Docker build context must be inside the repo or .r2a/artifacts.")
    return DockerPolicyResult(True)


def _validate_run(parts: list[str], repo_path: str | Path, *, cache_dir: str | Path | None) -> DockerPolicyResult:
    if "--rm" not in parts:
        return DockerPolicyResult(False, "Docker run smoke commands must include --rm.")
    for part in parts:
        if part == "--privileged":
            return DockerPolicyResult(False, "Docker run must not use --privileged.")
    volumes = _volume_specs(parts)
    for volume in volumes:
        host_path = _volume_host_path(volume)
        if not _volume_host_allowed(host_path, repo_path, cache_dir=cache_dir):
            return DockerPolicyResult(False, f"Docker volume host path is not allowed: {host_path}")
    return DockerPolicyResult(True)


def _contains_dangerous_text(parts: list[str]) -> bool:
    text = " ".join(parts).lower()
    forbidden_phrases = (
        "docker system prune",
        "docker container prune",
        "docker image prune",
        "docker volume prune",
        "docker network prune",
        "docker volume rm",
        "docker image rm",
        "docker rmi",
        "docker rm",
        "docker compose down -v",
        "docker compose rm",
        "docker builder prune",
        "docker buildx prune",
        "docker login",
        "docker push",
    )
    if any(phrase in text for phrase in forbidden_phrases):
        return True
    if "--privileged" in parts:
        return True
    return any(_looks_like_root_mount(part) for part in parts)


def _looks_like_root_mount(value: str) -> bool:
    normalized = value.replace("\\", "/").lower()
    return normalized.startswith("/:") or normalized.startswith("/:/") or normalized.startswith("c:/:") or normalized.startswith("c::") or normalized.startswith("~:")


def _option_value(parts: list[str], *names: str) -> str:
    for i, part in enumerate(parts):
        if part in names and i + 1 < len(parts):
            return parts[i + 1]
        for name in names:
            prefix = f"{name}="
            if part.startswith(prefix):
                return part[len(prefix):]
    return ""


def _volume_specs(parts: list[str]) -> list[str]:
    specs: list[str] = []
    for i, part in enumerate(parts):
        if part in {"-v", "--volume"} and i + 1 < len(parts):
            specs.append(parts[i + 1])
        elif part.startswith("-v") and len(part) > 2:
            specs.append(part[2:])
        elif part.startswith("--volume="):
            specs.append(part.split("=", 1)[1])
    return specs


def _volume_host_path(spec: str) -> str:
    normalized = spec.replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", normalized):
        marker = normalized.find(":", 2)
        return spec[:marker] if marker != -1 else spec
    return spec.split(":", 1)[0]


def _path_allowed(path: str | Path, repo_path: str | Path) -> bool:
    candidate = _resolve_against_repo(path, repo_path)
    repo = Path(repo_path).resolve()
    allowed_roots = [repo, artifact_dir(repo).resolve() / "artifacts"]
    return any(_is_relative_to(candidate, root) for root in allowed_roots)


def _volume_host_allowed(path: str | Path, repo_path: str | Path, *, cache_dir: str | Path | None) -> bool:
    text = str(path).strip().strip("\"'")
    if text in {"", "~"}:
        return False
    normalized = text.replace("\\", "/").lower()
    if normalized in SYSTEM_PATHS or normalized.startswith("/:/") or normalized.startswith("c:/:"):
        return False
    candidate = _resolve_against_repo(text, repo_path)
    repo = Path(repo_path).resolve()
    r2a_dir = artifact_dir(repo).resolve()
    allowed_roots = [
        repo,
        r2a_dir,
        r2a_dir / "artifacts",
        r2a_dir / "results",
        r2a_dir / "logs",
    ]
    if cache_dir:
        allowed_roots.append(Path(cache_dir).expanduser().resolve())
    return any(_is_relative_to(candidate, root) for root in allowed_roots)


def _resolve_against_repo(path: str | Path, repo_path: str | Path) -> Path:
    candidate = Path(str(path).strip().strip("\"'")).expanduser()
    if not candidate.is_absolute():
        candidate = Path(repo_path) / candidate
    return candidate.resolve()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _coerce_command(command: str | list[str]) -> list[str]:
    if isinstance(command, str):
        return shlex.split(command, posix=False)
    return [str(part) for part in command]
