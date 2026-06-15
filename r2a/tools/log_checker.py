from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


ERROR_PATTERNS = (
    re.compile(r"\bERROR\b", re.IGNORECASE),
    re.compile(r"\bTraceback \(most recent call last\):"),
    re.compile(r"\bException\b"),
)

IGNORED_CODEX_NOISE = (
    "remote plugin sync request",
    "startup remote plugin sync failed",
    "failed to warm featured plugin ids cache",
    "backend-api/plugins/",
    "backend-api/codex/analytics-events",
    "challenge-error-text",
    "cloudflare",
    "codex_core::tools::router",
    "you've hit your usage limit",
    "chatgpt.com/codex/settings/usage",
    "--ws-error-highlight",
)

IGNORED_R2A_LOG_NAMES = {
    "manager_stderr.log",
    "manager_stdout.log",
    "reviewer_stderr.log",
    "reviewer_stdout.log",
}


@dataclass(frozen=True)
class LogCheckIssue:
    file: str
    line: int
    level: str
    message: str


@dataclass(frozen=True)
class LogCheckReport:
    checked_files: list[str]
    issues: list[LogCheckIssue]

    @property
    def passed(self) -> bool:
        return not any(issue.level == "error" for issue in self.issues)


def check_log_file(path: str | Path) -> list[LogCheckIssue]:
    log_path = Path(path)
    issues: list[LogCheckIssue] = []
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return [LogCheckIssue(str(log_path), 0, "error", f"Unable to read log: {exc}")]

    for number, line in enumerate(lines, start=1):
        if _is_ignored_codex_noise(line):
            continue
        if any(pattern.search(line) for pattern in ERROR_PATTERNS):
            issues.append(LogCheckIssue(str(log_path), number, "error", line.strip()))
    return issues


def _is_ignored_codex_noise(line: str) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in IGNORED_CODEX_NOISE)


def check_logs(root: str | Path, *, since_mtime: float | None = None) -> LogCheckReport:
    root_path = Path(root)
    candidates = sorted(_iter_log_candidates(root_path, since_mtime=since_mtime))
    issues: list[LogCheckIssue] = []
    for path in candidates:
        issues.extend(check_log_file(path))
    return LogCheckReport([str(path) for path in candidates], issues)


def _iter_log_candidates(root_path: Path, *, since_mtime: float | None = None):
    artifact_logs = root_path / ".r2a" / "logs"
    if artifact_logs.exists():
        yield from (
            path
            for path in artifact_logs.glob("*.log")
            if path.name not in IGNORED_R2A_LOG_NAMES and _is_current_log(path, since_mtime)
        )
    for pattern in ("*.log", "*.txt"):
        for path in root_path.rglob(pattern):
            if ".git" in path.parts:
                continue
            if ".r2a" in path.parts or _is_archived_iteration_log(path):
                continue
            if not _is_current_log(path, since_mtime):
                continue
            yield path


def _is_archived_iteration_log(path: Path) -> bool:
    parts = set(path.parts)
    return ".r2a" in parts and "runs" in parts


def _is_current_log(path: Path, since_mtime: float | None) -> bool:
    if since_mtime is None:
        return True
    try:
        return path.stat().st_mtime >= since_mtime
    except OSError:
        return True
