from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class GitGuardReport:
    repo_path: str
    is_git_repo: bool
    clean: bool
    changed_files: list[str]
    warnings: list[str]


def _git(repo_path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=False,
    )


def is_git_repo(repo_path: str | Path) -> bool:
    repo = Path(repo_path)
    result = _git(repo, ["rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def git_status_porcelain(repo_path: str | Path) -> list[str]:
    repo = Path(repo_path)
    result = _git(repo, ["status", "--porcelain"])
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def inspect_repo(repo_path: str | Path) -> GitGuardReport:
    repo = Path(repo_path)
    warnings: list[str] = []
    if not is_git_repo(repo):
        warnings.append("Target path is not a git repository; git safety checks are limited.")
        return GitGuardReport(str(repo), False, True, [], warnings)

    changed = git_status_porcelain(repo)
    significant_changed = [line for line in changed if not _is_r2a_runtime_change(line)]
    if significant_changed:
        warnings.append("Repository has uncommitted changes before R2A execution.")
    return GitGuardReport(str(repo), True, not significant_changed, significant_changed, warnings)


def _is_r2a_runtime_change(status_line: str) -> bool:
    path = status_line[3:] if len(status_line) > 3 else status_line
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    normalized = path.replace("\\", "/").strip()
    return normalized == ".r2a" or normalized.startswith(".r2a/")
